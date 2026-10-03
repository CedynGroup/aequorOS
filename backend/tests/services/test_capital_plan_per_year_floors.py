"""Per-year capital floors in the plan's projection (deviation DV-005).

A capital plan projects five years. Resolving the minimum once, at today's
date, and measuring every projected year against it reports headroom that will
not exist if the regime changes during the horizon — and BoG has moved the
capital buffer in both directions. Each projected year end therefore resolves
the floor in force AT THAT DATE.

With today's open-ended parameter generations nothing moves, which is the
second thing under test: this change must not shift a single existing figure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryParameter
from app.schemas.capital_plan import CapitalPlanPut
from app.schemas.forecasting import ForecastRunCreate
from app.services import capital_plan, regulatory_forecasting
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.services.test_capital_plan import _content

MAKER = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)

pytestmark = pytest.mark.usefixtures("forecasting_run_authority")


def _period_id(db: Session):
    period = db.scalar(
        select(BankReportingPeriod)
        .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    assert period is not None
    return period.id


def _prepare(db: Session) -> None:
    materialize_canonical_test_book(db)
    capital_plan.put_capital_plan(
        db, MAKER, SAMPLE_BANK_ID, CapitalPlanPut(content=_content(), reason="Draft")
    )
    run = regulatory_forecasting.create_forecast_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        ForecastRunCreate(reporting_period_id=_period_id(db), scenario_code="base"),
    )
    assert run.status == "succeeded"


def test_each_projected_year_states_the_minima_it_was_measured_against(
    db_session: Session,
) -> None:
    _prepare(db_session)
    projection = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None
    base = next(entry for entry in projection.scenarios if entry.scenario_code == "base")
    for year in base.years:
        assert year.pillar1_min_pct is not None
        assert year.total_requirement_pct is not None
        assert year.total_requirement_pct == year.pillar1_min_pct + projection.pillar2_addon_pct
        if year.car_pct is not None:
            assert year.headroom_pp == year.car_pct - year.total_requirement_pct


def test_open_ended_generations_leave_every_year_on_the_same_floor(
    db_session: Session,
) -> None:
    """The seeds are open-ended today, so nothing moves — no golden changes."""
    _prepare(db_session)
    projection = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None
    base = next(entry for entry in projection.scenarios if entry.scenario_code == "base")
    requirements = {year.total_requirement_pct for year in base.years}
    assert requirements == {projection.total_requirement_pct}


def test_a_floor_that_commences_mid_horizon_applies_only_from_that_year(
    db_session: Session,
) -> None:
    _prepare(db_session)
    before = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert before is not None
    base = next(entry for entry in before.scenarios if entry.scenario_code == "base")
    switch_year = next(year for year in base.years if year.year == 3)
    assert switch_year.period_end is not None

    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code=bank.jurisdiction_code,
            value_numeric=Decimal("15"),
            unit="percent",
            source_citation="Test: a higher minimum commences part-way through the plan.",
            confirmation_status="pending",
            effective_from=switch_year.period_end,
            status="approved",
            proposed_by="test-suite",
            approved_by="test-suite",
        )
    )
    db_session.commit()

    after = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert after is not None
    years = {
        year.year: year
        for year in next(entry for entry in after.scenarios if entry.scenario_code == "base").years
    }
    assert years[2].pillar1_min_pct == before.pillar1_min_pct
    assert years[3].pillar1_min_pct == Decimal("15")
    assert years[4].pillar1_min_pct == Decimal("15")
    assert years[3].total_requirement_pct == Decimal("15") + after.pillar2_addon_pct
    year_three = years[3]
    requirement = year_three.total_requirement_pct
    if year_three.car_pct is not None and requirement is not None:
        assert year_three.headroom_pp == year_three.car_pct - requirement


def test_the_floors_can_be_resolved_for_several_dates_in_one_call(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    dates = [date(2025, 12, 31), date(2028, 12, 31)]
    resolved = capital_plan.capital_floors_by_date(
        db_session, MAKER, _bank(db_session), dates, ("car_min",)
    )
    assert set(resolved) == set(dates)
    assert all("car_min" in floors for floors in resolved.values())


def _bank(db: Session) -> Bank:
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank
