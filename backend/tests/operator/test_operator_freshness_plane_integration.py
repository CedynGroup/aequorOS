"""Operator-board freshness through the staff plane's own access boundary.

Regression coverage for the operator freshness blindness the tenant freshness
VIEW filter introduced (the stacked fix on parent PR
https://github.com/aequoros/aequoros/pull/840): the tenants board reused the
tenant freshness service with an actor-less operator context, so the
deny-closed per-module VIEW filter answered with a well-formed empty report —
every bank silently fresh, ``modules_reported`` 0, no error anywhere.

These tests pin the operator route contract: a bank whose filed figures have
drifted from current data is reported stale with its modules, a bank with no
computed live state is never silently fresh, and the staff boundary still
denies unauthenticated callers. Ordinary tenant freshness permissions are
pinned where they live
(``tests/live/api/test_live_engine_authorization_integration.py``) and are
untouched here: no tenant user, binding, or grant is created for the operator
seat — the staff board's authority is the staff plane's own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.live.public import BankReportingPeriod, LiveMetric, RegulatoryRun
from app.models import User
from app.operator.schemas import TenantsListRead
from tests.operator.conftest import operator_headers, provision_payload

_PERIOD_END = date(2026, 10, 31)
_OFFICIAL_LIQUIDITY_HASH = "official-liquidity-input-hash-0000000000000000"
_DRIFTED_LIQUIDITY_HASH = "drifted-current-liquidity-hash-00000000000"


def _provision(operator_client: TestClient) -> tuple[str, str]:
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = response.json()
    assert body["succeeded"] is True, body
    return body["organization_id"], body["bank_id"]


def _seed_filed_then_drifted_liquidity(
    operator_db: Session, organization_id: str, bank_id: str
) -> None:
    """One closed period, one succeeded official liquidity run, and a live
    metric computed from moved-on data — the drift the board must surface."""
    admin = operator_db.scalar(select(User).where(User.organization_id == organization_id))
    assert admin is not None
    period = BankReportingPeriod(
        organization_id=organization_id,
        bank_id=bank_id,
        period_start=_PERIOD_END.replace(day=1),
        period_end=_PERIOD_END,
        label=_PERIOD_END.strftime("%Y-%m"),
        status="closed",
    )
    operator_db.add(period)
    operator_db.flush()
    operator_db.add(
        RegulatoryRun(
            organization_id=organization_id,
            bank_id=bank_id,
            reporting_period_id=period.id,
            module="liquidity",
            scenario_code="baseline",
            status="succeeded",
            engine_version="test-1",
            input_schema_version="test-1",
            output_schema_version="test-1",
            input_hash=_OFFICIAL_LIQUIDITY_HASH,
            inputs={},
            created_by=admin.id,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    operator_db.add(
        LiveMetric(
            organization_id=organization_id,
            bank_id=bank_id,
            source_as_of_date=_PERIOD_END + timedelta(days=3),
            module="liquidity",
            status="green",
            computed_from_input_hash=_DRIFTED_LIQUIDITY_HASH,
            computed_at=datetime.now(UTC),
        )
    )
    operator_db.commit()


def _tenants_board(operator_client: TestClient) -> TenantsListRead:
    response = operator_client.get("/operator/v1/tenants", headers=operator_headers())
    assert response.status_code == 200, response.text
    return TenantsListRead.model_validate_json(response.text)


def test_operator_board_reports_drifted_modules_stale(
    operator_client: TestClient, operator_db: Session
) -> None:
    """A bank with a filed run and drifted live data is stale WITH modules.

    The regressed board answered ``is_stale: false, modules_reported: 0`` —
    deny-closed emptiness indistinguishable from authorized-empty."""
    organization_id, bank_id = _provision(operator_client)
    _seed_filed_then_drifted_liquidity(operator_db, organization_id, bank_id)

    board = _tenants_board(operator_client)
    assert len(board.tenants) == 1
    freshness = board.tenants[0].freshness
    assert freshness is not None, "a bank with periods has a freshness summary"
    assert freshness.is_stale is True
    assert freshness.modules_reported > 0
    assert "liquidity" in freshness.stale_modules
    assert freshness.latest_computed_at is not None


def test_operator_board_absent_live_state_is_not_silently_fresh(
    operator_client: TestClient, operator_db: Session
) -> None:
    """Periods without any computed live state are reported, not hidden.

    The board's fixed freshness vocabulary never collapses to a silently
    fresh empty report: a module with nothing computed yet has no evidence
    of freshness, and the board says so."""
    organization_id, bank_id = _provision(operator_client)
    operator_db.add(
        BankReportingPeriod(
            organization_id=organization_id,
            bank_id=bank_id,
            period_start=_PERIOD_END.replace(day=1),
            period_end=_PERIOD_END,
            label=_PERIOD_END.strftime("%Y-%m"),
            status="closed",
        )
    )
    operator_db.commit()

    board = _tenants_board(operator_client)
    freshness = board.tenants[0].freshness
    assert freshness is not None
    assert freshness.is_stale is True
    assert freshness.modules_reported > 0


def test_operator_board_still_denies_unauthenticated_callers(
    operator_client: TestClient,
) -> None:
    """The staff boundary holds: no bearer, no board."""
    response = operator_client.get("/operator/v1/tenants")
    assert response.status_code == 401
