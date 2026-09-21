"""Independent QA of ICAAP P0 items 1 and 6 — the capital-plan projection.

Agent 11 (Test/QA), 2026-09-19. Written against the behaviour the spec and the
regulatory audit (§4.1, §4.6) require, not against the implementation's own
tests. Every case builds its own state; nothing here edits implementation code.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    ParamCapitalThreshold,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.schemas.capital_plan import CapitalPlanContentInput, CapitalPlanPut, Pillar2AddOnInput
from app.schemas.forecasting import ForecastRunCreate
from app.services import capital_plan, regulatory_forecasting
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
    set_board_threshold,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
AS_OF = date(2026, 3, 31)
YEAR_END = date(2025, 12, 31)


# --- helpers -----------------------------------------------------------------


def _period_id(db: Session, period_end: date) -> Any:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    assert period_id is not None, period_end
    return period_id


def _forecast(db: Session, period_end: date = AS_OF, *, horizon_years: int | None = None) -> Any:
    payload: dict[str, Any] = {
        "reporting_period_id": _period_id(db, period_end),
        "scenario_code": "base",
    }
    if horizon_years is not None:
        payload["horizon_years"] = horizon_years
    run = regulatory_forecasting.create_forecast_run(
        db, MAKER, SAMPLE_BANK_ID, ForecastRunCreate(**payload)
    )
    assert run.status == "succeeded", run
    return run.id


def _register_rows(db: Session, code: str) -> list[ParamCapitalThreshold]:
    return list(
        db.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.organization_id == DEMO_ORG_ID,
                ParamCapitalThreshold.threshold_code == code,
            )
        ).all()
    )


def _set_register(db: Session, code: str, value: str | None) -> None:
    # D-042: the fixture register carries no row for a governed minimum (the
    # clamp supplies the control-plane value), so a board value is added here.
    set_board_threshold(db, code, value)
    db.commit()


def _control_rows(db: Session, code: str, scope_key: str) -> list[RegulatoryParameter]:
    return list(
        db.scalars(
            select(RegulatoryParameter).where(
                RegulatoryParameter.param_code == code,
                RegulatoryParameter.scope_type == "institution_class",
                RegulatoryParameter.scope_key == scope_key,
            )
        ).all()
    )


def _projection(db: Session) -> Any:
    summary = capital_plan.get_capital_plan(db, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is not None
    return summary.projection


def _make_sdi(db: Session) -> None:
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db.commit()


# --- P0-1: governed CAR floor ------------------------------------------------


def test_bank_board_car_min_of_10_is_measured_against_the_governed_13(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    # D-042: a register carries no governed minimum of its own, so the weaker
    # board value this case is about is set explicitly (it was the fixture's
    # literal default until 2026-09-19).
    _set_register(db_session, "car_min", "10")
    # Precondition: the bank register really holds the weaker 10.
    assert {row.value_pct for row in _register_rows(db_session, "car_min")} == {Decimal("10")}
    _forecast(db_session)
    projection = _projection(db_session)
    assert projection.pillar1_min_pct == Decimal("13")
    assert projection.pillar1_min.value_pct == Decimal("13")
    assert projection.pillar1_min.board_register_pct == Decimal("10")
    assert projection.pillar1_min.raised_to_regulatory_floor is True
    assert projection.total_requirement_pct == Decimal("13")
    for scenario in projection.scenarios:
        for year in scenario.years:
            if year.car_pct is not None:
                assert year.headroom_pp == year.car_pct - Decimal("13")


def test_pillar2_addons_stack_on_the_governed_floor_not_the_board_value(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    _forecast(db_session)
    capital_plan.put_capital_plan(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        CapitalPlanPut(
            content=CapitalPlanContentInput(
                pillar2_addons=[
                    Pillar2AddOnInput(
                        risk_type="concentration",
                        add_on_pct_rwa=Decimal("1.5"),
                        rationale="Board concentration add-on (test).",
                    )
                ]
            ),
            reason="QA draft",
        ),
    )
    projection = _projection(db_session)
    assert projection.pillar2_addon_pct == Decimal("1.5")
    assert projection.total_requirement_pct == Decimal("14.5")


@pytest.mark.parametrize(
    ("register", "expected", "raised"),
    [(None, Decimal("10"), False), ("8", Decimal("10"), True), ("12", Decimal("12"), False)],
)
def test_sdi_floor_is_its_own_statutory_10_percent(
    db_session: Session, register: str | None, expected: Decimal, raised: bool
) -> None:
    materialize_canonical_test_book(db_session)
    _forecast(db_session)
    _make_sdi(db_session)
    if register is None:
        _set_register(db_session, "car_min", None)
    else:
        _set_register(db_session, "car_min", register)
    projection = _projection(db_session)
    assert projection.pillar1_min.value_pct == expected
    assert projection.pillar1_min.raised_to_regulatory_floor is raised
    # Never the bank 13 for an SDI.
    assert projection.pillar1_min.regulatory_value_pct == Decimal("10")
    assert projection.cet1_min is None and projection.tier1_min is None


def test_floor_resolves_at_the_projection_as_of_across_a_generation_change(
    db_session: Session,
) -> None:
    """A control-plane generation change between two as-of dates: each
    projection must resolve the generation in force at ITS as-of."""
    materialize_canonical_test_book(db_session)
    (seeded,) = _control_rows(db_session, "car_min", "bank")
    change = date(2026, 1, 1)
    seeded.effective_to = change
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code=seeded.jurisdiction_code,
            value_numeric=Decimal("14"),
            value_json=None,
            unit="percent",
            source_citation="QA later generation",
            confirmation_status="confirmed",
            effective_from=change,
            effective_to=None,
            status="approved",
            proposed_by="qa",
            approved_by="qa-checker",
        )
    )
    db_session.commit()

    _forecast(db_session, YEAR_END)
    earlier = _projection(db_session)
    assert earlier.as_of_date == YEAR_END
    assert earlier.pillar1_min.value_pct == Decimal("13")
    assert earlier.pillar1_min.effective_from == seeded.effective_from

    _forecast(db_session, AS_OF)
    later = _projection(db_session)
    assert later.as_of_date == AS_OF
    assert later.pillar1_min.value_pct == Decimal("14")
    assert later.pillar1_min.effective_from == change
    assert later.pillar1_min.source_citation == "QA later generation"


def test_a_board_register_generation_after_the_as_of_is_not_applied(
    db_session: Session,
) -> None:
    """The register is read at the as-of too: a stricter board value approved
    to start after the as-of (but before today) must not move the projection."""
    materialize_canonical_test_book(db_session)
    _set_register(db_session, "car_min", "10")  # D-042: the weaker board value, explicit
    _forecast(db_session)
    (template,) = _register_rows(db_session, "car_min")
    db_session.add(
        ParamCapitalThreshold(
            organization_id=template.organization_id,
            jurisdiction_code=template.jurisdiction_code,
            threshold_code="car_min",
            value_pct=Decimal("16"),
            effective_from=date(2026, 6, 1),
            effective_to=None,
            approved_by="qa-board",
            approval_timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    db_session.commit()
    assert datetime.now(UTC).date() > date(2026, 6, 1)  # "today" would pick it up
    projection = _projection(db_session)
    assert projection.pillar1_min.value_pct == Decimal("13")
    assert projection.pillar1_min.board_register_pct == Decimal("10")


def test_missing_floor_refuses_with_409_missing_parameter(db_session: Session) -> None:
    """The projection still refuses with ``missing_parameter`` / ``car_min``; since
    fix round 1 (coordinator item 2, closing P0-QA-004) the refusal is reported
    on the summary as ``projection_unavailable`` instead of a 409 that hid the
    whole plan. Same strength: no projection is produced, and the code and
    parameter are named."""
    materialize_canonical_test_book(db_session)
    _forecast(db_session)
    _set_register(db_session, "car_min", None)
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code == "car_min")
    ).all():
        db_session.delete(row)
    db_session.commit()
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is None
    unavailable = summary.projection_unavailable
    assert unavailable is not None
    assert unavailable.error_code == "missing_parameter"
    assert unavailable.param_code == "car_min"


def test_missing_floor_does_not_hide_the_plan_document(db_session: Session) -> None:
    """The plan stays READABLE, and the projection refuses by name.

    ``assert summary.current is not None`` was the only assertion here until
    2026-09-20, which a summary carrying an empty husk of a plan would satisfy
    just as well — and it said nothing about the half that is supposed to
    refuse. Both halves are asserted now: the document a user typed comes back
    intact, and the figure that needs the missing floor is absent and named.
    """
    materialize_canonical_test_book(db_session)
    _forecast(db_session)
    written = capital_plan.put_capital_plan(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        CapitalPlanPut(content=CapitalPlanContentInput(), reason="QA draft"),
    )
    _set_register(db_session, "car_min", None)
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code == "car_min")
    ).all():
        db_session.delete(row)
    db_session.commit()
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    current = summary.current
    assert current is not None
    # The SAME document, not merely some row: identity and content both.
    assert current.id == written.id
    assert current.version == written.version
    assert current.status == written.status
    assert current.content == written.content
    # And the half that genuinely cannot be computed says so, by name, rather
    # than being quietly omitted or substituted.
    assert summary.projection is None
    unavailable = summary.projection_unavailable
    assert unavailable is not None
    assert unavailable.error_code == "missing_parameter"
    assert unavailable.param_code == "car_min"


# --- P0-6: CET1 / Tier 1, FY labels, 5-year horizon --------------------------


def test_cet1_and_tier1_are_absent_not_zero_when_the_forecast_lacks_them(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    run_id = _forecast(db_session)
    run = db_session.get(RegulatoryRun, run_id)
    assert run is not None
    metrics = dict(run.metrics)
    path = [dict(entry) for entry in metrics["path"]]
    assert all("cet1_ratio_pct" in entry and "tier1_ratio_pct" in entry for entry in path)
    path[2].pop("cet1_ratio_pct")
    path[2].pop("tier1_ratio_pct")
    path[3]["cet1_ratio_pct"] = "0"
    path[3]["tier1_ratio_pct"] = "0"
    metrics["path"] = path
    run.metrics = metrics
    db_session.commit()

    years = {year.year: year for year in _projection(db_session).scenarios[0].years}
    missing = years[int(path[2]["year"])]
    assert missing.cet1_pct is None and missing.tier1_pct is None
    assert missing.cet1_headroom_pp is None and missing.tier1_headroom_pp is None
    assert missing.car_pct is not None  # CAR still projected
    zero = years[int(path[3]["year"])]
    assert zero.cet1_pct == Decimal("0") and zero.tier1_pct == Decimal("0")
    assert zero.cet1_headroom_pp == Decimal("-6.5")
    assert zero.tier1_headroom_pp == Decimal("-8")


def test_cet1_and_tier1_minima_are_governed_without_the_conservation_buffer(
    db_session: Session,
) -> None:
    """D-008 / M20 left open: CET1 6.5 and Tier 1 8, not 9.5 / 11."""
    materialize_canonical_test_book(db_session)
    _set_register(db_session, "cet1_min", "5")
    _set_register(db_session, "tier1_min", "7")
    _forecast(db_session)
    projection = _projection(db_session)
    assert projection.cet1_min is not None and projection.tier1_min is not None
    assert projection.cet1_min.value_pct == Decimal("6.5")
    assert projection.cet1_min.raised_to_regulatory_floor is True
    assert projection.tier1_min.value_pct == Decimal("8")
    assert projection.tier1_min.raised_to_regulatory_floor is True


def test_year_end_as_of_labels_every_year_as_a_financial_year(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _forecast(db_session, YEAR_END)
    projection = _projection(db_session)
    assert projection.year_end_aligned is True
    years = projection.scenarios[0].years
    assert [year.year for year in years] == [0, 1, 2, 3, 4, 5]
    for year in years:
        assert year.period_end == date(2025 + year.year, 12, 31)
        assert year.period_label.startswith(f"FY{2025 + year.year} ")
        assert year.period_end.isoformat() in year.period_label


def test_non_year_end_as_of_is_never_labelled_as_a_financial_year(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _forecast(db_session, AS_OF)
    projection = _projection(db_session)
    assert projection.year_end_aligned is False
    for year in projection.scenarios[0].years:
        assert "FY" not in year.period_label
        assert "31 Dec" not in year.period_label and "12-31" not in year.period_label
        assert year.period_end == date(2026 + year.year, 3, 31)


def test_anniversary_of_a_leap_day_lands_on_28_february() -> None:
    assert capital_plan._anniversary(date(2028, 2, 29), 1) == date(2029, 2, 28)
    assert capital_plan._anniversary(date(2028, 2, 29), 4) == date(2032, 2, 29)


def test_a_later_period_with_only_a_short_horizon_run_does_not_displace_the_plan(
    db_session: Session,
) -> None:
    """The latest-period lookup itself must ignore non-5-year runs, so a newer
    period carrying only a 3-year desk run leaves the projection on the older
    period's 5-year run (never an empty or mixed projection)."""
    materialize_canonical_test_book(db_session)
    five_year = _forecast(db_session, YEAR_END)
    _forecast(db_session, AS_OF, horizon_years=3)
    projection = _projection(db_session)
    assert projection.as_of_date == YEAR_END
    assert [scenario.run_id for scenario in projection.scenarios] == [five_year]
    assert len(projection.scenarios[0].years) == 6


def test_an_explicit_five_year_horizon_is_still_a_planning_run(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    run_id = _forecast(db_session, AS_OF, horizon_years=5)
    projection = _projection(db_session)
    assert [scenario.run_id for scenario in projection.scenarios] == [run_id]


def test_the_horizon_predicate_compiles_for_postgres() -> None:
    compiled = str(
        select(RegulatoryRun.id)
        .where(capital_plan._planning_horizon())
        .compile(dialect=postgresql.dialect())
    )
    # JSON (not JSONB) column: ``->>`` text extraction cast to an integer.
    assert "inputs ->>" in compiled
    assert "AS INTEGER" in compiled
    assert "coalesce" in compiled.lower()


def test_approval_refusal_names_the_horizon_rule_when_only_a_short_run_exists(
    db_session: Session,
) -> None:
    from app.schemas.capital_plan import CapitalPlanApprove  # noqa: PLC0415
    from tests.services.test_capital_plan import CHECKER, _content, _ensure_checker  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    _ensure_checker(db_session)
    capital_plan.put_capital_plan(
        db_session, MAKER, SAMPLE_BANK_ID, CapitalPlanPut(content=_content(), reason="Draft")
    )
    _forecast(db_session, AS_OF, horizon_years=3)
    with pytest.raises(HTTPException) as excinfo:
        capital_plan.approve_capital_plan(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CapitalPlanApprove(approval_reference="BM-QA-1", reason="Annual approval"),
        )
    detail: Any = excinfo.value.detail
    assert detail["error_code"] == "no_forecast_run"
    assert "No successful forecast run exists" not in detail["message"]
