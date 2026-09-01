"""PD-estimator goldens (credit PR-8) — hand-verified.

Standard grade, ALL segments: 100 loan-months, 2 defaults → h = 0.02 →
PD₁₂ = 1 − 0.98¹² = 21.5283…% (0.98¹² = 0.7847167…). A grade with 40
loan-months and 0 defaults reports not-estimable (absence ≠ 0.00%); a grade
with 10 loan-months reports below-floor regardless of defaults.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.credit.pd import TransitionObservation, estimate_pd


def _many(grade: str, months: int, defaults: int, segment: str | None = None):
    return [
        TransitionObservation(grade=grade, segment=segment, defaulted=index < defaults)
        for index in range(months)
    ]


def test_hazard_annualises_to_the_hand_number() -> None:
    result = estimate_pd(_many("standard", 100, 2))
    estimate = result.for_grade("standard")
    assert estimate is not None
    assert estimate.monthly_hazard_pct == Decimal("2")
    assert estimate.pd_12m_pct == Decimal("21.5283")
    assert estimate.loan_months == 100
    assert estimate.defaults_observed == 2


def test_zero_defaults_is_absence_of_evidence_never_zero_pd() -> None:
    result = estimate_pd(_many("olem", 40, 0))
    estimate = result.for_grade("olem")
    assert estimate is not None
    assert estimate.pd_12m_pct is None
    assert "absence of evidence" in (estimate.not_estimable_reason or "")


def test_below_the_observation_floor_nothing_is_released() -> None:
    result = estimate_pd(_many("standard", 10, 5))
    estimate = result.for_grade("standard")
    assert estimate is not None
    assert estimate.pd_12m_pct is None
    assert "loan-months" in (estimate.not_estimable_reason or "")


def test_segments_estimate_independently() -> None:
    result = estimate_pd(
        [*_many("standard", 50, 5, "LN-SAL"), *_many("standard", 50, 1, "LN-CONS")]
    )
    salary = result.for_grade("standard", "LN-SAL")
    consumer = result.for_grade("standard", "LN-CONS")
    assert salary is not None and consumer is not None
    assert salary.pd_12m_pct is not None and consumer.pd_12m_pct is not None
    assert salary.pd_12m_pct > consumer.pd_12m_pct
