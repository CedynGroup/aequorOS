"""ICAAP capital-planning workflow (Phase 2 item 10).

The plan document (Pillar-2 register, management actions, triggers) follows
maker-checker with annual Board approval; approval requires stored forecast
runs (a multi-year plan without projected ratios is not a plan). The
projection assembles at read time — Pillar-1 + Pillar-2 requirement overlay
against each stored forecast scenario's CAR path — and the ILAAP component
refreshes quarterly as an append-only snapshot of stored liquidity evidence.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.policy import PolicyUnresolvedError, policy_unresolved
from app.models import (
    Bank,
    BankReportingPeriod,
    RegulatoryParameter,
    RegulatoryRun,
    User,
)
from app.schemas.capital_plan import (
    CapitalPlanApprove,
    CapitalPlanContentInput,
    CapitalPlanPut,
    CapitalTriggerInput,
    IlaapRefreshCreate,
    ManagementAction,
    Pillar2AddOnInput,
)
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import (
    capital_plan,
    institution_types,
    jurisdictions,
    regulatory_forecasting,
    regulatory_liquidity,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
    set_board_threshold,
)

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)

pytestmark = pytest.mark.usefixtures("forecasting_run_authority")
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


def _content() -> CapitalPlanContentInput:
    return CapitalPlanContentInput(
        pillar2_addons=[
            Pillar2AddOnInput(
                risk_type="Credit concentration",
                add_on_pct_rwa=Decimal("1.5"),
                rationale="Top-20 obligor concentration above internal appetite.",
            ),
            Pillar2AddOnInput(
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
            CapitalTriggerInput(
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
    materialize_canonical_test_book(db_session)
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
    assert excinfo.value.detail["error_code"] == "no_forecast_run"  # type: ignore[index]

    _run_forecast(db_session)
    # Maker-checker: the preparer cannot approve.
    with pytest.raises(HTTPException) as excinfo:
        capital_plan.approve_capital_plan(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            CapitalPlanApprove(approval_reference="BM-ICAAP-1", reason="Annual approval"),
        )
    assert excinfo.value.detail["error_code"] == "self_approval"  # type: ignore[index]

    approved = capital_plan.approve_capital_plan(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CapitalPlanApprove(approval_reference="BM-ICAAP-1", reason="Annual approval"),
    )
    assert approved.status == "approved"
    assert approved.approval_expires_at is not None
    assert (approved.approval_expires_at - approved.approval_timestamp.date()).days == 365  # type: ignore[index]

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
    materialize_canonical_test_book(db_session)
    period_id = _period_id(db_session)

    with pytest.raises(HTTPException) as excinfo:
        capital_plan.refresh_ilaap(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            IlaapRefreshCreate(reporting_period_id=period_id),
        )
    assert excinfo.value.detail["error_code"] == "no_baseline_run"  # type: ignore[index]

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


# --- ICAAP P0 (2026-09-19): governed floor, as-of resolution, CET1/Tier 1, FY labels ---


def _set_register(db: Session, code: str, value: str | None) -> None:
    """Set, add or (with ``None``) remove the institution's own register value.

    The fixture register carries no governed minimum (founder directive D-042:
    the clamp supplies the control-plane value), so a board value is added."""
    set_board_threshold(db, code, value)
    db.commit()


def _projection(db: Session):  # noqa: ANN202 - the schema type, read back
    summary = capital_plan.get_capital_plan(db, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is not None
    return summary.projection


def _run_forecast_for(db: Session, period_end: date, *, horizon_years: int = 5) -> None:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    assert period_id is not None
    run = regulatory_forecasting.create_forecast_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        ForecastRunCreate(
            reporting_period_id=period_id, scenario_code="base", horizon_years=horizon_years
        ),
    )
    assert run.status == "succeeded", run


def test_a_board_car_min_below_the_regulatory_floor_is_raised_to_it(db_session: Session) -> None:
    """The acceptance case: the bank register holds 10, the governed floor is
    13 — headroom is measured against 13, and the page says why.

    Since D-042 a register carries no governed minimum of its own, so the weaker
    board value this case is about is set explicitly."""
    materialize_canonical_test_book(db_session)
    _set_register(db_session, "car_min", "10")
    _run_forecast(db_session)
    projection = _projection(db_session)
    floor = projection.pillar1_min
    assert projection.pillar1_min_pct == Decimal("13")
    assert floor.value_pct == Decimal("13")
    assert floor.source == "control_plane"
    assert floor.board_register_pct == Decimal("10")
    assert floor.raised_to_regulatory_floor is True
    assert floor.regulatory_value_pct == Decimal("13")
    assert floor.source_citation is not None and "¶71" in floor.source_citation
    assert floor.confirmation_status == "confirmed"
    assert projection.total_requirement_pct == Decimal("13")
    base = projection.scenarios[0]
    for year in base.years:
        assert year.car_pct is not None
        assert year.headroom_pp == year.car_pct - Decimal("13")


def test_a_stricter_board_car_min_stands(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _set_register(db_session, "car_min", "15")
    _run_forecast(db_session)
    floor = _projection(db_session).pillar1_min
    assert floor.value_pct == Decimal("15")
    assert floor.source == "board_register"
    assert floor.raised_to_regulatory_floor is False


def test_no_register_row_falls_back_to_the_governed_floor(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)  # the forecast engine itself needs a register row
    _set_register(db_session, "car_min", None)
    floor = _projection(db_session).pillar1_min
    assert floor.value_pct == Decimal("13")
    assert floor.board_register_pct is None
    assert floor.source == "control_plane"


def test_an_sdi_board_car_min_is_raised_to_its_own_statutory_floor(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db_session.commit()
    _set_register(db_session, "car_min", "8")
    projection = _projection(db_session)
    assert projection.pillar1_min.value_pct == Decimal("10")
    assert projection.pillar1_min.raised_to_regulatory_floor is True
    # No Basel sub-tier floor, and no CET1 / Tier 1 ratio shown, under s.29.
    assert projection.cet1_min is None and projection.tier1_min is None
    assert projection.basel_ratios_applicable is False
    assert projection.pillar1_min.conservation_buffer == "not_applicable"
    assert all(
        year.cet1_pct is None and year.tier1_pct is None for year in projection.scenarios[0].years
    )


def test_an_unconfigured_floor_refuses_the_projection_but_not_the_plan(db_session: Session) -> None:
    """No resolvable CAR minimum: the projection refuses with a stated reason,
    while the plan document stays readable (fix round 1; QA P0-QA-004)."""
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    capital_plan.put_capital_plan(
        db_session, MAKER, SAMPLE_BANK_ID, CapitalPlanPut(content=_content(), reason="Draft")
    )
    _set_register(db_session, "car_min", None)
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code == "car_min")
    ).all():
        db_session.delete(row)
    db_session.commit()
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.current is not None
    assert summary.projection is None
    unavailable = summary.projection_unavailable
    assert unavailable is not None
    assert unavailable.error_code == "missing_parameter"
    assert unavailable.param_code == "car_min"
    assert "car_min" not in unavailable.reason  # written for the user, no codes


def _unresolved_type(db: Session, bank: Bank) -> NoReturn:
    raise institution_types.InstitutionTypeUnresolved(
        policy_unresolved("institution_type", reason="licence type not in the registry")
    )


def _unresolved_jurisdiction(db: Session, bank: Bank) -> NoReturn:
    raise PolicyUnresolvedError(
        policy_unresolved("jurisdiction", reason="jurisdiction not in the registry")
    )


def test_an_unresolved_licence_type_names_its_own_cause(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The registry's foreign keys make an unknown licence type unreachable in
    # the fixture, so the resolver's own refusal is injected at its seam.
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    monkeypatch.setattr(institution_types, "get_type", _unresolved_type)
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is None
    assert summary.projection_unavailable is not None
    assert summary.projection_unavailable.error_code == "institution_type_unresolved"
    assert "licence type" in summary.projection_unavailable.reason


def test_an_unresolved_jurisdiction_names_its_own_cause(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    monkeypatch.setattr(jurisdictions, "require_jurisdiction", _unresolved_jurisdiction)
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is None
    assert summary.projection_unavailable is not None
    assert summary.projection_unavailable.error_code == "jurisdiction_unresolved"
    assert "jurisdiction" in summary.projection_unavailable.reason


def test_the_minima_state_whether_they_include_the_conservation_buffer(
    db_session: Session,
) -> None:
    """M20 stays open, so the numbers are unchanged — but the API says what each
    minimum is measured against: CAR includes the buffer, CET1 / Tier 1 do not."""
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    projection = _projection(db_session)
    assert projection.basel_ratios_applicable is True
    assert projection.pillar1_min.conservation_buffer == "included"
    assert projection.cet1_min is not None and projection.tier1_min is not None
    assert projection.cet1_min.conservation_buffer == "excluded"
    assert projection.tier1_min.conservation_buffer == "excluded"
    assert projection.cet1_min.value_pct == Decimal("6.5")
    assert projection.tier1_min.value_pct == Decimal("8")


def test_the_floor_resolves_at_the_projection_as_of_not_today(db_session: Session) -> None:
    """A governed generation that starts AFTER the as-of cannot rewrite the
    projection (regulatory audit M24: it used to resolve at today's date)."""
    materialize_canonical_test_book(db_session)
    seeded = db_session.scalar(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "car_min",
            RegulatoryParameter.scope_key == "bank",
        )
    )
    assert seeded is not None
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code=seeded.jurisdiction_code,
            value_numeric=Decimal("15"),
            value_json=None,
            unit="percent",
            source_citation="Later generation (test)",
            confirmation_status="confirmed",
            effective_from=REPORTING_DATE + timedelta(days=1),
            effective_to=None,
            status="approved",
            proposed_by="test",
            approved_by="test-checker",
        )
    )
    db_session.commit()
    _run_forecast(db_session)
    projection = _projection(db_session)
    assert projection.as_of_date == REPORTING_DATE
    assert projection.pillar1_min.value_pct == Decimal("13")
    assert projection.pillar1_min.effective_from == seeded.effective_from


def test_cet1_and_tier1_are_projected_against_their_governed_minima(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    projection = _projection(db_session)
    assert projection.cet1_min is not None and projection.cet1_min.value_pct == Decimal("6.5")
    assert projection.tier1_min is not None and projection.tier1_min.value_pct == Decimal("8")
    run = db_session.get(RegulatoryRun, projection.scenarios[0].run_id)
    assert run is not None
    path = {int(entry["year"]): entry for entry in run.metrics["path"]}
    for year in projection.scenarios[0].years:
        assert year.cet1_pct == Decimal(path[year.year]["cet1_ratio_pct"])
        assert year.tier1_pct == Decimal(path[year.year]["tier1_ratio_pct"])
        assert year.cet1_headroom_pp == year.cet1_pct - Decimal("6.5")  # type: ignore[operator]
        assert year.tier1_headroom_pp == year.tier1_pct - Decimal("8")  # type: ignore[operator]


def test_labels_follow_the_as_of_anniversary(db_session: Session) -> None:
    """A non-year-end anchor says which date each year runs to; it is never
    labelled as a financial year it is not."""
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    projection = _projection(db_session)
    assert projection.year_end_aligned is False
    years = projection.scenarios[0].years
    assert years[0].period_label == "(as of 2026-03-31)"
    assert years[0].period_end == REPORTING_DATE
    assert years[1].period_label == "Year 1 (to 2027-03-31)"
    assert years[5].period_end == date(2031, 3, 31)


def test_a_year_end_projection_is_labelled_by_financial_year(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _run_forecast_for(db_session, date(2025, 12, 31))
    projection = _projection(db_session)
    assert projection.year_end_aligned is True
    assert projection.as_of_date == date(2025, 12, 31)
    labels = [year.period_label for year in projection.scenarios[0].years]
    assert labels[0] == "FY2025 (as of 2025-12-31)"
    assert labels[1:] == [
        "FY2026 (2026-12-31)",
        "FY2027 (2027-12-31)",
        "FY2028 (2028-12-31)",
        "FY2029 (2029-12-31)",
        "FY2030 (2030-12-31)",
    ]


def test_a_non_regulatory_horizon_run_never_becomes_the_projection(db_session: Session) -> None:
    """Only 5-year forecast runs project the plan (the ICAAP-STRESS rule): a
    later 3-year desk run does not displace them."""
    materialize_canonical_test_book(db_session)
    _run_forecast(db_session)
    first = _projection(db_session).scenarios[0].run_id
    _run_forecast_for(db_session, REPORTING_DATE, horizon_years=3)
    projection = _projection(db_session)
    assert projection.scenarios[0].run_id == first
    assert len(projection.scenarios[0].years) == 6
