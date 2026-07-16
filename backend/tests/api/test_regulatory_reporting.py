from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import RegulatoryPackage, User
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers

CHECKER_USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
REPORTING_DATE = "2026-03-31"
REGISTRY_CODES = {"BSD3", "BSD2", "IRRBB-PILOT", "FX-NOP", "ICAAP-STRESS"}


def _seed_latest_period(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _run_liquidity_baseline(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": "baseline",
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded", run
    return run


def _generate_bsd3(db_client: TestClient, **overrides: Any) -> Any:
    payload = {"return_code": "BSD3", "reporting_date": REPORTING_DATE, **overrides}
    return db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json=payload,
    )


def _seed_checker_user() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        exists = session.scalar(select(User.id).where(User.id == CHECKER_USER_ID))
        if exists is None:
            session.add(
                User(
                    id=CHECKER_USER_ID,
                    organization_id=ORG_1,
                    email="demo.checker@example.test",
                    display_name="Demo Checker",
                )
            )
            session.commit()
    finally:
        session.close()


def test_generate_package_snapshots_sources_and_versions(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    run = _run_liquidity_baseline(db_client, period["id"])

    response = _generate_bsd3(db_client, notes="First cut.")
    assert response.status_code == 201, response.text
    package = response.json()
    assert package["status"] == "generated"
    assert package["return_code"] == "BSD3"
    assert package["return_family"] == "liquidity"
    assert package["frequency"] == "monthly"
    assert package["reporting_date"] == REPORTING_DATE
    assert package["version"] == 1
    assert package["supersedes_id"] is None
    assert package["generated_by"] == str(USER_1)
    assert package["validation_report"] is None
    assert package["validation_passed"] is None
    assert package["notes"] == "First cut."

    snapshot = package["snapshot"]
    assert snapshot["schema_version"] == "regulatory-package-v1"
    assert snapshot["return_code"] == "BSD3"
    assert snapshot["fidelity"] == "PARTIAL"
    assert snapshot["reporting_date"] == REPORTING_DATE
    assert snapshot["institution"]["name"] == "Sample Bank Ltd"
    assert snapshot["institution"]["currency"] == "GHS"
    sections = {section["code"]: section for section in snapshot["sections"]}
    assert set(sections) == {
        "hqla",
        "outflows",
        "inflows",
        "lcr_summary",
        "nsfr_asf",
        "nsfr_rsf",
        "nsfr_summary",
    }
    hqla = sections["hqla"]
    assert hqla["total"]["equals_sum_of_rows"] is True
    assert sum(Decimal(row["value"]) for row in hqla["rows"]) == Decimal(hqla["total"]["value"])
    totals = {row["code"]: row for row in snapshot["totals"]}
    assert Decimal(totals["hqla_total_ghs"]["value"]) == Decimal("735000000")
    assert "lcr_pct" in totals and "nsfr_pct" in totals

    assert package["source_runs"] == [
        {
            "module": "liquidity",
            "run_id": run["id"],
            "input_hash": run["input_hash"],
            "engine_version": "regulatory-liquidity-v1.0.0",
        }
    ]

    # Regeneration mints a new immutable version and supersedes the prior one.
    second = _generate_bsd3(db_client)
    assert second.status_code == 201, second.text
    regenerated = second.json()
    assert regenerated["version"] == 2
    assert regenerated["supersedes_id"] == package["id"]
    assert regenerated["status"] == "generated"

    prior = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}",
        headers=headers(),
    )
    assert prior.status_code == 200
    assert prior.json()["status"] == "superseded"
    assert prior.json()["snapshot"] == snapshot

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        params={"return_code": "BSD3"},
    ).json()
    assert listed["total"] == 2
    current_only = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        params={"return_code": "BSD3", "include_superseded": False},
    ).json()
    assert current_only["total"] == 1
    assert current_only["packages"][0]["id"] == regenerated["id"]


def test_generate_requires_computed_data_and_registered_return(db_client: TestClient) -> None:
    _seed_latest_period(db_client)

    no_run = _generate_bsd3(db_client)
    assert no_run.status_code == 409, no_run.text
    assert no_run.json()["error"]["details"]["error_code"] == "no_baseline_run"

    unknown = _generate_bsd3(db_client, return_code="NOT-A-RETURN")
    assert unknown.status_code == 404
    assert "not registered" in unknown.json()["error"]["message"]

    no_period = _generate_bsd3(db_client, reporting_date="2027-01-31")
    assert no_period.status_code == 404
    assert "No reporting period ends on 2027-01-31" in no_period.json()["error"]["message"]


def test_validate_package_reports_findings_and_flips_status(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()

    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}/validate",
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    validated = response.json()
    assert validated["status"] == "validated"
    assert validated["validation_passed"] is True
    report = validated["validation_report"]
    assert report["passed"] is True
    assert report["error_count"] == 0
    rules = {finding["rule"] for finding in report["findings"]}
    assert rules == {
        "package.sections_complete",
        "package.totals_consistent",
        "package.prior_period_movement",
    }
    assert all(
        finding["severity"] in ("INFO", "WARNING", "ERROR") for finding in report["findings"]
    )


def test_validation_errors_keep_package_generated_and_block_approval(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()

    # Corrupt the stored snapshot: empty a required section's rows.
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        row = session.scalar(
            select(RegulatoryPackage).where(RegulatoryPackage.id == UUID(package["id"]))
        )
        assert row is not None
        snapshot = dict(row.snapshot)
        sections = [dict(section) for section in snapshot["sections"]]
        sections[0]["rows"] = []
        snapshot["sections"] = sections
        row.snapshot = snapshot
        session.commit()
    finally:
        session.close()

    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}/validate",
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "generated"
    assert body["validation_passed"] is False
    errors = [
        finding
        for finding in body["validation_report"]["findings"]
        if finding["severity"] == "ERROR"
    ]
    assert errors and "has no rows" in errors[0]["detail"]

    blocked = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}/request-approval",
        headers=headers(),
        json={},
    )
    assert blocked.status_code == 409


def test_full_lifecycle_happy_path_with_maker_checker(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    _seed_checker_user()
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"

    premature = db_client.post(f"{base}/request-approval", headers=headers(), json={})
    assert premature.status_code == 409  # must validate first

    assert db_client.post(f"{base}/validate", headers=headers()).json()["status"] == "validated"

    requested = db_client.post(
        f"{base}/request-approval",
        headers=headers(),
        json={"reason": "March LCR/NSFR filing."},
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "pending_approval"

    # Maker-checker: the generator cannot decide their own package.
    same_user = db_client.post(
        f"{base}/decide-approval",
        headers=headers(),
        json={"action": "approved"},
    )
    assert same_user.status_code == 409
    assert "different user" in same_user.json()["error"]["message"]

    # A rejection without a reason is rejected by the schema.
    missing_reason = db_client.post(
        f"{base}/decide-approval",
        headers=headers(ORG_1, CHECKER_USER_ID),
        json={"action": "rejected"},
    )
    assert missing_reason.status_code == 422

    approved = db_client.post(
        f"{base}/decide-approval",
        headers=headers(ORG_1, CHECKER_USER_ID),
        json={"action": "approved"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    actions = [(item["action"], item["actor_user_id"]) for item in body["approvals"]]
    assert actions == [
        ("requested", str(USER_1)),
        ("approved", str(CHECKER_USER_ID)),
    ]

    # A decided package cannot be decided again.
    again = db_client.post(
        f"{base}/decide-approval",
        headers=headers(ORG_1, CHECKER_USER_ID),
        json={"action": "approved"},
    )
    assert again.status_code == 409


def test_rejected_approval_returns_package_to_generated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    _seed_checker_user()
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"
    db_client.post(f"{base}/validate", headers=headers())
    db_client.post(f"{base}/request-approval", headers=headers(), json={})

    rejected = db_client.post(
        f"{base}/decide-approval",
        headers=headers(ORG_1, CHECKER_USER_ID),
        json={"action": "rejected", "reason": "HQLA composition needs rework."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "generated"

    # Rework path: the same package can be validated again.
    revalidated = db_client.post(f"{base}/validate", headers=headers())
    assert revalidated.json()["status"] == "validated"


def test_export_and_submit_are_stubbed_and_events_empty(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"

    export = db_client.post(f"{base}/export", headers=headers(), params={"kind": "xlsx"})
    assert export.status_code == 501
    assert "export" in export.json()["error"]["message"].lower()

    submit = db_client.post(f"{base}/submit", headers=headers(), json={"channel": "email"})
    assert submit.status_code == 501
    assert "submission wave" in submit.json()["error"]["message"]

    events = db_client.get(f"{base}/submission-events", headers=headers())
    assert events.status_code == 200
    assert events.json() == {
        "package_id": package["id"],
        "events": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


def test_calendar_lists_obligations_for_all_families(db_client: TestClient) -> None:
    _seed_latest_period(db_client)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-obligations",
        headers=headers(),
        params={"horizon_months": 3},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(SAMPLE_BANK_ID)
    assert body["horizon_months"] == 3
    obligations = body["obligations"]
    assert obligations
    assert {item["return_code"] for item in obligations} == REGISTRY_CODES
    assert {item["return_family"] for item in obligations} == {
        "liquidity",
        "capital",
        "irrbb",
        "fx",
        "icaap_stress",
    }
    due_dates = [item["due_date"] for item in obligations]
    assert due_dates == sorted(due_dates)
    for item in obligations:
        assert item["rag"] in ("overdue", "due_soon", "on_track")
        assert item["due_date"] > item["reporting_date"]
        assert item["package_id"] is None  # nothing generated for these dates yet

    wider = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-obligations",
        headers=headers(),
        params={"horizon_months": 12},
    ).json()["obligations"]
    assert len(wider) > len(obligations)


def test_return_templates_expose_registry_with_fidelity(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/regulatory-reporting/templates", headers=headers())
    assert response.status_code == 200, response.text
    templates = {item["code"]: item for item in response.json()["templates"]}
    assert set(templates) == REGISTRY_CODES
    assert templates["BSD3"]["fidelity"] == "PARTIAL"
    assert templates["BSD3"]["default_channel"] == "orass_sandbox"
    assert templates["BSD3"]["regulator"] == "BOG"
    assert templates["IRRBB-PILOT"]["fidelity"] == "REPRESENTATIVE"
    assert templates["ICAAP-STRESS"]["frequency"] == "annual"
    for template in templates.values():
        assert template["fidelity"] in ("CONFIRMED", "PARTIAL", "REPRESENTATIVE")
        assert template["directive_citation"]


def test_channel_config_credentials_are_write_only(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_latest_period(db_client)
    url = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox"

    missing = db_client.get(url, headers=headers())
    assert missing.status_code == 404

    created = db_client.put(
        url,
        headers=headers(),
        json={"config": {"institution_code": "SBL-001", "basis": "solo"}},
    )
    assert created.status_code == 200, created.text
    assert created.json()["has_credentials"] is False
    assert created.json()["config"]["institution_code"] == "SBL-001"

    # Without a vault master key, credential material is refused, not stored.
    refused = db_client.put(
        url,
        headers=headers(),
        json={"config": {}, "credentials": {"api_key": "secret"}},
    )
    assert refused.status_code == 409
    assert "CREDENTIAL_VAULT_MASTER_KEY" in refused.json()["error"]["message"]

    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "test-master-key-material")
    get_settings.cache_clear()
    stored = db_client.put(
        url,
        headers=headers(),
        json={"config": {"institution_code": "SBL-001"}, "credentials": {"api_key": "secret"}},
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["has_credentials"] is True
    assert len(body["credential_fingerprint"]) == 64
    assert "credentials" not in body
    assert "secret" not in stored.text

    fetched = db_client.get(url, headers=headers())
    assert fetched.status_code == 200
    assert fetched.json()["has_credentials"] is True
    assert "secret" not in fetched.text


def test_regulatory_reporting_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    org2 = headers(ORG_2)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages"

    assert (
        db_client.post(
            base,
            headers=org2,
            json={"return_code": "BSD3", "reporting_date": REPORTING_DATE},
        ).status_code
        == 404
    )
    assert db_client.get(base, headers=org2).status_code == 404
    assert db_client.get(f"{base}/{package['id']}", headers=org2).status_code == 404
    assert db_client.post(f"{base}/{package['id']}/validate", headers=org2).status_code == 404
    assert (
        db_client.post(
            f"{base}/{package['id']}/request-approval", headers=org2, json={}
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"{base}/{package['id']}/decide-approval",
            headers=org2,
            json={"action": "approved"},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"{base}/{package['id']}/submission-events", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-obligations", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/email",
            headers=org2,
        ).status_code
        == 404
    )
    assert (
        db_client.put(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/email",
            headers=org2,
            json={"config": {}},
        ).status_code
        == 404
    )

    # An unknown package id under the right tenant is also a 404.
    assert db_client.get(f"{base}/{uuid4()}", headers=headers()).status_code == 404
