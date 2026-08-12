from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import AuditEvent
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"

PROFILE_PAYLOAD: dict[str, Any] = {
    "reason": "Initial corporate profile capture",
    "institution_type": "Universal Bank",
    "legal_entity_structure": "Public Limited Company",
    "authorisation_date": "2005-08-15",
    "approved_capital": "400000000.00",
    "incorporation_date": "2004-11-02",
    "tin": "C0001234567",
    "registration_number": "CS-2004-11-0042",
    "orass_institution_code": "GH-UB-0042",
    "traded_on_exchange": True,
    "exchange_name": "Ghana Stock Exchange",
    "isin": "GH0000000042",
    "ownership_local_pct": "60.00",
    "ownership_foreign_pct": "40.00",
    "parent_country_code": "GH",
}


def _seed(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(f"{BASE}/reporting-periods", headers=headers()).json()["periods"]
    return periods[0]


def _audit_events(entity_id: str, event_type: str) -> list[AuditEvent]:
    with get_sessionmaker()() as session:
        return list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == entity_id,
                    AuditEvent.event_type == event_type,
                )
            )
        )


def _create_party(db_client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason": "Register related party",
        "party_type": "individual",
        "full_name": "Test Party",
        "roles": [],
        **overrides,
    }
    response = db_client.post(f"{BASE}/related-parties", headers=headers(), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_upsert_roundtrip_and_audit(db_client: TestClient) -> None:
    _seed(db_client)

    empty = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["profile"] is None
    assert body["related_parties"] == []
    assert body["outlets"] == []

    created = db_client.put(f"{BASE}/institution-profile", headers=headers(), json=PROFILE_PAYLOAD)
    assert created.status_code == 200, created.text
    profile = created.json()
    assert profile["institution_type"] == "Universal Bank"
    assert profile["orass_institution_code"] == "GH-UB-0042"
    assert profile["traded_on_exchange"] is True
    assert profile["parent_country_code"] == "GH"
    assert profile["warnings"] == []  # 60 + 40 = 100

    events = _audit_events(profile["id"], "institution_profile.created")
    assert len(events) == 1
    assert events[0].details["reason"] == "Initial corporate profile capture"
    assert events[0].details["orass_institution_code"] == "GH-UB-0042"

    # Second PUT updates the same 1:1 row and surfaces the ownership warning.
    updated = db_client.put(
        f"{BASE}/institution-profile",
        headers=headers(),
        json={
            **PROFILE_PAYLOAD,
            "reason": "Correct the ownership split",
            "ownership_foreign_pct": "20.00",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == profile["id"]
    assert any("sum" in warning for warning in updated.json()["warnings"])
    assert len(_audit_events(profile["id"], "institution_profile.updated")) == 1

    composed = db_client.get(f"{BASE}/institution-profile", headers=headers()).json()
    assert composed["profile"]["id"] == profile["id"]


def test_profile_rejects_unknown_parent_jurisdiction(db_client: TestClient) -> None:
    _seed(db_client)
    response = db_client.put(
        f"{BASE}/institution-profile",
        headers=headers(),
        json={**PROFILE_PAYLOAD, "parent_country_code": "XX"},
    )
    assert response.status_code == 409
    assert "XX" in response.json()["error"]["message"]


def test_related_party_director_roles_and_replace_on_write(db_client: TestClient) -> None:
    _seed(db_client)
    director = _create_party(
        db_client,
        reason="Appoint non-executive director",
        full_name="Ama Mensah",
        contact={"email": "ama.mensah@example.test", "phone": "+233200000001"},
        roles=[
            {
                "role": "director",
                "appointed_on": "2025-06-01",
                "term_of_appointment": "3 years renewable once",
                "sitting_allowance": "1500.00",
                "travel_allowance": "500.00",
                "annual_fees": "24000.00",
            }
        ],
    )
    assert director["party_type"] == "individual"
    assert len(director["roles"]) == 1
    role = director["roles"][0]
    assert role["role"] == "director"
    assert float(role["sitting_allowance"]) == 1500.0
    assert float(role["travel_allowance"]) == 500.0
    assert float(role["annual_fees"]) == 24000.0
    assert role["appointed_on"] == "2025-06-01"

    created_events = _audit_events(director["id"], "related_party.created")
    assert len(created_events) == 1
    assert created_events[0].details["reason"] == "Appoint non-executive director"
    assert created_events[0].details["roles"] == ["director"]

    # Update replaces the roles list wholesale (replace-on-write).
    updated = db_client.put(
        f"{BASE}/related-parties/{director['id']}",
        headers=headers(),
        json={
            "reason": "Elevated to board chairman",
            "party_type": "individual",
            "full_name": "Ama Mensah",
            "contact": {"email": "ama.mensah@example.test"},
            "roles": [
                {"role": "board_chairman", "appointed_on": "2026-01-01"},
                {"role": "shareholder"},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert [entry["role"] for entry in updated.json()["roles"]] == [
        "board_chairman",
        "shareholder",
    ]
    assert len(_audit_events(director["id"], "related_party.updated")) == 1


def test_external_auditor_carries_icag_registration(db_client: TestClient) -> None:
    _seed(db_client)
    auditor = _create_party(
        db_client,
        reason="Register external auditor",
        party_type="legal_entity",
        full_name="Assurance Partners Chartered Accountants",
        regulated_elsewhere=True,
        regulated_jurisdiction="Ghana (ICAG)",
        roles=[{"role": "external_auditor", "icag_registration": "ICAG/F/2026/042"}],
    )
    assert auditor["roles"][0]["icag_registration"] == "ICAG/F/2026/042"
    assert auditor["regulated_elsewhere"] is True


def test_shareholding_with_ubo_link_to_individual(db_client: TestClient) -> None:
    _seed(db_client)
    holdco = _create_party(
        db_client,
        reason="Register corporate shareholder",
        party_type="legal_entity",
        full_name="Golden Coast Holdings Ltd",
        roles=[{"role": "shareholder"}],
    )
    owner = _create_party(
        db_client,
        reason="Register ultimate beneficial owner",
        full_name="Kwame Owusu",
        roles=[{"role": "ultimate_beneficial_owner"}],
    )

    created = db_client.post(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings",
        headers=headers(),
        json={
            "reason": "Record ordinary shareholding with UBO",
            "share_type": "Ordinary",
            "share_subtype": "Class A",
            "shareholder_rights": "voting",
            "number_of_shares": "1250000.00",
            "pct_shareholding": "12.5000",
            "ubo_party_id": owner["id"],
        },
    )
    assert created.status_code == 201, created.text
    party = created.json()
    assert len(party["shareholdings"]) == 1
    holding = party["shareholdings"][0]
    assert holding["ubo_party_id"] == owner["id"]
    assert float(holding["pct_shareholding"]) == 12.5
    assert len(_audit_events(holding["id"], "shareholding.created")) == 1

    updated = db_client.put(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings/{holding['id']}",
        headers=headers(),
        json={
            "reason": "Rights issue uptake",
            "share_type": "Ordinary",
            "share_subtype": "Class A",
            "shareholder_rights": "voting",
            "number_of_shares": "1500000.00",
            "pct_shareholding": "15.0000",
            "ubo_party_id": owner["id"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert float(updated.json()["shareholdings"][0]["pct_shareholding"]) == 15.0
    assert len(_audit_events(holding["id"], "shareholding.updated")) == 1


def test_ubo_link_to_legal_entity_is_rejected(db_client: TestClient) -> None:
    _seed(db_client)
    holdco = _create_party(
        db_client,
        reason="Register corporate shareholder",
        party_type="legal_entity",
        full_name="Golden Coast Holdings Ltd",
        roles=[{"role": "shareholder"}],
    )
    nominee = _create_party(
        db_client,
        reason="Register nominee company",
        party_type="legal_entity",
        full_name="Nominee Services Ltd",
    )
    response = db_client.post(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings",
        headers=headers(),
        json={
            "reason": "Attempt UBO link to a company",
            "share_type": "Ordinary",
            "shareholder_rights": "voting",
            "number_of_shares": "100.00",
            "pct_shareholding": "1.0000",
            "ubo_party_id": nominee["id"],
        },
    )
    assert response.status_code == 409, response.text
    assert "individual" in response.json()["error"]["message"]

    # The UBO *role* is equally individual-only.
    rejected = db_client.post(
        f"{BASE}/related-parties",
        headers=headers(),
        json={
            "reason": "Attempt UBO role on a company",
            "party_type": "legal_entity",
            "full_name": "Shell Entity Ltd",
            "roles": [{"role": "ultimate_beneficial_owner"}],
        },
    )
    assert rejected.status_code == 409, rejected.text


def test_outlet_closure_stamps_closed_on(db_client: TestClient) -> None:
    _seed(db_client)
    created = db_client.post(
        f"{BASE}/outlets",
        headers=headers(),
        json={
            "reason": "Open the Kumasi branch",
            "outlet_type": "branch",
            "name": "Kumasi Adum Branch",
            "outlet_number": "BR-014",
            "address": {"city": "Kumasi", "street": "Adum High Street"},
            "opened_on": "2026-02-01",
        },
    )
    assert created.status_code == 201, created.text
    outlet = created.json()
    assert outlet["status"] == "active"
    assert outlet["closed_on"] is None
    assert len(_audit_events(outlet["id"], "outlet.created")) == 1

    closed = db_client.put(
        f"{BASE}/outlets/{outlet['id']}",
        headers=headers(),
        json={
            "reason": "Branch consolidation programme",
            "outlet_type": "branch",
            "name": "Kumasi Adum Branch",
            "outlet_number": "BR-014",
            "address": {"city": "Kumasi", "street": "Adum High Street"},
            "status": "closed",
            "opened_on": "2026-02-01",
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_on"] is not None
    events = _audit_events(outlet["id"], "outlet.updated")
    assert len(events) == 1
    assert events[0].details["reason"] == "Branch consolidation programme"


def test_products_licenses_and_name_history(db_client: TestClient) -> None:
    _seed(db_client)
    product = db_client.post(
        f"{BASE}/products",
        headers=headers(),
        json={
            "reason": "Propose SME overdraft product",
            "name": "SME Flex Overdraft",
            "product_type": "credit",
        },
    )
    assert product.status_code == 201, product.text
    assert product.json()["status"] == "proposed"
    approved = db_client.put(
        f"{BASE}/products/{product.json()['id']}",
        headers=headers(),
        json={
            "reason": "Regulator approved the product",
            "name": "SME Flex Overdraft",
            "product_type": "credit",
            "status": "approved",
            "approval_reference": "BOG/PRD/2026/017",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_reference"] == "BOG/PRD/2026/017"
    assert len(_audit_events(product.json()["id"], "bank_product.updated")) == 1

    license_created = db_client.post(
        f"{BASE}/licenses",
        headers=headers(),
        json={
            "reason": "Record universal banking license",
            "license_name": "Universal Banking License",
            "license_class": "Class 1",
            "issued_on": "2005-08-15",
        },
    )
    assert license_created.status_code == 201, license_created.text
    assert license_created.json()["status"] == "active"
    assert len(_audit_events(license_created.json()["id"], "bank_license.created")) == 1

    name_entry = db_client.post(
        f"{BASE}/name-history",
        headers=headers(),
        json={
            "reason": "Record pre-merger name",
            "previous_name": "Sample Savings & Loans Ltd",
            "changed_on": "2010-04-01",
            "change_reason": "Upgrade to universal banking license",
        },
    )
    assert name_entry.status_code == 201, name_entry.text
    assert name_entry.json()["change_reason"] == "Upgrade to universal banking license"
    assert len(_audit_events(name_entry.json()["id"], "bank_name_history.created")) == 1

    composed = db_client.get(f"{BASE}/institution-profile", headers=headers()).json()
    assert [row["name"] for row in composed["products"]] == ["SME Flex Overdraft"]
    assert [row["license_name"] for row in composed["licenses"]] == ["Universal Banking License"]
    assert [row["previous_name"] for row in composed["name_history"]] == [
        "Sample Savings & Loans Ltd"
    ]


def test_mutations_require_a_reason(db_client: TestClient) -> None:
    _seed(db_client)
    payload = {key: value for key, value in PROFILE_PAYLOAD.items() if key != "reason"}
    assert (
        db_client.put(f"{BASE}/institution-profile", headers=headers(), json=payload).status_code
        == 422
    )
    assert (
        db_client.post(
            f"{BASE}/related-parties",
            headers=headers(),
            json={"party_type": "individual", "full_name": "No Reason"},
        ).status_code
        == 422
    )
    assert (
        db_client.post(
            f"{BASE}/outlets",
            headers=headers(),
            json={"outlet_type": "branch", "name": "No Reason Branch"},
        ).status_code
        == 422
    )
    # An empty reason is as invalid as a missing one.
    assert (
        db_client.put(
            f"{BASE}/institution-profile",
            headers=headers(),
            json={**PROFILE_PAYLOAD, "reason": ""},
        ).status_code
        == 422
    )


def test_tenant_isolation_hides_the_register(db_client: TestClient) -> None:
    _seed(db_client)
    party = _create_party(db_client, full_name="Org-1 Party")

    assert db_client.get(f"{BASE}/institution-profile", headers=headers(ORG_2)).status_code == 404
    assert (
        db_client.put(
            f"{BASE}/institution-profile", headers=headers(ORG_2), json=PROFILE_PAYLOAD
        ).status_code
        == 404
    )
    assert (
        db_client.put(
            f"{BASE}/related-parties/{party['id']}",
            headers=headers(ORG_2),
            json={
                "reason": "Cross-tenant probe",
                "party_type": "individual",
                "full_name": "Intruder",
            },
        ).status_code
        == 404
    )


def test_generated_snapshot_carries_orass_institution_code(db_client: TestClient) -> None:
    period = _seed(db_client)

    profile = db_client.put(f"{BASE}/institution-profile", headers=headers(), json=PROFILE_PAYLOAD)
    assert profile.status_code == 200, profile.text

    run = db_client.post(
        f"{BASE}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "succeeded", run.json()

    package = db_client.post(
        f"{BASE}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD3", "reporting_date": period["period_end"]},
    )
    assert package.status_code == 201, package.text
    institution = package.json()["snapshot"]["institution"]
    assert institution["orass_institution_code"] == "GH-UB-0042"

    # The downtime email subject line now leads with the ORASS code instead
    # of the INSTITUTION-CODE-UNSET / short-name placeholder.
    instructions = db_client.get(
        f"{BASE}/regulatory-packages/{package.json()['id']}/email-fallback-instructions",
        headers=headers(),
    )
    assert instructions.status_code == 200, instructions.text
    assert instructions.json()["subject"].startswith("[GH-UB-0042]")
