"""Vintage (cohort) analysis (pure; credit PR-7).

The standard origination-quality view for a retail/microfinance lender: group
loans by the month they were ORIGINATED (the cohort), then track each cohort's
PAR30+ share at successive months-on-book. Two cohorts at the same age are
comparable regardless of calendar date, which is what makes vintages the
underwriting-quality instrument delinquency stocks cannot be — a worsening
book can hide behind growth, but a worsening COHORT curve cannot.

Inputs are month-end observations of individual loans; the engine never
interpolates: a cohort observed at months 1, 2 and 4 has a hole at 3, and the
triangle keeps the hole. Loans without an origination date belong to no cohort
and are EXCLUDED — coverage is disclosed by the caller, never an "Unknown"
cohort.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")


@dataclass(frozen=True)
class VintageObservation:
    """One loan at one month-end, reduced to what vintages need."""

    loan_key: str
    cohort: str  # origination month, "YYYY-MM"
    months_on_book: int
    exposure_ghs: Decimal
    #: 30+ days past due at this observation.
    par30: bool


@dataclass(frozen=True)
class VintagePoint:
    months_on_book: int
    exposure_ghs: Decimal
    par30_exposure_ghs: Decimal
    par30_pct: Decimal
    loan_count: int


@dataclass(frozen=True)
class VintageCohort:
    cohort: str
    #: Exposure at the cohort's earliest observed age (its "disbursed" proxy).
    initial_exposure_ghs: Decimal
    initial_loan_count: int
    points: tuple[VintagePoint, ...]

    def point_at(self, months_on_book: int) -> VintagePoint | None:
        return next((p for p in self.points if p.months_on_book == months_on_book), None)


@dataclass(frozen=True)
class VintageResult:
    cohorts: tuple[VintageCohort, ...]
    observation_count: int


def compute_vintages(
    observations: list[VintageObservation] | tuple[VintageObservation, ...],
) -> VintageResult:
    """The cohort × months-on-book PAR30+ triangle.

    Negative months-on-book (an observation dated before its own origination —
    a data error) are dropped. Where one loan appears twice at the same age
    (a restated month), the observations sum; the caller feeds one book per
    calendar month, so this only happens on genuinely duplicated input.
    """
    by_cohort: dict[str, dict[int, tuple[Decimal, Decimal, int]]] = {}
    for obs in observations:
        if obs.months_on_book < 0:
            continue
        ages = by_cohort.setdefault(obs.cohort, {})
        exposure, par30, count = ages.get(obs.months_on_book, (_ZERO, _ZERO, 0))
        ages[obs.months_on_book] = (
            exposure + obs.exposure_ghs,
            par30 + (obs.exposure_ghs if obs.par30 else _ZERO),
            count + 1,
        )
    cohorts: list[VintageCohort] = []
    for cohort in sorted(by_cohort):
        ages = by_cohort[cohort]
        points = tuple(
            VintagePoint(
                months_on_book=age,
                exposure_ghs=exposure,
                par30_exposure_ghs=par30,
                par30_pct=(
                    (par30 / exposure * _HUNDRED).quantize(_PCT_Q)
                    if exposure > _ZERO
                    else _ZERO
                ),
                loan_count=count,
            )
            for age, (exposure, par30, count) in sorted(ages.items())
        )
        first = points[0]
        cohorts.append(
            VintageCohort(
                cohort=cohort,
                initial_exposure_ghs=first.exposure_ghs,
                initial_loan_count=first.loan_count,
                points=points,
            )
        )
    return VintageResult(
        cohorts=tuple(cohorts),
        observation_count=sum(1 for obs in observations if obs.months_on_book >= 0),
    )
