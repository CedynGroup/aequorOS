"""Remediation proof for the PD band and the §2.2 weaker-of convention.

Covers the anchor-centred Bayesian band width, PIT/TTC Vasicek conditioning,
the Basel floor, reproducibility, the prior-only (no internal outcomes) state,
key-driver explainability, and the anti-manipulation input convention.
"""

from decimal import Decimal

from app.domain.rating.engine import (
    ComponentDefinition,
    RatingInputs,
    RatingMethodology,
    RatioDefinition,
    compute_rating,
    vasicek_conditional_pd,
)
from app.services import implied_rating

GRADE_ORDER = ("aaa", "aa", "a", "bbb", "bb", "b", "ccc", "c")

# Anchors chosen so mid grades sit well above the 0.03% Basel floor while the top
# grade sits below it (so the floor demonstrably binds).
_ANCHORS = {
    "aaa": Decimal("0.01"),
    "aa": Decimal("0.03"),
    "a": Decimal("0.06"),
    "bbb": Decimal("0.22"),
    "bb": Decimal("0.85"),
    "b": Decimal("4.50"),
    "ccc": Decimal("25.00"),
    "c": Decimal("100.00"),
}


def _methodology(**overrides: Decimal) -> RatingMethodology:
    params: dict[str, Decimal] = {
        "confidence_level": Decimal("0.90"),
        "moc_k_sigma": Decimal("1"),
        "asset_correlation": Decimal("0.24"),
        "prior_strength": Decimal("25"),
    }
    params.update(overrides)
    return RatingMethodology(
        ratio_definitions=(
            RatioDefinition(
                code="cet1_pct",
                component="capitalisation",
                weight=Decimal("1"),
                direction="higher_is_better",
                floor=Decimal("6.5"),
                cap=Decimal("20"),
            ),
            RatioDefinition(
                code="npl_pct",
                component="asset_quality",
                weight=Decimal("1"),
                direction="lower_is_better",
                floor=Decimal("2"),
                cap=Decimal("25"),
            ),
        ),
        components=(
            ComponentDefinition("capitalisation", Decimal("0.5")),
            ComponentDefinition("asset_quality", Decimal("0.5")),
        ),
        grade_cutpoints={
            "aaa": Decimal("0.95"),
            "aa": Decimal("0.85"),
            "a": Decimal("0.75"),
            "bbb": Decimal("0.65"),
            "bb": Decimal("0.55"),
            "b": Decimal("0.45"),
            "ccc": Decimal("0.25"),
            "c": Decimal("0"),
        },
        grade_order=GRADE_ORDER,
        grade_pd_anchors_pct=_ANCHORS,
        confidence_level=params["confidence_level"],
        moc_k_sigma=params["moc_k_sigma"],
        asset_correlation=params["asset_correlation"],
        prior_strength=params["prior_strength"],
    )


def _inputs(**overrides: object) -> RatingInputs:
    # cet1=14, npl=6 at a neutral operating environment (op-env=1 → adjusted=raw)
    # lands a mid "bbb" standalone grade; the strongest ceiling never binds.
    base: dict[str, object] = {
        "ratio_values": {"cet1_pct": Decimal("14"), "npl_pct": Decimal("6")},
        "operating_environment_score": Decimal("1"),
        "sovereign_ceiling": "aaa",
        "grade_obligors": {grade: 0 for grade in GRADE_ORDER},
        "grade_defaults": {grade: 0 for grade in GRADE_ORDER},
        "basis": "TTC",
        "systematic_factor": Decimal("0"),
        "support_uplift_notches": 0,
    }
    base.update(overrides)
    return RatingInputs(**base)  # type: ignore[arg-type]


def test_band_has_real_width() -> None:
    result = compute_rating(_inputs(), _methodology())

    assert result.issuer_grade == "bbb"
    band = result.pd_band
    assert band.lower_pct < band.point_pct < band.upper_pct


def test_pit_above_ttc_under_stress_and_below_under_benign() -> None:
    methodology = _methodology()
    ttc = compute_rating(_inputs(basis="TTC"), methodology)
    pit_stressed = compute_rating(
        _inputs(basis="PIT", systematic_factor=Decimal("-2")), methodology
    )
    pit_benign = compute_rating(
        _inputs(basis="PIT", systematic_factor=Decimal("2")), methodology
    )

    # Same issuer grade, so only the systematic conditioning differs.
    assert ttc.issuer_grade == pit_stressed.issuer_grade == pit_benign.issuer_grade
    assert pit_stressed.pd_band.central_tendency_pct > ttc.pd_band.central_tendency_pct
    assert pit_benign.pd_band.central_tendency_pct < ttc.pd_band.central_tendency_pct


def test_vasicek_conditional_pd_is_monotonic_in_z() -> None:
    pd = Decimal("0.02")
    rho = Decimal("0.24")
    conditionals = [
        vasicek_conditional_pd(pd, rho, Decimal(z)) for z in ("-2", "-1", "0", "1", "2")
    ]

    # A stronger systematic state (higher Z) strictly lowers the conditional PD.
    assert all(
        earlier > later
        for earlier, later in zip(conditionals, conditionals[1:], strict=False)
    )


def test_basel_floor_binds_for_a_top_grade() -> None:
    # cet1=20, npl=2 maxes the scorecard → issuer "aaa", whose 0.01% anchor is
    # below the 0.03% Basel floor.
    result = compute_rating(
        _inputs(ratio_values={"cet1_pct": Decimal("20"), "npl_pct": Decimal("2")}),
        _methodology(),
    )

    assert result.issuer_grade == "aaa"
    band = result.pd_band
    assert band.point_pct == Decimal("0.03")
    assert band.lower_pct == Decimal("0.03")
    assert band.central_tendency_pct == Decimal("0.03")


def test_band_is_reproducible_for_identical_inputs() -> None:
    methodology = _methodology()
    first = compute_rating(_inputs(), methodology)
    second = compute_rating(_inputs(), methodology)

    assert first.pd_band == second.pd_band


def test_prior_only_band_has_no_pluto_tasche_bound() -> None:
    # No internal obligors/defaults anywhere → the pooled internal outcome is
    # empty, so the band is the anchor-centred prior credible interval only.
    result = compute_rating(_inputs(), _methodology())
    band = result.pd_band

    assert band.internal_obligors == 0
    assert band.internal_defaults == 0
    assert band.pluto_tasche_upper_pct is None


def test_key_drivers_are_populated() -> None:
    payload = implied_rating._result_payload(  # noqa: SLF001 - explainability contract
        compute_rating(_inputs(), _methodology())
    )

    assert payload["key_drivers_up"]
    assert payload["key_drivers_down"]
    assert all("label" in entry for entry in payload["key_drivers_up"])
    assert all("label" in entry for entry in payload["key_drivers_down"])


def test_weaker_of_convention_picks_the_more_conservative_figure() -> None:
    ratio_values = {
        "npl_pct": Decimal("4"),  # latest looks benign vs a worse history
        "roa_pct": Decimal("2.5"),  # latest looks strong vs a weaker history
        "net_interest_margin_pct": Decimal("6"),
        "gross_income_to_assets_pct": Decimal("8"),
        "cost_to_income_pct": Decimal("55"),  # latest looks efficient
        "cet1_pct": Decimal("14"),  # capital ratio — must stay untouched
    }
    history = [
        {
            "npl_pct": Decimal("9"),
            "roa_pct": Decimal("1.0"),
            "net_interest_margin_pct": Decimal("5"),
            "gross_income_to_assets_pct": Decimal("6"),
            "cost_to_income_pct": Decimal("70"),
        },
        {
            "npl_pct": Decimal("8"),
            "roa_pct": Decimal("1.2"),
            "net_interest_margin_pct": Decimal("5.5"),
            "gross_income_to_assets_pct": Decimal("6.5"),
            "cost_to_income_pct": Decimal("68"),
        },
    ]

    block = implied_rating._apply_conservative_basis(  # noqa: SLF001 - convention contract
        ratio_values, history
    )

    assert block["applied"] is True
    assert block["annual_periods_used"] == 2
    # Problem-loan / cost ratios: worse = higher → the 3-year average wins.
    assert ratio_values["npl_pct"] == Decimal("8.5")
    assert ratio_values["cost_to_income_pct"] == Decimal("69")
    # Profitability ratios: worse = lower → the 3-year average wins.
    assert ratio_values["roa_pct"] == Decimal("1.1")
    assert ratio_values["net_interest_margin_pct"] == Decimal("5.25")
    assert ratio_values["gross_income_to_assets_pct"] == Decimal("6.25")
    # Capital ratio is latest-only.
    assert ratio_values["cet1_pct"] == Decimal("14")
    assert block["ratios"]["npl_pct"]["basis"] == "three_year_average"


def test_weaker_of_degrades_to_latest_without_enough_history() -> None:
    ratio_values = {
        "npl_pct": Decimal("4"),
        "roa_pct": Decimal("2.5"),
        "net_interest_margin_pct": Decimal("6"),
        "gross_income_to_assets_pct": Decimal("8"),
        "cost_to_income_pct": Decimal("55"),
    }
    single_period = [
        {
            "npl_pct": Decimal("9"),
            "roa_pct": Decimal("1.0"),
            "net_interest_margin_pct": Decimal("5"),
            "gross_income_to_assets_pct": Decimal("6"),
            "cost_to_income_pct": Decimal("70"),
        }
    ]

    block = implied_rating._apply_conservative_basis(  # noqa: SLF001 - convention contract
        ratio_values, single_period
    )

    assert block["applied"] is False
    assert block["annual_periods_used"] == 1
    assert ratio_values["npl_pct"] == Decimal("4")  # unchanged
