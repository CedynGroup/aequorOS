"""SR 11-7 outcomes-analysis metrics for the implied bank-rating / PD model.

Pure functions over predicted PDs (or risk scores) and realized default
outcomes. They implement the discrimination, calibration, and stability
back-tests specified in ``docs/internal/AequorOS_Implied_Rating_PD_Implementation.md``
§9 and required by the SR 11-7 governance section of
``docs/internal/credit_ratings_PD.md`` §6.

The module carries the low-default-portfolio (LDP) honesty requirement of §9.2
into code: calibration and discrimination back-tests have little statistical
power when defaults are near zero, so :func:`validate` refuses to dress a
handful of observations up as a passing result and instead returns an explicit
``insufficient_data`` verdict with a plain-language reason.

Style mirrors ``engine.py``: Decimal arithmetic, frozen dataclasses, explicit
errors, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import erf, log, sqrt

ZERO = Decimal("0")
ONE = Decimal("1")
_SQRT_TWO = sqrt(2.0)
_Q6 = Decimal("0.000001")

# Discrimination interpretation bands (§9.1: AUC > 0.7 acceptable, > 0.8 strong).
AUC_STRONG = Decimal("0.8")
AUC_ACCEPTABLE = Decimal("0.7")

# Stability interpretation bands (§9.3: < 0.10 stable, 0.10-0.25 shift, > 0.25 significant).
PSI_STABLE = Decimal("0.10")
PSI_SHIFT = Decimal("0.25")

# Traffic-light deviation bands (§9.2). One-sided: over-prediction (observed
# below predicted) is conservative and reads green; the flags escalate as the
# observed default rate runs ahead of the predicted PD.
TRAFFIC_AMBER_RATIO = Decimal("1.5")
TRAFFIC_RED_RATIO = Decimal("2.0")

# Rule-of-thumb small-sample guard for the binomial normal approximation.
_SMALL_SAMPLE_EXPECTED = Decimal("5")

# Default LDP power thresholds for :func:`validate`. Conservative and explicit;
# both are parameters so a caller can tighten or relax them per portfolio.
DEFAULT_MIN_OBSERVATIONS = 100
DEFAULT_MIN_DEFAULTS = 5

_HL_MIN_GROUPS = 3  # need groups - 2 >= 1 degree of freedom

_LDP_NOTE = (
    "These tests assume default independence and have low power in low-default "
    "portfolios; treat results as indicative and supplement with benchmark and "
    "expert review (§9.2)."
)


class ValidationError(ValueError):
    """Raised when a validation input is invalid or a metric is undefined."""


@dataclass(frozen=True)
class GradedExposure:
    """One rated obligor: its assigned grade, that grade's predicted PD, and
    whether it defaulted over the observation window (§11 default definition)."""

    grade: str
    predicted_pd: Decimal
    defaulted: bool


@dataclass(frozen=True)
class Discrimination:
    """Rank-ordering quality (§9.1)."""

    auc: Decimal
    accuracy_ratio: Decimal
    strength: str
    defaulters: int
    non_defaulters: int


@dataclass(frozen=True)
class BinomialTestResult:
    """Per-grade calibration test (§9.2)."""

    predicted_pd: Decimal
    obligors: int
    defaults: int
    observed_rate: Decimal
    expected_defaults: Decimal
    z_score: Decimal
    p_value: Decimal
    exact_recommended: bool
    note: str


@dataclass(frozen=True)
class HosmerLemeshowResult:
    """Across-grade calibration test (§9.2)."""

    chi_square: Decimal
    degrees_of_freedom: int
    groups: int
    note: str


@dataclass(frozen=True)
class GradeCalibration:
    """The calibration view for a single grade."""

    grade: str
    predicted_pd: Decimal
    obligors: int
    defaults: int
    binomial: BinomialTestResult
    traffic_light: str


@dataclass(frozen=True)
class Calibration:
    """Whole-portfolio calibration (§9.2)."""

    grades: tuple[GradeCalibration, ...]
    hosmer_lemeshow: HosmerLemeshowResult | None
    note: str


@dataclass(frozen=True)
class PsiResult:
    """Population Stability Index and its interpretation band (§9.3)."""

    psi: Decimal
    interpretation: str


@dataclass(frozen=True)
class DataSufficiency:
    """Whether the portfolio has the power for the §9.2 calibration tests."""

    sufficient: bool
    total_obligors: int
    total_defaults: int
    min_observations: int
    min_defaults: int
    reason: str | None


@dataclass(frozen=True)
class ValidationReport:
    """Structured SR 11-7 outcomes-analysis report (§9)."""

    verdict: str  # "ok" | "insufficient_data"
    sufficiency: DataSufficiency
    discrimination: Discrimination | None
    calibration: Calibration | None
    stability: PsiResult | None
    note: str


def _quantize(value: Decimal, quantum: Decimal = _Q6) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _normal_cdf(value: float) -> float:
    return (1.0 + erf(value / _SQRT_TWO)) / 2.0


def _as_outcome(value: int) -> int:
    outcome = int(value)
    if outcome not in {0, 1}:
        raise ValidationError("Outcomes must be 0 (no default) or 1 (default).")
    return outcome


# --------------------------------------------------------------------------- #
# 9.1 Discrimination                                                          #
# --------------------------------------------------------------------------- #


def roc_auc(scores: Sequence[Decimal], outcomes: Sequence[int]) -> Decimal:
    """Area under the ROC curve (§9.1).

    AUC is the probability that a randomly chosen defaulter is assigned a higher
    risk score than a randomly chosen non-defaulter (ties counted as one half):

        AUC = ( 1 / (n_d * n_nd) ) * [ #{d>nd} + 0.5 * #{d==nd} ]

    Computed in O(n log n) from the Mann-Whitney U statistic with average ranks
    for ties:  AUC = ( R_d - n_d*(n_d+1)/2 ) / ( n_d * n_nd )  where R_d is the
    average-rank sum of the defaulters (ranked by ascending score).

    Degenerate case honesty: with all-same outcomes there is no
    defaulter/non-defaulter pair and AUC is undefined, so this raises rather than
    inventing a 0.5. A perfectly discriminating model scores every defaulter
    above every non-defaulter (AUC = 1.0); a perfectly reversed one gives 0.0; a
    non-informative one sits near 0.5.
    """
    if len(scores) != len(outcomes):
        raise ValidationError("scores and outcomes must have equal length.")
    if not scores:
        raise ValidationError("At least one observation is required for AUC.")
    labels = [_as_outcome(value) for value in outcomes]
    n_d = sum(labels)
    n_nd = len(labels) - n_d
    if n_d == 0 or n_nd == 0:
        raise ValidationError(
            "AUC is undefined when every outcome is identical (no defaulter/"
            "non-defaulter pair to rank)."
        )

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [ZERO] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average_rank = (Decimal(i + 1) + Decimal(j + 1)) / Decimal(2)
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1

    rank_sum_d = sum((ranks[i] for i in range(len(labels)) if labels[i] == 1), ZERO)
    u_statistic = rank_sum_d - Decimal(n_d) * Decimal(n_d + 1) / Decimal(2)
    auc = u_statistic / (Decimal(n_d) * Decimal(n_nd))
    return _quantize(auc)


def accuracy_ratio(auc: Decimal) -> Decimal:
    """Accuracy Ratio / Gini coefficient (§9.1):  AR = 2 * AUC - 1.

    Maps AUC in [0, 1] to [-1, 1]: 1.0 for perfect discrimination, 0.0 for a
    coin flip, -1.0 for perfect reversal.
    """
    if not ZERO <= auc <= ONE:
        raise ValidationError("AUC must lie in [0, 1].")
    return _quantize(Decimal(2) * auc - ONE)


def _discrimination(scores: Sequence[Decimal], outcomes: Sequence[int]) -> Discrimination | None:
    n_d = sum(_as_outcome(value) for value in outcomes)
    n_nd = len(outcomes) - n_d
    if n_d == 0 or n_nd == 0:
        return None
    auc = roc_auc(scores, outcomes)
    if auc >= AUC_STRONG:
        strength = "strong"
    elif auc >= AUC_ACCEPTABLE:
        strength = "acceptable"
    else:
        strength = "weak"
    return Discrimination(auc, accuracy_ratio(auc), strength, n_d, n_nd)


# --------------------------------------------------------------------------- #
# 9.2 Calibration                                                             #
# --------------------------------------------------------------------------- #


def binomial_test(predicted_pd: Decimal, obligors: int, defaults: int) -> BinomialTestResult:
    """Per-grade binomial calibration test (§9.2).

    Under H0 that the true PD equals the predicted p, with k defaults among n
    obligors, the normal-approximation z-statistic is:

        z = ( k - n*p ) / sqrt( n*p*(1 - p) )

    A positive z means more defaults than predicted (PD understated); a negative
    z means fewer (PD overstated / conservative). The two-sided p-value uses the
    standard normal. For small n the normal approximation is poor, so the exact
    binomial should be used instead; ``exact_recommended`` flags that regime.
    """
    if obligors <= 0:
        raise ValidationError("obligors must be positive.")
    if not 0 <= defaults <= obligors:
        raise ValidationError("defaults must be between 0 and obligors.")
    if not ZERO < predicted_pd < ONE:
        raise ValidationError("predicted_pd must be strictly between 0 and 1.")

    n = Decimal(obligors)
    p = predicted_pd
    k = Decimal(defaults)
    expected = n * p
    variance = n * p * (ONE - p)
    z = (k - expected) / variance.sqrt()
    p_value = Decimal(2) * (ONE - Decimal(str(_normal_cdf(abs(float(z))))))
    exact_recommended = expected < _SMALL_SAMPLE_EXPECTED or n * (ONE - p) < _SMALL_SAMPLE_EXPECTED
    note = (
        "Normal approximation; use the exact binomial test for small n "
        "(few expected defaults). " + _LDP_NOTE
    )
    return BinomialTestResult(
        predicted_pd=_quantize(p),
        obligors=obligors,
        defaults=defaults,
        observed_rate=_quantize(k / n),
        expected_defaults=_quantize(expected),
        z_score=_quantize(z),
        p_value=_quantize(min(max(p_value, ZERO), ONE)),
        exact_recommended=exact_recommended,
        note=note,
    )


def hosmer_lemeshow(groups: Sequence[tuple[Decimal, int, int]]) -> HosmerLemeshowResult:
    """Hosmer-Lemeshow across-grade calibration chi-square (§9.2).

    For grades g with predicted PD p_g, obligors n_g, observed defaults O_g:

        chi2 = sum_g ( O_g - n_g*p_g )^2 / ( n_g*p_g*(1 - p_g) )

    Compared against a chi-square distribution with (groups - 2) degrees of
    freedom. At least 3 groups are needed for a positive df.
    """
    if len(groups) < _HL_MIN_GROUPS:
        raise ValidationError(
            f"Hosmer-Lemeshow needs at least {_HL_MIN_GROUPS} groups for a positive df."
        )
    chi_square = ZERO
    for predicted_pd, obligors, defaults in groups:
        if obligors <= 0:
            raise ValidationError("Each group must have positive obligors.")
        if not 0 <= defaults <= obligors:
            raise ValidationError("Each group's defaults must be between 0 and obligors.")
        if not ZERO < predicted_pd < ONE:
            raise ValidationError("Each group's predicted_pd must be strictly between 0 and 1.")
        n = Decimal(obligors)
        expected = n * predicted_pd
        variance = n * predicted_pd * (ONE - predicted_pd)
        chi_square += (Decimal(defaults) - expected) ** 2 / variance
    return HosmerLemeshowResult(
        chi_square=_quantize(chi_square),
        degrees_of_freedom=len(groups) - 2,
        groups=len(groups),
        note="Compare to chi-square with (groups - 2) df. " + _LDP_NOTE,
    )


def traffic_light(
    observed_rate: Decimal,
    predicted_pd: Decimal,
    *,
    amber_ratio: Decimal = TRAFFIC_AMBER_RATIO,
    red_ratio: Decimal = TRAFFIC_RED_RATIO,
) -> str:
    """Traffic-light calibration flag (§9.2): green / amber / red.

    Banding is one-sided on the ratio ``observed_rate / predicted_pd`` because
    under-prediction (realized defaults running ahead of the predicted PD) is the
    risk-relevant failure. Over-prediction is conservative and reads green.

        green   if ratio <= amber_ratio    (default 1.5)
        amber   if ratio <= red_ratio       (default 2.0)
        red     otherwise
    """
    if not ZERO < predicted_pd < ONE:
        raise ValidationError("predicted_pd must be strictly between 0 and 1.")
    if observed_rate < ZERO or observed_rate > ONE:
        raise ValidationError("observed_rate must lie in [0, 1].")
    if not ZERO < amber_ratio <= red_ratio:
        raise ValidationError("Require 0 < amber_ratio <= red_ratio.")
    ratio = observed_rate / predicted_pd
    if ratio <= amber_ratio:
        return "green"
    if ratio <= red_ratio:
        return "amber"
    return "red"


# --------------------------------------------------------------------------- #
# 9.3 Stability                                                               #
# --------------------------------------------------------------------------- #


def population_stability_index(
    expected_props: Sequence[Decimal], actual_props: Sequence[Decimal]
) -> PsiResult:
    """Population Stability Index across score buckets (§9.3).

        PSI = sum_b ( a_b - e_b ) * ln( a_b / e_b )

    where e_b, a_b are the expected (baseline) and actual proportions in bucket
    b. Rule of thumb: < 0.10 stable, 0.10-0.25 minor shift, > 0.25 significant
    shift. Identical distributions give exactly 0. Empty buckets make the log
    undefined, so every proportion must be strictly positive (the caller floors
    empties first).
    """
    if len(expected_props) != len(actual_props):
        raise ValidationError("expected_props and actual_props must have equal length.")
    if not expected_props:
        raise ValidationError("At least one bucket is required for PSI.")
    if abs(sum(expected_props, ZERO) - ONE) > _Q6 or abs(sum(actual_props, ZERO) - ONE) > _Q6:
        raise ValidationError("Each proportion vector must sum to 1.")

    psi = ZERO
    for expected, actual in zip(expected_props, actual_props, strict=True):
        if expected <= ZERO or actual <= ZERO:
            raise ValidationError(
                "PSI proportions must be strictly positive (floor empty buckets)."
            )
        psi += (actual - expected) * Decimal(str(log(float(actual / expected))))

    if psi < PSI_STABLE:
        interpretation = "stable"
    elif psi <= PSI_SHIFT:
        interpretation = "minor_shift"
    else:
        interpretation = "significant_shift"
    return PsiResult(psi=_quantize(psi), interpretation=interpretation)


# --------------------------------------------------------------------------- #
# Entrypoint                                                                  #
# --------------------------------------------------------------------------- #


def _aggregate_grades(
    exposures: Sequence[GradedExposure],
) -> tuple[tuple[str, Decimal, int, int], ...]:
    """Collapse obligor rows into (grade, mean predicted_pd, obligors, defaults),
    ordered by predicted PD then grade for deterministic output."""
    pd_sums: dict[str, Decimal] = {}
    obligor_counts: dict[str, int] = {}
    default_counts: dict[str, int] = {}
    for exposure in exposures:
        grade = exposure.grade
        pd_sums[grade] = pd_sums.get(grade, ZERO) + exposure.predicted_pd
        obligor_counts[grade] = obligor_counts.get(grade, 0) + 1
        default_counts[grade] = default_counts.get(grade, 0) + (1 if exposure.defaulted else 0)
    rows: list[tuple[str, Decimal, int, int]] = []
    for grade, obligors in obligor_counts.items():
        mean_pd = pd_sums[grade] / Decimal(obligors)
        rows.append((grade, mean_pd, obligors, default_counts[grade]))
    rows.sort(key=lambda row: (row[1], row[0]))
    return tuple(rows)


def _calibration(rows: Sequence[tuple[str, Decimal, int, int]]) -> Calibration:
    grade_results: list[GradeCalibration] = []
    hl_groups: list[tuple[Decimal, int, int]] = []
    for grade, predicted_pd, obligors, defaults in rows:
        binomial = binomial_test(predicted_pd, obligors, defaults)
        grade_results.append(
            GradeCalibration(
                grade=grade,
                predicted_pd=_quantize(predicted_pd),
                obligors=obligors,
                defaults=defaults,
                binomial=binomial,
                traffic_light=traffic_light(binomial.observed_rate, predicted_pd),
            )
        )
        hl_groups.append((predicted_pd, obligors, defaults))
    hl = hosmer_lemeshow(hl_groups) if len(hl_groups) >= _HL_MIN_GROUPS else None
    hl_note = (
        "Hosmer-Lemeshow omitted: fewer than "
        f"{_HL_MIN_GROUPS} grades (needs groups - 2 >= 1 df)."
        if hl is None
        else "Hosmer-Lemeshow computed across grades."
    )
    return Calibration(tuple(grade_results), hl, hl_note + " " + _LDP_NOTE)


def validate(
    exposures: Sequence[GradedExposure],
    *,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    min_defaults: int = DEFAULT_MIN_DEFAULTS,
    expected_distribution: Sequence[Decimal] | None = None,
    actual_distribution: Sequence[Decimal] | None = None,
) -> ValidationReport:
    """Run the §9 outcomes-analysis suite over a graded portfolio.

    Discrimination (§9.1), calibration (§9.2), and — when a baseline and current
    score distribution are supplied — stability (§9.3) are reported together.

    Low-default honesty (§9.2): when the portfolio has fewer than
    ``min_observations`` obligors or fewer than ``min_defaults`` realized
    defaults, the calibration and discrimination tests lack the power to mean
    anything, so the report is stamped ``insufficient_data`` with a plain reason
    and those metrics are withheld rather than presented as evidence. Stability
    (a distribution-shift metric that does not depend on outcomes) is still
    reported when its inputs are given.
    """
    if not exposures:
        raise ValidationError("At least one exposure is required to validate.")
    if min_observations < 0 or min_defaults < 0:
        raise ValidationError("min_observations and min_defaults must be non-negative.")
    for exposure in exposures:
        if not ZERO < exposure.predicted_pd < ONE:
            raise ValidationError(
                f"Grade {exposure.grade}: predicted_pd must be strictly between 0 and 1."
            )

    total_obligors = len(exposures)
    total_defaults = sum(1 for exposure in exposures if exposure.defaulted)

    stability: PsiResult | None = None
    if expected_distribution is not None and actual_distribution is not None:
        stability = population_stability_index(expected_distribution, actual_distribution)

    sufficient = total_obligors >= min_observations and total_defaults >= min_defaults
    if not sufficient:
        reason = (
            f"Only {total_defaults} default(s) across {total_obligors} obligor(s); "
            f"validation requires at least {min_defaults} defaults and "
            f"{min_observations} observations for the calibration and discrimination "
            "tests to have power in a low-default portfolio (§9.2). Discrimination "
            "and calibration are withheld; rely on benchmark and expert review."
        )
        return ValidationReport(
            verdict="insufficient_data",
            sufficiency=DataSufficiency(
                sufficient=False,
                total_obligors=total_obligors,
                total_defaults=total_defaults,
                min_observations=min_observations,
                min_defaults=min_defaults,
                reason=reason,
            ),
            discrimination=None,
            calibration=None,
            stability=stability,
            note=reason,
        )

    scores = [exposure.predicted_pd for exposure in exposures]
    outcomes = [1 if exposure.defaulted else 0 for exposure in exposures]
    discrimination = _discrimination(scores, outcomes)
    calibration = _calibration(_aggregate_grades(exposures))
    return ValidationReport(
        verdict="ok",
        sufficiency=DataSufficiency(
            sufficient=True,
            total_obligors=total_obligors,
            total_defaults=total_defaults,
            min_observations=min_observations,
            min_defaults=min_defaults,
            reason=None,
        ),
        discrimination=discrimination,
        calibration=calibration,
        stability=stability,
        note=(
            "SR 11-7 outcomes analysis (§9). Calibration back-tests retain limited "
            "power in low-default portfolios (§9.2)."
        ),
    )


__all__ = [
    "BinomialTestResult",
    "Calibration",
    "DataSufficiency",
    "Discrimination",
    "GradeCalibration",
    "GradedExposure",
    "HosmerLemeshowResult",
    "PsiResult",
    "ValidationError",
    "ValidationReport",
    "accuracy_ratio",
    "binomial_test",
    "hosmer_lemeshow",
    "population_stability_index",
    "roc_auc",
    "traffic_light",
    "validate",
]
