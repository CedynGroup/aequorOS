"""Institution profile / related-party register API against the ACTUAL primary.

Invariants over the real Sample Bank: every mutation needs a reason and lands an
audit event on the row it touched; the profile is 1:1 per bank (a second PUT
updates, ownership split warnings surface); related-party roles replace on write,
UBO links are individual-only, outlet closure stamps ``closed_on``; the composed
register carries what was written; the ORASS institution code flows into the
generated snapshot and the downtime email subject; tenant isolation. Opt-in via
REAL_DATA_DATABASE_URL; everything rolls back inside ``real_client``.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, InstitutionProfile
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

BASE = f"/api/v1/banks/{REAL_BANK_ID}"

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


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BASE}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _forget_profile(session: Session) -> None:
    """Start from "no profile yet" whatever the real bank has captured — the row
    is forgotten on the shared transaction and restored by the outer rollback."""
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(InstitutionProfile).where(
            InstitutionProfile.organization_id == REAL_ORG_ID,
            InstitutionProfile.bank_id == REAL_BANK_ID,
        )
    )
    session.commit()


def _audit_events(session: Session, entity_id: str, event_type: str) -> list[AuditEvent]:
    session.info["organization_id"] = REAL_ORG_ID
    events = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == REAL_ORG_ID,
                AuditEvent.entity_id == entity_id,
                AuditEvent.event_type == event_type,
            )
        )
    )
    session.commit()  # close the savepoint before the next API request
    return events


def _create_party(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason": "Register related party",
        "party_type": "individual",
        "full_name": "Test Party",
        "roles": [],
        **overrides,
    }
    response = client.post(f"{BASE}/related-parties", headers=real_headers(), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_upsert_roundtrip_and_audit(real_client: TestClient, real_session: Session) -> None:
    _forget_profile(real_session)

    empty = real_client.get(f"{BASE}/institution-profile", headers=real_headers())
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["profile"] is None
    # The register's other blocks are whatever the real bank holds — lists.
    assert isinstance(body["related_parties"], list)
    assert isinstance(body["outlets"], list)

    created = real_client.put(
        f"{BASE}/institution-profile", headers=real_headers(), json=PROFILE_PAYLOAD
    )
    assert created.status_code == 200, created.text
    profile = created.json()
    assert profile["institution_type"] == "Universal Bank"
    assert profile["orass_institution_code"] == "GH-UB-0042"
    assert profile["traded_on_exchange"] is True
    assert profile["parent_country_code"] == "GH"
    assert profile["warnings"] == []  # 60 + 40 = 100

    events = _audit_events(real_session, profile["id"], "institution_profile.created")
    assert len(events) == 1
    assert events[0].details["reason"] == "Initial corporate profile capture"
    assert events[0].details["orass_institution_code"] == "GH-UB-0042"

    # Second PUT updates the same 1:1 row and surfaces the ownership warning.
    updated = real_client.put(
        f"{BASE}/institution-profile",
        headers=real_headers(),
        json={
            **PROFILE_PAYLOAD,
            "reason": "Correct the ownership split",
            "ownership_foreign_pct": "20.00",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == profile["id"]
    assert any("sum" in warning for warning in updated.json()["warnings"])
    assert len(_audit_events(real_session, profile["id"], "institution_profile.updated")) == 1

    composed = real_client.get(f"{BASE}/institution-profile", headers=real_headers()).json()
    assert composed["profile"]["id"] == profile["id"]


def test_profile_rejects_unknown_parent_jurisdiction(real_client: TestClient) -> None:
    response = real_client.put(
        f"{BASE}/institution-profile",
        headers=real_headers(),
        json={**PROFILE_PAYLOAD, "parent_country_code": "XX"},
    )
    assert response.status_code == 409
    assert "XX" in response.json()["error"]["message"]


def test_related_party_director_roles_and_replace_on_write(
    real_client: TestClient, real_session: Session
) -> None:
    director = _create_party(
        real_client,
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

    created_events = _audit_events(real_session, director["id"], "related_party.created")
    assert len(created_events) == 1
    assert created_events[0].details["reason"] == "Appoint non-executive director"
    assert created_events[0].details["roles"] == ["director"]

    # Update replaces the roles list wholesale (replace-on-write).
    updated = real_client.put(
        f"{BASE}/related-parties/{director['id']}",
        headers=real_headers(),
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
    assert len(_audit_events(real_session, director["id"], "related_party.updated")) == 1

    # The composed register lists the party among the real bank's others.
    composed = real_client.get(f"{BASE}/institution-profile", headers=real_headers()).json()
    assert director["id"] in {party["id"] for party in composed["related_parties"]}


def test_external_auditor_carries_icag_registration(real_client: TestClient) -> None:
    auditor = _create_party(
        real_client,
        reason="Register external auditor",
        party_type="legal_entity",
        full_name="Assurance Partners Chartered Accountants",
        regulated_elsewhere=True,
        regulated_jurisdiction="Ghana (ICAG)",
        roles=[{"role": "external_auditor", "icag_registration": "ICAG/F/2026/042"}],
    )
    assert auditor["roles"][0]["icag_registration"] == "ICAG/F/2026/042"
    assert auditor["regulated_elsewhere"] is True


def test_shareholding_with_ubo_link_to_individual(
    real_client: TestClient, real_session: Session
) -> None:
    holdco = _create_party(
        real_client,
        reason="Register corporate shareholder",
        party_type="legal_entity",
        full_name="Golden Coast Holdings Ltd",
        roles=[{"role": "shareholder"}],
    )
    owner = _create_party(
        real_client,
        reason="Register ultimate beneficial owner",
        full_name="Kwame Owusu",
        roles=[{"role": "ultimate_beneficial_owner"}],
    )

    created = real_client.post(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings",
        headers=real_headers(),
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
    assert len(_audit_events(real_session, holding["id"], "shareholding.created")) == 1

    updated = real_client.put(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings/{holding['id']}",
        headers=real_headers(),
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
    assert len(_audit_events(real_session, holding["id"], "shareholding.updated")) == 1


def test_ubo_link_to_legal_entity_is_rejected(real_client: TestClient) -> None:
    holdco = _create_party(
        real_client,
        reason="Register corporate shareholder",
        party_type="legal_entity",
        full_name="Golden Coast Holdings Ltd",
        roles=[{"role": "shareholder"}],
    )
    nominee = _create_party(
        real_client,
        reason="Register nominee company",
        party_type="legal_entity",
        full_name="Nominee Services Ltd",
    )
    response = real_client.post(
        f"{BASE}/related-parties/{holdco['id']}/shareholdings",
        headers=real_headers(),
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
    rejected = real_client.post(
        f"{BASE}/related-parties",
        headers=real_headers(),
        json={
            "reason": "Attempt UBO role on a company",
            "party_type": "legal_entity",
            "full_name": "Shell Entity Ltd",
            "roles": [{"role": "ultimate_beneficial_owner"}],
        },
    )
    assert rejected.status_code == 409, rejected.text


def test_outlet_closure_stamps_closed_on(real_client: TestClient, real_session: Session) -> None:
    created = real_client.post(
        f"{BASE}/outlets",
        headers=real_headers(),
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
    assert len(_audit_events(real_session, outlet["id"], "outlet.created")) == 1

    closed = real_client.put(
        f"{BASE}/outlets/{outlet['id']}",
        headers=real_headers(),
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
    events = _audit_events(real_session, outlet["id"], "outlet.updated")
    assert len(events) == 1
    assert events[0].details["reason"] == "Branch consolidation programme"


def test_products_licenses_and_name_history(real_client: TestClient, real_session: Session) -> None:
    product = real_client.post(
        f"{BASE}/products",
        headers=real_headers(),
        json={
            "reason": "Propose SME overdraft product",
            "name": "SME Flex Overdraft",
            "product_type": "credit",
        },
    )
    assert product.status_code == 201, product.text
    assert product.json()["status"] == "proposed"
    approved = real_client.put(
        f"{BASE}/products/{product.json()['id']}",
        headers=real_headers(),
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
    assert len(_audit_events(real_session, product.json()["id"], "bank_product.updated")) == 1

    license_created = real_client.post(
        f"{BASE}/licenses",
        headers=real_headers(),
        json={
            "reason": "Record universal banking license",
            "license_name": "Universal Banking License",
            "license_class": "Class 1",
            "issued_on": "2005-08-15",
        },
    )
    assert license_created.status_code == 201, license_created.text
    assert license_created.json()["status"] == "active"
    assert (
        len(_audit_events(real_session, license_created.json()["id"], "bank_license.created")) == 1
    )

    name_entry = real_client.post(
        f"{BASE}/name-history",
        headers=real_headers(),
        json={
            "reason": "Record pre-merger name",
            "previous_name": "Sample Savings & Loans Ltd",
            "changed_on": "2010-04-01",
            "change_reason": "Upgrade to universal banking license",
        },
    )
    assert name_entry.status_code == 201, name_entry.text
    assert name_entry.json()["change_reason"] == "Upgrade to universal banking license"
    assert (
        len(_audit_events(real_session, name_entry.json()["id"], "bank_name_history.created")) == 1
    )

    # The composed register carries each new row alongside the real bank's own.
    composed = real_client.get(f"{BASE}/institution-profile", headers=real_headers()).json()
    assert product.json()["id"] in {row["id"] for row in composed["products"]}
    assert license_created.json()["id"] in {row["id"] for row in composed["licenses"]}
    assert name_entry.json()["id"] in {row["id"] for row in composed["name_history"]}
    assert "SME Flex Overdraft" in [row["name"] for row in composed["products"]]
    assert "Universal Banking License" in [row["license_name"] for row in composed["licenses"]]
    assert "Sample Savings & Loans Ltd" in [
        row["previous_name"] for row in composed["name_history"]
    ]


def test_mutations_require_a_reason(real_client: TestClient) -> None:
    payload = {key: value for key, value in PROFILE_PAYLOAD.items() if key != "reason"}
    assert (
        real_client.put(
            f"{BASE}/institution-profile", headers=real_headers(), json=payload
        ).status_code
        == 422
    )
    assert (
        real_client.post(
            f"{BASE}/related-parties",
            headers=real_headers(),
            json={"party_type": "individual", "full_name": "No Reason"},
        ).status_code
        == 422
    )
    assert (
        real_client.post(
            f"{BASE}/outlets",
            headers=real_headers(),
            json={"outlet_type": "branch", "name": "No Reason Branch"},
        ).status_code
        == 422
    )
    # An empty reason is as invalid as a missing one.
    assert (
        real_client.put(
            f"{BASE}/institution-profile",
            headers=real_headers(),
            json={**PROFILE_PAYLOAD, "reason": ""},
        ).status_code
        == 422
    )


def test_tenant_isolation_hides_the_register(real_client: TestClient) -> None:
    party = _create_party(real_client, full_name="Sample Bank Party")
    other = other_headers()

    assert real_client.get(f"{BASE}/institution-profile", headers=other).status_code == 404
    assert (
        real_client.put(
            f"{BASE}/institution-profile", headers=other, json=PROFILE_PAYLOAD
        ).status_code
        == 404
    )
    assert (
        real_client.put(
            f"{BASE}/related-parties/{party['id']}",
            headers=other,
            json={
                "reason": "Cross-tenant probe",
                "party_type": "individual",
                "full_name": "Intruder",
            },
        ).status_code
        == 404
    )


def test_generated_snapshot_carries_orass_institution_code(real_client: TestClient) -> None:
    period = _latest_period(real_client)

    profile = real_client.put(
        f"{BASE}/institution-profile", headers=real_headers(), json=PROFILE_PAYLOAD
    )
    assert profile.status_code == 200, profile.text

    # Generation binds the period's latest succeeded baseline liquidity run —
    # the real bank's stored one where present, else the engine runs now.
    runs = real_client.get(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        params={
            "module": "liquidity",
            "scenario_code": "baseline",
            "reporting_period_id": period["id"],
            "limit": 100,
        },
    )
    assert runs.status_code == 200, runs.text
    if not any(run["status"] == "succeeded" for run in runs.json()["runs"]):
        run = real_client.post(
            f"{BASE}/regulatory-runs",
            headers=real_headers(),
            json={
                "module": "liquidity",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        )
        assert run.status_code == 201, run.text
        assert run.json()["status"] == "succeeded", run.json()

    package = real_client.post(
        f"{BASE}/regulatory-packages",
        headers=real_headers(),
        json={"return_code": "LCR-NSFR", "reporting_date": period["period_end"]},
    )
    assert package.status_code == 201, package.text
    institution = package.json()["snapshot"]["institution"]
    assert institution["orass_institution_code"] == "GH-UB-0042"

    # The downtime email subject line leads with the profile's ORASS code (the
    # email channel config, pinned empty here, would otherwise override it)
    # instead of the INSTITUTION-CODE-UNSET / short-name placeholder.
    pinned = real_client.put(
        f"{BASE}/regulatory-reporting/channel-configs/email",
        headers=real_headers(),
        json={"config": {}},
    )
    assert pinned.status_code == 200, pinned.text
    instructions = real_client.get(
        f"{BASE}/regulatory-packages/{package.json()['id']}/email-fallback-instructions",
        headers=real_headers(),
    )
    assert instructions.status_code == 200, instructions.text
    assert instructions.json()["subject"].startswith("[GH-UB-0042]")
