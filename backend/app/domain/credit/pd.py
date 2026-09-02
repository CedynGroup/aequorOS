"""Migration-implied probability of default (pure; credit PR-8 — ADVISORY).

Estimates a 12-month PD per performing grade (and optionally per product
segment) from observed monthly grade→NPL transitions: the pooled monthly
default hazard ``h = defaulted loan-months ÷ performing loan-months`` is
annualised as ``PD₁₂ = 1 − (1 − h)¹²``. Transparent, evidence-based, and
honest about its thinness — every estimate carries its observation window,
loan-month count and observed default count, and a grade below the
observation floor (or with zero observed defaults) reports "not estimable"
with the reason rather than a number that looks like knowledge.

**ADVISORY.** These figures carry no regulatory authority and no back-testing
evidence yet. They exist to build the evidence base — the platform's standing
rule (the SDI scorecard releases no PD for exactly this reason) is that a PD
becomes releasable through validation, not through arithmetic. They are
surfaced as SUGGESTIONS the Board may adopt into the ECL assumption register
through the ordinary approver-gated path; nothing here writes to any register.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")

#: Below this many performing loan-months, a hazard is noise, not evidence.
DEFAULT_MIN_LOAN_MONTHS = 30


@dataclass(frozen=True)
class TransitionObservation:
    """One loan over one month: its opening grade and whether it ENTERED NPL."""

    grade: str
    segment: str | None
    defaulted: bool


@dataclass(frozen=True)
class PdEstimate:
    grade: str
    segment: str | None
    loan_months: int
    defaults_observed: int
    monthly_hazard_pct: Decimal | None
    pd_12m_pct: Decimal | None
    #: None = estimated; otherwise the reason no figure is released.
    not_estimable_reason: str | None


@dataclass(frozen=True)
class PdResult:
    estimates: tuple[PdEstimate, ...]
    total_loan_months: int

    def for_grade(self, grade: str, segment: str | None = None) -> PdEstimate | None:
        return next(
            (e for e in self.estimates if e.grade == grade and e.segment == segment), None
        )


def _annualise(hazard: Decimal) -> Decimal:
    survival = _ONE - hazard
    compounded = _ONE
    for _ in range(12):
        compounded *= survival
    return ((_ONE - compounded) * _HUNDRED).quantize(_PCT_Q)


def estimate_pd(
    observations: list[TransitionObservation] | tuple[TransitionObservation, ...],
    *,
    min_loan_months: int = DEFAULT_MIN_LOAN_MONTHS,
) -> PdResult:
    """Pooled monthly hazards per (grade, segment), annualised to 12 months.

    Only PERFORMING opening grades belong here — a loan already non-performing
    has no PD to estimate, and the caller must not feed it. A cell below
    ``min_loan_months`` or with zero observed defaults reports not-estimable:
    zero defaults over a thin window is absence of evidence, and releasing
    0.00% would present that absence as safety.
    """
    cells: dict[tuple[str, str | None], tuple[int, int]] = {}
    for obs in observations:
        months, defaults = cells.get((obs.grade, obs.segment), (0, 0))
        cells[(obs.grade, obs.segment)] = (months + 1, defaults + (1 if obs.defaulted else 0))

    estimates: list[PdEstimate] = []
    for (grade, segment), (months, defaults) in sorted(
        cells.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        if months < min_loan_months:
            estimates.append(
                PdEstimate(
                    grade=grade,
                    segment=segment,
                    loan_months=months,
                    defaults_observed=defaults,
                    monthly_hazard_pct=None,
                    pd_12m_pct=None,
                    not_estimable_reason=(
                        f"{months} performing loan-months observed; at least "
                        f"{min_loan_months} are needed before a hazard is evidence."
                    ),
                )
            )
            continue
        if defaults == 0:
            estimates.append(
                PdEstimate(
                    grade=grade,
                    segment=segment,
                    loan_months=months,
                    defaults_observed=0,
                    monthly_hazard_pct=None,
                    pd_12m_pct=None,
                    not_estimable_reason=(
                        f"No defaults observed in {months} loan-months. Zero observed "
                        "defaults is absence of evidence, not a 0.00% PD."
                    ),
                )
            )
            continue
        hazard = Decimal(defaults) / Decimal(months)
        estimates.append(
            PdEstimate(
                grade=grade,
                segment=segment,
                loan_months=months,
                defaults_observed=defaults,
                monthly_hazard_pct=(hazard * _HUNDRED).quantize(_PCT_Q),
                pd_12m_pct=_annualise(hazard),
                not_estimable_reason=None,
            )
        )
    return PdResult(estimates=tuple(estimates), total_loan_months=len(observations))
