"""Reconciliation: the two tables, and the control that compares like with like."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap.pillar2.types import MissingParameter
from app.domain.icaap.reconciliation import (
    TIER_AT1,
    TIER_CET1,
    TIER_TIER2,
    BaselineByRow,
    Component,
    RecognitionCaps,
    RegulatoryPolicy,
    RequirementLine,
    RequirementReconciliation,
    RiskInput,
    StressedByRow,
    pillar2_source_consistency,
    requirement_reconciliation,
    resources_reconciliation,
)

TOTAL_RWA = Decimal(1000)
POLICY = RegulatoryPolicy(
    car_min_pct=Decimal(13),
    car_min_includes_ccb1=True,
    ccb1_pct=Decimal(3),
    ccyb_pct=Decimal(0),
    dsib_pct=Decimal(0),
)
RISKS = [
    RiskInput("credit", "Credit risk", "pillar1", rwa=Decimal(800)),
    RiskInput("market", "Market risk", "pillar1", rwa=Decimal(50)),
    RiskInput("operational", "Operational risk", "pillar1", rwa=Decimal(150)),
    RiskInput(
        "credit_concentration",
        "Credit concentration",
        "pillar2",
        table5_row="credit_concentration",
        pillar2_baseline=Decimal("18.72"),
        supervisory=Decimal(20),
    ),
    RiskInput(
        "irrbb", "IRRBB", "pillar2", table5_row="irrbb", pillar2_baseline=Decimal("116.759902")
    ),
    RiskInput(
        "others", "Other risks", "pillar2", table5_row="others", pillar2_baseline=Decimal(55)
    ),
]


def lines(result: RequirementReconciliation) -> dict[str, RequirementLine]:
    return {line.line_key: line for line in result.lines}


def test_the_requirement_reference_case() -> None:
    result = requirement_reconciliation(RISKS, total_rwa=TOTAL_RWA, policy=POLICY)
    by_key = lines(result)
    assert by_key["credit"].internal == Decimal(104)
    assert by_key["market"].internal == Decimal("6.5")
    assert by_key["operational"].internal == Decimal("19.5")
    assert result.total_internal_capital_requirement == Decimal("320.4799")
    assert result.total_regulatory_requirement == Decimal(150)
    assert result.explanation_required is False


def test_an_icaap_figure_below_the_supervisory_add_on_must_be_explained() -> None:
    result = requirement_reconciliation(RISKS, total_rwa=TOTAL_RWA, policy=POLICY)
    concentration = lines(result)["credit_concentration"]
    assert concentration.difference == Decimal("-1.28")
    assert concentration.explanation_required is True
    irrbb = lines(result)["irrbb"]
    assert irrbb.difference == Decimal("116.7599")
    assert irrbb.explanation_required is False


def test_the_conservation_buffer_is_a_line_only_when_the_minimum_excludes_it() -> None:
    included = requirement_reconciliation(RISKS, total_rwa=TOTAL_RWA, policy=POLICY)
    assert "ccb1_pct" not in lines(included)
    excluded = requirement_reconciliation(
        RISKS,
        total_rwa=TOTAL_RWA,
        policy=RegulatoryPolicy(
            car_min_pct=Decimal(13),
            car_min_includes_ccb1=False,
            ccb1_pct=Decimal(3),
            ccyb_pct=Decimal(0),
            dsib_pct=Decimal(0),
        ),
    )
    buffer = lines(excluded)["ccb1_pct"]
    assert buffer.internal == Decimal(30)
    assert excluded.total_internal_capital_requirement == Decimal("350.4799")


def test_a_zero_buffer_is_still_printed_with_its_provenance() -> None:
    by_key = lines(requirement_reconciliation(RISKS, total_rwa=TOTAL_RWA, policy=POLICY))
    assert by_key["ccyb_pct"].internal == Decimal(0)
    assert by_key["ccyb_pct"].provenance == "ccyb_pct"
    assert by_key["dsib_buffer_pct"].internal == Decimal(0)


def test_an_unattributed_supervisory_add_on_is_a_regulatory_only_line() -> None:
    result = requirement_reconciliation(
        RISKS, total_rwa=TOTAL_RWA, policy=POLICY, supervisory_unattributed=Decimal(25)
    )
    line = lines(result)["supervisory_unattributed"]
    assert line.internal is None
    assert line.regulatory == Decimal(25)
    assert line.explanation_required is True


def test_an_internal_requirement_below_the_regulatory_one_asks_for_an_explanation() -> None:
    thin = [
        RiskInput("credit", "Credit risk", "pillar1", rwa=Decimal(800)),
        RiskInput(
            "credit_concentration",
            "Credit concentration",
            "pillar2",
            pillar2_baseline=Decimal(1),
            supervisory=Decimal(50),
        ),
    ]
    result = requirement_reconciliation(thin, total_rwa=TOTAL_RWA, policy=POLICY)
    assert result.total_internal_capital_requirement < result.total_regulatory_requirement
    assert result.explanation_required is True


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("car_min_pct", "car_min"),
        ("car_min_includes_ccb1", "icaap_car_min_includes_ccb1"),
        ("ccb1_pct", "ccb1_pct"),
        ("ccyb_pct", "ccyb_pct"),
        ("dsib_pct", "dsib_buffer_pct"),
    ],
)
def test_an_unresolved_policy_value_is_a_typed_refusal(field: str, code: str) -> None:
    policy = RegulatoryPolicy(
        **{
            "car_min_pct": Decimal(13),
            "car_min_includes_ccb1": True,
            "ccb1_pct": Decimal(3),
            "ccyb_pct": Decimal(0),
            "dsib_pct": Decimal(0),
            field: None,
        }
    )
    with pytest.raises(MissingParameter) as error:
        requirement_reconciliation(RISKS, total_rwa=TOTAL_RWA, policy=policy)
    assert error.value.param_code == code


COMPONENTS = [
    Component("cet1", "Common equity tier 1", TIER_CET1, Decimal(250), Decimal(250), True),
    Component("at1", "Additional tier 1", TIER_AT1, Decimal(20), Decimal(20), True),
    Component("tier2", "Tier 2", TIER_TIER2, Decimal(30), Decimal(30), True),
    Component("unaudited", "Unaudited profit", TIER_CET1, None, Decimal(10), False),
]
CAPS = RecognitionCaps(at1_cap_pct_rwa=Decimal("1.5"), tier2_cap_pct_rwa=Decimal(2))
TICR = Decimal("320.4799")


def test_the_resources_reference_case() -> None:
    result = resources_reconciliation(
        COMPONENTS,
        total_rwa=TOTAL_RWA,
        caps=CAPS,
        regulatory_total_capital=Decimal(285),
        ticr=TICR,
    )
    assert result.available_internal_capital == Decimal(310)
    assert result.recognised_regulatory_capital == Decimal(285)
    assert result.coverage_pct == Decimal("96.729935")
    assert result.internal_capital_surplus == Decimal("-10.4799")
    assert result.matches_regulatory_total is True


def test_capital_above_the_recognition_cap_is_reported_not_dropped() -> None:
    result = resources_reconciliation(
        COMPONENTS,
        total_rwa=TOTAL_RWA,
        caps=CAPS,
        regulatory_total_capital=Decimal(285),
        ticr=TICR,
    )
    by_key = {line.line_key: line for line in result.lines}
    assert by_key["at1"].recognised_amount == Decimal(15)
    assert by_key["at1"].above_cap_amount == Decimal(5)
    assert by_key["at1"].explanation_required is True
    assert by_key["tier2"].recognised_amount == Decimal(20)
    assert by_key["tier2"].above_cap_amount == Decimal(10)


def test_an_ineligible_component_counts_internally_and_not_regulatorily() -> None:
    result = resources_reconciliation(
        COMPONENTS,
        total_rwa=TOTAL_RWA,
        caps=CAPS,
        regulatory_total_capital=Decimal(285),
        ticr=TICR,
    )
    unaudited = {line.line_key: line for line in result.lines}["unaudited"]
    assert unaudited.internal_amount == Decimal(10)
    assert unaudited.regulatory_amount is None
    assert unaudited.recognised_amount == Decimal(0)
    assert unaudited.explanation_required is True


def test_a_mismatch_with_the_bound_capital_run_is_surfaced() -> None:
    result = resources_reconciliation(
        COMPONENTS,
        total_rwa=TOTAL_RWA,
        caps=CAPS,
        regulatory_total_capital=Decimal(300),
        ticr=TICR,
    )
    assert result.matches_regulatory_total is False
    assert result.explanation_required is True


def test_an_unresolved_recognition_cap_is_a_typed_refusal() -> None:
    with pytest.raises(MissingParameter) as error:
        resources_reconciliation(
            COMPONENTS,
            total_rwa=TOTAL_RWA,
            caps=RecognitionCaps(at1_cap_pct_rwa=None, tier2_cap_pct_rwa=Decimal(2)),
            regulatory_total_capital=None,
            ticr=TICR,
        )
    assert error.value.param_code == "at1_cap_pct_rwa"


def consistency(tolerance: Decimal | None = Decimal(1)):
    return pillar2_source_consistency(
        icaap_baseline=BaselineByRow(
            {"credit_concentration": Decimal("18.72"), "near": Decimal(100), "far": Decimal(100)}
        ),
        plan_baseline=BaselineByRow(
            {"credit_concentration": Decimal(20), "near": Decimal(101), "far": Decimal(102)}
        ),
        supervisory_baseline=BaselineByRow({"credit_concentration": Decimal(20)}),
        icaap_stressed=StressedByRow({"irrbb": Decimal("116.7599")}),
        overlay_stressed=StressedByRow({"irrbb": Decimal(117)}),
        tolerance_pct=tolerance,
    )


def test_the_tolerance_boundary_decides_consistency() -> None:
    by_key = {item.comparison_key: item for item in consistency()}
    assert by_key["baseline:capital_plan:near"].relative_difference_pct == Decimal("0.990099")
    assert by_key["baseline:capital_plan:near"].status == "consistent"
    assert by_key["baseline:capital_plan:far"].relative_difference_pct == Decimal("1.960784")
    assert by_key["baseline:capital_plan:far"].status == "inconsistent"
    assert by_key["baseline:capital_plan:credit_concentration"].relative_difference_pct == Decimal(
        "6.400000"
    )


def test_a_stressed_figure_is_compared_with_a_stressed_figure() -> None:
    by_key = {item.comparison_key: item for item in consistency()}
    overlay = by_key["stressed:stress_overlay:irrbb"]
    assert overlay.relative_difference_pct == Decimal("0.205214")
    assert overlay.status == "consistent"


def test_a_row_only_one_side_has_is_not_comparable() -> None:
    by_key = {item.comparison_key: item for item in consistency()}
    assert by_key["baseline:supervisory:near"].status == "not_comparable"


def test_two_absent_figures_are_not_an_inconsistency() -> None:
    comparisons = pillar2_source_consistency(
        icaap_baseline=BaselineByRow({"irrbb": None}),
        plan_baseline=BaselineByRow({"irrbb": None}),
        supervisory_baseline=BaselineByRow({}),
        icaap_stressed=StressedByRow({}),
        overlay_stressed=StressedByRow({}),
        tolerance_pct=Decimal(1),
    )
    statuses = {item.comparison_key: item.status for item in comparisons}
    assert statuses["baseline:capital_plan:irrbb"] == "both_absent"


def test_two_zeroes_are_consistent_rather_than_a_division() -> None:
    comparisons = pillar2_source_consistency(
        icaap_baseline=BaselineByRow({"irrbb": Decimal(0)}),
        plan_baseline=BaselineByRow({"irrbb": Decimal(0)}),
        supervisory_baseline=BaselineByRow({}),
        icaap_stressed=StressedByRow({}),
        overlay_stressed=StressedByRow({}),
        tolerance_pct=Decimal(1),
    )
    statuses = {item.comparison_key: item.status for item in comparisons}
    assert statuses["baseline:capital_plan:irrbb"] == "consistent"


def test_a_baseline_map_cannot_be_passed_where_a_stressed_one_is_expected() -> None:
    """The control exists to compare like with like; the types enforce it."""
    with pytest.raises(TypeError, match="basis_mismatch:icaap_stressed"):
        pillar2_source_consistency(
            icaap_baseline=BaselineByRow({}),
            plan_baseline=BaselineByRow({}),
            supervisory_baseline=BaselineByRow({}),
            icaap_stressed=BaselineByRow({}),  # type: ignore[arg-type]
            overlay_stressed=StressedByRow({}),
            tolerance_pct=Decimal(1),
        )


def test_an_unresolved_tolerance_is_a_typed_refusal() -> None:
    with pytest.raises(MissingParameter) as error:
        consistency(tolerance=None)
    assert error.value.param_code == "icaap_pillar2_source_tolerance_pct"
