"""Read authorization for the enterprise-stress run registry (docs/stress.md §4.5).

W5: the run list, latest and detail reads all require RISK/confidential VIEW on
the exact bank. An aggregated-only reader loses the list; a grant on bank A
never reads sibling bank B's runs; own-bank reads list only own-bank runs.

Sibling-bank pattern: tests/stress/api/test_scoped_authorization_integration.py
(read-only — PR #838 edits it). Runs are seeded directly as immutable
RegulatoryRun rows: the reads under test never execute the engine.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import SensitivityScope
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.live.models.regulatory import BankReportingPeriod
from app.live.public import RegulatoryRun
from app.stress.service import enterprise_stress
from tests.stress.authority import grant
from tests.support.helpers import ORG_1, USER_1, headers
from tests.support.ingestion_seeding import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
LATEST_URL = "/api/v1/banks/{bank_id}/enterprise-stress/latest"
SIBLING_BANK_ID = "BK-1S000002"
SCENARIO_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


@pytest.fixture
def unbound(db_client: TestClient, db_session: Session) -> TestClient:
    db_session.execute(
        delete(AuthorizationBinding).where(
            AuthorizationBinding.principal_user_id == USER_1,
            AuthorizationBinding.role_bundle != "member",
        )
    )
    db_session.commit()
    return db_client


def _metrics() -> dict[str, object]:
    """The immutable persisted fields the registry reads; no engine execution."""
    return {
        "outcome": {
            "capital": {
                "stressed_car_end_pct": "10.5",
                "baseline_car_end_pct": "13.0",
                "car_erosion_pp": "-2.5",
            },
            # LCR/NSFR not assessed for this bank (docs/sdi.md §4.6).
            "liquidity": {},
        },
        "projection": {
            "stress_stays_above_all_minima": True,
            "first_breach_year": None,
            "binding_minima": [],
        },
        "appendix_ii": {"table1_summary": {"capital_gap": "0"}},
    }


def _seed_run(db_session: Session, bank_id: str, scenario_code: str) -> RegulatoryRun:
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == bank_id,
            BankReportingPeriod.organization_id == ORG_1,
        )
    )
    assert period is not None
    run = RegulatoryRun(
        organization_id=ORG_1,
        bank_id=bank_id,
        reporting_period_id=period.id,
        module=enterprise_stress.MODULE_ENTERPRISE_STRESS,
        scenario_code=scenario_code,
        status="succeeded",
        engine_version="enterprise-stress-v5.0.0",
        input_schema_version="test",
        output_schema_version="test",
        input_hash="a" * 64,
        created_by=USER_1,
        inputs={"scenario": {"id": str(SCENARIO_ID), "code": scenario_code}},
        metrics=_metrics(),
    )
    db_session.add(run)
    db_session.commit()
    return run


def _seed_sibling(db_session: Session, sample_bank_id: str) -> str:
    bank = db_session.get(Bank, sample_bank_id)
    assert bank is not None
    db_session.add(
        Bank(
            id=SIBLING_BANK_ID,
            organization_id=ORG_1,
            name="Sibling institution",
            short_name="Sibling",
            currency=bank.currency,
            jurisdiction_code=bank.jurisdiction_code,
            license_type=bank.license_type,
            institution_type=bank.institution_type,
        )
    )
    db_session.add(
        BankReportingPeriod(
            organization_id=ORG_1,
            bank_id=SIBLING_BANK_ID,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
            label="2026 Q1",
            status="closed",
        )
    )
    db_session.commit()
    return SIBLING_BANK_ID


def test_aggregated_only_reader_loses_the_run_list(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    run = _seed_run(db_session, bank_id, "aggregated_denial")
    binding_id = grant(db_session, sensitivity=SensitivityScope.AGGREGATED)
    denied = unbound.get(RUNS_URL.format(bank_id=bank_id), headers=headers())
    assert denied.status_code == 403, denied.text
    assert str(run.id) not in denied.text
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.id == binding_id)
    )
    grant(db_session, bank_id=bank_id)
    allowed = unbound.get(RUNS_URL.format(bank_id=bank_id), headers=headers())
    assert allowed.status_code == 200, allowed.text
    assert [row["run_id"] for row in allowed.json()] == [str(run.id)]


def test_sibling_bank_grant_cannot_list_or_read_latest(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    _seed_run(db_session, bank_id, "own_bank_run")
    sibling_id = _seed_sibling(db_session, bank_id)
    sibling_run = _seed_run(db_session, sibling_id, "sibling_bank_run")
    grant(db_session, bank_id=bank_id)

    denied_list = unbound.get(RUNS_URL.format(bank_id=sibling_id), headers=headers())
    assert denied_list.status_code == 403, denied_list.text
    assert str(sibling_run.id) not in denied_list.text

    denied_latest = unbound.get(
        LATEST_URL.format(bank_id=sibling_id),
        params={
            "reporting_period_id": str(sibling_run.reporting_period_id),
            "scenario_id": str(SCENARIO_ID),
        },
        headers=headers(),
    )
    assert denied_latest.status_code == 403, denied_latest.text
    assert str(sibling_run.id) not in denied_latest.text


def _error_body(payload: dict[str, object]) -> object:
    """The error envelope minus the per-request id, for body comparison."""
    error = payload.get("error")
    if isinstance(error, dict):
        error.pop("request_id", None)
    return payload


def test_sibling_run_detail_is_opaque_via_both_bank_paths(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    _seed_run(db_session, bank_id, "own_bank_run")
    sibling_id = _seed_sibling(db_session, bank_id)
    sibling_run = _seed_run(db_session, sibling_id, "sibling_bank_run")
    grant(db_session, bank_id=bank_id)

    # Bank B path: the denial is identical to a missing run on the denied bank.
    via_sibling = unbound.get(
        f"{RUNS_URL.format(bank_id=sibling_id)}/{sibling_run.id}", headers=headers()
    )
    sibling_missing = unbound.get(
        f"{RUNS_URL.format(bank_id=sibling_id)}/{uuid4()}", headers=headers()
    )
    # Bank A path: the sibling id is indistinguishable from a missing run —
    # the bank-scoped query never reveals that the id exists on bank B.
    via_own_bank = unbound.get(
        f"{RUNS_URL.format(bank_id=bank_id)}/{sibling_run.id}", headers=headers()
    )
    own_missing = unbound.get(f"{RUNS_URL.format(bank_id=bank_id)}/{uuid4()}", headers=headers())
    assert via_sibling.status_code == sibling_missing.status_code == 404, via_sibling.text
    assert via_own_bank.status_code == own_missing.status_code == 404, via_own_bank.text
    assert _error_body(via_sibling.json()) == _error_body(sibling_missing.json())
    assert _error_body(via_own_bank.json()) == _error_body(own_missing.json())
    assert str(sibling_run.id) not in via_sibling.text
    assert str(sibling_run.id) not in via_own_bank.text


def test_own_bank_reads_list_exactly_own_runs_and_latest_and_detail(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    own_run = _seed_run(db_session, bank_id, "own_bank_run")
    sibling_id = _seed_sibling(db_session, bank_id)
    sibling_run = _seed_run(db_session, sibling_id, "sibling_bank_run")
    grant(db_session, bank_id=bank_id)

    listing = unbound.get(RUNS_URL.format(bank_id=bank_id), headers=headers())
    assert listing.status_code == 200, listing.text
    run_ids = [row["run_id"] for row in listing.json()]
    assert run_ids == [str(own_run.id)]
    assert str(sibling_run.id) not in run_ids

    latest = unbound.get(
        LATEST_URL.format(bank_id=bank_id),
        params={
            "reporting_period_id": str(own_run.reporting_period_id),
            "scenario_id": str(SCENARIO_ID),
        },
        headers=headers(),
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["run_id"] == str(own_run.id)

    detail = unbound.get(
        f"{RUNS_URL.format(bank_id=bank_id)}/{own_run.id}", headers=headers()
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["run_id"] == str(own_run.id)
