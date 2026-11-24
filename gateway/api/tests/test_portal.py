# pyright: standard
"""Supervisor API shapes and the view audit trail, on a small synthetic world."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping
from datetime import date

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from jwt.api_jws import PyJWS

from app.core.encoding import b64url_decode
from app.core.jcs import canonicalize
from app.keys.jwk import public_key_from_jwk
from tests.harness import BSD2_LINES, Harness

OCT = date(2026, 10, 31)


def _world(harness: Harness) -> tuple[str, str]:
    harness.bootstrap(("BSD2", "BSD5A", "BSD6", "LMT", "DBK-DAILY"))
    meridian = harness.enrol(supervisor="kowusu")
    palm = harness.enrol("0117", "Palmgrove Bank", supervisor="aboateng")
    first = harness.post_filing(meridian, harness.filing(meridian, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    second = harness.post_filing(palm, harness.filing(palm, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    return first, second


def test_overview(harness: Harness) -> None:
    first, _ = _world(harness)
    body = harness.client.get("/portal/api/overview", headers=harness.staff("kowusu")).json()
    assert body["reporting_date"] == "2026-10-31"
    assert body["totals"]["institutions"] == 2
    assert body["totals"]["received"] == 2
    assert body["totals"]["outstanding_past_due"] == body["totals"]["due"] - 2
    assert "BSD2" in body["heatmap"]["return_codes"]
    row = body["heatmap"]["rows"][0]
    assert set(row) == {"institution_code", "institution_name", "cells", "received", "due"}
    assert {c["state"] for r in body["heatmap"]["rows"] for c in r["cells"]} <= {
        "on_time",
        "late",
        "outstanding",
        "not_due",
    }
    assert any(a["kind"] == "outstanding" for a in body["attention"])
    assert body["latest"][0]["receipt_id"] in (first,) or body["latest"][1]["receipt_id"] == first
    channel = body["channel"]
    # Request counts reach the daily table only when the scheduled job flushes them;
    # until then availability is unmeasured, never a claimed 100%.
    assert channel["api_availability_30d"] is None
    assert channel["median_receipt_seconds"] is not None
    assert channel["filings_today"] == 2


def test_inbox_filters_and_counts(harness: Harness) -> None:
    _world(harness)
    kofi = harness.staff("kowusu")
    body = harness.client.get("/portal/api/submissions", headers=kofi).json()
    assert body["counts"]["all"] == 2
    assert body["counts"]["needs_review"] == 2
    assert (
        len(
            harness.client.get("/portal/api/submissions?status=needs_review", headers=kofi).json()[
                "items"
            ]
        )
        == 2
    )
    assert (
        harness.client.get("/portal/api/submissions?status=accepted", headers=kofi).json()["items"]
        == []
    )
    assert harness.client.get("/portal/api/submissions?status=all", headers=kofi).status_code == 400
    searched = harness.client.get("/portal/api/submissions?q=Palmgrove", headers=kofi).json()
    assert [r["institution_name"] for r in searched["items"]] == ["Palmgrove Bank"]
    page = harness.client.get("/portal/api/submissions?limit=1", headers=kofi).json()
    assert page["next_cursor"]
    rest = harness.client.get(
        f"/portal/api/submissions?limit=1&cursor={page['next_cursor']}", headers=kofi
    ).json()
    assert rest["items"][0]["receipt_id"] != page["items"][0]["receipt_id"]


def test_submission_detail_writes_a_view_entry(harness: Harness) -> None:
    first, _ = _world(harness)
    kofi = harness.staff("kowusu")
    detail = harness.client.get(f"/portal/api/submissions/{first}", headers=kofi).json()
    assert detail["evidence"]["signature"]["valid"] is True
    assert detail["evidence"]["receipt_signature"]["valid"] is True
    assert detail["evidence"]["chain_intact"] is True
    assert detail["evidence"]["certificate"]["valid"] is True
    assert [line["code"] for line in detail["content"]["lines"]][:6] == [
        "1.1",
        "1.2",
        "1.3",
        "1.4",
        "1.5",
        "1",
    ]
    assert {g["label"] for g in detail["validation"]["groups"]} == {
        "Structure",
        "Arithmetic",
        "Cross-return",
        "Period",
    }
    # Kofi supervises this institution, so the filing is already his: no "Assign to me".
    assert detail["row"]["reviewer_name"] == "K. Owusu"
    assert detail["allowed_actions"] == ["accept", "query", "request_resubmission"]
    titles = [a["title"] for a in detail["activity"]]
    assert "Receipt issued" in titles
    assert "Filing received" in titles
    audit = harness.client.get("/portal/api/audit?category=view", headers=kofi).json()
    assert any(
        e["action"] == "Opened submission" and e["object_ref"] == first for e in audit["items"]
    )
    assert (
        harness.client.get("/portal/api/submissions/SRG-26-0999999", headers=kofi).status_code
        == 404
    )


def _jws_payload(jws: str, keys: Mapping[str, EllipticCurvePublicKey]) -> bytes:
    kid = json.loads(b64url_decode(jws.split(".", maxsplit=1)[0]))["kid"]
    return PyJWS().decode_complete(jws, key=keys[kid], algorithms=["ES256"])["payload"]


def test_receipt_download_is_the_institutions_receipt(harness: Harness) -> None:
    first, _ = _world(harness)
    kofi = harness.staff("kowusu")
    response = harness.client.get(f"/portal/api/submissions/{first}/receipt", headers=kofi)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == f'attachment; filename="{first}-receipt.json"'
    body = response.json()
    assert body["receipt"]["receipt_id"] == first
    assert body["status"] == "in_review"
    jwks = harness.client.get("/v1/.well-known/jwks.json").json()
    keys = {k["kid"]: public_key_from_jwk(k) for k in jwks["keys"]}
    assert _jws_payload(body["receipt_jws"], keys) == canonicalize(body["receipt"])
    audit = harness.client.get("/portal/api/audit?category=view", headers=kofi).json()
    assert any(
        e["action"] == "Downloaded receipt" and e["object_ref"] == first for e in audit["items"]
    )
    missing = harness.client.get("/portal/api/submissions/SRG-26-0999999/receipt", headers=kofi)
    assert missing.status_code == 404


def test_filed_package_verifies_without_the_gateway(harness: Harness) -> None:
    first, _ = _world(harness)
    kofi = harness.staff("kowusu")
    response = harness.client.get(f"/portal/api/submissions/{first}/package", headers=kofi)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="{first}-filed-package.zip"'
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert names == {
        "manifest.json",
        "payload.json",
        "declaration.json",
        "signature.jws",
        "receipt.json",
        "receipt.jws",
        "institution-signing-key.json",
        "gateway-receipt-keys.json",
        "gateway-audit-keys.json",
        "ledger-entry.json",
        "transport.json",
        "validation.json",
        "VERIFY.txt",
        "index.json",
        "index.jws",
    }
    manifest_bytes = archive.read("manifest.json")
    manifest = json.loads(manifest_bytes)
    # 1. The institution's signature covers exactly the manifest bytes.
    signer = json.loads(archive.read("institution-signing-key.json"))
    institution_keys = {signer["kid"]: public_key_from_jwk(signer["public_jwk"])}
    assert _jws_payload(archive.read("signature.jws").decode(), institution_keys) == manifest_bytes
    # 2. The payload and declaration are the ones the manifest binds.
    payload_digest = hashlib.sha256(archive.read("payload.json")).hexdigest()
    assert payload_digest == manifest["payload_sha256"]
    declaration_digest = hashlib.sha256(archive.read("declaration.json")).hexdigest()
    assert declaration_digest == manifest["declaration_sha256"]
    # 3. The gateway receipt is signed and repeats the same digests.
    receipt = json.loads(archive.read("receipt.json"))
    gateway_keys = {
        k["kid"]: public_key_from_jwk(k)
        for k in json.loads(archive.read("gateway-receipt-keys.json"))["keys"]
    }
    assert _jws_payload(archive.read("receipt.jws").decode(), gateway_keys) == canonicalize(receipt)
    assert receipt["payload_sha256"] == manifest["payload_sha256"]
    assert receipt["receipt_id"] == first
    index = json.loads(archive.read("index.json"))
    for name, digest in index["files"].items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    # The index is signed by the gateway, so the package is tamper-evident as a whole.
    audit_keys = {
        k["kid"]: public_key_from_jwk(k)
        for k in json.loads(archive.read("gateway-audit-keys.json"))["keys"]
    }
    assert _jws_payload(archive.read("index.jws").decode(), audit_keys) == archive.read(
        "index.json"
    )
    # The ledger entry the receipt names is in the package and its hash recomputes.
    entry = json.loads(archive.read("ledger-entry.json"))
    assert entry["seq"] == receipt["ledger_seq"]
    body = {
        k: v
        for k, v in entry.items()
        if k not in ("prev_hash", "entry_hash", "signature", "signature_kid")
    }
    assert (
        hashlib.sha256((entry["prev_hash"] + "\n").encode() + canonicalize(body)).hexdigest()
        == entry["entry_hash"]
    )
    transport = json.loads(archive.read("transport.json"))
    assert transport["channel"] == "api"
    assert transport["client_certificate_sha256"]
    audit = harness.client.get("/portal/api/audit?category=view", headers=kofi).json()
    assert any(
        e["action"] == "Downloaded filed package" and e["object_ref"] == first
        for e in audit["items"]
    )


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCUMENTS = [
    ("BSD2_2026-10-31.xlsx", XLSX, b"PK\x03\x04 workbook with formulas"),
    ("BSD2_2026-10-31.csv", "text/csv", b"line,value\n1.1,412350\n"),
    ("BSD2_2026-10-31.pdf", "application/pdf", b"%PDF-1.7 signed return"),
]


def _filed_with_documents(harness: Harness) -> str:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol(supervisor="kowusu")
    response = harness.post_filing(bank, harness.filing(bank, BSD2_LINES, documents=DOCUMENTS))
    assert response.status_code == 202, response.text
    return response.json()["receipt"]["receipt_id"]


def test_documents_filed_with_a_return_are_listed_served_and_packaged(harness: Harness) -> None:
    receipt_id = _filed_with_documents(harness)
    kofi = harness.staff("kowusu")
    detail = harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=kofi).json()
    assert [(d["name"], d["media_type"], d["bytes"]) for d in detail["documents"]] == sorted(
        (name, media, len(content)) for name, media, content in DOCUMENTS
    )
    for name, media, content in DOCUMENTS:
        served = harness.client.get(
            f"/portal/api/submissions/{receipt_id}/documents/{name}", headers=kofi
        )
        assert served.status_code == 200
        assert served.content == content
        assert served.headers["content-type"].startswith(media)
        assert served.headers["content-disposition"] == f'attachment; filename="{name}"'
    missing = harness.client.get(
        f"/portal/api/submissions/{receipt_id}/documents/other.pdf", headers=kofi
    )
    assert missing.status_code == 404
    archive = zipfile.ZipFile(
        io.BytesIO(
            harness.client.get(
                f"/portal/api/submissions/{receipt_id}/package", headers=kofi
            ).content
        )
    )
    manifest = json.loads(archive.read("manifest.json"))
    listed = {d["name"]: d for d in manifest["documents"]}
    for name, _, content in DOCUMENTS:
        packaged = archive.read(f"documents/{name}")
        assert packaged == content
        assert hashlib.sha256(packaged).hexdigest() == listed[name]["sha256"]
        assert len(packaged) == listed[name]["bytes"]
    audit = harness.client.get("/portal/api/audit?category=view", headers=kofi).json()
    assert any(e["action"] == "Downloaded filed document" for e in audit["items"])


def test_a_document_that_does_not_match_the_signed_manifest_is_refused(
    harness: Harness,
) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol(supervisor="kowusu")
    body = harness.filing(bank, BSD2_LINES, documents=DOCUMENTS)
    # Swap the PDF's bytes after signing.
    body["documents"][2]["content_base64"] = base64.b64encode(b"%PDF-1.7 altered").decode()
    altered = harness.post_filing(bank, body)
    assert altered.status_code == 422
    assert altered.json()["code"] == "document_digest_mismatch"
    # A file the manifest does not list is refused.
    extra = harness.filing(bank, BSD2_LINES, documents=DOCUMENTS)
    extra["documents"].append(
        {"name": "notes.pdf", "media_type": "application/pdf", "content_base64": "JVBERg=="}
    )
    assert harness.post_filing(bank, extra).json()["code"] == "document_invalid"
    # Only Excel, CSV and PDF are accepted.
    script = harness.filing(
        bank, BSD2_LINES, documents=[("macro.xlsm", "application/x-msexcel", b"x")]
    )
    assert harness.post_filing(bank, script).json()["code"] == "document_invalid"
    # Nothing was recorded by the refusals.
    kofi = harness.staff("kowusu")
    listing = harness.client.get("/portal/api/submissions", headers=kofi).json()
    assert listing["items"] == []


def test_assign_to_me_is_not_offered_once_the_filing_is_yours(harness: Harness) -> None:
    _, second = _world(harness)  # Palmgrove's filing, reviewed by A. Boateng
    kofi = harness.staff("kowusu")
    before = harness.client.get(f"/portal/api/submissions/{second}", headers=kofi).json()
    assert "assign" in before["allowed_actions"]
    assigned = harness.client.post(
        f"/portal/api/submissions/{second}/assign",
        json={"reviewer_subject": "kowusu"},
        headers=kofi,
    )
    assert assigned.status_code == 204
    after = harness.client.get(f"/portal/api/submissions/{second}", headers=kofi).json()
    assert after["row"]["reviewer_name"] == "K. Owusu"
    assert "assign" not in after["allowed_actions"]
    assert "accept" in after["allowed_actions"]


def test_institutions_onboarding_calendar_returns_and_access(harness: Harness) -> None:
    _world(harness)
    kofi = harness.staff("kowusu")
    listed = harness.client.get("/portal/api/institutions", headers=kofi).json()["items"]
    assert {i["status"] for i in listed} == {"live"}
    profile = harness.client.get("/portal/api/institutions/0142", headers=kofi).json()
    assert profile["credentials"]["api_client"]["client_id"].endswith("-prod-01")
    assert profile["history"]["rows"][0]["return_code"] == "BSD2"
    note = harness.client.post(
        "/portal/api/institutions/0142/notes", headers=kofi, json={"body": "Watch liquidity."}
    )
    assert note.status_code == 201
    assert (
        harness.client.post(
            "/portal/api/institutions/0142/notes",
            headers=harness.staff("internal-audit"),
            json={"body": "x"},
        ).status_code
        == 403
    )
    assert (
        harness.client.get("/portal/api/institutions/0142", headers=kofi).json()["notes"][0]["body"]
        == "Watch liquidity."
    )
    onboarding = harness.client.get(
        "/portal/api/onboarding", headers=harness.staff("easante")
    ).json()
    assert [s["stage"] for s in onboarding["stages"]] == ["enrolled", "live", "suspended"]
    assert set(onboarding["register"][0]) == {
        "code",
        "name",
        "type",
        "channel",
        "software_declared",
        "enrolled_at",
        "client_id",
        "certificate_not_after",
        "certificate_expiring",
        "signing_kid",
        "status",
    }
    calendar = harness.client.get("/portal/api/calendar?month=2026-11", headers=kofi).json()
    assert len(calendar["days"]) == 30
    assert any(e["title"] == "Monthly · 31 Oct" for d in calendar["days"] for e in d["events"])
    returns = harness.client.get("/portal/api/returns", headers=kofi).json()["items"]
    assert {r["code"]: r["rule_count"] for r in returns}["BSD2"] == 58
    access = harness.client.get("/portal/api/access", headers=kofi).json()
    assert [r["group"] for r in access["roles"]] == [
        "srg-supervisors",
        "srg-senior-supervisors",
        "srg-returns-policy",
        "srg-access-approvers",
        "srg-internal-audit",
    ]
    assert harness.client.get("/portal/api/onboarding").status_code == 401


def test_problem_details_shape(harness: Harness) -> None:
    response = harness.client.get("/portal/api/me")
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "http://testserver/errors/unauthenticated"
    assert body["status"] == 401
    assert body["code"] == "unauthenticated"
