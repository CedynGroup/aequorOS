"""Forecast year-0 vs the authoritative capital engine — the High finding.

The 2026-08-21 forensic audit's highest-severity row:

    Forecast year-0 CAR | ``RegulatoryRun(capital)`` | ``RegulatoryRun(forecast).path[0]``
    | No: forecast fact scope excludes ECL exposure; capital may apply modeled ECL
    | Yes, confirmed by test comment | Dangerous semantic duplication | **High**

Root cause, established by reading both loaders rather than the summary:
``regulatory_forecasting._FORECAST_FACT_GROUPS`` was a strict subset of
``regulatory_capital._CAPITAL_FACT_GROUPS``. It omitted BOTH capital fact
groups that carry no balance-sheet amount of their own —

* ``ecl_exposure`` — staged IFRS 9 EADs. The capital run turns these into the
  modeled general ECL that REPLACES ingested general provisions in Tier 2, so
  omitting them moves the CAR numerator.
* ``crm_collateral`` — collateral recognized post-haircut against credit
  exposures, which nets down credit RWA, so omitting them moves the CAR
  denominator.

The audit named only the first. Both are fixed the same way, and the fix is
not a new methodology: the forecast now hands the SAME authoritative engine
the SAME input set the capital run hands it. Year 0 is the as-of book, so the
two must agree exactly.

These tests are hermetic on purpose. The real-data suite
(``test_forecast_run_parity.py``) proves it end to end on the primary, but it
skips without ``REAL_DATA_DATABASE_URL`` — which is precisely when a
regression would slip through. This file runs everywhere.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.capital.ecl import EclAssumption
from app.domain.capital.engine import CapitalFact, compute_capital_ratios, compute_rwa
from app.domain.forecasting.engine import (
    ForecastFact,
    ForecastParams,
    _parse_facts,
    _state_facts,
    _to_capital_facts,
    _to_liquidity_facts,
    project,
)
from app.domain.liquidity.engine import compute_lcr, compute_nsfr
from app.services.regulatory_capital import (
    _CAPITAL_FACT_GROUPS,
    _ActiveCapitalParams,
    _modeled_ecl,
)
from app.services.regulatory_forecasting import _FORECAST_FACT_GROUPS
from app.services.regulatory_liquidity import _LIQUIDITY_FACT_GROUPS
from tests.domain.test_forecasting_engine import (
    BASE_ASSUMPTIONS,
    SEVERELY_ADVERSE_ASSUMPTIONS,
    bog_forecast_params,
    sample_bank_latest_facts,
)
from tests.equivalence.conftest import EXACT

M = Decimal("1000000")

#: Staged EADs for the fixture's loan families. Amounts are the loan rows split
#: across stages; ``past_due_90`` is credit-impaired, hence stage 3.
ECL_EXPOSURE_FACTS = (
    ForecastFact("ecl_exposure", "corporate_unrated:stage1", Decimal("520") * M),
    ForecastFact("ecl_exposure", "corporate_unrated:stage2", Decimal("40") * M),
    ForecastFact("ecl_exposure", "sme_retail:stage1", Decimal("265") * M),
    ForecastFact("ecl_exposure", "sme_retail:stage2", Decimal("15") * M),
    ForecastFact("ecl_exposure", "past_due_90:stage3", Decimal("50") * M),
)
#: ``"<loan family>:<collateral class>"``. CASH carries a 0% supervisory
#: haircut, so its recognition is unambiguous and the arithmetic stays legible.
CRM_COLLATERAL_FACTS = (
    ForecastFact("crm_collateral", "corporate_unrated:CASH", Decimal("120") * M),
    ForecastFact("crm_collateral", "sme_retail:CASH", Decimal("30") * M),
)
ECL_ASSUMPTIONS = (
    EclAssumption(segment="ALL", stage=1, pd_pct=Decimal("1.2"), lgd_pct=Decimal("45")),
    EclAssumption(segment="ALL", stage=2, pd_pct=Decimal("8"), lgd_pct=Decimal("45")),
    EclAssumption(segment="ALL", stage=3, pd_pct=Decimal("100"), lgd_pct=Decimal("60")),
)
CRM_HAIRCUTS = {"CASH": Decimal("0")}


def _facts(*, ecl: bool, crm: bool) -> tuple[ForecastFact, ...]:
    rows = list(sample_bank_latest_facts())
    if ecl:
        rows.extend(ECL_EXPOSURE_FACTS)
    if crm:
        rows.extend(CRM_COLLATERAL_FACTS)
    return tuple(rows)


def _forecast_params(*, ecl: bool, crm: bool) -> ForecastParams:
    params = bog_forecast_params()
    return replace(
        params,
        capital=replace(params.capital, crm_haircuts=CRM_HAIRCUTS if crm else {}),
        ecl_assumptions=ECL_ASSUMPTIONS if ecl else (),
    )


def _capital_facts(
    facts: tuple[ForecastFact, ...], groups: set[str]
) -> tuple[CapitalFact, ...]:
    return tuple(
        CapitalFact(
            fact_group=row.fact_group,
            category=row.category,
            amount=row.amount,
            risk_weight_code=row.risk_weight_code,
            ccf_pct=row.ccf_pct,
            income_year=row.income_year,
            capital_tier=row.capital_tier,
            is_deduction=row.is_deduction,
            side=row.side,
        )
        for row in facts
        if row.fact_group in groups
    )


def _capital_run_ratios(facts: tuple[ForecastFact, ...], params: ForecastParams):
    """Reproduce ``regulatory_capital`` for the baseline scenario, hermetically.

    This deliberately calls the capital SERVICE's own ``_modeled_ecl`` rather
    than re-deriving the segment/stage split: if the forecast engine's parse
    ever drifts from the capital run's, this test is what convicts it.
    """
    capital_facts = _capital_facts(facts, set(_CAPITAL_FACT_GROUPS))
    active = _ActiveCapitalParams(
        risk_weights=dict(params.capital.risk_weights),
        thresholds={},
        crm_haircuts=dict(params.capital.crm_haircuts),
        ecl_assumptions=params.ecl_assumptions,
    )
    ecl = _modeled_ecl(capital_facts, active, {})
    rwa = compute_rwa(capital_facts, params.capital)
    ratios = compute_capital_ratios(
        capital_facts, rwa, params.capital, ecl.general_ecl if ecl is not None else None
    )
    return rwa, ratios


# ---------------------------------------------------------------------------
# Structural guard: the input SCOPES themselves
# ---------------------------------------------------------------------------


def test_forecast_fact_scope_covers_every_capital_and_liquidity_input() -> None:
    """The narrowing that caused the divergence cannot silently come back.

    A projection is only allowed to claim its year 0 equals the standalone runs
    if it reads at least everything those runs read. This is the cheapest place
    to catch someone trimming a fact group for hash stability.
    """
    forecast = set(_FORECAST_FACT_GROUPS)
    missing_capital = set(_CAPITAL_FACT_GROUPS) - forecast
    missing_liquidity = set(_LIQUIDITY_FACT_GROUPS) - forecast
    assert not missing_capital, (
        "the forecast snapshot omits capital inputs "
        f"{sorted(missing_capital)}; year-0 CAR can no longer equal the capital run's"
    )
    assert not missing_liquidity, (
        "the forecast snapshot omits liquidity inputs "
        f"{sorted(missing_liquidity)}; year-0 LCR/NSFR can no longer equal the liquidity run's"
    )


def test_any_capital_group_the_forecast_drops_is_provably_inert() -> None:
    """Loading a fact group is useless if the engine hand-off drops it again.

    ``_to_capital_facts`` narrows the projected state to what it believes the
    capital engine reads, so it is allowed to be smaller than
    ``_CAPITAL_FACT_GROUPS`` — but only for groups the engine genuinely ignores
    (today: ``securities``, the HQLA mirror rows, which are a liquidity
    construct). Rather than trust that list, this proves it: run the capital
    engine over the capital run's full scope and over the forecast's delivered
    scope and require identical results. A group that starts mattering to the
    capital engine and is still dropped here fails immediately.
    """
    facts = _facts(ecl=True, crm=True)
    params = _forecast_params(ecl=True, crm=True)
    delivered = {row.fact_group for row in _to_capital_facts(facts)}

    full = _capital_facts(facts, set(_CAPITAL_FACT_GROUPS))
    trimmed = _capital_facts(facts, delivered)
    full_rwa = compute_rwa(full, params.capital)
    trimmed_rwa = compute_rwa(trimmed, params.capital)
    assert full_rwa == trimmed_rwa, (
        "_to_capital_facts dropped a capital fact group that the RWA engine reads: "
        f"{sorted(set(_CAPITAL_FACT_GROUPS) - delivered)}"
    )
    assert compute_capital_ratios(full, full_rwa, params.capital) == compute_capital_ratios(
        trimmed, trimmed_rwa, params.capital
    )


def test_projected_fact_set_reaches_the_liquidity_engine_with_the_liquidity_scope() -> None:
    facts = _facts(ecl=True, crm=True)
    delivered = {row.fact_group for row in _to_liquidity_facts(facts)}
    assert delivered == {row.fact_group for row in facts} & set(_LIQUIDITY_FACT_GROUPS)


# ---------------------------------------------------------------------------
# The equivalence itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ecl", "crm"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["neither", "ecl_only", "crm_only", "both"],
)
def test_year_zero_capital_ratios_equal_the_capital_run(ecl: bool, crm: bool) -> None:
    """Year 0 is the as-of book, so its capital ratios ARE the capital run's.

    Parameterized across the four register configurations because the bug only
    appeared once a tenant configured one of them: with neither register the
    two paths already agreed, which is exactly why the divergence stayed
    latent and undetected on the live book.
    """
    facts = _facts(ecl=ecl, crm=crm)
    params = _forecast_params(ecl=ecl, crm=crm)
    _, expected = _capital_run_ratios(facts, params)
    year0 = project(facts, params, BASE_ASSUMPTIONS).years[0]

    assert abs(year0.car_pct - expected.car_pct) <= EXACT, (
        f"forecast year-0 CAR {year0.car_pct} != capital run CAR {expected.car_pct}"
    )
    assert abs(year0.tier1_ratio_pct - expected.tier1_ratio_pct) <= EXACT
    assert abs(year0.cet1_ratio_pct - expected.cet1_ratio_pct) <= EXACT


@pytest.mark.parametrize(
    "assumptions", [BASE_ASSUMPTIONS, SEVERELY_ADVERSE_ASSUMPTIONS], ids=["base", "severe"]
)
def test_year_zero_is_scenario_independent(assumptions) -> None:  # noqa: ANN001
    """No assumption set may move year 0 — it is the observed book, not a
    projection. A scenario that changes it is projecting into the past."""
    facts = _facts(ecl=True, crm=True)
    params = _forecast_params(ecl=True, crm=True)
    _, expected = _capital_run_ratios(facts, params)
    assert project(facts, params, assumptions).years[0].car_pct == expected.car_pct


def test_year_zero_liquidity_ratios_equal_the_liquidity_engine() -> None:
    """The audit rated forecast-vs-liquidity year 0 SAFE under tested inputs.

    "Safe under tested inputs" is not a control. This pins it, including on the
    widened fact set, so a future liquidity or forecast change cannot quietly
    make the two disagree.
    """
    facts = _facts(ecl=True, crm=True)
    params = _forecast_params(ecl=True, crm=True)
    liquidity_facts = _to_liquidity_facts(facts)
    year0 = project(facts, params, BASE_ASSUMPTIONS).years[0]
    assert abs(year0.lcr_pct - compute_lcr(liquidity_facts, params.liquidity).lcr_pct) <= EXACT
    assert abs(year0.nsfr_pct - compute_nsfr(liquidity_facts, params.liquidity).nsfr_pct) <= EXACT


def test_ecl_and_crm_facts_do_not_disturb_the_balance_sheet() -> None:
    """Neither group is an asset. They qualify exposures the loan rows already
    carry, so adding them must not move a single balance-sheet figure — only
    the capital ratios computed over them."""
    plain = project(_facts(ecl=False, crm=False), bog_forecast_params(), BASE_ASSUMPTIONS)
    widened = project(
        _facts(ecl=True, crm=True), _forecast_params(ecl=True, crm=True), BASE_ASSUMPTIONS
    )
    for left, right in zip(plain.years, widened.years, strict=True):
        for field in (
            "total_assets",
            "loans",
            "securities",
            "cash",
            "deposits",
            "borrowings_plug",
            "equity",
            "net_income",
        ):
            assert getattr(left, field) == getattr(right, field), (
                f"year {left.year}: {field} moved when ECL/CRM inputs were added"
            )


# ---------------------------------------------------------------------------
# Proof the wiring is live (a parity test passes trivially if nothing is wired)
# ---------------------------------------------------------------------------


def test_the_registers_actually_move_the_projected_ratio() -> None:
    """Guard against a vacuous suite.

    If ``ecl_assumptions``/``crm_haircuts`` were dropped on the floor, every
    equivalence assertion above would still pass — both sides would be the
    unconfigured number. These inequalities prove the inputs reach the engine,
    and they state the direction each one pushes CAR.
    """
    baseline = project(
        _facts(ecl=False, crm=False), bog_forecast_params(), BASE_ASSUMPTIONS
    ).years[0]

    # Modeled ECL REPLACES ingested general provisions in Tier 2. The fixture's
    # ingested general provisions are 15m and the modeled stage-1/2 ECL is far
    # smaller, so recognizing the model shrinks Tier 2 and CAR falls.
    ecl_only = project(
        _facts(ecl=True, crm=False), _forecast_params(ecl=True, crm=False), BASE_ASSUMPTIONS
    ).years[0]
    assert ecl_only.car_pct < baseline.car_pct

    # Recognized collateral nets down the credit exposure, so credit RWA falls
    # and CAR rises.
    crm_only = project(
        _facts(ecl=False, crm=True), _forecast_params(ecl=False, crm=True), BASE_ASSUMPTIONS
    ).years[0]
    assert crm_only.car_pct > baseline.car_pct


def test_the_registers_move_every_projected_year_not_just_year_zero() -> None:
    """The parity fix is not a year-0 patch: ECL EADs and collateral track the
    loan book, so the capital effect persists across the horizon."""
    plain = project(_facts(ecl=False, crm=False), bog_forecast_params(), BASE_ASSUMPTIONS)
    widened = project(
        _facts(ecl=True, crm=True), _forecast_params(ecl=True, crm=True), BASE_ASSUMPTIONS
    )
    assert all(
        left.car_pct != right.car_pct
        for left, right in zip(plain.years, widened.years, strict=True)
    )


# ---------------------------------------------------------------------------
# The operational-income base (the divergence the audits did not name)
# ---------------------------------------------------------------------------

#: A REAL book's ``operational_income`` group is not just ``gross_income_*``.
#: The live one carries five series across three years — gross income, net
#: income, net interest income, operating expenses and provisions — all with an
#: ``income_year``. Only ``gross_income_*`` is the Basel II ¶649 base;
#: ``compute_rwa`` selects it by name out of whatever the projection emits, so
#: this mixed book is what proves the projection neither drops the base rows
#: nor lets the other four series contaminate it.
MIXED_INCOME_FACTS = (
    ForecastFact("operational_income", "net_income_2023", Decimal("90") * M, income_year=2023),
    ForecastFact("operational_income", "net_income_2024", Decimal("104") * M, income_year=2024),
    ForecastFact("operational_income", "net_income_2025", Decimal("118") * M, income_year=2025),
    ForecastFact("operational_income", "provisions_2023", Decimal("22") * M, income_year=2023),
    ForecastFact("operational_income", "provisions_2024", Decimal("26") * M, income_year=2024),
    ForecastFact("operational_income", "provisions_2025", Decimal("31") * M, income_year=2025),
)


def _mixed_income_facts() -> tuple[ForecastFact, ...]:
    return (*_facts(ecl=True, crm=True), *MIXED_INCOME_FACTS)


def test_year_zero_operational_rwa_uses_the_as_of_income_rows_verbatim() -> None:
    """Year 0 must hand back the operational-income evidence it was given.

    The projection previously emitted only the last three ``gi_history``
    entries, renamed ``gross_income_<year>``. On a book carrying more than one
    income series that silently discarded most of the BIA base and renamed the
    rest, so the projection's operational RWA — and therefore its year-0 CAR —
    was computed over a different book than the capital run's. This is the
    regression guard for that, and it is the reason the hermetic fixture alone
    was not enough to catch the High finding.

    Still exactly the right invariant after the 2026-08-21 ¶649 fix: the
    capital engine now selects the ``gross_income_*`` series and the three most
    recent years itself, but it can only select from what year 0 hands it, so
    year 0 renaming or dropping a row would still move the BIA base.
    """
    facts = _mixed_income_facts()
    params = _forecast_params(ecl=True, crm=True)
    _, expected = _capital_run_ratios(facts, params)
    year0 = project(facts, params, BASE_ASSUMPTIONS).years[0]
    assert year0.car_pct == expected.car_pct

    state, meta = _parse_facts(facts)
    emitted = {
        (row.category, row.income_year, row.amount)
        for row in _state_facts(state, meta)
        if row.fact_group == "operational_income"
    }
    as_of = {
        (row.category, row.income_year, row.amount)
        for row in facts
        if row.fact_group == "operational_income"
    }
    assert emitted == as_of, "year 0 renamed, dropped or invented an operational-income row"


def test_the_projection_keeps_the_income_window_the_width_it_started_with() -> None:
    """Each projected year appends one income row and retires the oldest.

    An evidence base that changes width mid-projection changes what the BIA is
    selected FROM for reasons that have nothing to do with the bank, which is
    how the year-0 defect hid: the base looked plausible at every horizon.
    Since 2026-08-21 the ¶649 denominator itself is a count of gross-income
    YEARS chosen inside ``compute_rwa``, never this row count — see
    ``test_every_projected_year_takes_the_bia_over_three_gross_income_years``
    for the guard on the window that actually feeds the charge.
    """
    facts = _mixed_income_facts()
    width = len([row for row in facts if row.fact_group == "operational_income"])
    state, meta = _parse_facts(facts)
    assert meta.gi_window == width
    for _ in range(5):
        state.gi_history.append((state.gi_history[-1][0] + 1, "gross_income_x", Decimal("1")))
        rows = [
            row for row in _state_facts(state, meta) if row.fact_group == "operational_income"
        ]
        assert len(rows) == width


def test_every_projected_year_takes_the_bia_over_three_gross_income_years() -> None:
    """The ¶649 window must advance with the projection, not decay inside it.

    The capital engine owns the selection, but it can only select from what the
    projection emits. Retirement is oldest-first and each year appends exactly
    one gross-income year, so the count of gross-income years the projection
    carries never falls below the as-of book's — this is the executable form of
    that argument, on the mixed five-series book where the two counts differ.
    """
    facts = _mixed_income_facts()
    params = _forecast_params(ecl=True, crm=True)
    state, meta = _parse_facts(facts)
    as_of_years = {row.income_year for row in facts if row.category.startswith("gross_income")}
    assert len(as_of_years) == 3

    for year in range(6):
        if year:
            state.gi_history.append(
                (state.gi_history[-1][0] + 1, f"gross_income_{state.gi_history[-1][0] + 1}", M)
            )
        emitted = _state_facts(state, meta)
        rwa = compute_rwa(_to_capital_facts(emitted), params.capital)
        assert rwa.positive_income_years == 3, f"year {year} lost a gross-income year"
        windowed = sorted(
            item.line_code
            for item in rwa.line_items
            if item.section == "operational_rwa"
            and item.line_code.startswith("gross_income")
            and "Excluded" not in item.description
        )
        assert len(windowed) == 3
        assert windowed == sorted(windowed)[-3:]


def test_projected_years_never_admit_a_non_gross_income_series_to_the_bia() -> None:
    """Adding the four non-BIA series to the book must not move any year's CAR.

    Net interest income, net income, operating expenses and provisions ride the
    same ``operational_income`` group for the implied-rating engine. If any of
    them reached the BIA the whole projected CAR path would shift — which is
    exactly what happened before 2026-08-21.
    """
    params = _forecast_params(ecl=True, crm=True)
    plain = project(_facts(ecl=True, crm=True), params, BASE_ASSUMPTIONS)
    mixed = project(_mixed_income_facts(), params, BASE_ASSUMPTIONS)

    assert [row.car_pct for row in mixed.years] == [row.car_pct for row in plain.years]
