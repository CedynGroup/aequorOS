# pyright: standard
"""Filing: every refusal of SPEC Part 1 (no receipt), receipts, idempotency, amendments."""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any

from jwt.api_jws import PyJWS
from sqlalchemy import func, select

from app.core.encoding import b64url_decode, jcs_sha256
from app.core.jcs import canonicalize
from app.db.models import Receipt, Submission
from app.keys.jwk import public_key_from_jwk
from app.seed.client import new_key
from tests.harness import BSD2_LINES, Harness

OCT = date(2026, 10, 31)


def _code(response: Any) -> str:
    return response.json()["code"]


def _receipts(harness: Harness) -> int:
    with harness.ctx.db.session() as session:
        return int(session.scalar(select(func.count()).select_from(Receipt)) or 0)


def _setup(harness: Harness) -> Any:
    harness.bootstrap(("BSD2", "BSD5A", "BSD6"))
    return harness.enrol()


def test_receipt_verifies_offline_against_the_published_jwks(any_harness: Harness) -> None:
    harness = any_harness
    bank = _setup(harness)
    body = harness.filing(bank, BSD2_LINES)
    response = harness.post_filing(bank, body)
    assert response.status_code == 202, response.text
    envelope = response.json()
    receipt = envelope["receipt"]
    assert receipt["receipt_id"].startswith("SRG-26-")
    assert receipt["version"] == 1
    assert receipt["payload_sha256"] == jcs_sha256(body["payload"])
    assert receipt["manifest_sha256"] == jcs_sha256(body["manifest"])
    assert receipt["signing_kid"] == bank.keys.signing_kid
    assert envelope["status"] == "validating"
    jwks = harness.client.get("/v1/.well-known/jwks.json").json()
    header = json.loads(b64url_decode(envelope["receipt_jws"].split(".")[0]))
    assert header["typ"] == "srg-receipt+jws"
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    verified = PyJWS().decode_complete(
        envelope["receipt_jws"], key=public_key_from_jwk(key), algorithms=["ES256"]
    )
    assert verified["payload"] == canonicalize(receipt)
    # The ledger entry recorded the receipt.
    entry = harness.client.get(
        f"/portal/api/audit/{receipt['ledger_seq']}", headers=harness.staff("internal-audit")
    )
    assert entry.status_code == 200
    assert entry.json()["object_ref"] == receipt["receipt_id"]
    # Validation ran after the receipt; the stored receipt is served unchanged.
    stored = harness.client.get(
        f"/v1/submissions/{receipt['receipt_id']}/receipt", headers=harness.bank_headers(bank)
    )
    assert stored.json()["receipt_jws"] == envelope["receipt_jws"]
    assert stored.json()["status"] == "in_review"
    status = harness.client.get(
        f"/v1/submissions/{receipt['receipt_id']}", headers=harness.bank_headers(bank)
    ).json()
    assert status["validation"]["failed"] == 0
    assert status["validation"]["passed"] == 34  # cross-return and period rules are "not checked"


def test_refusals_leave_no_receipt(harness: Harness) -> None:
    bank = _setup(harness)
    other = harness.enrol("0117", "Palmgrove Bank")
    headers = harness.bank_headers(bank)

    def refused(body: dict[str, Any], status: int, code: str, *, key: str | None = None) -> None:
        response = harness.post_filing(bank, body, headers=headers, key=key)
        assert response.status_code == status, (code, response.text)
        assert _code(response) == code

    good = harness.filing(bank, BSD2_LINES)
    no_key = dict(headers)
    response = harness.client.post("/v1/submissions", json=good, headers=no_key)
    assert response.status_code == 400
    assert _code(response) == "idempotency_key_required"

    unknown_kid = harness.filing(bank, BSD2_LINES, kid="nobody-sign-1")
    refused(unknown_kid, 422, "signing_key_unknown")
    others_kid = harness.filing(
        bank, BSD2_LINES, kid=other.keys.signing_kid, signing_key=other.keys.signing_key
    )
    refused(others_kid, 422, "signing_key_unknown")
    bad_signature = harness.filing(bank, BSD2_LINES, signing_key=new_key())
    refused(bad_signature, 422, "signature_invalid")
    tampered = copy.deepcopy(good)
    tampered["manifest"]["created_at"] = "2026-11-21T10:00:01.000Z"
    refused(tampered, 422, "manifest_mismatch")
    wrong_institution = harness.filing(bank, BSD2_LINES, institution_code="0117")
    refused(wrong_institution, 403, "institution_mismatch")
    payload_changed = copy.deepcopy(good)
    payload_changed["payload"]["lines"]["1.1"] += 1
    refused(payload_changed, 422, "payload_digest_mismatch")
    declaration_changed = copy.deepcopy(good)
    declaration_changed["declaration"]["approved_by_role"] = "Someone else"
    refused(declaration_changed, 422, "declaration_digest_mismatch")
    refused(harness.filing(bank, BSD2_LINES, return_code="NOPE"), 422, "return_unknown")
    refused(
        harness.filing(bank, BSD2_LINES, version="2099.1"), 422, "return_version_not_applicable"
    )
    refused(
        harness.filing(bank, BSD2_LINES, reporting_date=date(2025, 10, 31)),
        422,
        "return_version_not_applicable",
    )
    refused(
        harness.filing(bank, BSD2_LINES, reporting_date=date(2026, 10, 30)),
        422,
        "return_version_not_applicable",
    )
    missing_line = dict(BSD2_LINES)
    del missing_line["2.4"]
    schema = harness.post_filing(bank, harness.filing(bank, missing_line), headers=headers)
    assert schema.status_code == 422
    assert _code(schema) == "schema_invalid"
    assert schema.json()["issues"][0]["rule_id"] == "schema"
    extra = harness.filing(bank, {**BSD2_LINES, "9.9": 1})
    assert _code(harness.post_filing(bank, extra, headers=headers)) == "schema_invalid"
    not_integer = harness.filing(bank, {**BSD2_LINES, "1.1": "412350"})  # pyright: ignore[reportArgumentType]
    assert _code(harness.post_filing(bank, not_integer, headers=headers)) == "schema_invalid"
    malformed = copy.deepcopy(good)
    del malformed["declaration"]
    refused(malformed, 400, "invalid_request")
    assert _receipts(harness) == 0


def test_scope_needed_to_file(harness: Harness) -> None:
    bank = _setup(harness)
    response = harness.post_filing(
        bank,
        harness.filing(bank, BSD2_LINES),
        headers=harness.bank_headers(bank, "submissions:read"),
    )
    assert response.status_code == 403
    assert _code(response) == "insufficient_scope"


def test_idempotency_replay_and_conflict(any_harness: Harness) -> None:
    harness = any_harness
    bank = _setup(harness)
    headers = harness.bank_headers(bank)
    body = harness.filing(bank, BSD2_LINES)
    first = harness.post_filing(
        bank, body, headers=headers, key="0f6c3c7e-0000-4000-8000-000000000001"
    )
    replay = harness.post_filing(
        bank, body, headers=headers, key="0f6c3c7e-0000-4000-8000-000000000001"
    )
    assert replay.status_code == 202
    assert replay.json() == first.json()
    changed = harness.filing(
        bank, {**BSD2_LINES, "1.1": 1, "1": BSD2_LINES["1"] - BSD2_LINES["1.1"] + 1}
    )
    conflict = harness.post_filing(
        bank, changed, headers=headers, key="0f6c3c7e-0000-4000-8000-000000000001"
    )
    assert conflict.status_code == 409
    assert _code(conflict) == "idempotency_conflict"
    assert _receipts(harness) == 1


def test_a_retry_re_signed_over_the_same_content_replays_the_receipt(
    any_harness: Harness,
) -> None:
    """ES256 signatures are randomised: a retry re-signs the identical manifest."""
    harness = any_harness
    bank = _setup(harness)
    headers = harness.bank_headers(bank)
    first_body = harness.filing(bank, BSD2_LINES)
    retry_body = harness.filing(bank, BSD2_LINES)
    assert retry_body["manifest"] == first_body["manifest"]
    assert retry_body["signature"] != first_body["signature"]
    key = "0f6c3c7e-0000-4000-8000-000000000002"

    first = harness.post_filing(bank, first_body, headers=headers, key=key)
    retry = harness.post_filing(bank, retry_body, headers=headers, key=key)

    assert first.status_code == 202
    assert retry.status_code == 202
    assert retry.json() == first.json()
    assert _receipts(harness) == 1


def test_duplicate_and_amendment_rules(any_harness: Harness) -> None:
    harness = any_harness
    bank = _setup(harness)
    headers = harness.bank_headers(bank)
    v1 = harness.post_filing(bank, harness.filing(bank, BSD2_LINES), headers=headers).json()[
        "receipt"
    ]["receipt_id"]
    duplicate = harness.post_filing(bank, harness.filing(bank, BSD2_LINES), headers=headers)
    assert duplicate.status_code == 409
    assert _code(duplicate) == "duplicate_submission"
    lines = {**BSD2_LINES, "1.2": 286400 - 4200, "1.3": 1184900 + 4200}
    no_reason = harness.post_filing(
        bank, harness.filing(bank, lines, supersedes=v1), headers=headers, amend=v1
    )
    assert _code(no_reason) == "amendment_reason_required"
    wrong_path = harness.post_filing(
        bank,
        harness.filing(bank, lines, supersedes=v1, reason="r"),
        headers=headers,
        amend="SRG-26-9999999",
    )
    assert wrong_path.status_code == 400
    assert _code(wrong_path) == "invalid_request"
    first_version_with_supersedes = harness.post_filing(
        bank, harness.filing(bank, lines, supersedes=v1, reason="r"), headers=headers
    )
    assert _code(first_version_with_supersedes) == "invalid_request"
    amended = harness.post_filing(
        bank,
        harness.filing(bank, lines, supersedes=v1, reason="Reclassified placements"),
        headers=headers,
        amend=v1,
    )
    assert amended.status_code == 202, amended.text
    v2 = amended.json()["receipt"]
    assert v2["version"] == 2
    assert v2["supersedes_receipt_id"] == v1
    stale = harness.post_filing(
        bank, harness.filing(bank, lines, supersedes=v1, reason="again"), headers=headers, amend=v1
    )
    assert stale.status_code == 409
    assert _code(stale) == "supersedes_not_current"
    old = harness.client.get(f"/v1/submissions/{v1}", headers=headers).json()
    assert old["status"] == "superseded"
    assert old["superseded_by"] == v2["receipt_id"]
    new = harness.client.get(f"/v1/submissions/{v2['receipt_id']}", headers=headers).json()
    assert any(i["rule_id"] == "AMENDMENT" for i in new["validation"]["issues"])
    with harness.ctx.db.session() as session:
        rows = session.scalars(select(Submission).order_by(Submission.version)).all()
        assert [r.receipt_id for r in rows] == [v1, v2["receipt_id"]]


def test_rejected_filing_is_receipted_and_refile_requested(harness: Harness) -> None:
    bank = _setup(harness)
    lines = {**BSD2_LINES, "1": BSD2_LINES["1"] + 1000}
    response = harness.post_filing(bank, harness.filing(bank, lines))
    assert response.status_code == 202
    receipt_id = response.json()["receipt"]["receipt_id"]
    status = harness.client.get(
        f"/v1/submissions/{receipt_id}", headers=harness.bank_headers(bank)
    ).json()
    assert status["status"] == "rejected"
    assert status["validation"]["failed"] > 0
    queries = harness.client.get("/v1/queries", headers=harness.bank_headers(bank)).json()["items"]
    assert queries[0]["kind"] == "resubmission"
    assert queries[0]["receipt_id"] == receipt_id
    refile = harness.post_filing(
        bank,
        harness.filing(bank, BSD2_LINES, supersedes=receipt_id, reason="Corrected totals"),
        amend=receipt_id,
    )
    assert refile.status_code == 202, refile.text


def test_signing_key_retired_keeps_old_filings_verifiable(harness: Harness) -> None:
    from app.enrolment.service import RegisterKeyRequest, register_signing_key, retire_signing_key
    from app.seed.client import InstitutionKeys
    from tests.harness import OPERATOR

    bank = _setup(harness)
    receipt_id = harness.post_filing(bank, harness.filing(bank, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    newer = InstitutionKeys(signing_kid="0142-sign-2")
    with harness.ctx.db.session() as session:
        register_signing_key(
            session,
            harness.ctx,
            OPERATOR,
            bank.code,
            RegisterKeyRequest(kid=newer.signing_kid, public_jwk=newer.signing_jwk()),
        )
        retire_signing_key(session, harness.ctx, OPERATOR, bank.code, bank.keys.signing_kid)
    old_key = harness.post_filing(
        bank, harness.filing(bank, BSD2_LINES, reporting_date=date(2026, 9, 30))
    )
    assert old_key.status_code == 422
    assert _code(old_key) == "signing_key_revoked"
    new_key_filing = harness.filing(
        bank,
        BSD2_LINES,
        reporting_date=date(2026, 9, 30),
        kid=newer.signing_kid,
        signing_key=newer.signing_key,
    )
    assert harness.post_filing(bank, new_key_filing).status_code == 202
    detail = harness.client.get(
        f"/portal/api/submissions/{receipt_id}", headers=harness.staff("kowusu")
    ).json()
    assert detail["evidence"]["signature"]["valid"] is True
    assert detail["evidence"]["signature"]["kid"] == bank.keys.signing_kid


def test_obligations_status_and_due_dates(harness: Harness) -> None:
    bank = _setup(harness)
    harness.post_filing(bank, harness.filing(bank, BSD2_LINES))
    rows = harness.client.get(
        "/v1/obligations?from=2026-10-31&to=2026-10-31", headers=harness.bank_headers(bank)
    ).json()["items"]
    by_code = {r["return_code"]: r for r in rows}
    assert (
        by_code["BSD2"]["due_at"] == "2026-11-13T23:59:59.000Z"
    )  # placeholder rule: 10 business days
    assert by_code["BSD2"]["status"] == "filed_late"  # the harness clock is 21 Nov 2026
    assert by_code["BSD2"]["receipt_id"].startswith("SRG-26-")
    assert by_code["BSD6"]["status"] == "overdue"
    assert by_code["BSD6"]["receipt_id"] is None


def test_institution_reads_are_scoped_to_itself(harness: Harness) -> None:
    bank = _setup(harness)
    other = harness.enrol("0117", "Palmgrove Bank")
    receipt_id = harness.post_filing(bank, harness.filing(bank, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    response = harness.client.get(
        f"/v1/submissions/{receipt_id}", headers=harness.bank_headers(other)
    )
    assert response.status_code == 404
    assert (
        harness.client.get("/v1/submissions", headers=harness.bank_headers(other)).json()["items"]
        == []
    )
