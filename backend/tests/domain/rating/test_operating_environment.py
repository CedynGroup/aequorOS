"""Pure BICRA operating-environment domain (spec docs/internal/operating_environment_score.md).

A worked example (known inputs → exact sub-scores/pillars/composite/score), the
sovereign-governor binding for a CCC sovereign, monotonicity, and value-based
digest reproducibility.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.rating.operating_environment import (
    DEFAULT_PARAMETERS,
    OperatingEnvironmentError,
    OperatingEnvironmentInputs,
    compute_operating_environment,
    normalize_sovereign_category,
)

# The worked example (values in percent unless noted; GDP per capita in USD).
_OBSERVATIONS = {
    "real_gdp_growth_pct": Decimal("4.0"),
    "gdp_per_capita_usd": Decimal("2200"),
    "cpi_inflation_pct": Decimal("23"),
    "private_credit_to_gdp_growth_pct": Decimal("11"),
    "policy_rate_pct": Decimal("27"),
    "system_npl_pct": Decimal("21"),
    "private_debt_to_gdp_pct": Decimal("32"),
    "system_roa_pct": Decimal("1.2"),
    "system_credit_growth_pct": Decimal("18"),
    "system_loan_to_deposit_pct": Decimal("70"),
    "system_car_pct": Decimal("14"),
    "external_funding_pct": Decimal("25"),
}
_JUDGMENTS = {"regulatory_quality_score": 4}


def _inputs(sovereign: str = "bbb", **obs_overrides: Decimal) -> OperatingEnvironmentInputs:
    observations = {**_OBSERVATIONS, **obs_overrides}
    return OperatingEnvironmentInputs(
        observations=observations,
        judgments=dict(_JUDGMENTS),
        sovereign_category=sovereign,
    )


def _sub_scores(result) -> dict[str, int]:  # noqa: ANN001 - test helper over the result tree
    return {
        item.code: item.sub_score
        for pillar in result.pillars
        for sub_factor in pillar.sub_factors
        for item in sub_factor.inputs
    }


def _sub_factor(result, code: str):  # noqa: ANN001, ANN202 - test helper
    for pillar in result.pillars:
        for sub_factor in pillar.sub_factors:
            if sub_factor.code == code:
                return sub_factor
    raise AssertionError(f"sub-factor {code} not found")


def _pillar(result, code: str):  # noqa: ANN001, ANN202 - test helper
    for pillar in result.pillars:
        if pillar.code == code:
            return pillar
    raise AssertionError(f"pillar {code} not found")


def test_worked_example_full_breakdown() -> None:
    """Known inputs (BBB sovereign) → exact sub-scores, pillars, composite, score."""
    result = compute_operating_environment(_inputs(sovereign="bbb"), DEFAULT_PARAMETERS)

    assert _sub_scores(result) == {
        "real_gdp_growth_pct": 3,
        "gdp_per_capita_usd": 5,
        "cpi_inflation_pct": 5,
        "private_credit_to_gdp_growth_pct": 3,
        "policy_rate_pct": 4,
        "system_npl_pct": 5,
        "private_debt_to_gdp_pct": 2,
        "regulatory_quality_score": 4,
        "sovereign_rating": 3,  # BBB → sovereign_risk_table['bbb']
        "system_roa_pct": 3,
        "system_credit_growth_pct": 3,
        "system_loan_to_deposit_pct": 2,
        "system_car_pct": 4,
        "external_funding_pct": 3,
    }

    assert _sub_factor(result, "economic_resilience").score == Decimal("3.800000")
    assert _sub_factor(result, "economic_imbalances").score == Decimal("4.100000")
    assert _sub_factor(result, "credit_risk_economy").score == Decimal("3.800000")
    assert _sub_factor(result, "institutional_framework").score == Decimal("3.500000")
    assert _sub_factor(result, "competitive_dynamics").score == Decimal("3.000000")
    assert _sub_factor(result, "systemwide_funding").score == Decimal("3.000000")

    assert _pillar(result, "economic").score == Decimal("3.890000")
    assert _pillar(result, "industry").score == Decimal("3.200000")

    assert result.composite_risk == Decimal("3.545000")
    assert result.strength_raw == Decimal("0.491000")
    assert result.governor_cap == Decimal("0.800000")
    assert result.governor_applied is False
    assert result.score == Decimal("0.491000")
    assert result.parameters_version == DEFAULT_PARAMETERS.version


def test_score_is_in_unit_interval() -> None:
    result = compute_operating_environment(_inputs(), DEFAULT_PARAMETERS)
    assert Decimal("0") <= result.score <= Decimal("1")


def test_sovereign_governor_binds_for_ccc() -> None:
    """A CCC sovereign caps the system strength below the raw composite path."""
    result = compute_operating_environment(_inputs(sovereign="ccc"), DEFAULT_PARAMETERS)
    # The raw strength exceeds the CCC governor cap (0.40), so the cap binds.
    assert result.strength_raw == Decimal("0.431000")
    assert result.governor_cap == Decimal("0.400000")
    assert result.governor_applied is True
    assert result.score == Decimal("0.400000")
    assert result.score < result.strength_raw


def test_monotonic_in_a_lower_is_better_input() -> None:
    """Raising a higher-is-worse input (inflation) never raises the score."""
    base = compute_operating_environment(_inputs(), DEFAULT_PARAMETERS)
    worse = compute_operating_environment(
        _inputs(cpi_inflation_pct=Decimal("45")), DEFAULT_PARAMETERS
    )
    assert worse.score <= base.score


def test_monotonic_in_a_higher_is_better_input() -> None:
    """Raising a higher-is-better input (GDP growth) never lowers the score."""
    base = compute_operating_environment(_inputs(), DEFAULT_PARAMETERS)
    better = compute_operating_environment(
        _inputs(real_gdp_growth_pct=Decimal("8")), DEFAULT_PARAMETERS
    )
    assert better.score >= base.score


def test_input_digest_is_reproducible_and_input_sensitive() -> None:
    first = compute_operating_environment(_inputs(), DEFAULT_PARAMETERS)
    again = compute_operating_environment(_inputs(), DEFAULT_PARAMETERS)
    assert first.input_digest == again.input_digest

    changed = compute_operating_environment(
        _inputs(system_npl_pct=Decimal("30")), DEFAULT_PARAMETERS
    )
    assert changed.input_digest != first.input_digest

    sovereign_changed = compute_operating_environment(_inputs(sovereign="ccc"), DEFAULT_PARAMETERS)
    assert sovereign_changed.input_digest != first.input_digest


def test_normalize_sovereign_category() -> None:
    assert normalize_sovereign_category("CCC+") == "ccc"
    assert normalize_sovereign_category("BBB-") == "bbb"
    assert normalize_sovereign_category("Caa2") == "ccc"  # Moody's
    assert normalize_sovereign_category("Ba3") == "bb"
    assert normalize_sovereign_category("B1") == "b"
    assert normalize_sovereign_category("aa") == "aa"
    with pytest.raises(OperatingEnvironmentError):
        normalize_sovereign_category("not-a-rating")


def test_missing_observation_raises() -> None:
    incomplete = OperatingEnvironmentInputs(
        observations={"real_gdp_growth_pct": Decimal("4")},
        judgments=dict(_JUDGMENTS),
        sovereign_category="bbb",
    )
    with pytest.raises(OperatingEnvironmentError):
        compute_operating_environment(incomplete, DEFAULT_PARAMETERS)


def test_unknown_sovereign_category_raises() -> None:
    with pytest.raises(OperatingEnvironmentError):
        compute_operating_environment(_inputs(sovereign="zzz"), DEFAULT_PARAMETERS)
