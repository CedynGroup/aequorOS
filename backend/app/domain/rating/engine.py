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
PD_FLOOR_FRAC = PD_FLOOR_PCT / HUNDRED  # 0.0003 — the Basel floor as a fraction
_PD_CEIL_FRAC = Decimal("0.999999")  # keep central tendencies strictly inside (0,1)
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
    # The calibrated central tendency this band is built around: the master-scale
    # anchor for TTC, or the Vasicek systematic-conditioned anchor for PIT.
    central_tendency_pct: Decimal
    # Systematic factor Z used for the PIT conditioning (0 for the TTC view).
    systematic_factor: Decimal
    # Diagnostics (reported, not the sole driver): the anchor-centred Bayesian
    # posterior upper quantile, the Pluto-Tasche zero-default most-prudent bound
    # on any real internal pool (``None`` when there is no internal outcome data
    # yet — the honest low-default state), and the margin of conservatism.
    bayesian_upper_pct: Decimal
    pluto_tasche_upper_pct: Decimal | None
    margin_of_conservatism_pct: Decimal
    prior_strength: Decimal
    internal_obligors: int
    internal_defaults: int


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
    # The single sourced master scale: grade -> long-run (TTC, unconditional)
    # central-tendency PD in percent. PIT is DERIVED from this by Vasicek
    # conditioning on the live systematic factor — there is no separate PIT table.
    grade_pd_anchors_pct: Mapping[str, Decimal]
    confidence_level: Decimal
    moc_k_sigma: Decimal
    # Vasicek asset correlation (the sovereign is the systematic factor, §6.1);
    # elevated for the Ghana sovereign-bank nexus. Versioned parameter.
    asset_correlation: Decimal = Decimal("0.24")
    # Bayesian prior strength kappa: the effective number of external
    # observations backing each grade's anchor. Larger -> tighter band. The
    # Stage-1 band width is the anchor-centred prior credible interval; it
    # narrows as real internal default outcomes accrue (Stage 2).
    prior_strength: Decimal = Decimal("25")
    operating_environment_matrix: tuple[tuple[Decimal, Decimal], tuple[Decimal, Decimal]] = (
        (ZERO, ZERO),
        (ZERO, ONE),
    )


@dataclass(frozen=True)
class RatingInputs:
    ratio_values: Mapping[str, Decimal]
    operating_environment_score: Decimal
    sovereign_ceiling: str
    # REAL internal rated-portfolio outcomes per grade (obligors observed and
    # defaults among them). Empty/zero in Stage 1 (no internal default history),
    # which yields a prior-only band; they update the posterior once real
    # outcomes accrue. NOT a synthetic reference population.
    grade_obligors: Mapping[str, int]
    grade_defaults: Mapping[str, int]
    basis: str
    # The live systematic factor Z for PIT conditioning (negative = stressed
    # state -> PIT above TTC). Ignored for the TTC (unconditional) view.
    systematic_factor: Decimal = ZERO
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


def _pd_band(  # noqa: PLR0913 - the governed band parameters, spelled out
    *,
    anchor_frac: Decimal,
    basis: str,
    systematic_factor: Decimal,
    asset_correlation: Decimal,
    prior_strength: Decimal,
    confidence: Decimal,
    moc_k_sigma: Decimal,
    internal_obligors: int,
    internal_defaults: int,
) -> PdBand:
    """Build the published PD band around a calibrated central tendency.

    Design (remediation of the flat-band defect):
    1. **Central tendency.** TTC is the master-scale anchor (long-run,
       unconditional). PIT is that anchor Vasicek-conditioned on the live
       systematic factor ``Z`` (§6.1) — a stressed state (Z < 0) lifts PIT
       above TTC, a benign state lowers it. PIT is therefore *derived*, never a
       second hardcoded table.
    2. **Band width.** An anchor-centred Bayesian posterior: prior
       ``Beta(kappa·c, kappa·(1−c))`` has mean = the central tendency ``c`` and
       strength ``kappa`` = the external evidence behind the grade. With no
       internal default history the band is the prior credible interval (honest
       low-default uncertainty); real internal outcomes update the posterior and
       narrow it. This replaces the old ``max(anchor, raw_pluto_tasche)`` that
       always collapsed to the anchor.
    3. **Conservatism + floor.** upper = posterior upper quantile + k·σ MoC;
       everything floored at the Basel 0.03%.
    Pluto-Tasche is retained as a zero-default *diagnostic* on any real internal
    pool (``None`` until such data exist) — it is not the band driver, so its
    tiny sampling bound can no longer crush the band.
    """
    if prior_strength <= ZERO:
        raise RatingComputationError("Prior strength (kappa) must be positive.")
    if internal_obligors < 0 or not 0 <= internal_defaults <= max(internal_obligors, 0):
        raise RatingComputationError("Internal outcome counts are invalid.")

    anchor = min(max(anchor_frac, PD_FLOOR_FRAC), _PD_CEIL_FRAC)
    if basis == "PIT":
        central = vasicek_conditional_pd(anchor, asset_correlation, systematic_factor)
    else:
        central = anchor
    central = min(max(central, PD_FLOOR_FRAC), _PD_CEIL_FRAC)

    a0 = prior_strength * central
    b0 = prior_strength * (ONE - central)
    alpha = a0 + Decimal(internal_defaults)
    beta = b0 + Decimal(internal_obligors - internal_defaults)
    mean = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + ONE))
    sd = variance.sqrt()

    z_upper = Decimal(str(_normal_ppf(float(confidence))))
    moc = max(moc_k_sigma, ZERO) * sd
    point = max(mean, PD_FLOOR_FRAC)
    lower = min(max(mean - z_upper * sd, PD_FLOOR_FRAC), point)
    bayesian_upper = mean + z_upper * sd
    upper = max(bayesian_upper + moc, point)

    pluto = (
        _pluto_tasche_upper(internal_defaults, internal_obligors, confidence)
        if internal_obligors > 0
        else None
    )

    return PdBand(
        lower_pct=_quantize(lower * HUNDRED, _PCT_Q),
        point_pct=_quantize(point * HUNDRED, _PCT_Q),
        upper_pct=_quantize(upper * HUNDRED, _PCT_Q),
        confidence_level=confidence,
        basis=basis,
        central_tendency_pct=_quantize(central * HUNDRED, _PCT_Q),
        systematic_factor=_quantize(systematic_factor, _SCORE_Q),
        bayesian_upper_pct=_quantize(bayesian_upper * HUNDRED, _PCT_Q),
        pluto_tasche_upper_pct=(None if pluto is None else _quantize(pluto * HUNDRED, _PCT_Q)),
        margin_of_conservatism_pct=_quantize(moc * HUNDRED, _PCT_Q),
        prior_strength=prior_strength,
        internal_obligors=internal_obligors,
        internal_defaults=internal_defaults,
    )


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
        if risk_weighted_assets is None or risk_weighted_assets == ZERO
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
    # Support uplift moves the grade UP (lower index), never above the sovereign
    # ceiling — a distressed sovereign cannot credibly backstop its banks (§4.3).
    issuer_index = max(implied_index - max(inputs.support_uplift_notches, 0), ceiling_index)
    issuer_grade = methodology.grade_order[issuer_index]
    # The band is built around the ISSUER grade's anchor (post ceiling + support),
    # conditioned to PIT/TTC. Internal outcome counts pool this grade and worse
    # (Pluto-Tasche monotonic-ordering assumption); they are zero in Stage 1.
    pooled_grades = methodology.grade_order[issuer_index:]
    pooled_obligors = sum(inputs.grade_obligors.get(grade, 0) for grade in pooled_grades)
    pooled_defaults = sum(inputs.grade_defaults.get(grade, 0) for grade in pooled_grades)
    pd_band = _pd_band(
        anchor_frac=methodology.grade_pd_anchors_pct[issuer_grade] / HUNDRED,
        basis=inputs.basis,
        systematic_factor=inputs.systematic_factor,
        asset_correlation=methodology.asset_correlation,
        prior_strength=methodology.prior_strength,
        confidence=methodology.confidence_level,
        moc_k_sigma=methodology.moc_k_sigma,
        internal_obligors=pooled_obligors,
        internal_defaults=pooled_defaults,
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