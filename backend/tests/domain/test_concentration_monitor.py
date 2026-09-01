"""Concentration-monitor goldens (credit PR-3) — hand-verified numbers.

Book: four loans, GHS 1,000 total. Employers: ACME 600 (2 loans), GES 300
(1 loan), one loan (100) with no stated employer.
- Employer coverage = 900/1000 = 90%; shares within STATED book: ACME 600/900,
  GES 300/900 → HHI = ((6/9)² + (3/9)²) × 10,000 = (0.4444…+0.1111…)×10,000
  = 5,556 (quantized to 1).
- Share of BOOK (limit basis) uses the TOTAL book: ACME 60%, GES 30%.
- Capital base 2,000 → ACME 30% of capital.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.credit.concentration_monitor import (
    LIMIT_HHI,
    LIMIT_SHARE_OF_BOOK,
    LIMIT_SHARE_OF_CAPITAL,
    STATUS_ABOVE,
    STATUS_NOT_COMPUTABLE,
    STATUS_NOT_SET,
    STATUS_WITHIN,
    ConcentrationLimit,
    monitor_concentration,
)
from app.domain.stress.concentration import DIM_EMPLOYER, DIM_SINGLE_NAME, ConcentrationExposure


def _book() -> list[ConcentrationExposure]:
    return [
        ConcentrationExposure("L1", Decimal("400"), "cp:ama", employer="ACME"),
        ConcentrationExposure("L2", Decimal("200"), "cp:kofi", employer="ACME"),
        ConcentrationExposure("L3", Decimal("300"), "cp:esi", employer="GES"),
        ConcentrationExposure("L4", Decimal("100"), "cp:yaw"),
    ]


def test_employer_dimension_hhi_coverage_and_shares_are_the_hand_numbers() -> None:
    result = monitor_concentration(_book(), capital_base_ghs=Decimal("2000"))
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    assert employer.hhi == Decimal("5556")
    assert employer.coverage_pct == Decimal("90")
    assert employer.bucket_count == 2

    acme = employer.buckets[0]
    assert acme.key == "ACME"
    assert acme.loan_count == 2
    assert acme.share_of_book_pct == Decimal("60")
    assert acme.share_of_capital_pct == Decimal("30")
    # No limit configured: not_set, never an invented threshold.
    assert acme.limit_status == STATUS_NOT_SET
    assert acme.utilization_pct is None


def test_limits_assess_and_breach_with_capital_basis_outranking_book_basis() -> None:
    limits = (
        ConcentrationLimit(DIM_EMPLOYER, LIMIT_SHARE_OF_CAPITAL, Decimal("25")),
        ConcentrationLimit(DIM_EMPLOYER, LIMIT_SHARE_OF_BOOK, Decimal("99")),
    )
    result = monitor_concentration(_book(), limits, capital_base_ghs=Decimal("2000"))
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    acme, ges = employer.buckets[0], employer.buckets[1]
    # ACME 30% of capital vs the 25% capital limit → breach at 120% utilisation.
    assert acme.limit_kind == LIMIT_SHARE_OF_CAPITAL
    assert acme.limit_status == STATUS_ABOVE
    assert acme.utilization_pct == Decimal("120")
    assert ges.limit_status == STATUS_WITHIN
    assert [b.key for b in result.breaches] == ["ACME"]


def test_capital_limit_without_a_capital_base_is_not_computable_never_zero() -> None:
    limits = (ConcentrationLimit(DIM_EMPLOYER, LIMIT_SHARE_OF_CAPITAL, Decimal("25")),)
    result = monitor_concentration(_book(), limits, capital_base_ghs=None)
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    acme = employer.buckets[0]
    assert acme.share_of_capital_pct is None
    assert acme.limit_status == STATUS_NOT_COMPUTABLE
    assert not result.breaches


def test_named_bucket_limit_overrides_the_dimension_wide_row() -> None:
    limits = (
        ConcentrationLimit(DIM_EMPLOYER, LIMIT_SHARE_OF_BOOK, Decimal("10")),
        ConcentrationLimit(DIM_EMPLOYER, LIMIT_SHARE_OF_BOOK, Decimal("70"), bucket_key="ACME"),
    )
    result = monitor_concentration(_book(), limits)
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    acme, ges = employer.buckets[0], employer.buckets[1]
    assert acme.limit_value == Decimal("70")
    assert acme.limit_status == STATUS_WITHIN  # 60% ≤ 70% named limit
    assert ges.limit_value == Decimal("10")
    assert ges.limit_status == STATUS_ABOVE  # 30% > 10% dimension-wide


def test_hhi_limit_assesses_the_dimension_and_single_name_uses_group_keys() -> None:
    limits = (ConcentrationLimit(DIM_EMPLOYER, LIMIT_HHI, Decimal("5000")),)
    result = monitor_concentration(_book(), limits)
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    assert employer.hhi_limit == Decimal("5000")
    assert employer.hhi_status == STATUS_ABOVE  # 5,556 > 5,000

    single = result.dimension(DIM_SINGLE_NAME)
    assert single is not None
    assert single.coverage_pct == Decimal("100")  # every row has a group key
    assert single.bucket_count == 4


def test_unstated_dimension_is_excluded_never_grouped_as_unknown() -> None:
    result = monitor_concentration(_book())
    employer = result.dimension(DIM_EMPLOYER)
    assert employer is not None
    assert {bucket.key for bucket in employer.buckets} == {"ACME", "GES"}
    assert employer.stated_exposure_ghs == Decimal("900")
