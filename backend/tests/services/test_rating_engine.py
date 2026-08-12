from decimal import Decimal

from app.domain.rating.engine import (
    ComponentDefinition,
    RatingInputs,
    RatingMethodology,
    RatioDefinition,
    compute_rating,
    ddep_stress,
    vasicek_conditional_pd,
)

GRADE_ORDER = ("aaa", "aa", "a", "bbb", "bb", "b", "ccc", "c")


def _methodology() -> RatingMethodology:
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
        grade_pd_anchors_pct={
            grade: Decimal(index + 1) / Decimal("10")
            for index, grade in enumerate(GRADE_ORDER)
        },
        confidence_level=Decimal("0.90"),
        moc_k_sigma=Decimal("1"),
    )


def test_rating_applies_sovereign_ceiling_and_conservative_pd_band() -> None:
    result = compute_rating(
        RatingInputs(
            ratio_values={"cet1_pct": Decimal("20"), "npl_pct": Decimal("2")},
            operating_environment_score=Decimal("1"),
            sovereign_ceiling="b",
            grade_obligors={"b": 800},
            grade_defaults={"b": 0},
            basis="PIT",
            support_uplift_notches=3,
        ),
        _methodology(),
    )

    assert result.standalone_grade == "aaa"
    assert result.implied_grade == "b"
    assert result.issuer_grade == "b"
    assert result.ceiling_applied is True
    assert result.pd_band.lower_pct >= Decimal("0.03")
    assert result.pd_band.upper_pct >= result.pd_band.point_pct
    # Pluto-Tasche zero-default 90% bound for 800 pooled obligors is ~0.2874%.
    assert result.pd_band.pluto_tasche_upper_pct == Decimal("0.287409")
    assert result.pd_band.lower_pct <= result.pd_band.point_pct <= result.pd_band.upper_pct


def test_vasicek_sovereign_stress_increases_pd_and_ddep_can_fail_capital() -> None:
    base_pd = Decimal("0.02")
    stressed_pd = vasicek_conditional_pd(base_pd, Decimal("0.25"), Decimal("-2"))
    stress = ddep_stress(
        sovereign_holdings=Decimal("100"),
        haircut_pct=Decimal("30"),
        capital=Decimal("20"),
        risk_weighted_assets=Decimal("200"),
    )

    assert stressed_pd > base_pd
    assert stress.sovereign_loss == Decimal("30.000000")
    assert stress.post_stress_capital == Decimal("-10.000000")
    assert stress.eligible is False