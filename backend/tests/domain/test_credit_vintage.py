"""Vintage-triangle goldens (credit PR-7) — hand-verified.

Cohort 2026-01: at MOB 1, loans A(100, current) + B(50, 45dpd) → PAR30 = 50/150
= 33.3333%; at MOB 2, A(90, 31dpd) + B(48, 60dpd) → 138/138 = 100%.
Cohort 2026-03: MOB 0, C(200, current) → 0%. A MOB-3 hole for 2026-01 stays a
hole. A pre-origination observation is dropped.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.credit.vintage import VintageObservation, compute_vintages


def _obs(key: str, cohort: str, mob: int, exposure: str, par30: bool) -> VintageObservation:
    return VintageObservation(
        loan_key=key,
        cohort=cohort,
        months_on_book=mob,
        exposure_ghs=Decimal(exposure),
        par30=par30,
    )


def test_the_triangle_carries_the_hand_numbers_with_holes_kept() -> None:
    result = compute_vintages(
        [
            _obs("A", "2026-01", 1, "100", False),
            _obs("B", "2026-01", 1, "50", True),
            _obs("A", "2026-01", 2, "90", True),
            _obs("B", "2026-01", 2, "48", True),
            _obs("C", "2026-03", 0, "200", False),
            # Age 4 observed, 3 missing: the hole is kept, never interpolated.
            _obs("A", "2026-01", 4, "80", True),
            # Pre-origination data error: dropped.
            _obs("D", "2026-05", -1, "999", True),
        ]
    )
    jan = next(c for c in result.cohorts if c.cohort == "2026-01")
    mob1 = jan.point_at(1)
    assert mob1 is not None
    assert mob1.par30_pct == Decimal("33.3333")
    assert mob1.exposure_ghs == Decimal("150")
    mob2 = jan.point_at(2)
    assert mob2 is not None and mob2.par30_pct == Decimal("100")
    assert jan.point_at(3) is None
    assert jan.point_at(4) is not None
    assert jan.initial_exposure_ghs == Decimal("150")
    assert jan.initial_loan_count == 2

    mar = next(c for c in result.cohorts if c.cohort == "2026-03")
    mar_start = mar.point_at(0)
    assert mar_start is not None
    assert mar_start.par30_pct == Decimal("0")
    assert result.observation_count == 6
