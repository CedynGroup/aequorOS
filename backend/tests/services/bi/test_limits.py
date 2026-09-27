"""``app.services.bi.limits``: the limit a BI figure is judged against (D-069).

Every expectation here is worked from the two registers the canonical fixture
bank carries, never from a number this file invented:

* the **regulatory control plane** seeded for every hermetic schema by
  ``tests/fixtures/reference_data.py`` from ``regulatory_parameters.SEED_ROWS``
  — where ``car_min`` for the bank class lives; and
* the bank's own **board register**, seeded by
  ``tests/fixtures/canonical_bank_fixture.py`` from
  ``parameter_register.BANK_CAPITAL_THRESHOLDS`` / ``BANK_FX_THRESHOLDS`` —
  where the net-open-position and internal liquidity limits live.

Both catalogues are imported rather than copied, so a value staff change in
either register moves the expectation with it and cannot leave a stale literal
here asserting yesterday's limit.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from decimal import Decimal
from typing import Any, get_args

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue, MeasureDef, catalogue
from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES
from app.models import Bank, ParamCapitalThreshold
from app.services import parameter_register, regulatory_parameters
from app.services.bi import limits
from app.services.bi.limits import (
    GovernedLimit,
    LimitResolver,
    NoGovernedLimit,
    absence_copy,
    is_governed,
    measure_limit,
)
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
    set_board_threshold,
)

#: Any date the fixture's registers are effective on; both are open-ended from
#: well before it, so the choice of date is not load-bearing for most tests.
AS_OF = date(2026, 6, 30)

#: The regulatory floor for a bank's capital adequacy ratio, taken from the seed
#: catalogue the control plane is built from — not written out here.
CAR_MIN = next(
    spec
    for spec in regulatory_parameters.SEED_PARAMETERS
    if spec.param_code == "car_min" and spec.scope_key == "bank"
)
if CAR_MIN.value is None:  # pragma: no cover - a scalar floor in every generation
    raise AssertionError("car_min is a scalar in the control-plane seed catalogue")
CAR_MIN_VALUE = Decimal(CAR_MIN.value)
#: The board's own aggregate net-open-position limit and internal LCR floor.
NOP_AGGREGATE_LIMIT = Decimal(parameter_register.BANK_FX_THRESHOLDS["fx_nop_aggregate_limit_pct"])
BOARD_LCR_MIN = Decimal(parameter_register.BANK_CAPITAL_THRESHOLDS["lcr_min"])

# The measures under test, by the ids the catalogue publishes.
CAR = "engine.car_pct.crd.official"
NOP_AGGREGATE = "engine.nop_pct_tier1.crd.official"
LCR = "engine.lcr_pct.crd.official"
EAR = "engine.ear_up_200_ghs.crd.official"
RWA = "engine.total_rwa_ghs.crd.official"
CAR_ACTUAL = "engine.car_pct.crd.official.budget.actual"
CAR_VARIANCE_PCT = "engine.car_pct.crd.official.budget.variance_pct"
CAR_ATTAINMENT = "engine.car_pct.crd.official.budget.attainment_pct"


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    """The canonical fixture bank, with both registers as a real tenant has them."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    row = db_session.get(Bank, SAMPLE_BANK_ID)
    assert row is not None
    # Anything the fixture itself resolved is somebody else's provenance; the
    # ledger assertions below are about what THIS module reads.
    regulatory_parameters.consume_parameter_provenance(db_session)
    return row


def resolve(db: Session, bank: Bank, cat: Catalogue, measure_id: str) -> Any:
    return measure_limit(db, bank, cat.measure(measure_id), as_of=AS_OF)


def governed(outcome: Any) -> GovernedLimit:
    """The outcome as a governed limit, failing the test if it is not one."""
    assert is_governed(outcome), outcome
    return outcome


def absent(outcome: Any) -> NoGovernedLimit:
    assert isinstance(outcome, NoGovernedLimit), outcome
    return outcome


# --- the two registers, and their precedence ------------------------------------------------


def test_the_capital_ratio_limit_is_the_regulators_own_row(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    limit = governed(resolve(db_session, bank, cat, CAR))
    assert limit.param_code == "car_min"
    assert limit.value == CAR_MIN_VALUE
    assert limit.authority == "regulatory_register"
    assert limit.stated_as == "pct"
    assert limit.source_unit == "percent"
    assert limit.source == CAR_MIN.source_citation
    assert limit.confirmation_status == CAR_MIN.confirmation_status
    assert limit.is_provisional is (CAR_MIN.confirmation_status == "pending")
    assert limit.clamped is False
    assert limit.effective_from == regulatory_parameters.SEED_EFFECTIVE_FROM
    assert limit.effective_to is None
    assert limit.as_of == AS_OF
    # Stated as the register states it, with no Numeric(18,6) tail.
    assert str(limit.value) == str(CAR_MIN.value)


def test_the_net_open_position_limit_is_the_boards_own_row(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """The ALCO widget that had to say its limits were "not shown" (D-069)."""
    limit = governed(resolve(db_session, bank, cat, NOP_AGGREGATE))
    assert limit.param_code == "fx_nop_aggregate_limit_pct"
    assert limit.value == NOP_AGGREGATE_LIMIT
    assert limit.authority == "board_register"
    assert limit.source_unit == "percent"
    # A board limit has no external citation to make: who approved it, and when.
    assert "board register" in limit.source
    assert limit.confirmation_status is None
    assert limit.is_provisional is False
    assert limit.clamped is False


def test_the_internal_liquidity_floor_is_the_boards_own_row(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """No regulator imposes an LCR here, so the register is the only authority."""
    assert not any(
        spec.param_code == "lcr_min" for spec in regulatory_parameters.SEED_PARAMETERS
    ), "the control plane has gained an lcr_min row; this test's premise moved"
    limit = governed(resolve(db_session, bank, cat, LCR))
    assert limit.value == BOARD_LCR_MIN
    assert limit.authority == "board_register"


def test_a_board_value_weaker_than_the_regulatory_floor_is_clamped(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """Tighten-only, and the board's own figure travels as evidence."""
    floor = CAR_MIN_VALUE
    set_board_threshold(db_session, "car_min", str(floor - Decimal(3)))
    limit = governed(resolve(db_session, bank, cat, CAR))
    assert limit.value == floor
    assert limit.authority == "regulatory_register"
    assert limit.clamped is True
    assert limit.clamped_from == floor - Decimal(3)


def test_a_board_value_stricter_than_the_regulatory_floor_stands(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    stricter = CAR_MIN_VALUE + Decimal(2)
    set_board_threshold(db_session, "car_min", str(stricter))
    limit = governed(resolve(db_session, bank, cat, CAR))
    assert limit.value == stricter
    assert limit.authority == "board_register"
    assert limit.clamped is False
    assert limit.clamped_from is None


def test_the_effective_window_is_the_supplying_rows_own(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """A superseded board generation is not resolved for a later date."""
    boundary = date(2026, 3, 1)
    first = db_session.scalars(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.threshold_code == "fx_nop_aggregate_limit_pct"
        )
    ).one()
    first.effective_to = boundary
    successor = ParamCapitalThreshold(
        organization_id=first.organization_id,
        jurisdiction_code=first.jurisdiction_code,
        threshold_code=first.threshold_code,
        value_pct=NOP_AGGREGATE_LIMIT - Decimal(5),
        effective_from=boundary,
        effective_to=None,
        approved_by=first.approved_by,
        approval_timestamp=first.approval_timestamp,
    )
    db_session.add(successor)
    db_session.flush()

    measure = cat.measure(NOP_AGGREGATE)
    resolver = LimitResolver.load(
        db_session, bank, as_of_dates=[date(2026, 1, 31), date(2026, 6, 30)]
    )
    before = governed(resolver.for_measure(measure, as_of=date(2026, 1, 31)))
    after = governed(resolver.for_measure(measure, as_of=date(2026, 6, 30)))
    assert before.value == NOP_AGGREGATE_LIMIT
    assert before.effective_to == boundary
    assert after.value == NOP_AGGREGATE_LIMIT - Decimal(5)
    assert after.effective_from == boundary
    assert after.effective_to is None


# --- absent is never zero -------------------------------------------------------------------


def test_an_unset_limit_is_absent_and_has_no_value_to_read(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """The structural half of D-069: no sentinel, no zero, no attribute."""
    set_board_threshold(db_session, "lcr_min", None)
    outcome = absent(resolve(db_session, bank, cat, LCR))
    assert outcome.reason == "no_governed_row"
    assert outcome.param_code == "lcr_min"
    assert not hasattr(outcome, "value")
    assert "value" not in {field.name for field in fields(NoGovernedLimit)}
    assert "0" not in outcome.detail
    assert outcome.detail == absence_copy("no_governed_row")


def test_a_measure_no_limit_governs_says_exactly_that(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    outcome = absent(resolve(db_session, bank, cat, RWA))
    assert outcome.reason == "no_threshold_declared"
    assert outcome.param_code is None


def test_neither_outcome_is_a_truthiness_test() -> None:
    """A limit OF zero must not be falsy, and an absent one must not be truthy.

    Neither class defines ``__bool__``, so ``if limit:`` cannot be used as the
    check — :func:`is_governed` is. A zero limit is a real limit (a board may
    hold a product margin at zero) and is the one value a truthiness test gets
    wrong in the dangerous direction.
    """
    zero = GovernedLimit(
        param_code="ftp_min_product_margin_pct",
        measure_id="x",
        value=Decimal(0),
        stated_as="pct",
        authority="board_register",
        source_unit="percent",
        source="test",
        confirmation_status=None,
        source_row_id="row",
        effective_from=AS_OF,
        effective_to=None,
        as_of=AS_OF,
    )
    assert is_governed(zero)
    assert bool(zero) is True
    assert zero.value == Decimal(0)
    missing = NoGovernedLimit(param_code="x", measure_id="x", reason="no_governed_row", as_of=AS_OF)
    assert not is_governed(missing)


def test_every_catalogue_measure_gets_one_stated_outcome(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """Across the whole catalogue: a value, or a named reason. Never a gap."""
    measures = cat.measures()
    resolver = LimitResolver.load(db_session, bank, as_of_dates=[AS_OF])
    outcomes = resolver.for_measures(measures, as_of=AS_OF)
    assert set(outcomes) == {measure.id for measure in measures}
    reasons = set()
    valued = 0
    for measure in measures:
        outcome = outcomes[measure.id]
        if is_governed(outcome):
            assert isinstance(outcome.value, Decimal)
            assert outcome.stated_as == measure.value_type
            assert outcome.param_code == measure.thresholds_source
            valued += 1
        else:
            assert absence_copy(outcome.reason)
            reasons.add(outcome.reason)
    assert valued > 0
    # Nothing in the catalogue reaches these two on the fixture, and a silent
    # arrival of either is a thing to look at rather than to assume.
    assert "scope_unresolved" not in reasons
    assert "value_type_not_comparable" not in reasons


# --- what the resolver refuses ---------------------------------------------------------------


def test_a_percentage_limit_is_refused_against_an_amount_measure(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """The earnings-at-risk case: a percentage-of-income limit, a currency figure.

    The catalogue points the earnings-at-risk measures at the board's
    ``irr_nii_limit_pct``, which is a percentage of net interest income, while
    the measures themselves are amounts in the reporting currency. Converting
    would need the income base, which BI does not hold, so the limit is refused
    rather than drawn at a number that is not a limit on this figure.
    """
    measure = cat.measure(EAR)
    assert measure.value_type == "amount"
    assert measure.thresholds_source == "irr_nii_limit_pct"
    outcome = absent(resolve(db_session, bank, cat, EAR))
    assert outcome.reason == "unit_not_reconcilable"


def test_a_comparison_against_the_banks_plan_carries_no_limit(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """``.actual`` restates the figure and keeps the limit; a variance does not."""
    assert governed(resolve(db_session, bank, cat, CAR_ACTUAL)).value == Decimal(
        CAR_MIN.value or ""
    )
    for measure_id in (CAR_VARIANCE_PCT, CAR_ATTAINMENT):
        outcome = absent(resolve(db_session, bank, cat, measure_id))
        assert outcome.reason == "comparison_not_the_figure", measure_id


def test_an_institution_whose_jurisdiction_is_unregistered_gets_no_limit(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """No parameter set can be selected, and another country's is never borrowed.

    The bank is built in memory rather than persisted because the column carries
    a foreign key into the jurisdictions registry — which is the database half of
    the same rule. The resolver must still refuse rather than raise through a
    read surface, so a misconfigured institution shows no limit instead of a 500.
    """
    unregistered = Bank(
        id="BK-NOSCOPE1",
        organization_id=bank.organization_id,
        name="Unregistered Jurisdiction Bank",
        short_name="Unregistered",
        currency=bank.currency,
        jurisdiction_code="ZZ",
        license_type=bank.license_type,
        institution_type=bank.institution_type,
    )
    resolver = LimitResolver.load(db_session, unregistered, as_of_dates=[AS_OF])
    for measure_id in (CAR, NOP_AGGREGATE, LCR):
        outcome = absent(resolver.for_measure(cat.measure(measure_id), as_of=AS_OF))
        assert outcome.reason == "scope_unresolved", measure_id


# --- the unit rule itself --------------------------------------------------------------------


def test_the_unit_rule_admits_only_exact_scalings() -> None:
    factors = limits.conversion_factors()
    assert set(factors) == {
        ("percent", "pct"),
        ("percent", "fraction"),
        ("bps", "pct"),
        ("bps", "fraction"),
        ("count", "count"),
    }
    for (unit, value_type), factor in factors.items():
        assert value_type in NUMERIC_VALUE_TYPES, (unit, value_type)
        # A power of ten: exact in decimal, so no limit is ever rounded.
        assert factor.normalize().as_tuple().digits == (1,), (unit, factor)


def test_a_percentage_row_is_scaled_into_a_fraction_valued_measure() -> None:
    """13 percent against a proportion-of-one figure is 0.13, exactly."""
    factor = limits.conversion_factors()[("percent", "fraction")]
    assert Decimal("13") * factor == Decimal("0.13")
    assert Decimal("6.5") * factor == Decimal("0.065")


def test_the_value_is_stated_the_way_the_control_plane_states_its_own() -> None:
    """``_normalise`` mirrors ``ResolvedParameter.normalized_value``, not its own rule."""
    for raw in ("100.000000", "13", "6.500000", "0.065000", "1250.000000"):
        value = Decimal(raw)
        mirrored = regulatory_parameters.ResolvedParameter(
            param_code="x",
            value=value,
            value_json=None,
            unit="percent",
            source_citation="",
            confirmation_status="confirmed",
            scope_type="institution_class",
            scope_key="bank",
            jurisdiction_code="GH",
            effective_from=AS_OF,
            parameter_id="row",
        ).normalized_value
        assert limits._normalise(value) == mirrored
        assert str(limits._normalise(value)) == str(mirrored)


# --- the plane, and the shape of the batch ---------------------------------------------------


def test_no_limit_read_enters_the_run_consumption_ledger(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """D-078: BI seals no run, so its reads are nobody's run provenance."""
    resolver = LimitResolver.load(db_session, bank, as_of_dates=[AS_OF])
    resolver.for_measures(cat.measures(), as_of=AS_OF)
    measure_limit(db_session, bank, cat.measure(CAR), as_of=AS_OF)
    assert regulatory_parameters.consume_parameter_provenance(db_session) == []


def test_the_batch_answers_every_widget_without_another_round_trip(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """Both registers load once; resolving N measures then queries nothing."""
    measures = [measure for measure in cat.measures() if measure.thresholds_source]
    assert len(measures) > 100, "the catalogue no longer has a batch worth prefetching"
    resolver = LimitResolver.load(db_session, bank, as_of_dates=[AS_OF])

    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "after_cursor_execute", record)
    try:
        outcomes = resolver.for_measures(measures, as_of=AS_OF)
    finally:
        event.remove(bind, "after_cursor_execute", record)

    assert len(outcomes) == len(measures)
    assert statements == []


def test_the_single_measure_form_agrees_with_the_batch(
    db_session: Session, bank: Bank, cat: Catalogue
) -> None:
    """One code path, so a widget and a dashboard cannot state different limits."""
    measures: list[MeasureDef] = [
        cat.measure(measure_id)
        for measure_id in (CAR, NOP_AGGREGATE, LCR, EAR, RWA, CAR_ACTUAL, CAR_VARIANCE_PCT)
    ]
    batched = LimitResolver.load(db_session, bank, as_of_dates=[AS_OF]).for_measures(
        measures, as_of=AS_OF
    )
    for measure in measures:
        assert measure_limit(db_session, bank, measure, as_of=AS_OF) == batched[measure.id]


def test_every_absence_reason_has_production_copy() -> None:
    """A reason with no sentence is a widget that shows nothing and says nothing."""
    for reason in get_args(limits.LimitAbsenceReason):
        copy = absence_copy(reason)
        assert copy.endswith(".")
        assert copy[0].isupper()
        assert "_" not in copy, reason
