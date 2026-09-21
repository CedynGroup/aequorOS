"""The full granularity adjustment: P5-DESIGN §4.6 reference cases and properties.

Every case in the design's table is asserted here at six decimal places against
the figures the blueprint pins, so the module cannot drift from the reviewed
derivation. Three of those cases are books the seeded effective-name floor
declines — their stated GA is what the formula gives, and the test shows BOTH:
the figure with the gate lowered, and the refusal at the seeded floor.

The properties are the claims the table cannot make on its own: order
invariance, exact 1/N scaling, the Pigou-Dalton reverse (concentrating a book
cannot lower the adjustment), a perfectly granular book converging on zero, and
a refusal for every input the formula cannot honestly consume.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.credit.granularity import (
    REASON_BELOW_MIN_EFFECTIVE_NAMES,
    REASON_NO_EXPOSURES,
    SEGMENT_CORPORATE,
    SEGMENT_RETAIL_OTHER,
    STATUS_COMPUTED,
    STATUS_NOT_APPLICABLE,
    WARNING_EXCEEDS_K_STAR,
    GaExposure,
    GaResult,
    GranularityInputError,
    asset_correlation,
    exposure_k_and_r,
    granularity_adjustment,
)
from tests.domain.credit.ga_fixtures import (
    CORRELATIONS,
    DELTA,
    DELTA_EXACT,
    ELGD_25,
    ELGD_45,
    LGD_VARIANCE_GAMMA,
    MATURITY_ON,
    PD_TWO_PCT,
    gate_passing_book,
    homogeneous,
    names,
    params,
    sixty_name_book,
    three_name_book,
    ungated,
)

SIX_PLACES = Decimal("0.000001")


def at6(value: Decimal | None) -> Decimal:
    assert value is not None
    return value.quantize(SIX_PLACES)


def _ga(result: GaResult) -> Decimal:
    assert result.ga is not None
    return result.ga


# --- §4.6 reference cases ---------------------------------------------------


def test_homogeneous_pd_2pct_ten_names_reproduces_the_reference_case() -> None:
    """GA 0.129173, K* 0.076617, with rho 0.164146, R 0.009 and C 0.5875."""
    book = homogeneous(10)
    result = granularity_adjustment(book, ungated())

    assert at6(result.ga) == Decimal("0.129173")
    assert at6(result.k_star) == Decimal("0.076617")
    assert at6(result.n_eff) == Decimal("10.000000")
    assert result.n_obligors == 10

    rho = asset_correlation(PD_TWO_PCT, CORRELATIONS[SEGMENT_CORPORATE])
    unexpected, expected = exposure_k_and_r(book[0], ungated())
    assert at6(rho) == Decimal("0.164146")
    assert at6(expected) == Decimal("0.009000")
    assert at6(unexpected) == Decimal("0.076617")
    # C = (ELGD^2 + gamma.ELGD(1-ELGD)) / ELGD
    gamma = Decimal(LGD_VARIANCE_GAMMA)
    c = (ELGD_45**2 + gamma * ELGD_45 * (1 - ELGD_45)) / ELGD_45
    assert at6(c) == Decimal("0.587500")


def test_homogeneous_pd_2pct_hundred_names_reproduces_the_reference_case() -> None:
    result = granularity_adjustment(homogeneous(100), ungated())
    assert at6(result.ga) == Decimal("0.012917")
    assert at6(result.k_star) == Decimal("0.076617")


def test_homogeneous_pd_2pct_thousand_names_reproduces_the_reference_case() -> None:
    result = granularity_adjustment(homogeneous(1000), ungated())
    assert at6(result.ga) == Decimal("0.001292")
    assert at6(result.k_star) == Decimal("0.076617")


def test_the_seeded_delta_rounding_is_visible_at_six_places() -> None:
    """The seed rounds δ; the reference case says what that costs."""
    seeded = granularity_adjustment(homogeneous(100), ungated(delta=DELTA))
    exact = granularity_adjustment(homogeneous(100), ungated(delta=DELTA_EXACT))
    assert at6(seeded.ga) == Decimal("0.012917")
    assert at6(exact.ga) == Decimal("0.012929")


def test_maturity_adjustment_at_two_and_a_half_years_reproduces_the_reference_case() -> None:
    result = granularity_adjustment(homogeneous(100, maturity="2.5"), ungated(ma=MATURITY_ON))
    assert at6(result.ga) == Decimal("0.012640")
    assert at6(result.k_star) == Decimal("0.091883")


def test_retail_other_correlation_reproduces_the_reference_case() -> None:
    result = granularity_adjustment(homogeneous(100, segment=SEGMENT_RETAIL_OTHER), ungated())
    assert at6(result.ga) == Decimal("0.014003")
    assert at6(result.k_star) == Decimal("0.046389")


def test_three_names_exceed_k_star_and_are_declined_at_the_seeded_floor() -> None:
    """GA 0.588936 > K* 0.074109 — a book the asymptotic model cannot carry."""
    book = three_name_book()
    stated = granularity_adjustment(book, ungated())
    assert at6(stated.ga) == Decimal("0.588936")
    assert at6(stated.k_star) == Decimal("0.074109")
    assert at6(stated.n_eff) == Decimal("2.173913")
    assert stated.warnings == (WARNING_EXCEEDS_K_STAR,)

    declined = granularity_adjustment(book, params())
    assert declined.status == STATUS_NOT_APPLICABLE
    assert declined.reason == REASON_BELOW_MIN_EFFECTIVE_NAMES
    assert declined.ga is None
    assert declined.addon_amount is None
    # The diagnostics that explain the refusal survive it.
    assert at6(declined.n_eff) == Decimal("2.173913")
    assert at6(declined.k_star) == Decimal("0.074109")


def test_sixty_name_book_is_gated_at_fifty_and_computes_at_thirty() -> None:
    """The design's gate case: N_eff 33.33 against a seeded floor of 50."""
    book = sixty_name_book()

    declined = granularity_adjustment(book, params())
    assert declined.status == STATUS_NOT_APPLICABLE
    assert declined.reason == REASON_BELOW_MIN_EFFECTIVE_NAMES
    assert at6(declined.n_eff) == Decimal("33.333333")
    assert declined.n_obligors == 60
    assert at6(declined.total_ead) == Decimal("2000.000000")

    computed = granularity_adjustment(book, params(min_effective_names="30"))
    assert computed.status == STATUS_COMPUTED
    assert at6(computed.ga) == Decimal("0.032745")
    assert at6(computed.k_star) == Decimal("0.073252")
    assert at6(computed.addon_amount) == Decimal("65.489268")


def test_sixty_name_book_with_lower_elgd_on_the_small_names() -> None:
    result = granularity_adjustment(sixty_name_book(ELGD_25), ungated())
    assert at6(result.ga) == Decimal("0.038208")
    assert at6(result.k_star) == Decimal("0.053723")
    assert at6(result.addon_amount) == Decimal("76.415261")


def test_gate_passing_book_computes_at_the_seeded_floor() -> None:
    """N_eff 66.67 >= 50: the one reference book that runs as seeded."""
    result = granularity_adjustment(gate_passing_book(), params())
    assert result.status == STATUS_COMPUTED
    assert at6(result.ga) == Decimal("0.016372")
    assert at6(result.k_star) == Decimal("0.073252")
    assert at6(result.n_eff) == Decimal("66.666667")
    assert at6(result.total_ead) == Decimal("2000.000000")
    assert at6(result.addon_amount) == Decimal("32.744634")
    assert result.warnings == ()
    assert result.reason is None


def test_the_add_on_reproduces_from_the_figures_the_result_prints() -> None:
    """A reader must be able to recompute the amount from GA and total EAD."""
    result = granularity_adjustment(gate_passing_book(), params())
    assert result.ga is not None and result.addon_amount is not None
    assert result.addon_amount == (result.ga * result.total_ead).quantize(Decimal("0.000001"))


# --- properties -------------------------------------------------------------


def test_the_adjustment_scales_exactly_with_one_over_n() -> None:
    """A homogeneous book's GA is exactly proportional to 1/N."""
    products = [
        _ga(granularity_adjustment(homogeneous(count), ungated())) * count
        for count in (10, 100, 1000)
    ]
    assert at6(products[0]) == at6(products[1]) == at6(products[2])


def test_a_perfectly_granular_book_converges_on_zero() -> None:
    """GA falls monotonically towards nothing as the book is split finer."""
    coarse = _ga(granularity_adjustment(homogeneous(100), ungated()))
    finer = _ga(granularity_adjustment(homogeneous(1000), ungated()))
    finest = granularity_adjustment(homogeneous(10000), ungated())
    assert coarse > finer > _ga(finest) > 0
    # Each tenfold split leaves a tenth of the adjustment behind; the limit of
    # a book with no name in it big enough to matter is nothing at all.
    assert _ga(finest) < Decimal("0.0002")
    assert finest.status == STATUS_COMPUTED


def test_the_adjustment_is_invariant_to_the_order_the_exposures_arrive_in() -> None:
    book = sixty_name_book()
    shuffled = [*book[30:], *reversed(book[:30])]
    assert granularity_adjustment(shuffled, ungated()) == granularity_adjustment(book, ungated())


def test_concentrating_the_book_cannot_lower_the_adjustment() -> None:
    """Pigou-Dalton reverse: EAD moved to a larger name raises GA."""
    base = homogeneous(60)
    transferred = [
        replace(base[0], ead=Decimal("1.5")),
        replace(base[1], ead=Decimal("0.5")),
        *base[2:],
    ]
    before = granularity_adjustment(base, ungated())
    after = granularity_adjustment(transferred, ungated())
    assert before.ga is not None and after.ga is not None
    assert after.ga > before.ga
    assert after.n_eff < before.n_eff


def test_the_gate_measures_effective_names_not_the_raw_count() -> None:
    """One dominant obligor plus a thousand tiny ones passes no honest test."""
    book = [
        GaExposure("BIG", "BIG", Decimal(1_000_000), PD_TWO_PCT, ELGD_45, SEGMENT_CORPORATE),
        *names(1000, ead="1", pd=PD_TWO_PCT, start=2),
    ]
    result = granularity_adjustment(book, params())
    assert result.n_obligors == 1001
    assert result.n_eff < Decimal(2)
    assert result.status == STATUS_NOT_APPLICABLE
    assert result.reason == REASON_BELOW_MIN_EFFECTIVE_NAMES


def test_connected_exposures_are_one_obligor() -> None:
    """Two facilities to one group are one name, not two."""
    split = names(2, ead="50", pd=PD_TWO_PCT)
    connected = [replace(exposure, group_key="GROUP") for exposure in split]
    merged = [GaExposure("ONE", "GROUP", Decimal(100), PD_TWO_PCT, ELGD_45, SEGMENT_CORPORATE)]
    padding = names(200, ead="1", pd=PD_TWO_PCT, start=100)

    as_connected = granularity_adjustment([*connected, *padding], ungated())
    as_one = granularity_adjustment([*merged, *padding], ungated())
    as_two = granularity_adjustment([*split, *padding], ungated())

    assert as_connected.n_obligors == as_one.n_obligors == 201
    assert as_connected.ga == as_one.ga
    assert as_two.n_obligors == 202
    assert as_connected.ga is not None and as_two.ga is not None
    assert as_connected.ga > as_two.ga


def test_top_contributors_rank_the_obligors_that_drive_the_figure() -> None:
    result = granularity_adjustment(gate_passing_book(), params())
    contributions = [value for _key, value in result.top_contributors]
    assert len(result.top_contributors) == 5
    assert contributions == sorted(contributions, reverse=True)
    assert sum(contributions) < _ga(result)  # a share of it, never the whole


# --- refusals ---------------------------------------------------------------


def test_an_empty_book_is_declined_not_zero() -> None:
    result = granularity_adjustment([], params())
    assert result.status == STATUS_NOT_APPLICABLE
    assert result.reason == REASON_NO_EXPOSURES
    assert result.ga is None
    assert result.addon_amount is None


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("ead", Decimal(0), "ga_ead_not_positive"),
        ("ead", Decimal(-1), "ga_ead_not_positive"),
        ("pd", Decimal(0), "ga_pd_out_of_range"),
        ("pd", Decimal(1), "ga_pd_out_of_range"),
        ("pd", Decimal("-0.01"), "ga_pd_out_of_range"),
        ("elgd", Decimal(0), "ga_elgd_out_of_range"),
        ("elgd", Decimal("1.01"), "ga_elgd_out_of_range"),
        ("segment", "sovereign", "ga_segment_correlation_missing"),
    ],
)
def test_an_input_the_formula_cannot_consume_is_a_typed_refusal(
    field: str, value: object, code: str
) -> None:
    book = homogeneous(60)
    broken = replace(book[0], **{field: value})
    with pytest.raises(GranularityInputError) as raised:
        granularity_adjustment([broken, *book[1:]], ungated())
    assert raised.value.code == code
    assert raised.value.ref == broken.ref


def test_the_maturity_adjustment_refuses_a_book_with_no_maturity() -> None:
    with pytest.raises(GranularityInputError) as raised:
        granularity_adjustment(homogeneous(60), ungated(ma=MATURITY_ON))
    assert raised.value.code == "ga_maturity_missing"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"q": Decimal(0)}, "ga_confidence_out_of_range"),
        ({"q": Decimal(1)}, "ga_confidence_out_of_range"),
        ({"delta": Decimal(-1)}, "ga_parameter_out_of_range"),
        ({"gamma": Decimal(-1)}, "ga_parameter_out_of_range"),
        ({"min_effective_names": Decimal(-1)}, "ga_parameter_out_of_range"),
        (
            {"corr": {SEGMENT_CORPORATE: (Decimal("0.12"), Decimal("0.24"), Decimal(0))}},
            "ga_correlation_out_of_range",
        ),
        (
            {"corr": {SEGMENT_CORPORATE: (Decimal("0.12"), Decimal(1), Decimal(50))}},
            "ga_correlation_out_of_range",
        ),
    ],
)
def test_a_calibration_outside_its_range_is_a_typed_refusal(
    overrides: dict[str, object], code: str
) -> None:
    base = ungated()
    broken = replace(base, **overrides)
    with pytest.raises(GranularityInputError) as raised:
        granularity_adjustment(homogeneous(60), broken)
    assert raised.value.code == code


# --- the governed δ ---------------------------------------------------------


def test_the_seeded_delta_matches_the_gamma_factor_derivation() -> None:
    """δ = (q_a − 1)(ξ + (1 − ξ)/q_a), q_a the α-quantile of Gamma(ξ, 1/ξ)."""
    scipy_stats = pytest.importorskip("scipy.stats")
    xi = 0.25
    alpha = 0.999
    quantile = scipy_stats.gamma.ppf(alpha, a=xi, scale=1 / xi)
    derived = (quantile - 1) * (xi + (1 - xi) / quantile)

    exact = Decimal(repr(float(derived)))
    assert abs(exact - Decimal(DELTA)) < Decimal("0.01")
    assert exact.quantize(SIX_PLACES) == Decimal(DELTA_EXACT).quantize(SIX_PLACES)
