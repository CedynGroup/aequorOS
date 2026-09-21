"""Projecting one instrument onto the bucket ladder.

The Standardised Framework prices cash flows, not balances, so a position's
terms — how it amortises, how often it pays, when it next reprices, whether a
floating margin keeps running past the reset — decide where its money lands.
Where a term is missing the engine substitutes a governed default and SAYS SO:
every substitution leaves a marker, and those markers are what the disclosure
reports as the run's modelling assumptions. A projection that quietly invents a
schedule is the failure mode these tests exist to prevent.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.irr.standardised_cash_flows import (
    TALLY_ACCRUAL_START_UNKNOWN,
    TALLY_HORIZONLESS,
    TALLY_MARKERS,
    TALLY_NO_SPREAD_LEG,
    TALLY_PAST_DUE,
    TALLY_PROFILE_AMORTISATION,
    TALLY_PROFILE_FREQUENCY,
    InstrumentTerms,
    ProjectedFlows,
    project,
    swap_legs,
)
from tests.domain.irr.test_sf_fixtures import sf_parameters

AS_OF = date(2026, 12, 31)
SIX = Decimal("0.000001")
PARAMS = sf_parameters()


def q(value: Decimal) -> Decimal:
    return value.quantize(SIX, rounding=ROUND_HALF_UP)


def at(flows: ProjectedFlows, bucket: str) -> tuple[Decimal, Decimal]:
    index = PARAMS.bucket_index(bucket)
    return q(flows.principal[index]), q(flows.interest[index])


def _loan(**overrides: object) -> InstrumentTerms:
    terms = {
        "ref": "L-1",
        "family": "LOAN",
        "currency": "GHS",
        "side": "asset",
        "principal": Decimal(1000),
        "rate_pct": Decimal(24),
        "maturity": date(2028, 12, 31),
        "amortisation": "bullet",
        "frequency_months": 12,
    }
    terms.update(overrides)
    return InstrumentTerms(**terms)  # type: ignore[arg-type]


def test_a_bullet_pays_its_coupons_and_returns_the_principal_at_maturity() -> None:
    flows = project(_loan(), AS_OF, PARAMS)

    assert at(flows, "b06") == (Decimal("0.000000"), Decimal("240.000000"))
    assert at(flows, "b08") == (Decimal("1000.000000"), Decimal("240.000000"))
    assert q(flows.total_principal) == Decimal("1000.000000")
    assert flows.defaulted == ()


def test_a_bullets_scheduled_balance_only_falls_at_maturity() -> None:
    flows = project(_loan(), AS_OF, PARAMS)

    ends = [q(value) for value in flows.outstanding_end]
    assert ends[: PARAMS.bucket_index("b08")] == [Decimal("1000.000000")] * 7
    assert set(ends[PARAMS.bucket_index("b08") :]) == {Decimal("0.000000")}


def test_a_linear_loan_repays_equal_principal_and_its_coupon_falls() -> None:
    flows = project(_loan(amortisation="linear"), AS_OF, PARAMS)

    assert at(flows, "b06") == (Decimal("500.000000"), Decimal("240.000000"))
    assert at(flows, "b08") == (Decimal("500.000000"), Decimal("120.000000"))


def test_an_annuity_pays_a_level_instalment() -> None:
    flows = project(_loan(amortisation="annuity"), AS_OF, PARAMS)

    first = PARAMS.bucket_index("b06")
    last = PARAMS.bucket_index("b08")
    assert q(flows.interest[first]) == Decimal("240.000000")
    assert q(flows.total_principal) == Decimal("1000.000000")
    # A level instalment: interest falls as principal rises, and the two
    # payments are the same size.
    assert q(flows.principal[first] + flows.interest[first]) == q(
        flows.principal[last] + flows.interest[last]
    )
    assert flows.principal[first] < flows.principal[last]
    assert flows.interest[first] > flows.interest[last]


def test_a_floating_position_reprices_its_whole_principal_at_the_next_reset() -> None:
    terms = InstrumentTerms(
        ref="P-1",
        family="INTERBANK_PLACEMENT",
        currency="GHS",
        side="asset",
        principal=Decimal(500),
        rate_pct=Decimal(20),
        rate_type="FLOATING",
        next_reset=date(2027, 3, 31),
        maturity=date(2030, 12, 31),
        amortisation="bullet",
        frequency_months=0,
    )

    flows = project(terms, AS_OF, PARAMS)

    # Three months out, not four years: the reset is where the rate risk ends.
    assert at(flows, "b03") == (Decimal("500.000000"), Decimal("24.657534"))
    assert TALLY_ACCRUAL_START_UNKNOWN in flows.defaulted
    assert TALLY_NO_SPREAD_LEG in flows.defaulted


def test_a_known_accrual_start_is_used_and_not_tallied() -> None:
    terms = InstrumentTerms(
        ref="P-2",
        family="INTERBANK_PLACEMENT",
        currency="GHS",
        side="asset",
        principal=Decimal(500),
        rate_pct=Decimal(20),
        maturity=date(2027, 3, 31),
        start_date=date(2027, 1, 15),
        amortisation="bullet",
        frequency_months=0,
    )

    flows = project(terms, AS_OF, PARAMS)

    assert at(flows, "b03") == (Decimal("500.000000"), Decimal("20.547945"))
    assert TALLY_ACCRUAL_START_UNKNOWN not in flows.defaulted


def test_a_floating_margin_keeps_running_to_contractual_maturity() -> None:
    """The spread does not reprice, so it is rate risk past the reset."""
    terms = InstrumentTerms(
        ref="S-1",
        family="SECURITY_HOLDING",
        currency="GHS",
        side="asset",
        principal=Decimal(1000),
        rate_pct=Decimal(20),
        rate_type="FLOATING",
        next_reset=date(2027, 6, 30),
        maturity=date(2029, 6, 30),
        spread_pct=Decimal(2),
        amortisation="bullet",
        frequency_months=6,
    )

    flows = project(terms, AS_OF, PARAMS)

    assert at(flows, "b04") == (Decimal("1000.000000"), Decimal("100.000000"))
    for bucket in ("b06", "b07", "b08", "b09"):
        assert at(flows, bucket) == (Decimal("0.000000"), Decimal("10.000000")), bucket
    assert TALLY_NO_SPREAD_LEG not in flows.defaulted


def test_missing_terms_take_the_governed_profile_and_are_tallied() -> None:
    terms = InstrumentTerms(
        ref="L-2",
        family="LOAN",
        currency="GHS",
        side="asset",
        principal=Decimal(1200),
        rate_pct=Decimal(12),
        maturity=date(2027, 12, 31),
    )

    flows = project(terms, AS_OF, PARAMS)

    assert TALLY_PROFILE_AMORTISATION in flows.defaulted
    assert TALLY_PROFILE_FREQUENCY in flows.defaulted
    # The governed profile is a monthly annuity, so the principal spreads
    # across every bucket up to maturity rather than sitting at the end.
    assert q(flows.total_principal) == Decimal("1200.000000")
    assert at(flows, "b02")[0] > Decimal(0)
    assert at(flows, "b06")[0] > Decimal(0)


def test_a_position_with_no_horizon_takes_the_profiles_bucket() -> None:
    terms = InstrumentTerms(
        ref="L-3",
        family="LOAN",
        currency="GHS",
        side="asset",
        principal=Decimal(900),
        rate_pct=Decimal(15),
        amortisation="bullet",
        frequency_months=12,
    )

    flows = project(terms, AS_OF, PARAMS)

    assert TALLY_HORIZONLESS in flows.defaulted
    assert at(flows, "b13") == (Decimal("900.000000"), Decimal("0.000000"))
    assert q(flows.total_interest) == Decimal("0.000000")


def test_a_past_due_flow_lands_overnight_rather_than_disappearing() -> None:
    terms = _loan(maturity=date(2026, 6, 30))

    flows = project(terms, AS_OF, PARAMS)

    assert at(flows, "b01")[0] == Decimal("1000.000000")
    assert q(flows.total_principal) == Decimal("1000.000000")


def test_a_past_due_placement_is_tallied_like_every_other_substitution() -> None:
    """The placement is defensible; being silent about it was not (audit R-3).

    The overnight bucket's midpoint is days, so principal put there is
    discounted at the shortest rate and barely moves when rates move — the
    least conservative place a past-due balance can sit. This module's own
    docstring promises that every substitution is tallied, and until 2026-09-20
    this one was the exception: nothing counted it, nothing disclosed it, and a
    bank with a material non-performing book filed an understated ΔEVE with no
    marker a reader could see. Before this test the assertion below found an
    empty ``defaulted`` tuple.
    """
    terms = _loan(maturity=date(2026, 6, 30))

    flows = project(terms, AS_OF, PARAMS)

    assert TALLY_PAST_DUE in flows.defaulted
    assert TALLY_PAST_DUE in TALLY_MARKERS
    # A position that has NOT matured must not carry it, or the marker means
    # nothing and every book prints the disclosure.
    assert TALLY_PAST_DUE not in project(_loan(), AS_OF, PARAMS).defaulted


def test_a_stale_floating_reset_is_not_reported_as_past_due() -> None:
    """Overnight is the RIGHT answer here, so no understatement is claimed.

    A floating position whose next reset is already behind the reporting date
    reprices immediately; putting it in the shortest bucket is exact, not a
    substitution. Sharing the past-due marker with it would print a warning
    that this measure understates ΔEVE about a position it measures precisely,
    and a disclosure that cries wolf is one a reader stops reading.
    """
    stale = _loan(
        rate_type="FLOATING",
        next_reset=date(2026, 9, 30),
        maturity=date(2030, 12, 31),
        spread_pct=Decimal("2.5"),
    )

    flows = project(stale, AS_OF, PARAMS)

    assert at(flows, "b01")[0] == Decimal("1000.000000")
    assert TALLY_PAST_DUE not in flows.defaulted


def test_every_past_due_position_is_counted_not_just_the_first() -> None:
    """A representative assumption used forty times is a different exposure.

    The markers are accumulated per projection, so the caller's counter sees
    one application per position rather than one per run.
    """
    matured = [_loan(ref=f"L-{index}", maturity=date(2026, 6, 30)) for index in range(3)]

    counted = sum(
        project(terms, AS_OF, PARAMS).defaulted.count(TALLY_PAST_DUE) for terms in matured
    )

    assert counted == 3


def test_a_swap_splits_into_two_legs_on_opposite_sides() -> None:
    swap = InstrumentTerms(
        ref="IRS-1",
        family="INTEREST_RATE_SWAP",
        currency="GHS",
        side="asset",
        principal=Decimal(2000),
        rate_pct=Decimal(18),
        maturity=date(2029, 12, 31),
    )

    fixed, floating = swap_legs(swap, pay_fixed=True, float_reset=date(2027, 3, 31))

    assert (fixed.side, floating.side) == ("liability", "asset")
    fixed_flows = project(fixed, AS_OF, PARAMS)
    float_flows = project(floating, AS_OF, PARAMS)
    # The fixed leg pays every six months on the governed swap frequency and
    # returns the notional at maturity; both of the third-year coupons fall in
    # the two-to-three-year bucket, so it carries 360. The floating leg is gone
    # at its next reset.
    assert at(fixed_flows, "b04") == (Decimal("0.000000"), Decimal("180.000000"))
    assert at(fixed_flows, "b06") == (Decimal("0.000000"), Decimal("180.000000"))
    assert at(fixed_flows, "b09") == (Decimal("2000.000000"), Decimal("360.000000"))
    assert q(fixed_flows.total_interest) == Decimal("1080.000000")
    assert at(float_flows, "b03") == (Decimal("2000.000000"), Decimal("0.000000"))
    assert q(float_flows.total_principal) == Decimal("2000.000000")
