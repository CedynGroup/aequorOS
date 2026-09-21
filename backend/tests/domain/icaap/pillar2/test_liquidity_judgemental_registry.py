"""The two non-computed methods, and the registry that declares every method."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap.methods import PILLAR2_METHODS
from app.domain.icaap.pillar2 import (
    DEFERRED_METHODS,
    IMPLEMENTED_P5_METHODS,
    METHOD_REGISTRY,
)
from app.domain.icaap.pillar2.judgemental import (
    COMPONENT_DIVERSIFICATION,
    validate_judgemental,
)
from app.domain.icaap.pillar2.liquidity import ILAAP_FACT_KEYS, not_capitalised
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter


def test_liquidity_is_assessed_and_carries_no_capital_line() -> None:
    result = not_capitalised(
        ilaap_facts={
            "ilaap_adequate": "true",
            "lcr_pct": "180.5",
            "nsfr_pct": "130.2",
            "worst_stressed_lcr_pct": "112.0",
        }
    )
    assert result.status is MethodStatus.NOT_CAPITALISED
    assert result.baseline_amount is None
    assert result.stressed_amount is None
    assert result.basis is None
    assert result.detail["lcr_pct"] == "180.5"
    assert result.reasons == ()


def test_an_unbound_ilaap_fact_is_named_rather_than_left_blank() -> None:
    result = not_capitalised()
    assert result.status is MethodStatus.NOT_CAPITALISED
    assert set(result.detail) == set(ILAAP_FACT_KEYS)
    assert len(result.reasons) == len(ILAAP_FACT_KEYS)


def judgement(**overrides):
    kwargs = {
        "component": "strategic_risk",
        "baseline": Decimal(50),
        "stressed": Decimal(70),
        "rationale": "Board judgement recorded in the minutes of the risk committee",
        "evidence_count": 1,
        "risk_is_material": True,
    }
    kwargs.update(overrides)
    return validate_judgemental(**kwargs)


def test_a_complete_judgement_is_accepted() -> None:
    result = judgement()
    assert result.status is MethodStatus.COMPUTED
    assert result.baseline_amount == Decimal(50)
    assert result.stressed_amount == Decimal(70)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"rationale": "  "}, "rationale_required"),
        ({"evidence_count": 0}, "evidence_required"),
        ({"stressed": None}, "stressed_required"),
        ({"baseline": None}, "baseline_required"),
        ({"baseline": Decimal(-1)}, "negative_amount_not_allowed"),
        ({"baseline": Decimal(0)}, "zero_amount_justification_required"),
    ],
)
def test_an_incomplete_judgement_says_exactly_what_is_missing(
    overrides: dict[str, object], reason: str
) -> None:
    result = judgement(**overrides)
    assert result.status is MethodStatus.INCOMPLETE
    assert reason in result.reasons


def test_a_declared_same_as_baseline_stressed_figure_is_accepted() -> None:
    result = judgement(stressed=None, stressed_same_as_baseline=True)
    assert result.status is MethodStatus.COMPUTED
    assert result.stressed_derivation == "same_as_baseline"
    assert result.stressed_amount == Decimal(50)


def test_a_material_risk_may_carry_zero_capital_if_the_zero_is_justified() -> None:
    result = judgement(
        baseline=Decimal(0),
        stressed=Decimal(0),
        zero_amount_justification="Covered by the operational scenario add-on",
    )
    assert result.status is MethodStatus.COMPUTED


def test_diversification_is_the_only_negative_item_and_only_when_allowed() -> None:
    allowed = validate_judgemental(
        component=COMPONENT_DIVERSIFICATION,
        baseline=Decimal(-10),
        stressed=Decimal(-10),
        rationale="Supervisory agreement recorded",
        evidence_count=1,
        diversification_allowed=True,
    )
    assert allowed.status is MethodStatus.COMPUTED
    refused = validate_judgemental(
        component=COMPONENT_DIVERSIFICATION,
        baseline=Decimal(-10),
        stressed=Decimal(-10),
        rationale="Supervisory agreement recorded",
        evidence_count=1,
        diversification_allowed=False,
    )
    assert "diversification_not_allowed" in refused.reasons
    positive = validate_judgemental(
        component=COMPONENT_DIVERSIFICATION,
        baseline=Decimal(10),
        stressed=Decimal(10),
        rationale="Supervisory agreement recorded",
        evidence_count=1,
        diversification_allowed=True,
    )
    assert "diversification_must_not_increase_capital" in positive.reasons


def test_the_diversification_switch_must_be_resolved() -> None:
    with pytest.raises(MissingParameter) as error:
        validate_judgemental(
            component=COMPONENT_DIVERSIFICATION,
            baseline=Decimal(-10),
            rationale="x",
            evidence_count=1,
        )
    assert error.value.param_code == "icaap_diversification_benefit_allowed"


def test_the_registry_covers_the_whole_method_vocabulary() -> None:
    assert set(METHOD_REGISTRY) == set(PILLAR2_METHODS)
    for key, spec in METHOD_REGISTRY.items():
        assert spec.label == PILLAR2_METHODS[key].title
        assert spec.phase == PILLAR2_METHODS[key].phase
        assert spec.supported_input_modes


def test_the_methods_this_phase_does_not_build_are_declared_not_silent() -> None:
    """Every declared method is now built, and PHASE still says who built it.

    The granularity adjustment left this set when its service path landed
    (P5-C) and the IRRBB standardised framework left it when its block resolver
    and Pillar 2 method landed (P5-D): a bank can select either, and each
    answers with a figure or a NAMED refusal instead of
    ``method_not_available``. Neither method's PHASE moves — the phase says
    which phase built it, which stays true forever, and the two facts are
    separate on purpose.

    The empty set is the assertion, not an absence of one: a method declared in
    the vocabulary with no implementation behind it must show up here.
    """
    assert set(DEFERRED_METHODS) == set()
    for key in ("granularity_adjustment", "irrbb_standardised_framework"):
        assert METHOD_REGISTRY[key].phase == "P5"
        assert key not in DEFERRED_METHODS
        assert key in IMPLEMENTED_P5_METHODS
    assert set(IMPLEMENTED_P5_METHODS) == {
        key for key, spec in METHOD_REGISTRY.items() if spec.phase == "P5"
    }
