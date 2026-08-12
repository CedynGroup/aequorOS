"""Transparent Stage-1 bank scorecard, rating, and low-default PD model.

All inputs and methodology parameters are explicit so a persisted rating run
can be reproduced from its immutable input snapshot and approved version.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import comb, erf, exp, log, sqrt

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
PD_FLOOR_PCT = Decimal("0.03")
_SCORE_Q = Decimal("0.000001")
_PCT_Q = Decimal("0.000001")
_SQRT_TWO = sqrt(2.0)


class RatingComputationError(ValueError):
    """Raised when a rating methodology parameter set or input is invalid."""


@dataclass(frozen=True)
class RatioDefinition:
    code: str
    component: str
    weight: Decimal
    direction: str
    floor: Decimal
    cap: Decimal
    transform: str = "piecewise_linear"
    steepness: Decimal | None = None
    midpoint: Decimal | None = None


@dataclass(frozen=True)
class ComponentDefinition:
    code: str
    weight: Decimal


@dataclass(frozen=True)
class RatioScore:
    code: str
    value: Decimal
    raw_score: Decimal
    adjusted_score: Decimal
    weight: Decimal


@dataclass(frozen=True)
class ComponentScore:
    code: str
    score: Decimal
    weight: Decimal
    contribution: Decimal
    ratios: tuple[RatioScore, ...]


@dataclass(frozen=True)
class PdBand:
    lower_pct: Decimal
    point_pct: Decimal
    upper_pct: Decimal
    confidence_level: Decimal
    basis: str
    pluto_tasche_upper_pct: Decimal
    bayesian_upper_pct: Decimal
    margin_of_conservatism_pct: Decimal


@dataclass(frozen=True)
class SovereignStressResult:
    sovereign_loss: Decimal
    post_stress_capital: Decimal
    post_stress_capital_ratio_pct: Decimal | None
    eligible: bool


@dataclass(frozen=True)
class RatingResult:
    standalone_score: Decimal
    standalone_grade: str
    implied_grade: str
    issuer_grade: str
    sovereign_ceiling: str
    ceiling_applied: bool
    support_uplift_notches: int
    component_scores: tuple[ComponentScore, ...]
    pd_band: PdBand


@dataclass(frozen=True)
class RatingMethodology:
    ratio_definitions: tuple[RatioDefinition, ...]
    components: tuple[ComponentDefinition, ...]
    grade_cutpoints: Mapping[str, Decimal]
    grade_order: tuple[str, ...]
    grade_pd_anchors_pct: Mapping[str, Decimal]
    confidence_level: Decimal
    moc_k_sigma: Decimal
    operating_environment_matrix: tuple[tuple[Decimal, Decimal], tuple[Decimal, Decimal]] = (
        (ZERO, ZERO),
        (ZERO, ONE),
    )
    bayesian_prior_alpha: Decimal = ONE
    bayesian_prior_beta: Decimal = Decimal("99")


@dataclass(frozen=True)
class RatingInputs:
    ratio_values: Mapping[str, Decimal]
    operating_environment_score: Decimal
    sovereign_ceiling: str
    grade_obligors: Mapping[str, int]
    grade_defaults: Mapping[str, int]
    basis: str
    support_uplift_notches: int = 0


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _validate_unit_interval(value: Decimal, label: str) -> None:
    if not ZERO <= value <= ONE:
        raise RatingComputationError(f"{label} must be between 0 and 1.")


def _normal_cdf(value: float) -> float:
    return (1.0 + erf(value / _SQRT_TWO)) / 2.0


def _normal_ppf(probability: float) -> float:
    """Acklam's rational approximation for the inverse standard-normal CDF."""
    if not 0.0 < probability < 1.0:
        raise RatingComputationError(
            "Normal probability must be strictly between 0 and 1."
        )
    a = (
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.357751867269,
        -30.66479806614716,
        2.506628277459239,
    )
    b = (
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    )
    c = (
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    )
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416)
    low, high = 0.02425, 0.97575
    if probability < low:
        q = sqrt(-2.0 * log(probability))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
        return numerator / denominator
    if probability > high:
        q = sqrt(-2.0 * log(1.0 - probability))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
        return -numerator / denominator
    q = probability - 0.5
    r = q * q
    numerator = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    denominator = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r) + 1.0
    return numerator / denominator


def ratio_subscore(value: Decimal, definition: RatioDefinition) -> Decimal:
    """Apply a bounded monotonic transform; one is always strongest."""
    if definition.direction not in {"higher_is_better", "lower_is_better"}:
        raise RatingComputationError(f"Unknown direction for {definition.code}.")
    if definition.floor >= definition.cap:
        raise RatingComputationError(f"{definition.code} requires floor < cap.")
    if definition.transform == "piecewise_linear":
        numerator = (
            value - definition.floor
            if definition.direction == "higher_is_better"
            else definition.cap - value
        )
        score = numerator / (definition.cap - definition.floor)
    elif definition.transform == "logistic":
        if definition.steepness is None or definition.midpoint is None:
            raise RatingComputationError(
                f"Logistic transform for {definition.code} needs steepness and midpoint."
            )
        sign = ONE if definition.direction == "higher_is_better" else -ONE
        exponent = -sign * definition.steepness * (value - definition.midpoint)
        score = ONE / (ONE + Decimal(str(exp(float(exponent)))))
    else:
        raise RatingComputationError(f"Unknown transform for {definition.code}.")
    return _quantize(min(max(score, ZERO), ONE), _SCORE_Q)


def adjusted_subscore(
    raw_score: Decimal,
    operating_environment_score: Decimal,
    matrix: tuple[tuple[Decimal, Decimal], tuple[Decimal, Decimal]],
) -> Decimal:
    """Bilinearly interpolate a versioned ratio-score/environment matrix."""
    _validate_unit_interval(raw_score, "Raw ratio score")
    _validate_unit_interval(
        operating_environment_score, "Operating-environment score"
    )
    if len(matrix) != 2 or any(len(row) != 2 for row in matrix):
        raise RatingComputationError("Operating-environment matrix must be two by two.")
    low_ratio_low_environment, low_ratio_high_environment = matrix[0]
    high_ratio_low_environment, high_ratio_high_environment = matrix[1]
    for value in (
        low_ratio_low_environment,
        low_ratio_high_environment,
        high_ratio_low_environment,
        high_ratio_high_environment,
    ):
        _validate_unit_interval(value, "Operating-environment matrix value")
    ratio_low = (
        low_ratio_low_environment * (ONE - operating_environment_score)
        + low_ratio_high_environment * operating_environment_score
    )
    ratio_high = (
        high_ratio_low_environment * (ONE - operating_environment_score)
        + high_ratio_high_environment * operating_environment_score
    )
    return _quantize(ratio_low * (ONE - raw_score) + ratio_high * raw_score, _SCORE_Q)


def score_components(
    ratio_values: Mapping[str, Decimal],
    definitions: Sequence[RatioDefinition],
    components: Sequence[ComponentDefinition],
    operating_environment_score: Decimal,
    operating_environment_matrix: tuple[tuple[Decimal, Decimal], tuple[Decimal, Decimal]] = (
        (ZERO, ZERO),
        (ZERO, ONE),
    ),
) -> tuple[ComponentScore, ...]:
    component_by_code = {component.code: component for component in components}
    component_weight_total = sum((item.weight for item in components), ZERO)
    if component_weight_total <= ZERO:
        raise RatingComputationError("Component weights must have a positive total.")
    by_component: dict[str, list[RatioScore]] = {
        component.code: [] for component in components
    }
    for definition in definitions:
        if definition.component not in component_by_code:
            raise RatingComputationError(
                f"Ratio {definition.code} references an unknown component."
            )
        if definition.code not in ratio_values:
            raise RatingComputationError(f"Missing required rating ratio: {definition.code}.")
        raw = ratio_subscore(ratio_values[definition.code], definition)
        by_component[definition.component].append(
            RatioScore(
                definition.code,
                ratio_values[definition.code],
                raw,
                adjusted_subscore(
                    raw, operating_environment_score, operating_environment_matrix
                ),
                definition.weight,
            )
        )
    results: list[ComponentScore] = []
    for component in components:
        ratios = tuple(by_component[component.code])
        if not ratios:
            raise RatingComputationError(f"Component {component.code} has no ratios.")
        if sum((ratio.weight for ratio in ratios), ZERO) != ONE:
            raise RatingComputationError(f"Ratio weights for {component.code} must sum to 1.")
        score = sum((ratio.adjusted_score * ratio.weight for ratio in ratios), ZERO)
        normalized_weight = component.weight / component_weight_total
        results.append(
            ComponentScore(
                component.code,
                _quantize(score, _SCORE_Q),
                _quantize(normalized_weight, _SCORE_Q),
                _quantize(score * normalized_weight, _SCORE_Q),
                ratios,
            )
        )
    return tuple(results)


def grade_for_score(
    score: Decimal, cutpoints: Mapping[str, Decimal], grade_order: Sequence[str]
) -> str:
    _validate_unit_interval(score, "Standalone score")
    if tuple(cutpoints) != tuple(grade_order):
        raise RatingComputationError("Grade cutpoints must be ordered from strongest to weakest.")
    last = ONE
    for grade in grade_order:
        cutpoint = cutpoints[grade]
        if cutpoint > last or not ZERO <= cutpoint <= ONE:
            raise RatingComputationError("Grade cutpoints must be descending within [0, 1].")
        if score >= cutpoint:
            return grade
        last = cutpoint
    return grade_order[-1]


def _grade_index(grade: str, grade_order: Sequence[str]) -> int:
    try:
        return grade_order.index(grade)
    except ValueError as exc:
        raise RatingComputationError(f"Unknown master-scale grade: {grade}.") from exc


def _pluto_tasche_upper(defaults: int, obligors: int, confidence: Decimal) -> Decimal:
    if obligors <= 0 or not 0 <= defaults <= obligors:
        raise RatingComputationError(
            "Defaults must be between zero and obligors, with at least one obligor."
        )
    _validate_unit_interval(confidence, "Confidence level")
    if confidence in {ZERO, ONE}:
        raise RatingComputationError("Confidence level must be strictly between 0 and 1.")
    if defaults == 0:
        return ONE - (ONE - confidence) ** (ONE / Decimal(obligors))
    target = ONE - confidence
    low, high = 0.0, 1.0
    for _ in range(100):
        probability = (low + high) / 2.0
        tail = sum(
            comb(obligors, observed)
            * probability**observed
            * (1 - probability) ** (obligors - observed)
            for observed in range(defaults + 1)
        )
        if tail > float(target):
            low = probability
        else:
            high = probability
    return Decimal(str((low + high) / 2.0))


def _beta_posterior_upper(
    defaults: int,
    obligors: int,
    confidence: Decimal,
    prior_alpha: Decimal,
    prior_beta: Decimal,
) -> Decimal:
    """Conservative beta-binomial approximation using posterior mean + normal quantile."""
    if prior_alpha <= ZERO or prior_beta <= ZERO:
        raise RatingComputationError("Bayesian prior parameters must be positive.")
    alpha, beta = prior_alpha + defaults, prior_beta + obligors - defaults
    mean = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + ONE))
    return min(ONE, mean + Decimal(str(_normal_ppf(float(confidence)))) * variance.sqrt())


def vasicek_conditional_pd(
    pd: Decimal, asset_correlation: Decimal, systematic_factor: Decimal
) -> Decimal:
    _validate_unit_interval(asset_correlation, "Asset correlation")
    if asset_correlation in {ZERO, ONE}:
        raise RatingComputationError("Asset correlation must be strictly between 0 and 1.")
    if not ZERO < pd < ONE:
        raise RatingComputationError(
            "PD must be strictly between 0 and 1 for Vasicek conditioning."
        )
    conditional = _normal_cdf(
        (_normal_ppf(float(pd)) - sqrt(float(asset_correlation)) * float(systematic_factor))
        / sqrt(1.0 - float(asset_correlation))
    )
    return Decimal(str(conditional))


def ddep_stress(
    sovereign_holdings: Decimal,
    haircut_pct: Decimal,
    capital: Decimal,
    risk_weighted_assets: Decimal | None = None,
) -> SovereignStressResult:
    if sovereign_holdings < ZERO or capital < ZERO or not ZERO <= haircut_pct <= HUNDRED:
        raise RatingComputationError("Sovereign holdings, capital, and haircut are invalid.")
    loss = sovereign_holdings * haircut_pct / HUNDRED
    post_stress_capital = capital - loss
    ratio = (
        None
        if risk_weighted_assets in {None, ZERO}
        else post_stress_capital / risk_weighted_assets * HUNDRED
    )
    return SovereignStressResult(
        _quantize(loss, _PCT_Q),
        _quantize(post_stress_capital, _PCT_Q),
        None if ratio is None else _quantize(ratio, _PCT_Q),
        post_stress_capital > ZERO,
    )


def compute_rating(
    inputs: RatingInputs,
    methodology: RatingMethodology,
) -> RatingResult:
    if inputs.basis not in {"PIT", "TTC"}:
        raise RatingComputationError("PD basis must be PIT or TTC.")
    component_scores = score_components(
        inputs.ratio_values,
        methodology.ratio_definitions,
        methodology.components,
        inputs.operating_environment_score,
        methodology.operating_environment_matrix,
    )
    standalone_score = _quantize(
        sum((component.contribution for component in component_scores), ZERO), _SCORE_Q
    )
    standalone_grade = grade_for_score(
        standalone_score, methodology.grade_cutpoints, methodology.grade_order
    )
    standalone_index = _grade_index(standalone_grade, methodology.grade_order)
    ceiling_index = _grade_index(inputs.sovereign_ceiling, methodology.grade_order)
    implied_index = max(standalone_index, ceiling_index)
    implied_grade = methodology.grade_order[implied_index]
    issuer_grade = methodology.grade_order[
        max(implied_index - max(inputs.support_uplift_notches, 0), ceiling_index)
    ]
    pooled_grades = methodology.grade_order[implied_index:]
    pooled_obligors = sum(inputs.grade_obligors.get(grade, 0) for grade in pooled_grades)
    pooled_defaults = sum(inputs.grade_defaults.get(grade, 0) for grade in pooled_grades)
    pluto = _pluto_tasche_upper(
        pooled_defaults, pooled_obligors, methodology.confidence_level
    )
    bayesian = _beta_posterior_upper(
        pooled_defaults,
        pooled_obligors,
        methodology.confidence_level,
        methodology.bayesian_prior_alpha,
        methodology.bayesian_prior_beta,
    )
    point = Decimal(pooled_defaults) / Decimal(pooled_obligors)
    estimate = max(pluto, bayesian)
    sigma = (estimate * (ONE - estimate) / Decimal(pooled_obligors)).sqrt()
    moc = max(methodology.moc_k_sigma, ZERO) * sigma
    anchor = methodology.grade_pd_anchors_pct[issuer_grade]
    lower = max(anchor, PD_FLOOR_PCT)
    point_pct = max(point * HUNDRED, lower)
    upper = max((estimate + moc) * HUNDRED, point_pct)
    pd_band = PdBand(
        _quantize(lower, _PCT_Q),
        _quantize(point_pct, _PCT_Q),
        _quantize(upper, _PCT_Q),
        methodology.confidence_level,
        inputs.basis,
        _quantize(pluto * HUNDRED, _PCT_Q),
        _quantize(bayesian * HUNDRED, _PCT_Q),
        _quantize(moc * HUNDRED, _PCT_Q),
    )
    return RatingResult(
        standalone_score,
        standalone_grade,
        implied_grade,
        issuer_grade,
        inputs.sovereign_ceiling,
        implied_grade != standalone_grade,
        max(inputs.support_uplift_notches, 0),
        component_scores,
        pd_band,
    )