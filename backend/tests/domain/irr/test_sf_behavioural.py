"""SF-5: the behavioural layer — deposits, prepayments and early redemption.

Three optionalities the customer holds, not the bank, and all three respond to
the scenario. The engine must apply each one the way the framework prescribes
and must REFUSE rather than clamp where the bank owns the judgement:

* non-maturing deposits: the bank estimates how much of a balance is core, the
  framework caps how much of that estimate counts and how long it may be
  slotted. The volume cap scales pro rata; the average-maturity cap refuses,
  because silently shortening a bank's own slotting would file a number the
  bank never made;
* prepayments: the base rate is the bank's, the scenario multiplier is the
  framework's, and the product is capped at full prepayment;
* term deposits: a redeemable deposit moves a governed share of its balance to
  the overnight bucket, and a wholesale deposit with no evidence of a
  redemption penalty is priced at whichever exercise hurts the bank more —
  chosen ONCE and then applied to the base position and every shocked position
  alike, because an option priced on one leg of a subtraction is not
  conservatism, it is a broken measure (audit W1, 2026-09-20).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.domain.irr import standardised_params as sp
from app.domain.irr.standardised import (
    CurrencyInputs,
    Ladder,
    LadderKind,
    NmdBalance,
    NmdMaturityCapExceededError,
    SfInputs,
    SfResult,
    apply_prepayment,
    apply_redemption,
    behavioural_rate,
)
from app.domain.irr.standardised import run as sf_run
from tests.domain.irr.test_sf_fixtures import (
    flat_curve,
    running_outstanding,
    sf_parameters,
    simple_ladder,
)

AS_OF = date(2026, 12, 31)
SIX = Decimal("0.000001")


def q(value: Decimal) -> Decimal:
    return value.quantize(SIX, rounding=ROUND_HALF_UP)


def _liability_currency(params: sp.SfParameters, size: str) -> CurrencyInputs:
    return CurrencyInputs(
        currency="GHS",
        zero_cc=flat_curve("0.10", params),
        fx_to_reporting=Decimal(1),
        bb_assets_rep=Decimal(0),
        bb_liabilities_rep=Decimal(size),
    )


def _asset_currency(params: sp.SfParameters, size: str) -> CurrencyInputs:
    return CurrencyInputs(
        currency="GHS",
        zero_cc=flat_curve("0.10", params),
        fx_to_reporting=Decimal(1),
        bb_assets_rep=Decimal(size),
        bb_liabilities_rep=Decimal(0),
    )


def _run(params: sp.SfParameters, inputs: SfInputs) -> SfResult:
    return sf_run(inputs, params)


def _delta(result: SfResult, code: str) -> Decimal:
    row = next(row for row in result.scenarios if row.code == code)
    return row.by_currency[0].delta_eve_native


# --- non-maturing deposits ---------------------------------------------------


def _nmd_inputs(params: sp.SfParameters, deposits: tuple[NmdBalance, ...]) -> SfInputs:
    total = sum((deposit.balance for deposit in deposits), Decimal(0))
    return SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(),
        nmds=deposits,
        currencies=(_liability_currency(params, str(total)),),
        tier1=Decimal(1000),
    )


def test_sf5_the_core_volume_cap_binds_and_the_excess_goes_overnight() -> None:
    """A 93% core estimate against a 90% cap leaves 100 overnight."""
    params = sf_parameters()
    deposits = (
        NmdBalance(
            currency="GHS",
            category="retail_transactional",
            product="CURRENT",
            balance=Decimal(1000),
            core_estimate=Decimal("0.93"),
            core_maturity_years=Decimal(5),
            history_years=Decimal(10),
        ),
    )

    result = _run(params, _nmd_inputs(params, deposits))

    disclosure = result.nmd_disclosure[0]
    assert disclosure.core == Decimal("900.000000")
    assert disclosure.non_core == Decimal("100.000000")
    assert disclosure.cap_binding is True
    ladder = {row.bucket_key: row.principal for row in result.ladder_base}
    assert ladder["b01"] == Decimal("-100.000000")
    assert ladder["b11"] == Decimal("-900.000000")
    row = next(r for r in result.scenarios if r.code == "parallel_up").by_currency[0]
    assert row.eve_base_native == Decimal("-673.837340")
    assert _delta(result, "parallel_up") == Decimal("-105.209869")
    assert _delta(result, "parallel_down") == Decimal("128.822460")


def test_sf5_table7_reports_the_assigned_deposit_maturities() -> None:
    params = sf_parameters()
    deposits = (
        NmdBalance(
            currency="GHS",
            category="retail_transactional",
            product="CURRENT",
            balance=Decimal(1000),
            core_estimate=Decimal("0.93"),
            core_maturity_years=Decimal(5),
            history_years=Decimal(10),
        ),
    )

    result = _run(params, _nmd_inputs(params, deposits))

    # 900 at five years and 100 overnight: a weighted average of 4.5 years.
    assert result.table7_quantitative.average_repricing_maturity_years == Decimal("4.500000")
    assert result.table7_quantitative.longest_repricing_maturity_years == Decimal("5.000000")


def test_sf5_the_average_maturity_cap_is_inclusive() -> None:
    """450 at 2.5 years and 450 at 7.5 years average exactly the 5-year cap."""
    params = sf_parameters()
    deposits = tuple(
        NmdBalance(
            currency="GHS",
            category="retail_transactional",
            product=product,
            balance=Decimal(500),
            core_estimate=Decimal("0.9"),
            core_maturity_years=Decimal(years),
            history_years=Decimal(10),
        )
        for product, years in (("CURRENT-A", "2.5"), ("CURRENT-B", "7.5"))
    )

    result = _run(params, _nmd_inputs(params, deposits))

    assert result.nmd_disclosure[0].average_core_maturity_years == Decimal("5.000000")
    assert result.nmd_disclosure[0].cap_binding is False


def test_sf5_slotting_past_the_average_maturity_cap_refuses() -> None:
    params = sf_parameters()
    deposits = (
        NmdBalance(
            currency="GHS",
            category="retail_transactional",
            product="CURRENT",
            balance=Decimal(1000),
            core_estimate=Decimal("0.9"),
            core_maturity_years=Decimal("6.5"),
            history_years=Decimal(10),
        ),
    )

    with pytest.raises(NmdMaturityCapExceededError) as caught:
        _run(params, _nmd_inputs(params, deposits))

    assert caught.value.code == "nmd_avg_maturity_cap_exceeded"
    assert caught.value.detail["category"] == "retail_transactional"
    assert caught.value.detail["average"] == "6.500000"
    assert caught.value.detail["cap"] == "5.000000"


def test_sf5_a_deposit_with_no_core_estimate_is_entirely_non_core() -> None:
    """The framework's residual, tallied — never a code default of some share."""
    params = sf_parameters()
    deposits = (
        NmdBalance(
            currency="GHS",
            category="wholesale",
            product="OPERATING",
            balance=Decimal(1000),
            core_estimate=None,
        ),
    )

    result = _run(params, _nmd_inputs(params, deposits))

    assert result.nmd_disclosure[0].core == Decimal("0.000000")
    assert result.assumption_tallies["nmd_no_core_estimate"] == 1
    assert result.assumption_tallies["nmd_history_short"] == 1
    ladder = {row.bucket_key: row.principal for row in result.ladder_base}
    assert ladder["b01"] == Decimal("-1000.000000")


def test_sf5_a_core_share_with_no_duration_cannot_be_slotted() -> None:
    params = sf_parameters()
    deposits = (
        NmdBalance(
            currency="GHS",
            category="retail_non_transactional",
            product="SAVINGS",
            balance=Decimal(1000),
            core_estimate=Decimal("0.5"),
            core_maturity_years=None,
            history_years=Decimal(10),
        ),
    )

    result = _run(params, _nmd_inputs(params, deposits))

    assert result.assumption_tallies["nmd_core_without_duration"] == 1
    assert result.nmd_disclosure[0].core == Decimal("0.000000")


# --- prepayment --------------------------------------------------------------

#: Base cash flows of a 1,000 bullet maturing in bucket eleven at a 10% CPR.
SF5_CPR_BASE: tuple[str, ...] = (
    "0.288617", "8.452994", "17.254643", "25.320448", "24.662212", "24.021086",
    "46.185032", "43.814968", "81.000000", "72.900000", "656.100000",
)


def _prepayable(params: sp.SfParameters, rate: str) -> Ladder:
    principal = tuple(
        Decimal(1000) if index == 10 else Decimal(0) for index in range(params.bucket_count)
    )
    return Ladder(
        currency="GHS",
        portfolio="MORTGAGE",
        kind="prepayable",
        principal=principal,
        interest=tuple(Decimal(0) for _ in principal),
        outstanding_end=running_outstanding(principal),
        cpr0=Decimal(rate),
    )


def test_sf5_the_scenario_multiplier_moves_the_prepayment_rate() -> None:
    params = sf_parameters()

    assert behavioural_rate(Decimal("0.10"), params.cpr_multipliers["parallel_up"]) == Decimal(
        "0.080"
    )
    assert behavioural_rate(Decimal("0.10"), params.cpr_multipliers["parallel_down"]) == Decimal(
        "0.120"
    )
    # Capped at full prepayment: 0.9 x 1.2 would be 1.08.
    assert behavioural_rate(Decimal("0.90"), params.cpr_multipliers["parallel_down"]) == Decimal(
        "1"
    )


def test_sf5_prepayment_redistributes_a_bullet_across_the_ladder() -> None:
    params = sf_parameters()

    principal, _ = apply_prepayment(_prepayable(params, "0.10"), Decimal("0.10"), params)

    assert [str(q(amount)) for amount in principal[:11]] == list(SF5_CPR_BASE)
    assert q(sum(principal, Decimal(0))) == Decimal("1000.000000")


def test_sf5_prepayment_responds_to_the_scenario() -> None:
    """Prepaying faster when rates fall is the option the borrower holds."""
    params = sf_parameters()
    inputs = SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(_prepayable(params, "0.10"),),
        nmds=(),
        currencies=(_asset_currency(params, "1000"),),
        tier1=Decimal(1000),
    )

    result = _run(params, inputs)

    row = next(r for r in result.scenarios if r.code == "parallel_up").by_currency[0]
    assert row.eve_base_native == Decimal("705.582223")
    assert _delta(result, "parallel_up") == Decimal("114.065109")
    assert _delta(result, "parallel_down") == Decimal("-125.191182")


def test_sf5_a_frozen_prepayment_rate_understates_the_response() -> None:
    """The same ladder with no scenario response, for contrast."""
    params = sf_parameters()
    frozen = simple_ladder(
        params, "GHS", {index + 1: SF5_CPR_BASE[index] for index in range(len(SF5_CPR_BASE))}
    )
    inputs = SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(frozen,),
        nmds=(),
        currencies=(_asset_currency(params, "1000"),),
        tier1=Decimal(1000),
    )

    result = _run(params, inputs)

    assert _delta(result, "parallel_up") == Decimal("97.944795")
    assert _delta(result, "parallel_down") == Decimal("-117.652179")


def test_sf5_the_as_printed_time_scaling_prepays_far_more() -> None:
    """Why the reading of the per-bucket formula is escalated, not assumed.

    Read literally, the printed per-bucket rate prepays 65% of a 4.5-year bullet
    before it matures; scaled to the bucket's width it prepays 34%. Both modes
    are governed, so confirming the reading is a console change, not a release.
    """
    params = sf_parameters(
        {
            sp.CODE_CPR_TIME_SCALING: sp.GovernedValue(
                param_code=sp.CODE_CPR_TIME_SCALING,
                value_json={"schema": "irrbb-sf-cpr-scaling-v1", "mode": "per_bucket_as_printed"},
                unit="method",
                source_citation="console generation",
                confirmation_status="pending",
            )
        }
    )

    principal, _ = apply_prepayment(_prepayable(params, "0.10"), Decimal("0.10"), params)

    assert q(principal[10]) == Decimal("348.678440")
    assert q(sum(principal, Decimal(0))) == Decimal("1000.000000")


# --- term deposits -----------------------------------------------------------


def _term_deposit(params: sp.SfParameters, rate: str) -> Ladder:
    principal = tuple(
        Decimal(-500) if index == 4 else Decimal(0) for index in range(params.bucket_count)
    )
    return Ladder(
        currency="GHS",
        portfolio="TERM-RETAIL",
        kind="td_retail_redeemable",
        principal=principal,
        interest=tuple(Decimal(0) for _ in principal),
        outstanding_end=running_outstanding(principal),
        tdrr0=Decimal(rate),
    )


@pytest.mark.parametrize(
    ("rate", "overnight", "contractual"),
    (
        ("0.20", "-100.000000", "-400.000000"),
        ("0.24", "-120.000000", "-380.000000"),
        ("0.16", "-80.000000", "-420.000000"),
    ),
)
def test_sf5_redemption_splits_the_balance(
    rate: str, overnight: str, contractual: str
) -> None:
    params = sf_parameters()

    principal, _ = apply_redemption(_term_deposit(params, rate), Decimal(rate))

    assert q(principal[0]) == Decimal(overnight)
    assert q(principal[4]) == Decimal(contractual)
    assert q(sum(principal, Decimal(0))) == Decimal("-500.000000")


def test_sf5_redemption_responds_to_the_scenario() -> None:
    params = sf_parameters()
    inputs = SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(_term_deposit(params, "0.20"),),
        nmds=(),
        currencies=(_liability_currency(params, "500"),),
        tier1=Decimal(1000),
    )

    result = _run(params, inputs)

    row = next(r for r in result.scenarios if r.code == "parallel_up").by_currency[0]
    assert row.eve_base_native == Decimal("-475.737229")
    assert _delta(result, "parallel_up") == Decimal("-8.709080")
    assert _delta(result, "parallel_down") == Decimal("10.058277")


#: One 1,000 term deposit at the 4.5-year bucket midpoint on a flat 20%
#: continuously-compounded curve, against 1,000 of Tier 1. The audit's
#: reproduction vector for W1, kept as data so both tests below read it.
_W1_MIDPOINT = Decimal("0.0028")
_W1_ZERO_CC = Decimal("0.20")
_W1_BALANCE = Decimal(1000)


def _single_deposit_inputs(params: sp.SfParameters, kind: LadderKind) -> SfInputs:
    """The audit's vector: one term deposit, nothing else on the book."""
    return SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(simple_ladder(params, "GHS", {11: "-1000"}, kind=kind),),  # b11 = 4.5y
        nmds=(),
        currencies=(
            CurrencyInputs(
                currency="GHS",
                zero_cc=flat_curve(str(_W1_ZERO_CC), params),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(0),
                bb_liabilities_rep=_W1_BALANCE,
            ),
        ),
        tier1=_W1_BALANCE,
    )


def _overnight_par_value(zero_cc: Decimal) -> Decimal:
    """A demandable liability's value: the balance, discounted overnight only."""
    return -_W1_BALANCE * (-(zero_cc * _W1_MIDPOINT)).exp()


def test_sf5_a_wholesale_deposit_prices_the_base_and_the_shock_on_one_book() -> None:
    """The exercise is settled ONCE and then prices both legs (audit W1).

    This test replaces an inequality that asserted a wholesale ladder's ΔEVE is
    never smaller than the contractual ladder's. That inequality was not a
    property of the method — it was the signature of the defect. Until
    2026-09-20 the base leg fell through to contractual while the shocked leg
    took the worst of {contractual, fully redeemed}; full redemption minimises
    a liability's present value at every rate level, so it won unconditionally
    and ΔEVE collected the whole discount of a term liability instead of the
    change the shock caused. On exactly this vector the old code reported
    ΔEVE 593.010331 — 59.301033% of Tier 1 and a supervisory OUTLIER — with the
    six shocks moving the answer by 0.279648, i.e. 0.05% of it. The inequality
    held perfectly throughout.

    What is asserted now is the arithmetic itself, derived here rather than
    copied from the engine: one behavioural book, priced at the base curve and
    at the shocked curve, and a ΔEVE that changes sign with the direction of
    the shock.
    """
    params = sf_parameters()

    result = _run(params, _single_deposit_inputs(params, "td_wholesale_redeemable"))

    row = next(r for r in result.scenarios if r.code == "parallel_down").by_currency[0]
    # The base leg is the SAME book as the shocked leg: demandable at par.
    assert row.eve_base_native == q(_overnight_par_value(_W1_ZERO_CC))
    shocked = _W1_ZERO_CC - params.shock_bp("parallel", "GHS") / Decimal(10000)
    assert row.eve_scenario_native == q(_overnight_par_value(shocked))
    assert _delta(result, "parallel_down") == q(
        _overnight_par_value(_W1_ZERO_CC) - _overnight_par_value(shocked)
    )
    # A rate measure moves with rates: the down shock costs what the up shock
    # earns, to within the convexity of a two-day discount factor.
    assert _delta(result, "parallel_down") > Decimal(0)
    assert _delta(result, "parallel_up") < Decimal(0)
    # And the outlier verdict is no longer manufactured by the asymmetry.
    assert result.measures.outlier_set.measure == Decimal("0.139833")
    assert result.pct_tier1 == Decimal("0.013983")
    assert result.outlier is False


def test_sf5_the_same_deposit_priced_as_a_plain_term_liability() -> None:
    """The honest comparison the audit made, pinned at magnitude.

    Priced as an ordinary fixed liability the deposit reports ΔEVE 91.258660 —
    9.125866% of Tier 1, not an outlier — and the six shocks move the answer by
    165.788374, two orders of magnitude more than the defect allowed. The two
    tests are kept side by side because the ratio between them IS the finding:
    a measure whose shocks move it by 0.05% of itself is not a rate measure.
    """
    params = sf_parameters()

    result = _run(params, _single_deposit_inputs(params, "fixed"))

    deltas = [row.by_currency[0].delta_eve_native for row in result.scenarios]
    assert _delta(result, "parallel_down") == Decimal("91.258660")
    assert _delta(result, "parallel_up") == Decimal("-74.529714")
    assert result.measures.outlier_set.measure == Decimal("91.258660")
    assert result.pct_tier1 == Decimal("9.125866")
    assert result.outlier is False
    assert max(deltas) - min(deltas) == Decimal("165.788374")


def test_sf5_a_wholesale_deposit_takes_the_exercise_worst_for_the_bank() -> None:
    """The choice itself, on the book the earlier inequality test used.

    The depositor's option is still priced at the exercise most disadvantageous
    to the institution — that has not changed and must not. What changed is
    that the chosen exercise now builds the base book too, so the option shows
    up as a lower baseline economic value rather than as a phantom loss. A
    demandable liability is worth its face value to the bank, so the base EVE
    is the overnight-discounted balance, NOT the contractual discount.
    """
    params = sf_parameters()
    principal = tuple(
        Decimal(-500) if index == 8 else Decimal(0) for index in range(params.bucket_count)
    )
    wholesale = Ladder(
        currency="GHS",
        portfolio="TERM-WHOLESALE",
        kind="td_wholesale_redeemable",
        principal=principal,
        interest=tuple(Decimal(0) for _ in principal),
        outstanding_end=running_outstanding(principal),
    )
    contractual = Ladder(
        currency="GHS",
        portfolio="TERM-WHOLESALE",
        kind="fixed",
        principal=principal,
        interest=wholesale.interest,
        outstanding_end=wholesale.outstanding_end,
    )
    build = lambda ladder: SfInputs(  # noqa: E731 - one expression, read as data
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(ladder,),
        nmds=(),
        currencies=(_liability_currency(params, "500"),),
        tier1=Decimal(1000),
    )

    worst = _run(params, build(wholesale))
    fixed = _run(params, build(contractual))

    worst_row = next(r for r in worst.scenarios if r.code == "parallel_up").by_currency[0]
    fixed_row = next(r for r in fixed.scenarios if r.code == "parallel_up").by_currency[0]
    # The exercise chosen is full redemption — the worse baseline for the bank.
    assert worst_row.eve_base_native < fixed_row.eve_base_native
    assert worst_row.eve_base_native == q(
        Decimal(-500) * (-(Decimal("0.10") * _W1_MIDPOINT)).exp()
    )
    # One book: every scenario row quotes the same base, and it is that one.
    assert {r.by_currency[0].eve_base_native for r in worst.scenarios} == {
        worst_row.eve_base_native
    }
    # And the deposit now carries the rate sensitivity of overnight money,
    # which is the honest consequence of pricing it as repayable on demand.
    for scenario in sp.SCENARIOS:
        assert abs(_delta(worst, scenario)) < abs(_delta(fixed, scenario)), scenario
