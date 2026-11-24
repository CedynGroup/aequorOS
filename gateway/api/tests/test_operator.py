# pyright: standard
"""Operator API (SPEC Part 5) and operator support sessions."""

from __future__ import annotations

from typing import Any

from app.seed.client import InstitutionKeys, csr_pem, new_key
from tests.harness import BSD2_LINES, Harness, cert_header

INSTITUTION = {
    "code": "0601",
    "name": "Tidewater Example Bank",
    "type": "universal_bank",
    "contacts": [
        {"role": "Compliance officer", "name": "A. Person", "email": "a@tidewater.example"}
    ],
}


def _code(response: Any) -> str:
    return response.json()["code"]


def test_only_operator_clients_reach_ops_api(harness: Harness) -> None:
    bank = harness.enrol()
    as_bank = harness.client.post(
        "/ops/api/institutions", json=INSTITUTION, headers=harness.bank_headers(bank)
    )
    assert as_bank.status_code == 403
    assert _code(as_bank) == "insufficient_scope"
    staff = harness.client.post(
        "/ops/api/institutions", json=INSTITUTION, headers=harness.staff("kowusu")
    )
    assert staff.status_code == 401
    with_cert = harness.client.post(
        "/ops/api/institutions",
        json=INSTITUTION,
        headers={**harness.staff("kowusu"), **cert_header(bank.certificate_pem)},
    )
    assert with_cert.status_code == 401
    # Institution clients can never obtain ops:enrol.
    asking = harness.token_response(
        bank.client_id,
        bank.keys.client_key,
        bank.keys.client_kid,
        bank.certificate_pem,
        scope="ops:enrol",
    )
    assert asking.status_code == 400
    # Operator clients can never file.
    op = harness.operator_headers()
    assert harness.client.get("/v1/returns", headers=op).status_code == 403
    filing = harness.client.post(
        "/v1/submissions",
        json=harness.filing(bank, BSD2_LINES),
        headers={**op, "Idempotency-Key": "k" * 10},
    )
    assert filing.status_code == 403


def test_enrolment_is_idempotent_and_conflicts_are_refused(harness: Harness) -> None:
    op = harness.operator_headers()
    created = harness.client.post("/ops/api/institutions", json=INSTITUTION, headers=op)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "enrolled"
    again = harness.client.post("/ops/api/institutions", json=INSTITUTION, headers=op)
    assert again.status_code == 200
    assert again.json() == created.json()
    renamed = harness.client.post(
        "/ops/api/institutions", json={**INSTITUTION, "name": "Other Name"}, headers=op
    )
    assert renamed.status_code == 409
    assert _code(renamed) == "conflict"
    retyped = harness.client.post(
        "/ops/api/institutions", json={**INSTITUTION, "type": "rural_bank"}, headers=op
    )
    assert retyped.status_code == 409
    audit = harness.client.get(
        "/portal/api/audit?actor_type=operator", headers=harness.staff("internal-audit")
    ).json()
    assert {item["action"] for item in audit["items"]} >= {
        "Enrolled institution",
        "Re-enrolment matched the existing institution",
    }


def test_full_enrolment_then_filing(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    op = harness.operator_headers()
    harness.client.post("/ops/api/institutions", json=INSTITUTION, headers=op)
    keys = InstitutionKeys(signing_kid="0601-sign-20261121")
    request = {
        "environment": "production",
        "csr_pem": csr_pem(keys.client_key, "tidewater"),
        "jwks": keys.client_jwks(),
        "software": {"name": "AequorOS", "version": "2026.11"},
    }
    issued = harness.client.post("/ops/api/institutions/0601/clients", json=request, headers=op)
    assert issued.status_code == 201, issued.text
    client = issued.json()
    assert client["scopes"] == [
        "returns:read",
        "obligations:read",
        "submissions:write",
        "submissions:read",
        "queries:read",
        "queries:write",
    ]
    assert client["certificate_chain_pem"].count("BEGIN CERTIFICATE") == 2
    retried = harness.client.post("/ops/api/institutions/0601/clients", json=request, headers=op)
    assert retried.status_code == 200
    assert retried.json()["client_id"] == client["client_id"]
    key = {"kid": keys.signing_kid, "public_jwk": keys.signing_jwk()}
    assert (
        harness.client.post(
            "/ops/api/institutions/0601/signing-keys", json=key, headers=op
        ).status_code
        == 201
    )
    assert (
        harness.client.post(
            "/ops/api/institutions/0601/signing-keys", json=key, headers=op
        ).status_code
        == 200
    )
    other = InstitutionKeys()
    clash = harness.client.post(
        "/ops/api/institutions/0601/signing-keys",
        json={"kid": keys.signing_kid, "public_jwk": other.signing_jwk()},
        headers=op,
    )
    assert clash.status_code == 409
    view = harness.client.get("/ops/api/institutions/0601", headers=op).json()
    assert view["clients"][0]["client_id"] == client["client_id"]
    assert view["signing_keys"] == [
        {
            "kid": keys.signing_kid,
            "status": "active",
            "registered_at": view["signing_keys"][0]["registered_at"],
        }
    ]
    from tests.harness import Bank

    bank = Bank("0601", INSTITUTION["name"], keys, client["client_id"], client["certificate_pem"])
    filed = harness.post_filing(bank, harness.filing(bank, BSD2_LINES))
    assert filed.status_code == 202, filed.text
    institutions = harness.client.get(
        "/portal/api/institutions", headers=harness.staff("kowusu")
    ).json()["items"]
    assert next(i for i in institutions if i["code"] == "0601")["status"] == "live"


def test_csr_must_match_the_assertion_key(harness: Harness) -> None:
    op = harness.operator_headers()
    harness.client.post("/ops/api/institutions", json=INSTITUTION, headers=op)
    keys = InstitutionKeys()
    response = harness.client.post(
        "/ops/api/institutions/0601/clients",
        json={
            "environment": "production",
            "csr_pem": csr_pem(new_key(), "x"),
            "jwks": keys.client_jwks(),
            "software": None,
        },
        headers=op,
    )
    assert response.status_code == 400
    assert "CSR public key" in response.json()["detail"]


def test_suspended_institution_cannot_get_tokens(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    headers = harness.bank_headers(bank)
    op = harness.operator_headers()
    response = harness.client.post(
        f"/ops/api/institutions/{bank.code}/status",
        json={"status": "suspended", "reason": "Licence review"},
        headers=op,
    )
    assert response.status_code == 204
    token = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
    )
    assert token.status_code == 403
    assert harness.client.get("/v1/returns", headers=headers).status_code == 403
    harness.client.post(
        f"/ops/api/institutions/{bank.code}/status",
        json={"status": "live", "reason": "Cleared"},
        headers=op,
    )
    assert (
        harness.token_response(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
        ).status_code
        == 200
    )


def test_revoke_and_retire_via_ops_api(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    op = harness.operator_headers()
    receipt = harness.post_filing(bank, harness.filing(bank, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    retire = harness.client.post(
        f"/ops/api/institutions/{bank.code}/signing-keys/{bank.keys.signing_kid}/retire", headers=op
    )
    assert retire.status_code == 204
    view = harness.client.get(f"/ops/api/institutions/{bank.code}", headers=op).json()
    assert view["signing_keys"][0]["status"] == "retired"
    detail = harness.client.get(
        f"/portal/api/submissions/{receipt}", headers=harness.staff("kowusu")
    ).json()
    assert detail["evidence"]["signature"]["valid"] is True
    assert (
        harness.client.post(
            f"/ops/api/clients/{bank.client_id}/revoke", json={"reason": "rotated"}, headers=op
        ).status_code
        == 204
    )
    assert (
        harness.token_response(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
        ).status_code
        == 401
    )


def test_operator_support_session_flow_and_separation_of_duties(harness: Harness) -> None:
    op = harness.operator_headers()
    body = {
        "operator_name": "Michael Adjei",
        "operator_title": "Engineer",
        "scope": "Read-only logs",
        "excludes": "Filing content",
        "reason": "Slow receipts",
        "incident_ref": "INC-1",
    }
    number = harness.client.post("/ops/api/access-requests", json=body, headers=op).json()["number"]
    assert number.startswith("SUP-2026-")
    michael = harness.staff("madjei")
    own = harness.client.post(
        f"/portal/api/access/requests/{number}/approve",
        json={"duration_minutes": 60},
        headers=michael,
    )
    assert own.status_code == 403
    assert "requested" in own.json()["detail"]
    not_approver = harness.client.post(
        f"/portal/api/access/requests/{number}/approve",
        json={"duration_minutes": 60},
        headers=harness.staff("kowusu"),
    )
    assert not_approver.status_code == 403
    other = harness.client.post(
        "/ops/api/access-requests", json={**body, "operator_name": "Rita Quaye"}, headers=op
    ).json()["number"]
    assert (
        harness.client.post(
            f"/portal/api/access/requests/{other}/approve",
            json={"duration_minutes": 45},
            headers=michael,
        ).status_code
        == 400
    )
    assert (
        harness.client.post(
            f"/portal/api/access/requests/{other}/approve",
            json={"duration_minutes": 30},
            headers=michael,
        ).status_code
        == 204
    )
    view = harness.client.get("/portal/api/access", headers=michael).json()
    assert [p["number"] for p in view["pending"]] == [number]
    assert view["sessions"][0]["status"] == "active"
    harness.clock.advance(minutes=61)
    michael = harness.staff("madjei")
    later = harness.client.get("/portal/api/access", headers=michael).json()
    assert later["pending"] == []
    assert {s["number"]: s["status"] for s in later["sessions"]} == {
        number: "expired",
        other: "ended",
    }
    actors = harness.client.get(
        "/portal/api/audit?category=operator_access", headers=michael
    ).json()["items"]
    assert any(item["actor_label"] == "Channel operator" for item in actors)
    assert (
        harness.client.post(
            "/ops/api/access-requests", json=body, headers=harness.staff("kowusu")
        ).status_code
        == 401
    )


def test_operator_lists_every_institution_by_code(harness: Harness) -> None:
    op = harness.operator_headers()
    for code, name in (("0457", "Second Bank"), ("0456", "First Bank"), ("0458", "Third Bank")):
        body = {**INSTITUTION, "code": code, "name": name}
        assert (
            harness.client.post("/ops/api/institutions", json=body, headers=op).status_code == 201
        )
    first = harness.client.get("/ops/api/institutions?limit=2", headers=op)
    assert first.status_code == 200, first.text
    page = first.json()
    assert [item["code"] for item in page["items"]] == ["0456", "0457"]
    assert page["next_cursor"] == "0457"
    rest = harness.client.get(f"/ops/api/institutions?cursor={page['next_cursor']}", headers=op)
    assert [item["code"] for item in rest.json()["items"]] == ["0458"]
    assert rest.json()["next_cursor"] is None
    assert harness.client.get("/ops/api/institutions", headers=harness.staff()).status_code == 401
