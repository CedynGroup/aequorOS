"""Regulatory-reporting hub API tests against the ACTUAL primary database.

Invariants, never magnitudes: the package lifecycle (generate → validate → request
approval → maker-checker → export / channel submit / poll) over the real Sample
Bank; a new version supersedes whatever the real current chain holds; registry,
calendar and template membership derive from the registry itself (recoded
``LCR-NSFR``/``CAR-RWA`` plus the official BoG ``bsd`` family, weekly returns
included); tenant isolation via a real neighbouring tenant; exports go only to the
in-memory seam. Opt-in via REAL_DATA_DATABASE_URL (tests/real_data.py); every
test rolls back inside ``real_client`` — nothing reaches the primary or storage.
"""

from __future__ import annotations

import email as email_lib
import hashlib
import io
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from email import policy as email_policy
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import (
    Bank,
    RegulatoryArtifactVersion,
    RegulatoryChannelConfig,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatoryRun,
    User,
)
from app.services.ingestion import bank_slug
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.services.regulatory_reporting.bog_forms.catalog import (
    WEEKLY_ANCHOR_WEEKDAY,
    all_form_codes,
)
from app.services.regulatory_reporting.registry import REGISTRY
from app.storage.client import ObjectMetadata, StorageLocation
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_USER_ID,
    other_headers,
    real_headers,
    requires_real_data,
)
from tests.storage.inmemory import InMemoryStorageClient

pytestmark = requires_real_data

BASE = f"/api/v1/banks/{REAL_BANK_ID}"
PACKAGES = f"{BASE}/regulatory-packages"
RETURN_CODE = "LCR-NSFR"
# A cheap, engine-free return of a DIFFERENT code for the comparison-mismatch path
# (register-only corporate pack; the LMTD tools return would rebuild ladders over
# every real position just to be refused).
OTHER_RETURN_CODE = "LRT-OUTLET"
CHECKER_EMAIL = "real-suite.checker@samplebank.test"
FOUR_DP = Decimal("0.0001")
# Periodic registry entries expand into calendar obligations; the event-driven
# ones (corporate LRT packs, the Board/ALCO stress pack) never do.
PERIODIC_CODES = {code for code, item in REGISTRY.items() if not item.event_driven}
EVENT_DRIVEN_CODES = set(REGISTRY) - PERIODIC_CODES
BOG_BSD_CODES = set(all_form_codes())
LCR_SECTIONS = {
    "hqla",
    "outflows",
    "inflows",
    "lcr_summary",
    "nsfr_asf",
    "nsfr_rsf",
    "nsfr_summary",
    "headline_comparative",
}


# --- real-bank helpers --------------------------------------------------------


def _bank(client: TestClient) -> dict[str, Any]:
    response = client.get(BASE, headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _packages(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get(PACKAGES, headers=real_headers(), params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["packages"]


def _working_period(client: TestClient) -> dict[str, Any]:
    """The newest period this suite can regenerate its returns for.

    An ACKNOWLEDGED return is final at the regulator and only regenerates under a
    granted resubmission request — that gate is not this suite's subject, so a
    period whose current package is acknowledged is skipped over.
    """
    for period in _periods(client):
        acknowledged = _packages(client, reporting_date=period["period_end"], status="acknowledged")
        if not any(
            item["return_code"] in (RETURN_CODE, OTHER_RETURN_CODE) for item in acknowledged
        ):
            return period
    pytest.fail(
        "every real reporting period carries an acknowledged package of this suite's returns"
    )


def _stored_baseline_run(client: TestClient, period_id: str) -> dict[str, Any] | None:
    """The succeeded baseline liquidity run generation would bind (newest first)."""
    response = client.get(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        params={
            "module": "liquidity",
            "scenario_code": "baseline",
            "reporting_period_id": period_id,
            "limit": 100,
        },
    )
    assert response.status_code == 200, response.text
    for summary in response.json()["runs"]:
        if summary["status"] == "succeeded":
            detail = client.get(f"{BASE}/regulatory-runs/{summary['id']}", headers=real_headers())
            assert detail.status_code == 200, detail.text
            return detail.json()
    return None


def _baseline_run(client: TestClient, period_id: str) -> dict[str, Any]:
    """Reuse the real bank's stored baseline run for the period; run the engine
    only when none exists (a real run is the expensive part of this suite)."""
    stored = _stored_baseline_run(client, period_id)
    if stored is not None:
        return stored
    response = client.post(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
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


def _period_without_baseline_run(client: TestClient, session: Session) -> dict[str, Any]:
    """A period with NO succeeded baseline liquidity run (the ``no_baseline_run``
    path). Newest such real period; if the whole spine is computed, the newest
    period's baseline runs are deleted on the shared (rolled-back) transaction."""
    periods = _periods(client)
    for period in periods:
        if _stored_baseline_run(client, period["id"]) is None:
            return period
    period = periods[0]
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(RegulatoryRun).where(
            RegulatoryRun.organization_id == REAL_ORG_ID,
            RegulatoryRun.bank_id == REAL_BANK_ID,
            RegulatoryRun.reporting_period_id == UUID(period["id"]),
            RegulatoryRun.module == "liquidity",
            RegulatoryRun.scenario_code == "baseline",
        )
    )
    session.commit()
    return period


def _generate(client: TestClient, reporting_date: str, **overrides: Any) -> Any:
    payload = {"return_code": RETURN_CODE, "reporting_date": reporting_date, **overrides}
    return client.post(PACKAGES, headers=real_headers(), json=payload)


def _generated(client: TestClient, reporting_date: str, **overrides: Any) -> dict[str, Any]:
    response = _generate(client, reporting_date, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def _checker(session: Session) -> UUID:
    """A second ACTIVE user in the real org so maker-checker has a checker; the
    row lives only inside the rolled-back transaction."""
    session.info["organization_id"] = REAL_ORG_ID
    existing = session.scalar(
        select(User.id).where(User.organization_id == REAL_ORG_ID, User.email == CHECKER_EMAIL)
    )
    if existing is not None:
        session.commit()
        return existing
    checker = User(
        id=uuid4(),
        organization_id=REAL_ORG_ID,
        email=CHECKER_EMAIL,
        display_name="Real-suite Checker",
        role="approver",
    )
    session.add(checker)
    session.commit()
    return checker.id


def _checker_headers(checker_id: UUID, roles: tuple[str, ...] = ("admin",)) -> dict[str, str]:
    return real_headers(user_id=checker_id, roles=roles, email=CHECKER_EMAIL)


def _relax_signing(client: TestClient, return_code: str = RETURN_CODE) -> None:
    """Opt the return out of the signing gate the way an administrator would (an
    audited PUT), so what these journeys exercise is the workflow, not the
    attestation gate proved in tests/services/test_attestation_spine.py."""
    response = client.put(
        "/api/v1/attestation/signing-policies",
        headers=real_headers(),
        json={
            "return_code": return_code,
            "required_signatures": [],
            "require_signature": False,
            "effective_from": "2000-01-01",
            "reason": "test fixture: this suite is not about the signing gate",
        },
    )
    assert response.status_code in (200, 201), response.text


def _approve_package(client: TestClient, session: Session, package_id: str) -> None:
    checker = _checker(session)
    base = f"{PACKAGES}/{package_id}"
    validated = client.post(f"{base}/validate", headers=real_headers())
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "validated"
    requested = client.post(f"{base}/request-approval", headers=real_headers(), json={})
    assert requested.status_code == 200, requested.text
    approved = client.post(
        f"{base}/decide-approval",
        headers=_checker_headers(checker),
        json={"action": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


def _pin_sandbox(client: TestClient, config: dict[str, Any] | None = None) -> None:
    """The ORASS sandbox's poll outcome is bank-configurable (ack/reject/slow/
    downtime); pin it so the assertion is about the workflow, not the bank's
    current setting. Rolled back with everything else."""
    response = client.put(
        f"{BASE}/regulatory-reporting/channel-configs/orass_sandbox",
        headers=real_headers(),
        json={"config": {"sandbox_behavior": "ack", **(config or {})}},
    )
    assert response.status_code == 200, response.text


def _four_dp(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(FOUR_DP)


@pytest.fixture
def fake_export_seam(
    monkeypatch: pytest.MonkeyPatch, storage_engine: InMemoryStorageClient
) -> InMemoryStorageClient:
    """Fake the lazy exports seam: write real bytes to the in-memory outputs
    tier (the same client real_client wires as the storage dependency) and mint
    the artifact row — the download endpoint round-trips against it and the
    primary's object storage never receives a test artifact."""

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


# --- generation + versioning --------------------------------------------------


def _assert_snapshot_binds_run(
    package: dict[str, Any], run: dict[str, Any], bank: dict[str, Any], reporting_date: str
) -> None:
    """The LCR/NSFR snapshot is a sealed copy of the bound baseline run: its
    headline totals ARE the run's metrics and its provenance names the run."""
    snapshot = package["snapshot"]
    assert snapshot["schema_version"] == "regulatory-package-v1"
    assert snapshot["return_code"] == RETURN_CODE
    assert snapshot["fidelity"] == "PARTIAL"
    assert snapshot["reporting_date"] == reporting_date
    assert snapshot["institution"]["name"] == bank["name"]
    assert snapshot["institution"]["currency"] == bank["currency"]
    sections = {section["code"]: section for section in snapshot["sections"]}
    assert set(sections) == LCR_SECTIONS
    hqla = sections["hqla"]
    assert hqla["total"]["equals_sum_of_rows"] is True
    assert sum(Decimal(row["value"]) for row in hqla["rows"]) == Decimal(hqla["total"]["value"])
    totals = {row["code"]: row for row in snapshot["totals"]}
    assert Decimal(totals["hqla_total_ghs"]["value"]) == Decimal(
        str(run["metrics"]["hqla_total_ghs"])
    )
    assert _four_dp(totals["lcr_pct"]["value"]) == _four_dp(run["metrics"]["lcr_pct"])
    assert _four_dp(totals["nsfr_pct"]["value"]) == _four_dp(run["metrics"]["nsfr_pct"])

    # Provenance: the latest succeeded liquidity run per scenario the real bank
    # holds for the period, the baseline that fed the figures among them.
    source_runs = package["source_runs"]
    assert {
        "module": "liquidity",
        "run_id": run["id"],
        "input_hash": run["input_hash"],
        "engine_version": run["engine_version"],
    } in source_runs
    assert all(entry["module"] == "liquidity" for entry in source_runs)
    assert len({entry["run_id"] for entry in source_runs}) == len(source_runs)


def test_generate_package_snapshots_sources_and_versions(real_client: TestClient) -> None:
    period = _working_period(real_client)
    reporting_date = period["period_end"]
    run = _baseline_run(real_client, period["id"])
    bank = _bank(real_client)
    # Whatever the real chain already holds for this date, the new version
    # supersedes its current head and continues the numbering.
    before = _packages(
        real_client, return_code=RETURN_CODE, reporting_date=reporting_date, basis="solo"
    )
    prior_current = [item for item in before if item["status"] != "superseded"]
    assert len(prior_current) <= 1
    prior_max_version = max((item["version"] for item in before), default=0)

    package = _generated(real_client, reporting_date, notes="First cut.")
    assert package["status"] == "generated"
    assert package["return_code"] == RETURN_CODE
    assert package["return_family"] == "liquidity"
    assert package["frequency"] == "monthly"
    assert package["reporting_date"] == reporting_date
    assert package["basis"] == "solo"
    assert package["version"] == prior_max_version + 1
    assert package["supersedes_id"] == (prior_current[0]["id"] if prior_current else None)
    assert package["generated_by"] == str(REAL_USER_ID)
    assert package["validation_report"] is None
    assert package["validation_passed"] is None
    assert package["notes"] == "First cut."

    snapshot = package["snapshot"]
    _assert_snapshot_binds_run(package, run, bank, reporting_date)

    # Regeneration mints a new immutable version and supersedes the prior one.
    regenerated = _generated(real_client, reporting_date)
    assert regenerated["version"] == package["version"] + 1
    assert regenerated["supersedes_id"] == package["id"]
    assert regenerated["status"] == "generated"

    prior = real_client.get(f"{PACKAGES}/{package['id']}", headers=real_headers())
    assert prior.status_code == 200
    assert prior.json()["status"] == "superseded"
    assert prior.json()["snapshot"] == snapshot

    listed = _packages(
        real_client, return_code=RETURN_CODE, reporting_date=reporting_date, basis="solo"
    )
    assert len(listed) == len(before) + 2
    current_only = _packages(
        real_client,
        return_code=RETURN_CODE,
        reporting_date=reporting_date,
        basis="solo",
        include_superseded=False,
    )
    assert [item["id"] for item in current_only] == [regenerated["id"]]


def test_generate_requires_computed_data_and_registered_return(
    real_client: TestClient, real_session: Session
) -> None:
    uncomputed = _period_without_baseline_run(real_client, real_session)
    no_run = _generate(real_client, uncomputed["period_end"])
    assert no_run.status_code == 409, no_run.text
    assert no_run.json()["error"]["details"]["error_code"] == "no_baseline_run"

    period_end = _periods(real_client)[0]["period_end"]
    unknown = _generate(real_client, period_end, return_code="NOT-A-RETURN")
    assert unknown.status_code == 404
    assert "not registered" in unknown.json()["error"]["message"]

    # A date no real period ends on (the spine is monthly month-ends from the
    # first ingested history; a pre-history month-end can never be one). The
    # reporting date is BoG's and the return is registered — what is missing is
    # a computed position as of that date, so this is the same 409 conflict
    # ``no_baseline_run`` reports one step later, not a 404.
    absent = "1990-01-31"
    assert absent not in {item["period_end"] for item in _periods(real_client)}
    no_period = _generate(real_client, absent)
    assert no_period.status_code == 409, no_period.text
    body = no_period.json()["error"]
    assert body["details"]["error_code"] == "no_computed_position"
    # The refusal names the date required, and never offers an earlier book as
    # a substitute for it.
    assert absent in body["message"]
    assert "not a substitute" in body["message"]


def test_validate_package_reports_findings_and_flips_status(real_client: TestClient) -> None:
    period = _working_period(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])

    response = real_client.post(f"{PACKAGES}/{package['id']}/validate", headers=real_headers())
    assert response.status_code == 200, response.text
    validated = response.json()
    report = validated["validation_report"]
    # The real bank's LCR/NSFR return validates cleanly: every required section
    # populated and every declared total cross-footing.
    assert validated["status"] == "validated"
    assert validated["validation_passed"] is True
    assert report["passed"] is True
    assert report["error_count"] == 0
    rules = {finding["rule"] for finding in report["findings"]}
    assert {
        "package.sections_complete",
        "package.totals_consistent",
        "package.prior_period_movement",
    } <= rules
    assert all(
        finding["severity"] in ("INFO", "WARNING", "ERROR") for finding in report["findings"]
    )
    assert report["error_count"] + report["warning_count"] + report["info_count"] == len(
        report["findings"]
    )


def test_validation_errors_keep_package_generated_and_block_approval(
    real_client: TestClient, real_session: Session
) -> None:
    period = _working_period(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])

    # Corrupt the stored snapshot on the shared transaction: empty a required
    # section's rows (the outer rollback discards it).
    real_session.info["organization_id"] = REAL_ORG_ID
    row = real_session.scalar(
        select(RegulatoryPackage).where(RegulatoryPackage.id == UUID(package["id"]))
    )
    assert row is not None
    snapshot = dict(row.snapshot)
    sections = [dict(section) for section in snapshot["sections"]]
    sections[0]["rows"] = []
    snapshot["sections"] = sections
    row.snapshot = snapshot
    real_session.commit()

    response = real_client.post(f"{PACKAGES}/{package['id']}/validate", headers=real_headers())
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

    blocked = real_client.post(
        f"{PACKAGES}/{package['id']}/request-approval", headers=real_headers(), json={}
    )
    assert blocked.status_code == 409


# --- maker-checker ------------------------------------------------------------


def test_full_lifecycle_happy_path_with_maker_checker(
    real_client: TestClient, real_session: Session
) -> None:
    period = _working_period(real_client)
    # A bare approval decision is refused while the return's signatures are
    # outstanding — approving and signing are one act now. This journey is about
    # the decision's own rules (status, maker-checker, reason, re-decide), so opt
    # the return out the way an administrator would; the one-act composition is
    # proved in tests/services/test_attestation_workspace.py.
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    checker = _checker(real_session)
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"

    premature = real_client.post(f"{base}/request-approval", headers=real_headers(), json={})
    assert premature.status_code == 409  # must validate first

    validated = real_client.post(f"{base}/validate", headers=real_headers())
    assert validated.json()["status"] == "validated"

    requested = real_client.post(
        f"{base}/request-approval",
        headers=real_headers(),
        json={"reason": "Month-end LCR/NSFR filing."},
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "pending_approval"

    # Maker-checker: the generator cannot decide their own package.
    same_user = real_client.post(
        f"{base}/decide-approval", headers=real_headers(), json={"action": "approved"}
    )
    assert same_user.status_code == 409
    assert "different user" in same_user.json()["error"]["message"]

    # A rejection without a reason is rejected by the schema.
    missing_reason = real_client.post(
        f"{base}/decide-approval",
        headers=_checker_headers(checker),
        json={"action": "rejected"},
    )
    assert missing_reason.status_code == 422

    approved = real_client.post(
        f"{base}/decide-approval",
        headers=_checker_headers(checker),
        json={"action": "approved"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    actions = [(item["action"], item["actor_user_id"]) for item in body["approvals"]]
    assert actions == [
        ("requested", str(REAL_USER_ID)),
        ("approved", str(checker)),
    ]

    # A decided package cannot be decided again.
    again = real_client.post(
        f"{base}/decide-approval",
        headers=_checker_headers(checker),
        json={"action": "approved"},
    )
    assert again.status_code == 409


def test_rejected_approval_returns_package_to_generated(
    real_client: TestClient, real_session: Session
) -> None:
    period = _working_period(real_client)
    _baseline_run(real_client, period["id"])
    checker = _checker(real_session)
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"
    assert real_client.post(f"{base}/validate", headers=real_headers()).status_code == 200
    assert (
        real_client.post(f"{base}/request-approval", headers=real_headers(), json={}).status_code
        == 200
    )

    rejected = real_client.post(
        f"{base}/decide-approval",
        headers=_checker_headers(checker),
        json={"action": "rejected", "reason": "HQLA composition needs rework."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "generated"

    # Rework path: the same package can be validated again.
    revalidated = real_client.post(f"{base}/validate", headers=real_headers())
    assert revalidated.json()["status"] == "validated"


def test_submit_and_poll_gate_on_approval_and_events_start_empty(
    real_client: TestClient,
) -> None:
    period = _working_period(real_client)
    # Signing is required for every return by default, and this journey is not
    # about who signed — opt the return out the way an administrator would, so
    # what fails here is the gate under test rather than a missing signature.
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"

    # A merely-generated package cannot reach a channel (maker-checker first).
    submit = real_client.post(f"{base}/submit", headers=real_headers(), json={"channel": "email"})
    assert submit.status_code == 409
    assert "generated" in submit.json()["error"]["message"]

    poll = real_client.post(f"{base}/poll", headers=real_headers())
    assert poll.status_code == 409
    assert "submitted" in poll.json()["error"]["message"]

    events = real_client.get(f"{base}/submission-events", headers=real_headers())
    assert events.status_code == 200
    assert events.json() == {
        "package_id": package["id"],
        "events": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


# --- calendar + registry ------------------------------------------------------


def _expected_rag(item: dict[str, Any], as_of: date) -> set[str]:
    """The RAG grades consistent with an obligation's own fields."""
    if item["package_status"] in ("submitted", "acknowledged"):
        # Complete — unless a downtime email submission still awaits its ORASS
        # re-upload, which the row does not expose; either grade is coherent.
        return {"on_track", "overdue", "due_soon"}
    due = date.fromisoformat(item["due_date"])
    if as_of > due:
        return {"overdue"}
    if (due - as_of).days <= 7:  # noqa: PLR2004 - calendar.DUE_SOON_DAYS
        return {"due_soon"}
    return {"on_track"}


def _all_reporting_obligations(
    real_client: TestClient, horizon_months: int
) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = real_client.get(
            f"{BASE}/reporting-obligations",
            headers=real_headers(),
            params={"horizon_months": horizon_months, "limit": 100, "offset": offset},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        obligations.extend(body["obligations"])
        if not body["has_more"]:
            assert len(obligations) == body["total"]
            return obligations
        offset += body["limit"]


def test_calendar_lists_obligations_for_all_families(real_client: TestClient) -> None:
    response = real_client.get(
        f"{BASE}/reporting-obligations", headers=real_headers(), params={"horizon_months": 3}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == REAL_BANK_ID
    assert body["horizon_months"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["has_more"] is True
    assert len(body["obligations"]) == body["limit"]
    assert sum(body["summary"][key] for key in ("overdue", "due_soon", "on_track")) == body["total"]
    as_of = date.fromisoformat(body["as_of"])
    obligations = _all_reporting_obligations(real_client, 3)
    assert obligations
    # Exactly the periodic registry — the event-driven corporate LRT packs and
    # the stress pack never become calendar obligations; every official BoG BSD
    # form (family "bsd", weekly returns included) does.
    codes = {item["return_code"] for item in obligations}
    assert codes == PERIODIC_CODES
    assert not codes & EVENT_DRIVEN_CODES
    assert codes >= BOG_BSD_CODES
    assert {item["return_family"] for item in obligations} == {
        REGISTRY[code].family for code in PERIODIC_CODES
    }
    assert "bsd" in {item["return_family"] for item in obligations}
    due_dates = [item["due_date"] for item in obligations]
    assert due_dates == sorted(due_dates)
    for item in obligations:
        assert item["rag"] in _expected_rag(item, as_of), item
        assert item["due_date"] > item["reporting_date"]
        assert item["frequency"] == REGISTRY[item["return_code"]].frequency
        # Obligations are enumerated on the solo basis only (the calendar is
        # not doubled per basis); packages still carry basis independently.
        assert item["basis"] == "solo"
        # A linked package is the CURRENT one for that return + date, and the
        # row mirrors it; an unlinked row has no package at all.
        if item["package_id"] is None:
            assert item["package_status"] is None and item["package_version"] is None
        else:
            linked = real_client.get(f"{PACKAGES}/{item['package_id']}", headers=real_headers())
            assert linked.status_code == 200, linked.text
            package = linked.json()
            assert package["return_code"] == item["return_code"]
            assert package["reporting_date"] == item["reporting_date"]
            assert package["status"] == item["package_status"] != "superseded"
            assert package["version"] == item["package_version"]

    # DBK is a daily family: it appears via a small trailing business-day window
    # (not expanded across the horizon) and carries a 10:00 next-day cut-off time.
    dbk = [item for item in obligations if item["return_code"] == "DBK-DAILY"]
    assert dbk
    assert all(item["due_time"] == "10:00" for item in dbk)
    assert all(item["frequency"] == "daily" for item in dbk)
    assert all(item["due_time"] is None for item in obligations if item["frequency"] != "daily")

    # Weekly BoG returns anchor on the Friday close (the Guide fixes cadence and
    # the 9-day limit, not the weekday) and are all official BSD forms.
    weekly = [item for item in obligations if item["frequency"] == "weekly"]
    assert weekly
    assert {item["return_family"] for item in weekly} == {"bsd"}
    assert all(
        date.fromisoformat(item["reporting_date"]).weekday() == WEEKLY_ANCHOR_WEEKDAY
        for item in weekly
    )

    wider = _all_reporting_obligations(real_client, 12)
    # Periodic returns expand with the horizon; the fixed daily window does not.
    assert len(wider) > len(obligations)
    assert len([item for item in wider if item["frequency"] == "daily"]) == len(dbk)


def test_return_templates_expose_registry_with_fidelity(real_client: TestClient) -> None:
    response = real_client.get("/api/v1/regulatory-reporting/templates", headers=real_headers())
    assert response.status_code == 200, response.text
    templates = {item["code"]: item for item in response.json()["templates"]}
    # The registry (and hence the templates endpoint) also carries the
    # event-driven corporate LRT packs; only the calendar excludes them.
    assert set(templates) == set(REGISTRY)
    assert set(templates) >= EVENT_DRIVEN_CODES
    assert set(templates) >= BOG_BSD_CODES
    assert templates[RETURN_CODE]["fidelity"] == "PARTIAL"
    assert templates[RETURN_CODE]["default_channel"] == "orass_sandbox"
    assert templates[RETURN_CODE]["regulator"] == "BOG"
    assert templates["CAR-RWA"]["family"] == "capital"
    assert templates["IRRBB-PILOT"]["fidelity"] == "REPRESENTATIVE"
    assert templates["ICAAP-STRESS"]["frequency"] == "annual"
    for code in BOG_BSD_CODES:
        assert templates[code]["family"] == "bsd"
        assert templates[code]["generator"] == "bog_form"
    for code, template in templates.items():
        assert template["fidelity"] in ("CONFIRMED", "PARTIAL", "REPRESENTATIVE")
        assert template["directive_citation"]
        assert template["family"] == REGISTRY[code].family
        assert template["frequency"] == REGISTRY[code].frequency


def test_template_gated_returns_appear_in_calendar_but_refuse_generation(
    real_client: TestClient,
) -> None:
    """Phase 2 item 14: LAS-QUARTERLY is a real periodic obligation
    (calendar-visible today) whose BoG form is unpublished — generation refuses
    with template_pending instead of inventing a layout. (BSD-MONTHLY was the
    same gate for the balance-sheet/P&L pack until the official BSD workbooks
    landed; BSD2/BSD7A now generate for real — see test_bog_forms_framework.)"""
    obligations = _all_reporting_obligations(real_client, 6)
    codes = {item["return_code"] for item in obligations}
    assert "LAS-QUARTERLY" in codes
    assert "BSD-MONTHLY" not in codes  # retired placeholder
    assert {"BSD2", "BSD7A"} <= codes  # the official forms now carry the obligation

    period_end = _periods(real_client)[0]["period_end"]
    response = real_client.post(
        PACKAGES,
        headers=real_headers(),
        json={"return_code": "LAS-QUARTERLY", "reporting_date": period_end},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "template_pending"


def test_channel_config_credentials_are_write_only(
    real_client: TestClient, real_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"{BASE}/regulatory-reporting/channel-configs/orass_sandbox"
    # Start from "never configured" whatever the real bank has set — the row is
    # forgotten on the shared transaction and restored by the outer rollback.
    real_session.info["organization_id"] = REAL_ORG_ID
    real_session.execute(
        delete(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.organization_id == REAL_ORG_ID,
            RegulatoryChannelConfig.bank_id == REAL_BANK_ID,
            RegulatoryChannelConfig.channel == "orass_sandbox",
        )
    )
    real_session.commit()

    missing = real_client.get(url, headers=real_headers())
    assert missing.status_code == 404

    created = real_client.put(
        url,
        headers=real_headers(),
        json={"config": {"institution_code": "SBL-001", "basis": "solo"}},
    )
    assert created.status_code == 200, created.text
    assert created.json()["has_credentials"] is False
    assert created.json()["config"]["institution_code"] == "SBL-001"

    # Without a vault master key, credential material is refused, not stored.
    refused = real_client.put(
        url,
        headers=real_headers(),
        json={"config": {}, "credentials": {"api_key": "secret"}},
    )
    assert refused.status_code == 409
    assert "CREDENTIAL_VAULT_MASTER_KEY" in refused.json()["error"]["message"]

    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "test-master-key-material")
    get_settings.cache_clear()
    stored = real_client.put(
        url,
        headers=real_headers(),
        json={"config": {"institution_code": "SBL-001"}, "credentials": {"api_key": "secret"}},
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["has_credentials"] is True
    assert len(body["credential_fingerprint"]) == 64
    assert "credentials" not in body
    assert "secret" not in stored.text

    fetched = real_client.get(url, headers=real_headers())
    assert fetched.status_code == 200
    assert fetched.json()["has_credentials"] is True
    assert "secret" not in fetched.text


def test_regulatory_reporting_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _working_period(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    other = other_headers()

    assert (
        real_client.post(
            PACKAGES,
            headers=other,
            json={"return_code": RETURN_CODE, "reporting_date": period["period_end"]},
        ).status_code
        == 404
    )
    assert real_client.get(PACKAGES, headers=other).status_code == 404
    assert real_client.get(f"{PACKAGES}/{package['id']}", headers=other).status_code == 404
    assert (
        real_client.post(f"{PACKAGES}/{package['id']}/validate", headers=other).status_code == 404
    )
    assert (
        real_client.post(
            f"{PACKAGES}/{package['id']}/request-approval", headers=other, json={}
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"{PACKAGES}/{package['id']}/decide-approval",
            headers=other,
            json={"action": "approved"},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"{PACKAGES}/{package['id']}/submission-events", headers=other).status_code
        == 404
    )
    assert real_client.get(f"{BASE}/reporting-obligations", headers=other).status_code == 404
    assert (
        real_client.get(
            f"{BASE}/regulatory-reporting/channel-configs/email", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.put(
            f"{BASE}/regulatory-reporting/channel-configs/email",
            headers=other,
            json={"config": {}},
        ).status_code
        == 404
    )

    # Export/submission wave endpoints are tenant-scoped 404s too.
    assert (
        real_client.post(
            f"{PACKAGES}/{package['id']}/export", headers=other, params={"kind": "xlsx"}
        ).status_code
        == 404
    )
    assert (
        real_client.post(f"{PACKAGES}/{package['id']}/submit", headers=other, json={}).status_code
        == 404
    )
    assert real_client.post(f"{PACKAGES}/{package['id']}/poll", headers=other).status_code == 404
    assert (
        real_client.get(
            f"{PACKAGES}/{package['id']}/email-fallback-instructions", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"{BASE}/regulatory-artifacts/{uuid4()}/download", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"{PACKAGES}/{package['id']}/artifact-versions", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(f"{PACKAGES}/{package['id']}/version-chain", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(
            f"{PACKAGES}/{package['id']}/comparison",
            headers=other,
            params={"against": package["id"]},
        ).status_code
        == 404
    )

    # An unknown package id under the right tenant is also a 404.
    assert real_client.get(f"{PACKAGES}/{uuid4()}", headers=real_headers()).status_code == 404


# --- exports + artifacts ------------------------------------------------------


def test_export_creates_artifact_and_download_round_trips(
    real_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _working_period(real_client)
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"

    exported = real_client.post(f"{base}/export", headers=real_headers(), params={"kind": "xlsx"})
    assert exported.status_code == 201, exported.text
    artifact = exported.json()
    assert artifact["kind"] == "xlsx"
    assert artifact["package_id"] == package["id"]
    assert artifact["object_path"].endswith(f"{package['id']}/{RETURN_CODE}.xlsx")
    assert artifact["size_bytes"] > 0
    assert len(artifact["checksum_sha256"]) == 64

    download = real_client.get(
        f"{BASE}/regulatory-artifacts/{artifact['id']}/download", headers=real_headers()
    )
    assert download.status_code == 200, download.text
    expected = f"{RETURN_CODE}:xlsx:{package['id']}".encode()
    assert download.content == expected
    assert hashlib.sha256(download.content).hexdigest() == artifact["checksum_sha256"]
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert f'filename="{RETURN_CODE}.xlsx"' in download.headers["content-disposition"]

    # Unknown artifact under the right tenant is a 404.
    assert (
        real_client.get(
            f"{BASE}/regulatory-artifacts/{uuid4()}/download", headers=real_headers()
        ).status_code
        == 404
    )


def _seed_artifact_version(
    session: Session, package_id: str, storage: InMemoryStorageClient, content: bytes
) -> RegulatoryArtifactVersion:
    """One archived revision plus its bytes, without running the export engine.

    The fake export seam mints artifact rows only; a version row is what the
    signing ceremony appends, and this suite has no signer. Written on the
    shared transaction, so it is rolled back with everything else.
    """
    session.info["organization_id"] = REAL_ORG_ID
    package = session.get(RegulatoryPackage, UUID(package_id))
    assert package is not None
    bank = session.get(Bank, package.bank_id)
    assert bank is not None
    slug = bank_slug(session, bank)
    object_path = f"bog_returns/{package.reporting_date.isoformat()}/{package.id}/{RETURN_CODE}.pdf"
    checksum = hashlib.sha256(content).hexdigest()
    stored = storage.write(
        StorageLocation(institution_slug=slug, tier="outputs", object_path=object_path),
        io.BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier="outputs",
            checksum_sha256=checksum,
            written_at=datetime.now(UTC),
            written_by="test-signer",
        ),
    )
    version = RegulatoryArtifactVersion(
        organization_id=REAL_ORG_ID,
        package_id=package.id,
        kind="pdf",
        object_path=object_path,
        storage_version_id=stored.version_id,
        checksum_sha256=checksum,
        size_bytes=len(content),
    )
    session.add(version)
    session.commit()
    return version


def test_artifact_version_list_and_download_round_trip(
    real_client: TestClient, real_session: Session, fake_export_seam: InMemoryStorageClient
) -> None:
    """The version routes exist, are tenant-scoped, and prove what they serve.

    The signed-revision resolution is exercised against a real ceremony in
    tests/services/test_attestation_artifact_signing.py; what is on trial here
    is the wire — that a version id is not an artifact id, that the checksum is
    re-verified before any byte is sent, and that a neighbouring tenant sees a
    404 rather than another bank's return.
    """
    period = _working_period(real_client)
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    version = _seed_artifact_version(
        real_session, package["id"], fake_export_seam, b"%PDF-1.4 unsigned base"
    )

    listed = real_client.get(
        f"{PACKAGES}/{package['id']}/artifact-versions", headers=real_headers()
    )
    assert listed.status_code == 200, listed.text
    versions = listed.json()["versions"]
    assert [entry["id"] for entry in versions] == [str(version.id)]
    # Unsigned: nothing pinned it, so nothing is the filed document yet.
    assert versions[0]["signed_by"] is None
    assert versions[0]["is_latest"] is True
    assert versions[0]["is_filed"] is False

    download = real_client.get(
        f"{BASE}/regulatory-artifact-versions/{version.id}/download", headers=real_headers()
    )
    assert download.status_code == 200, download.text
    assert download.content == b"%PDF-1.4 unsigned base"
    assert download.headers["content-type"].startswith("application/pdf")
    assert f'filename="{RETURN_CODE}.pdf"' in download.headers["content-disposition"]

    # An artifact id is not a version id, and neither is another tenant's.
    assert (
        real_client.get(
            f"{BASE}/regulatory-artifact-versions/{uuid4()}/download", headers=real_headers()
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"{BASE}/regulatory-artifact-versions/{version.id}/download",
            headers=other_headers(),
        ).status_code
        == 404
    )


def test_version_chain_and_comparison_serve_a_prior_version(
    real_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    """A superseded version over the wire: its files (or the absence of them),
    and a figures diff against the version that replaced it.

    The chain endpoint is what turns the Prior-versions card from a list of
    timestamps into something an examiner can act on, so the contract it
    publishes — ``has_retrievable_files``, the artifact surfaces, the diff
    shape — is asserted rather than assumed.
    """
    period = _working_period(real_client)
    reporting_date = period["period_end"]
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    prior = _generated(real_client, reporting_date)
    # Exported while it was still current — the only moment it can be, and the
    # reason a superseded version has files to offer at all.
    exported = real_client.post(
        f"{PACKAGES}/{prior['id']}/export", headers=real_headers(), params={"kind": "pdf"}
    )
    assert exported.status_code == 201, exported.text
    current = _generated(real_client, reporting_date)

    chained = real_client.get(f"{PACKAGES}/{prior['id']}/version-chain", headers=real_headers())
    assert chained.status_code == 200, chained.text
    chain = chained.json()
    assert chain["current_package_id"] == current["id"]
    # Newest first, contiguous, and reaching back over whatever the real chain
    # already held for this date.
    versions = [entry["version"] for entry in chain["versions"]]
    assert versions == sorted(versions, reverse=True)
    assert versions[:2] == [current["version"], prior["version"]]
    assert versions == list(range(current["version"], 0, -1))
    entries = {entry["package_id"]: entry for entry in chain["versions"]}

    prior_entry = entries[prior["id"]]
    assert prior_entry["status"] == "superseded"
    assert prior_entry["is_current"] is False
    assert [artifact["kind"] for artifact in prior_entry["artifacts"]] == ["pdf"]
    assert prior_entry["has_retrievable_files"] is True
    assert prior_entry["signatures"] == []

    # The replacement was never exported, so the card must say so rather than
    # render a download control that cannot work.
    current_entry = entries[current["id"]]
    assert current_entry["is_current"] is True
    assert current_entry["artifacts"] == []
    assert current_entry["artifact_versions"] == []
    assert current_entry["has_retrievable_files"] is False
    assert sum(1 for entry in chain["versions"] if entry["is_current"]) == 1

    compared = real_client.get(
        f"{PACKAGES}/{prior['id']}/comparison",
        headers=real_headers(),
        params={"against": current["id"]},
    )
    assert compared.status_code == 200, compared.text
    comparison = compared.json()
    assert comparison["base"]["version"] == prior["version"]
    assert comparison["target"]["version"] == current["version"]
    # Regeneration off unchanged canonical data reproduces the figures exactly;
    # only volatile generation metadata differs, and that is not a figure.
    assert comparison["identical"] is True
    assert comparison["sections"] == []
    assert comparison["unchanged_section_count"] > 0

    # A different return has no line items in common — refused, not rendered.
    other = real_client.post(
        PACKAGES,
        headers=real_headers(),
        json={"return_code": OTHER_RETURN_CODE, "reporting_date": reporting_date},
    )
    assert other.status_code == 201, other.text
    mismatched = real_client.get(
        f"{PACKAGES}/{prior['id']}/comparison",
        headers=real_headers(),
        params={"against": other.json()["id"]},
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["error"]["details"]["error_code"] == "comparison_return_mismatch"


# --- channels -----------------------------------------------------------------


def test_submit_default_channel_auto_exports_then_poll_acknowledges(
    real_client: TestClient, real_session: Session, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _working_period(real_client)
    _relax_signing(real_client)
    _pin_sandbox(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"
    _approve_package(real_client, real_session, package["id"])

    # No channel in the payload -> the registry default for the return
    # (orass_sandbox); no artifacts yet -> the workflow auto-exports xlsx first.
    submitted = real_client.post(f"{base}/submit", headers=real_headers(), json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    events = real_client.get(f"{base}/submission-events", headers=real_headers()).json()
    assert events["total"] == 1
    event = events["events"][0]
    assert event["channel"] == "orass_sandbox"
    assert event["event"] == "submitted"
    # ORASS-style form-set reference: return-code prefix + zero-padded
    # per-(bank, return) submission sequence — the real bank's count so far + 1.
    assert re.fullmatch(r"LCRN\d{5}", event["external_ref"]), event["external_ref"]
    assert event["detail"]["sandbox"] is True
    assert "not publicly documented" in event["detail"]["note"]
    assert event["detail"]["auto_exported_kinds"] == ["xlsx"]

    polled = real_client.post(f"{base}/poll", headers=real_headers())
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["poll_status"] == "acknowledged"
    assert body["package"]["status"] == "acknowledged"
    assert body["event"]["event"] == "status_poll"
    assert body["event"]["detail"]["result"] == "acknowledged"

    events = real_client.get(f"{base}/submission-events", headers=real_headers()).json()
    assert [item["event"] for item in events["events"]] == [
        "acknowledged",
        "status_poll",
        "submitted",
    ]


def test_downtime_then_email_fallback_then_orass_reupload(
    real_client: TestClient, real_session: Session, fake_export_seam: InMemoryStorageClient
) -> None:
    period = _working_period(real_client)
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"
    _approve_package(real_client, real_session, package["id"])

    # The operator can preview the guided email bundle at any time.
    instructions = real_client.get(f"{base}/email-fallback-instructions", headers=real_headers())
    assert instructions.status_code == 200, instructions.text
    bundle = instructions.json()
    assert bundle["pending_orass_reupload"] is True
    assert "bsdletters@bog.gov.gh" in bundle["instructions"]
    assert "500 penalty units" in bundle["penalty_reminder"]
    assert "– submitted under ORASS downtime" in bundle["subject"]
    assert bundle["recipient_guidance"]["downtime_return_address"] is None  # UNKNOWN per research

    # ORASS is down -> structured 409 directing to the email fallback.
    _pin_sandbox(real_client, {"downtime": True})
    downtime = real_client.post(
        f"{base}/submit", headers=real_headers(), json={"channel": "orass_sandbox"}
    )
    assert downtime.status_code == 409, downtime.text
    details = downtime.json()["error"]["details"]
    assert details["error_code"] == "channel_downtime"
    assert details["fallback"]["channel"] == "email"
    assert details["fallback"]["endpoint"].endswith(f"{package['id']}/submit")

    # Email fallback submits but does NOT complete the obligation.
    emailed = real_client.post(f"{base}/submit", headers=real_headers(), json={"channel": "email"})
    assert emailed.status_code == 200, emailed.text
    assert emailed.json()["status"] == "submitted"
    events = real_client.get(f"{base}/submission-events", headers=real_headers()).json()
    email_event = events["events"][0]
    assert email_event["channel"] == "email"
    assert email_event["external_ref"].startswith(f"EMAIL-{RETURN_CODE}-")
    assert email_event["detail"]["pending_orass_reupload"] is True

    # ORASS restored -> re-upload (submitted -> submitted) clears the flag.
    _pin_sandbox(real_client)
    reuploaded = real_client.post(
        f"{base}/submit", headers=real_headers(), json={"channel": "orass_sandbox"}
    )
    assert reuploaded.status_code == 200, reuploaded.text
    assert reuploaded.json()["status"] == "submitted"
    events = real_client.get(f"{base}/submission-events", headers=real_headers()).json()
    orass_event = events["events"][0]
    assert orass_event["channel"] == "orass_sandbox"
    assert orass_event["detail"]["pending_orass_reupload"] is False
    assert orass_event["detail"]["reupload_of"] == email_event["external_ref"]

    # After the re-upload the normal acknowledgement flow applies.
    polled = real_client.post(f"{base}/poll", headers=real_headers())
    assert polled.status_code == 200
    assert polled.json()["poll_status"] == "acknowledged"

    # A completed package cannot be submitted again.
    again = real_client.post(f"{base}/submit", headers=real_headers(), json={"channel": "email"})
    assert again.status_code == 409


def test_email_fallback_eml_downloads_as_rfc822_with_attachments(
    real_client: TestClient, fake_export_seam: InMemoryStorageClient
) -> None:
    """W7: the downtime bundle is downloadable as a send-ready .eml whose
    subject/body/attachments mirror the email-fallback instructions."""
    period = _working_period(real_client)
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"
    exported = real_client.post(f"{base}/export", headers=real_headers(), params={"kind": "xlsx"})
    assert exported.status_code == 201

    response = real_client.get(f"{base}/email-fallback.eml", headers=real_headers())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("message/rfc822")
    message = email_lib.message_from_bytes(response.content, policy=email_policy.default)
    assert "submitted under ORASS downtime" in message["Subject"]
    attachments = [part for part in message.walk() if part.get_filename()]
    assert [part.get_filename() for part in attachments] == [f"{RETURN_CODE}.xlsx"]
    assert attachments[0].get_payload(decode=True) == (
        f"{RETURN_CODE}:xlsx:{package['id']}".encode()
    )
    # Body carries the re-upload rule and the Act 930 penalty reminder.
    body_part = message.get_body(preferencelist=("plain",))
    assert body_part is not None
    body = body_part.get_content()
    assert "re-upload" in body.lower() or "reupload" in body.lower() or "restored" in body.lower()
    assert "penalty" in body.lower()


# --- roles + settings ---------------------------------------------------------


def test_control_actions_require_approver_role(
    real_client: TestClient, real_session: Session
) -> None:
    """W2 role gates: analysts prepare (generate/validate/export), but approval
    decisions, channel submissions, polls, and resubmission decisions need the
    ``approver`` role — the AequorOS mirror of ORASS's Principal-only submit."""
    period = _working_period(real_client)
    # Role gates are the subject; the signing gate is not. Without this the
    # approver's decision would be refused for want of a signature and the 403/200
    # contrast this test draws would disappear.
    _relax_signing(real_client)
    _baseline_run(real_client, period["id"])
    checker = _checker(real_session)
    package = _generated(real_client, period["period_end"])
    base = f"{PACKAGES}/{package['id']}"
    analyst = real_headers(roles=("analyst",))
    approver = _checker_headers(checker, roles=("approver",))

    # Analysts CAN prepare.
    assert real_client.post(f"{base}/validate", headers=analyst).status_code == 200
    assert real_client.post(f"{base}/request-approval", headers=analyst, json={}).status_code == 200
    # Analysts CANNOT decide, submit, poll, or decide resubmissions.
    decided = real_client.post(
        f"{base}/decide-approval", headers=analyst, json={"action": "approved"}
    )
    assert decided.status_code == 403
    assert "approver" in decided.json()["error"]["message"]
    assert real_client.post(f"{base}/submit", headers=analyst, json={}).status_code == 403
    assert real_client.post(f"{base}/poll", headers=analyst).status_code == 403

    # An approver (who is not the maker) can decide.
    approved = real_client.post(
        f"{base}/decide-approval", headers=approver, json={"action": "approved"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


def test_viewer_is_read_only_across_the_hub(real_client: TestClient) -> None:
    viewer = real_headers(roles=("viewer",))
    period_end = _periods(real_client)[0]["period_end"]
    refused = real_client.post(
        PACKAGES,
        headers=viewer,
        json={"return_code": RETURN_CODE, "reporting_date": period_end},
    )
    assert refused.status_code == 403
    listed = real_client.get(PACKAGES, headers=viewer)
    assert listed.status_code == 200


def test_organization_users_directory_resolves_actors(
    real_client: TestClient, real_session: Session
) -> None:
    checker = _checker(real_session)
    response = real_client.get("/api/v1/organization/users", headers=real_headers())
    assert response.status_code == 200
    users = response.json()["users"]
    by_id = {user["id"]: user for user in users}
    assert str(REAL_USER_ID) in by_id
    assert str(checker) in by_id
    assert by_id[str(checker)]["email"] == CHECKER_EMAIL
    assert all({"id", "display_name", "role", "is_active"} <= set(u) for u in users)


def test_reporting_settings_endpoints_round_trip_and_shift_due_dates(
    real_client: TestClient,
) -> None:
    url = f"{BASE}/regulatory-reporting/settings"

    before = real_client.get(url, headers=real_headers())
    assert before.status_code == 200, before.text
    assert isinstance(before.json()["deadline_overrides"], dict)
    without_override = [
        item
        for item in _all_reporting_obligations(real_client, 1)
        if item["return_code"] == "CAR-RWA"
    ]
    assert without_override

    put = real_client.put(url, headers=real_headers(), json={"deadline_overrides": {"CAR-RWA": 21}})
    assert put.status_code == 200, put.text
    assert put.json()["deadline_overrides"] == {"CAR-RWA": 21}

    stored = real_client.get(url, headers=real_headers())
    assert stored.json()["deadline_overrides"] == {"CAR-RWA": 21}

    obligations = _all_reporting_obligations(real_client, 1)
    car_rwa = [item for item in obligations if item["return_code"] == "CAR-RWA"]
    assert car_rwa and all(item["due_date"].endswith("-21") for item in car_rwa)
    # The override moves the deadline, never the reporting date or the count.
    assert [item["reporting_date"] for item in car_rwa] == [
        item["reporting_date"] for item in without_override
    ]

    # Out-of-range days are rejected by the schema (422).
    bad = real_client.put(url, headers=real_headers(), json={"deadline_overrides": {"CAR-RWA": 40}})
    assert bad.status_code == 422


def test_basis_dimension_via_create_and_list_endpoints(real_client: TestClient) -> None:
    period = _working_period(real_client)
    reporting_date = period["period_end"]
    _baseline_run(real_client, period["id"])

    solo = _generated(real_client, reporting_date, basis="solo")
    assert solo["basis"] == "solo"
    consolidated = _generated(real_client, reporting_date, basis="consolidated")
    assert consolidated["basis"] == "consolidated"
    # Solo and consolidated are independent version chains for the same
    # (return, reporting date): the consolidated version never supersedes solo.
    assert consolidated["supersedes_id"] != solo["id"]

    for basis, created in (("solo", solo), ("consolidated", consolidated)):
        current = _packages(
            real_client,
            return_code=RETURN_CODE,
            reporting_date=reporting_date,
            basis=basis,
            include_superseded=False,
        )
        assert [item["id"] for item in current] == [created["id"]]
        assert current[0]["basis"] == basis
