"""ICAAP capital-planning workflow (Phase 2 item 10).

The plan document (Pillar-2 register, management actions, triggers) follows
maker-checker with annual Board approval; approval requires stored forecast
runs (a multi-year plan without projected ratios is not a plan). The
projection assembles at read time — Pillar-1 + Pillar-2 requirement overlay
against each stored forecast scenario's CAR path — and the ILAAP component
refreshes quarterly as an append-only snapshot of stored liquidity evidence.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, User
from app.schemas.capital_plan import (
    CapitalPlanApprove,
    CapitalPlanContent,
    CapitalPlanPut,
    CapitalTrigger,
    IlaapRefreshCreate,
    ManagementAction,
    Pillar2AddOn,
)
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import capital_plan, regulatory_forecasting, regulatory_liquidity
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
)
REPORTING_DATE = date(2026, 3, 31)


def _period_id(db: Session) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def _ensure_checker(db: Session) -> None:
    if db.scalar(select(User.id).where(User.id == CHECKER.actor_user_id)) is None:
        db.add(
            User(
                id=CHECKER.actor_user_id,
                organization_id=DEMO_ORG_ID,
                email="demo.checker@example.test",
                display_name="Demo Checker",
                role="approver",
            )
        )
        db.commit()


def _content() -> CapitalPlanContent:
    return CapitalPlanContent(
        pillar2_addons=[
            Pillar2AddOn(
                risk_type="Credit concentration",
                add_on_pct_rwa=Decimal("1.5"),
                rationale="Top-20 obligor concentration above internal appetite.",
            ),
            Pillar2AddOn(
                risk_type="IRRBB",
                add_on_pct_rwa=Decimal("0.5"),
                rationale="EVE sensitivity near the supervisory outlier threshold.",
            ),
        ],
        management_actions=[
            ManagementAction(
                action="Suspend dividends",
                trigger="CAR within 1pp of the total requirement",
                owner="Board",
            )
        ],
        trigger_framework=[
            CapitalTrigger(
                metric_code="car_pct",
                early_warning_level=Decimal("16"),
                action_level=Decimal("14.5"),
                escalation="CFO notifies ALCO; Board paper within 10 business days.",
            )
        ],
    )


def _run_forecast(db: Session) -> None:
    run = regulatory_forecasting.create_forecast_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        ForecastRunCreate(reporting_period_id=_period_id(db), scenario_code="base"),
    )
    assert run.status == "succeeded", run


def test_capital_plan_lifecycle_requires_forecast_and_maker_checker(
    db_session: Session,
) -> None:
    seed_sample_bank(db_session)
    _ensure_checker(db_session)

    capital_plan.put_capital_plan(
        db_session, MAKER, SAMPLE_BANK_ID, CapitalPlanPut(content=_content(), reason="Draft")
    )
    # Approval needs projected ratios: no forecast run yet -> refused.
    with pytest.raises(HTTPException) as excinfo:
        capital_plan.approve_capital_plan(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CapitalPlanApprove(approval_reference="BM-ICAAP-1", reason="Annual approval"),
        )
    assert excinfo.value.detail["error_code"] == "no_forecast_run"

    _run_forecast(db_session)
    # Maker-checker: the preparer cannot approve.
    with pytest.raises(HTTPException) as excinfo:
        capital_plan.approve_capital_plan(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            CapitalPlanApprove(approval_reference="BM-ICAAP-1", reason="Annual approval"),
        )
    assert excinfo.value.detail["error_code"] == "self_approval"

    approved = capital_plan.approve_capital_plan(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CapitalPlanApprove(approval_reference="BM-ICAAP-1", reason="Annual approval"),
    )
    assert approved.status == "approved"
    assert approved.approval_expires_at is not None
    assert (approved.approval_expires_at - approved.approval_timestamp.date()).days == 365

    # The projection assembles from the stored forecast run with the
    # Pillar-1 + Pillar-2 requirement overlay.
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    projection = summary.projection
    assert projection is not None
    assert projection.pillar2_addon_pct == Decimal("2.0")
    assert projection.total_requirement_pct == projection.pillar1_min_pct + Decimal("2.0")
    base = next(s for s in projection.scenarios if s.scenario_code == "base")
    # Year 0 is the as-of starting position, then the five projection years.
    assert len(base.years) == 6
    first = base.years[0]
    assert first.car_pct is not None and first.headroom_pp is not None
    assert first.headroom_pp == first.car_pct - projection.total_requirement_pct
    assert len(base.input_hash) == 64


def test_ilaap_component_refreshes_quarterly_from_stored_liquidity_state(
    db_session: Session,
) -> None:
    seed_sample_bank(db_session)
    period_id = _period_id(db_session)

    with pytest.raises(HTTPException) as excinfo:
        capital_plan.refresh_ilaap(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            IlaapRefreshCreate(reporting_period_id=period_id),
        )
    assert excinfo.value.detail["error_code"] == "no_baseline_run"

    run = regulatory_liquidity.create_liquidity_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    combined = regulatory_liquidity.create_liquidity_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="combined"
        ),
    )
    assert combined.status == "succeeded"

    snapshot = capital_plan.refresh_ilaap(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        IlaapRefreshCreate(reporting_period_id=period_id, notes="Q1 refresh"),
    )
    assert snapshot.as_of_date == REPORTING_DATE
    assert snapshot.adequate is True  # seeded baseline is green/green
    assert snapshot.lcr_status == "green" and snapshot.nsfr_status == "green"
    # The combined scenario's LCR (87.36) is the worst stressed observation.
    assert snapshot.worst_stressed_lcr_pct is not None
    assert snapshot.worst_stressed_lcr_pct < Decimal("100")
    assert snapshot.ewi_escalation_state == "normal"
    assert snapshot.cfp_approved is False

    # Quarterly-refreshable: a second refresh appends, never overwrites.
    again = capital_plan.refresh_ilaap(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        IlaapRefreshCreate(reporting_period_id=period_id),
    )
    snapshots = capital_plan.list_ilaap_snapshots(db_session, MAKER, SAMPLE_BANK_ID).snapshots
    assert [entry.id for entry in snapshots][0] == again.id
    assert len(snapshots) == 2
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.latest_ilaap is not None and summary.latest_ilaap.id == again.id
