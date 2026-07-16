from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import Bank, RegulatoryPackage, RegulatoryPackageArtifact, User
from app.services.ingestion import bank_slug
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from app.storage.client import ObjectMetadata, StorageLocation
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.storage.inmemory import InMemoryStorageClient

CHECKER_USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
REPORTING_DATE = "2026-03-31"
REGISTRY_CODES = {"BSD3", "LMT", "BSD2", "IRRBB-PILOT", "FX-NOP", "ICAAP-STRESS"}


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


def _approve_package(db_client: TestClient, package_id: str) -> None:
    _seed_checker_user()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    validated = db_client.post(f"{base}/validate", headers=headers())
    assert validated.status_code == 200 and validated.json()["status"] == "validated"
    requested = db_client.post(f"{base}/request-approval", headers=headers(), json={})
    assert requested.status_code == 200, requested.text
    approved = db_client.post(
        f"{base}/decide-approval",
        headers=headers(ORG_1, CHECKER_USER_ID),
        json={"action": "approved"},
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"


@pytest.fixture
def fake_export_seam(
    monkeypatch: pytest.MonkeyPatch, storage_engine: InMemoryStorageClient
) -> InMemoryStorageClient:
    """Fake the lazy exports seam: write real bytes to the in-memory outputs
    tier (the same client db_client wires as the storage dependency) and mint
    the artifact row — the download endpoint round-trips against it."""

    def _fake_export(
        db: Session, ctx: TenantContext, package: RegulatoryPackage, kind: str
    ) -> RegulatoryPackageArtifact:
        _ = ctx
        bank = db.get(Bank, package.bank_id)
        assert bank is not None
        slug = bank_slug(db, bank)
        content = f"{package.return_code}:{kind}:{package.id}".encode()
        checksum = hashlib.sha256(content).hexdigest()
        object_path = (
            f"bog_returns/{package.reporting_date.isoformat()}/"
            f"{package.id}/{package.return_code}.{kind}"
        )
        location = StorageLocation(institution_slug=slug, tier="outputs", object_path=object_path)
        storage_engine.write(
            location,
            io.BytesIO(content),
            ObjectMetadata(
                institution_slug=slug,
                tier="outputs",
                checksum_sha256=checksum,
                written_at=datetime.now(UTC),
                written_by="test-exporter",
            ),
        )
        artifact = RegulatoryPackageArtifact(
            organization_id=package.organization_id,
            package_id=package.id,
            kind=kind,
            object_path=object_path,
            checksum_sha256=checksum,
            size_bytes=len(content),
        )
        db.add(artifact)
        db.flush()
        return artifact

    monkeypatch.setattr(reporting_workflow, "_resolve_exporter", lambda: _fake_export)
    return storage_engine


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


def test_submit_and_poll_gate_on_approval_and_events_start_empty(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"

    # A merely-generated package cannot reach a channel (maker-checker first).
    submit = db_client.post(f"{base}/submit", headers=headers(), json={"channel": "email"})
    assert submit.status_code == 409
    assert "generated" in submit.json()["error"]["message"]

    poll = db_client.post(f"{base}/poll", headers=headers())
    assert poll.status_code == 409
    assert "submitted" in poll.json()["error"]["message"]

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
        db_client.get(f"{base}/{package['id']}/submission-events", headers=org2).status_code == 404
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

    # Export/submission wave endpoints are tenant-scoped 404s too.
    assert (
        db_client.post(
            f"{base}/{package['id']}/export", headers=org2, params={"kind": "xlsx"}
        ).status_code
        == 404
    )
    assert (
        db_client.post(f"{base}/{package['id']}/submit", headers=org2, json={}).status_code == 404
    )
    assert db_client.post(f"{base}/{package['id']}/poll", headers=org2).status_code == 404
    assert (
        db_client.get(
            f"{base}/{package['id']}/email-fallback-instructions", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifacts/{uuid4()}/download",
            headers=org2,
        ).status_code
        == 404
    )

    # An unknown package id under the right tenant is also a 404.
    assert db_client.get(f"{base}/{uuid4()}", headers=headers()).status_code == 404


def test_export_creates_artifact_and_download_round_trips(
    db_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"

    exported = db_client.post(f"{base}/export", headers=headers(), params={"kind": "xlsx"})
    assert exported.status_code == 201, exported.text
    artifact = exported.json()
    assert artifact["kind"] == "xlsx"
    assert artifact["package_id"] == package["id"]
    assert artifact["object_path"].endswith(f"{package['id']}/BSD3.xlsx")
    assert artifact["size_bytes"] > 0
    assert len(artifact["checksum_sha256"]) == 64

    download = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifacts/{artifact['id']}/download",
        headers=headers(),
    )
    assert download.status_code == 200, download.text
    expected = f"BSD3:xlsx:{package['id']}".encode()
    assert download.content == expected
    assert hashlib.sha256(download.content).hexdigest() == artifact["checksum_sha256"]
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="BSD3.xlsx"' in download.headers["content-disposition"]

    # Unknown artifact under the right tenant is a 404.
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifacts/{uuid4()}/download",
            headers=headers(),
        ).status_code
        == 404
    )


def test_submit_default_channel_auto_exports_then_poll_acknowledges(
    db_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"
    _approve_package(db_client, package["id"])

    # No channel in the payload -> the registry default for BSD3 (orass_sandbox);
    # no artifacts yet -> the workflow auto-exports xlsx first.
    submitted = db_client.post(f"{base}/submit", headers=headers(), json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    events = db_client.get(f"{base}/submission-events", headers=headers()).json()
    assert events["total"] == 1
    event = events["events"][0]
    assert event["channel"] == "orass_sandbox"
    assert event["event"] == "submitted"
    assert event["external_ref"].startswith("SANDBOX-ORASS-BSD3-")
    assert event["detail"]["sandbox"] is True
    assert "not publicly documented" in event["detail"]["note"]
    assert event["detail"]["auto_exported_kinds"] == ["xlsx"]

    polled = db_client.post(f"{base}/poll", headers=headers())
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["poll_status"] == "acknowledged"
    assert body["package"]["status"] == "acknowledged"
    assert body["event"]["event"] == "status_poll"
    assert body["event"]["detail"]["result"] == "acknowledged"

    events = db_client.get(f"{base}/submission-events", headers=headers()).json()
    assert [item["event"] for item in events["events"]] == [
        "acknowledged",
        "status_poll",
        "submitted",
    ]


def test_downtime_then_email_fallback_then_orass_reupload(
    db_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _seed_latest_period(db_client)
    _run_liquidity_baseline(db_client, period["id"])
    package = _generate_bsd3(db_client).json()
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}"
    config_url = (
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox"
    )
    _approve_package(db_client, package["id"])

    # The operator can preview the guided email bundle at any time.
    instructions = db_client.get(f"{base}/email-fallback-instructions", headers=headers())
    assert instructions.status_code == 200, instructions.text
    bundle = instructions.json()
    assert bundle["pending_orass_reupload"] is True
    assert "bsdletters@bog.gov.gh" in bundle["instructions"]
    assert "500 penalty units" in bundle["penalty_reminder"]
    assert "– submitted under ORASS downtime" in bundle["subject"]
    assert bundle["recipient_guidance"]["downtime_return_address"] is None  # UNKNOWN per research

    # ORASS is down -> structured 409 directing to the email fallback.
    assert db_client.put(config_url, headers=headers(), json={"config": {"downtime": True}})
    downtime = db_client.post(
        f"{base}/submit", headers=headers(), json={"channel": "orass_sandbox"}
    )
    assert downtime.status_code == 409, downtime.text
    details = downtime.json()["error"]["details"]
    assert details["error_code"] == "channel_downtime"
    assert details["fallback"]["channel"] == "email"
    assert details["fallback"]["endpoint"].endswith(f"{package['id']}/submit")

    # Email fallback submits but does NOT complete the obligation.
    emailed = db_client.post(f"{base}/submit", headers=headers(), json={"channel": "email"})
    assert emailed.status_code == 200, emailed.text
    assert emailed.json()["status"] == "submitted"
    events = db_client.get(f"{base}/submission-events", headers=headers()).json()
    email_event = events["events"][0]
    assert email_event["channel"] == "email"
    assert email_event["external_ref"].startswith("EMAIL-BSD3-")
    assert email_event["detail"]["pending_orass_reupload"] is True

    # ORASS restored -> re-upload (submitted -> submitted) clears the flag.
    assert db_client.put(config_url, headers=headers(), json={"config": {}})
    reuploaded = db_client.post(
        f"{base}/submit", headers=headers(), json={"channel": "orass_sandbox"}
    )
    assert reuploaded.status_code == 200, reuploaded.text
    assert reuploaded.json()["status"] == "submitted"
    events = db_client.get(f"{base}/submission-events", headers=headers()).json()
    orass_event = events["events"][0]
    assert orass_event["channel"] == "orass_sandbox"
    assert orass_event["detail"]["pending_orass_reupload"] is False
    assert orass_event["detail"]["reupload_of"] == email_event["external_ref"]

    # After the re-upload the normal acknowledgement flow applies.
    polled = db_client.post(f"{base}/poll", headers=headers())
    assert polled.status_code == 200
    assert polled.json()["poll_status"] == "acknowledged"

    # A completed package cannot be submitted again.
    again = db_client.post(f"{base}/submit", headers=headers(), json={"channel": "email"})
    assert again.status_code == 409
