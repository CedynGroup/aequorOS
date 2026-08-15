"""Operating-Environment score — a BICRA-style jurisdiction assessment (pure).

Spec: ``docs/internal/operating_environment_score.md``. This module is the
pure quant core: immutable inputs (economic + industry sub-factor observable
values, the one explicit judgment sub-score, and the sovereign grade) map,
through a **versioned threshold/weight parameter object**, to two pillar
scores, a composite risk, a ``[0, 1]`` strength, and a sovereign-governor cap
— returning an immutable result with the full breakdown and a value-based
``input_digest``. No I/O; nothing volatile ever enters the digest.

Every threshold table, weight, ``RISK_MIN/MAX`` and governor value is a
documented **calibration placeholder** pending independent validation (spec
Non-goals / §8.2), exactly like the PD master scale in
``app/domain/rating/engine.py`` — the shape and provenance are load-bearing,
the numbers are not yet.

Design mirrors ``engine.py``: pure-``Decimal`` arithmetic, frozen dataclasses,
and an ``input_digest`` reused from ``app.domain.curves.objects`` so two
computations over identical inputs are provably identical.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.domain.curves.objects import canonical_input_digest

__all__ = [
    "DEFAULT_PARAMETERS",
    "InputDefinition",
    "InputScore",
    "OperatingEnvironmentError",
    "OperatingEnvironmentInputs",
    "OperatingEnvironmentParameters",
    "OperatingEnvironmentResult",
    "PillarDefinition",
    "PillarScore",
    "SubFactorDefinition",
    "SubFactorScore",
    "ThresholdTable",
    "compute_operating_environment",
    "normalize_sovereign_category",
]

ZERO = Decimal("0")
ONE = Decimal("1")
_Q6 = Decimal("0.000001")

# The nine sovereign-rating CATEGORIES the governor + sovereign-risk tables are
# keyed by (agency notches collapse into these; §governance sovereign rule).
SOVEREIGN_CATEGORIES: tuple[str, ...] = (
    "aaa",
    "aa",
    "a",
    "bbb",
    "bb",
    "b",
    "ccc",
    "cc",
    "c",
)

type InputKind = Literal["threshold", "judgment", "sovereign"]


class OperatingEnvironmentError(ValueError):
    """An operating-environment parameter set or input is invalid."""


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_Q6, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Versioned parameter object (threshold tables, weights, governor).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdTable:
    """A versioned value → 1..6 risk sub-score map for one observable input.

    Direction is baked into the ``scores`` sequence: ``boundaries`` is strictly
    ascending, and a value is scored by the FIRST band whose upper bound it
    falls strictly under (``value < boundary``); values at or above the last
    boundary take ``scores[-1]``. ``scores`` therefore has one more entry than
    ``boundaries``. A "lower-is-better" input carries ascending scores (higher
    value → higher risk); a "higher-is-better" input carries descending scores.
    """

    boundaries: tuple[Decimal, ...]
    scores: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.scores) != len(self.boundaries) + 1:
            raise OperatingEnvironmentError(
                "ThresholdTable.scores must have exactly one more entry than boundaries."
            )
        if not self.boundaries:
            raise OperatingEnvironmentError("ThresholdTable needs at least one boundary.")
        for left, right in zip(self.boundaries, self.boundaries[1:], strict=False):
            if right <= left:
                raise OperatingEnvironmentError("ThresholdTable boundaries must ascend strictly.")

    def score_for(self, value: Decimal) -> int:
        for boundary, score in zip(self.boundaries, self.scores, strict=False):
            if value < boundary:
                return score
        return self.scores[-1]


@dataclass(frozen=True)
class InputDefinition:
    """One observable input of a sub-factor, with its within-sub-factor weight.

    ``kind`` selects where the 1..6 sub-score comes from:
      * ``threshold`` — the analyst/auto-pulled numeric value through ``table``;
      * ``judgment``  — a directly-supplied 1..6 judgment sub-score (the one
        explicit judgment, spec §Pillar 2 institutional framework);
      * ``sovereign`` — the sovereign category through the parameters'
        ``sovereign_risk_table`` (no per-input table).
    """

    code: str
    weight: Decimal
    kind: InputKind
    table: ThresholdTable | None = None

    def __post_init__(self) -> None:
        if self.kind == "threshold" and self.table is None:
            raise OperatingEnvironmentError(
                f"threshold input {self.code!r} requires a ThresholdTable."
            )
        if self.kind != "threshold" and self.table is not None:
            raise OperatingEnvironmentError(
                f"{self.kind} input {self.code!r} must not carry a ThresholdTable."
            )


@dataclass(frozen=True)
class SubFactorDefinition:
    code: str
    weight: Decimal
    inputs: tuple[InputDefinition, ...]


@dataclass(frozen=True)
class PillarDefinition:
    code: str
    weight: Decimal
    sub_factors: tuple[SubFactorDefinition, ...]


@dataclass(frozen=True)
class OperatingEnvironmentParameters:
    """The whole versioned parameter set — one immutable object, one version.

    Placeholder calibration: the numbers below are documented starting points,
    not validated calibrations (spec Non-goals). ``version`` travels into the
    ``input_digest`` and the persisted assessment so a stored score is
    reproducible under the exact parameters that produced it.
    """

    version: str
    parameter_status: str
    pillars: tuple[PillarDefinition, ...]
    sovereign_risk_table: Mapping[str, int]
    sovereign_governor: Mapping[str, Decimal]
    risk_min: Decimal = ONE
    risk_max: Decimal = Decimal("6")


# ---------------------------------------------------------------------------
# Inputs + result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatingEnvironmentInputs:
    """The observed inputs for one jurisdiction on one COB date.

    ``observations`` carries every ``threshold`` input value keyed by input
    code; ``judgments`` carries the ``judgment`` sub-scores (1..6);
    ``sovereign_category`` is one of :data:`SOVEREIGN_CATEGORIES` (the service
    reduces a published agency rating to it via
    :func:`normalize_sovereign_category`).
    """

    observations: Mapping[str, Decimal]
    judgments: Mapping[str, int]
    sovereign_category: str


@dataclass(frozen=True)
class InputScore:
    code: str
    kind: InputKind
    value: Decimal
    sub_score: int
    weight: Decimal


@dataclass(frozen=True)
class SubFactorScore:
    code: str
    pillar: str
    score: Decimal
    weight: Decimal
    inputs: tuple[InputScore, ...]


@dataclass(frozen=True)
class PillarScore:
    code: str
    score: Decimal
    weight: Decimal
    sub_factors: tuple[SubFactorScore, ...]


@dataclass(frozen=True)
class OperatingEnvironmentResult:
    """One immutable BICRA computation: full breakdown + value-based digest."""

    score: Decimal
    strength_raw: Decimal
    composite_risk: Decimal
    pillars: tuple[PillarScore, ...]
    sovereign_category: str
    governor_cap: Decimal
    governor_applied: bool
    risk_min: Decimal
    risk_max: Decimal
    parameters_version: str
    input_digest: str


# ---------------------------------------------------------------------------
# Sovereign category normalisation (pure).
# ---------------------------------------------------------------------------

_MOODY_TO_CATEGORY: Mapping[str, str] = {
    "aaa": "aaa",
    "aa1": "aa",
    "aa2": "aa",
    "aa3": "aa",
    "a1": "a",
    "a2": "a",
    "a3": "a",
    "baa1": "bbb",
    "baa2": "bbb",
    "baa3": "bbb",
    "ba1": "bb",
    "ba2": "bb",
    "ba3": "bb",
    "b1": "b",
    "b2": "b",
    "b3": "b",
    "caa1": "ccc",
    "caa2": "ccc",
    "caa3": "ccc",
    "ca": "cc",
    "c": "c",
}


def normalize_sovereign_category(rating: str) -> str:
    """Reduce a published agency rating to one of :data:`SOVEREIGN_CATEGORIES`.

    Handles both S&P/Fitch notation (``CCC+``, ``B-``, ``BBB``) and Moody's
    (``Caa2``, ``Ba3``, ``B1``). Notches (+/-, 1/2/3) collapse into the letter
    category — the governor and sovereign-risk tables key on category, not on
    the individual notch (a jurisdiction-level input does not need notch
    resolution). Raises when the string maps to no known category.
    """
    normalized = rating.strip().lower().replace("−", "-").replace(" ", "")
    if not normalized:
        raise OperatingEnvironmentError("Sovereign rating is empty.")
    moody = _MOODY_TO_CATEGORY.get(normalized)
    if moody is not None:
        return moody
    base = normalized.rstrip("+-")
    if base in SOVEREIGN_CATEGORIES:
        return base
    if base in ("d", "sd", "rd"):  # default states: floor at the weakest category
        return "c"
    raise OperatingEnvironmentError(
        f"Sovereign rating {rating!r} cannot be mapped to an operating-environment category."
    )


# ---------------------------------------------------------------------------
# The computation.
# ---------------------------------------------------------------------------


def _input_sub_score(
    definition: InputDefinition,
    inputs: OperatingEnvironmentInputs,
    parameters: OperatingEnvironmentParameters,
) -> tuple[int, Decimal]:
    """Return (sub_score, observed_value) for one input definition."""
    if definition.kind == "threshold":
        if definition.code not in inputs.observations:
            raise OperatingEnvironmentError(
                f"Missing observation for threshold input {definition.code!r}."
            )
        value = inputs.observations[definition.code]
        assert definition.table is not None  # guaranteed by InputDefinition.__post_init__
        return definition.table.score_for(value), value
    if definition.kind == "judgment":
        if definition.code not in inputs.judgments:
            raise OperatingEnvironmentError(
                f"Missing judgment sub-score for input {definition.code!r}."
            )
        score = int(inputs.judgments[definition.code])
        if not parameters.risk_min <= Decimal(score) <= parameters.risk_max:
            raise OperatingEnvironmentError(
                f"Judgment sub-score for {definition.code!r} must be in "
                f"[{parameters.risk_min}, {parameters.risk_max}]."
            )
        return score, Decimal(score)
    # sovereign
    score = parameters.sovereign_risk_table.get(inputs.sovereign_category, -1)
    if score < 0:
        raise OperatingEnvironmentError(
            f"sovereign_risk_table has no entry for category {inputs.sovereign_category!r}."
        )
    return score, Decimal(score)


def _sub_factor_score(
    sub_factor: SubFactorDefinition,
    pillar_code: str,
    inputs: OperatingEnvironmentInputs,
    parameters: OperatingEnvironmentParameters,
) -> SubFactorScore:
    if not sub_factor.inputs:
        raise OperatingEnvironmentError(f"Sub-factor {sub_factor.code!r} has no inputs.")
    weight_total = sum((item.weight for item in sub_factor.inputs), ZERO)
    if weight_total != ONE:
        raise OperatingEnvironmentError(
            f"Input weights for sub-factor {sub_factor.code!r} must sum to 1 (got {weight_total})."
        )
    scored: list[InputScore] = []
    total = ZERO
    for definition in sub_factor.inputs:
        sub_score, value = _input_sub_score(definition, inputs, parameters)
        scored.append(
            InputScore(
                code=definition.code,
                kind=definition.kind,
                value=value,
                sub_score=sub_score,
                weight=definition.weight,
            )
        )
        total += Decimal(sub_score) * definition.weight
    return SubFactorScore(
        code=sub_factor.code,
        pillar=pillar_code,
        score=_quantize(total),
        weight=sub_factor.weight,
        inputs=tuple(scored),
    )


def _pillar_score(
    pillar: PillarDefinition,
    inputs: OperatingEnvironmentInputs,
    parameters: OperatingEnvironmentParameters,
) -> PillarScore:
    if not pillar.sub_factors:
        raise OperatingEnvironmentError(f"Pillar {pillar.code!r} has no sub-factors.")
    weight_total = sum((sf.weight for sf in pillar.sub_factors), ZERO)
    if weight_total != ONE:
        raise OperatingEnvironmentError(
            f"Sub-factor weights for pillar {pillar.code!r} must sum to 1 (got {weight_total})."
        )
    scored = tuple(
        _sub_factor_score(sub_factor, pillar.code, inputs, parameters)
        for sub_factor in pillar.sub_factors
    )
    total = sum((sf.score * sf.weight for sf in scored), ZERO)
    return PillarScore(
        code=pillar.code,
        score=_quantize(total),
        weight=pillar.weight,
        sub_factors=scored,
    )


def _digest_payload(
    inputs: OperatingEnvironmentInputs, parameters: OperatingEnvironmentParameters
) -> dict[str, object]:
    """The value-based digest envelope: what went IN, never the outputs.

    Decimals serialise as strings and judgment scores as ints so the payload
    is JSON-safe and deterministic (``canonical_input_digest`` sorts keys).
    """
    return {
        "parameters_version": parameters.version,
        "observations": {code: str(value) for code, value in inputs.observations.items()},
        "judgments": {code: int(value) for code, value in inputs.judgments.items()},
        "sovereign_category": inputs.sovereign_category,
    }


def compute_operating_environment(
    inputs: OperatingEnvironmentInputs,
    parameters: OperatingEnvironmentParameters,
) -> OperatingEnvironmentResult:
    """Compute the ``[0, 1]`` operating-environment strength for a jurisdiction.

    ``sub_score → weighted sub-factor → weighted pillar → composite risk →
    strength → sovereign-governor cap``. The governor maps the sovereign
    category to a maximum plausible strength — "a banking system can't be much
    stronger than its sovereign" — mirroring the rating model's sovereign
    ceiling. Returns the full breakdown and a reproducible ``input_digest``.
    """
    if inputs.sovereign_category not in parameters.sovereign_governor:
        raise OperatingEnvironmentError(
            f"sovereign_governor has no entry for category {inputs.sovereign_category!r}."
        )
    span = parameters.risk_max - parameters.risk_min
    if span <= ZERO:
        raise OperatingEnvironmentError("risk_max must be greater than risk_min.")
    pillar_total = sum((pillar.weight for pillar in parameters.pillars), ZERO)
    if pillar_total != ONE:
        raise OperatingEnvironmentError(
            f"Pillar weights must sum to 1 (got {pillar_total})."
        )

    pillars = tuple(_pillar_score(pillar, inputs, parameters) for pillar in parameters.pillars)
    composite = sum((pillar.score * pillar.weight for pillar in pillars), ZERO)
    strength_raw = (parameters.risk_max - composite) / span
    strength_raw = min(max(strength_raw, ZERO), ONE)

    governor_cap = parameters.sovereign_governor[inputs.sovereign_category]
    score = min(strength_raw, governor_cap)
    governor_applied = governor_cap < strength_raw

    return OperatingEnvironmentResult(
        score=_quantize(score),
        strength_raw=_quantize(strength_raw),
        composite_risk=_quantize(composite),
        pillars=pillars,
        sovereign_category=inputs.sovereign_category,
        governor_cap=_quantize(governor_cap),
        governor_applied=governor_applied,
        risk_min=parameters.risk_min,
        risk_max=parameters.risk_max,
        parameters_version=parameters.version,
        input_digest=canonical_input_digest(_digest_payload(inputs, parameters)),
    )


# ---------------------------------------------------------------------------
# Versioned default parameters (documented calibration placeholders).
# ---------------------------------------------------------------------------


def _tt(boundaries: tuple[str, ...], scores: tuple[int, ...]) -> ThresholdTable:
    return ThresholdTable(
        boundaries=tuple(Decimal(b) for b in boundaries), scores=scores
    )


# Threshold tables per observable (placeholder calibration; scores 1 = lowest
# risk … 6 = highest). "higher-is-better" inputs carry descending scores.
_REAL_GDP_GROWTH = _tt(("0", "2", "3.5", "5", "6.5"), (6, 5, 4, 3, 2, 1))
_GDP_PER_CAPITA = _tt(("1500", "2500", "4000", "7000", "12000"), (6, 5, 4, 3, 2, 1))
_CPI_INFLATION = _tt(("5", "8", "12", "20", "30"), (1, 2, 3, 4, 5, 6))
_PRIVATE_CREDIT_GROWTH = _tt(("5", "10", "15", "20", "30"), (1, 2, 3, 4, 5, 6))
_POLICY_RATE = _tt(("12", "18", "24", "30", "40"), (1, 2, 3, 4, 5, 6))
_SYSTEM_NPL = _tt(("5", "8", "12", "18", "25"), (1, 2, 3, 4, 5, 6))
_PRIVATE_DEBT_TO_GDP = _tt(("20", "35", "50", "70", "100"), (1, 2, 3, 4, 5, 6))
_SYSTEM_ROA = _tt(("0", "0.5", "1", "1.5", "2.5"), (6, 5, 4, 3, 2, 1))
_SYSTEM_CREDIT_GROWTH = _tt(("5", "12", "20", "30", "45"), (1, 2, 3, 4, 5, 6))
_SYSTEM_LTD = _tt(("60", "75", "90", "105", "120"), (1, 2, 3, 4, 5, 6))
_SYSTEM_CAR = _tt(("10", "13", "16", "20", "25"), (6, 5, 4, 3, 2, 1))
_EXTERNAL_FUNDING = _tt(("10", "20", "30", "40", "55"), (1, 2, 3, 4, 5, 6))


DEFAULT_PARAMETERS = OperatingEnvironmentParameters(
    version="oe-bicra/2026.08-placeholder",
    parameter_status=(
        "BICRA-style two-pillar placeholder calibration: threshold tables, "
        "weights, RISK_MIN/MAX, the sovereign-risk table and the governor map "
        "are documented starting points pending independent validation (spec "
        "Non-goals / §8.2) — do NOT size a live repo haircut off this version."
    ),
    pillars=(
        PillarDefinition(
            code="economic",
            weight=Decimal("0.5"),
            sub_factors=(
                SubFactorDefinition(
                    code="economic_resilience",
                    weight=Decimal("0.35"),
                    inputs=(
                        InputDefinition(
                            "real_gdp_growth_pct", Decimal("0.6"), "threshold", _REAL_GDP_GROWTH
                        ),
                        InputDefinition(
                            "gdp_per_capita_usd", Decimal("0.4"), "threshold", _GDP_PER_CAPITA
                        ),
                    ),
                ),
                SubFactorDefinition(
                    code="economic_imbalances",
                    weight=Decimal("0.30"),
                    inputs=(
                        InputDefinition(
                            "cpi_inflation_pct", Decimal("0.4"), "threshold", _CPI_INFLATION
                        ),
                        InputDefinition(
                            "private_credit_to_gdp_growth_pct",
                            Decimal("0.3"),
                            "threshold",
                            _PRIVATE_CREDIT_GROWTH,
                        ),
                        InputDefinition(
                            "policy_rate_pct", Decimal("0.3"), "threshold", _POLICY_RATE
                        ),
                    ),
                ),
                SubFactorDefinition(
                    code="credit_risk_economy",
                    weight=Decimal("0.35"),
                    inputs=(
                        InputDefinition(
                            "system_npl_pct", Decimal("0.6"), "threshold", _SYSTEM_NPL
                        ),
                        InputDefinition(
                            "private_debt_to_gdp_pct",
                            Decimal("0.4"),
                            "threshold",
                            _PRIVATE_DEBT_TO_GDP,
                        ),
                    ),
                ),
            ),
        ),
        PillarDefinition(
            code="industry",
            weight=Decimal("0.5"),
            sub_factors=(
                SubFactorDefinition(
                    code="institutional_framework",
                    weight=Decimal("0.40"),
                    inputs=(
                        InputDefinition(
                            "regulatory_quality_score", Decimal("0.5"), "judgment"
                        ),
                        InputDefinition("sovereign_rating", Decimal("0.5"), "sovereign"),
                    ),
                ),
                SubFactorDefinition(
                    code="competitive_dynamics",
                    weight=Decimal("0.25"),
                    inputs=(
                        InputDefinition(
                            "system_roa_pct", Decimal("0.6"), "threshold", _SYSTEM_ROA
                        ),
                        InputDefinition(
                            "system_credit_growth_pct",
                            Decimal("0.4"),
                            "threshold",
                            _SYSTEM_CREDIT_GROWTH,
                        ),
                    ),
                ),
                SubFactorDefinition(
                    code="systemwide_funding",
                    weight=Decimal("0.35"),
                    inputs=(
                        InputDefinition(
                            "system_loan_to_deposit_pct", Decimal("0.4"), "threshold", _SYSTEM_LTD
                        ),
                        InputDefinition(
                            "system_car_pct", Decimal("0.4"), "threshold", _SYSTEM_CAR
                        ),
                        InputDefinition(
                            "external_funding_pct", Decimal("0.2"), "threshold", _EXTERNAL_FUNDING
                        ),
                    ),
                ),
            ),
        ),
    ),
    # Sovereign category → 1..6 institutional-framework risk sub-score.
    sovereign_risk_table={
        "aaa": 1,
        "aa": 1,
        "a": 2,
        "bbb": 3,
        "bb": 4,
        "b": 5,
        "ccc": 6,
        "cc": 6,
        "c": 6,
    },
    # Sovereign category → maximum plausible operating-environment strength.
    sovereign_governor={
        "aaa": Decimal("1.00"),
        "aa": Decimal("0.95"),
        "a": Decimal("0.90"),
        "bbb": Decimal("0.80"),
        "bb": Decimal("0.65"),
        "b": Decimal("0.55"),
        "ccc": Decimal("0.40"),
        "cc": Decimal("0.30"),
        "c": Decimal("0.20"),
    },
)


# The full ordered list of observable (threshold) input codes the service must
# collect from the analyst / auto-pull, in breakdown order — a convenience for
# the service and console contract (kept in sync with DEFAULT_PARAMETERS).
THRESHOLD_INPUT_CODES: tuple[str, ...] = tuple(
    definition.code
    for pillar in DEFAULT_PARAMETERS.pillars
    for sub_factor in pillar.sub_factors
    for definition in sub_factor.inputs
    if definition.kind == "threshold"
)
JUDGMENT_INPUT_CODES: tuple[str, ...] = tuple(
    definition.code
    for pillar in DEFAULT_PARAMETERS.pillars
    for sub_factor in pillar.sub_factors
    for definition in sub_factor.inputs
    if definition.kind == "judgment"
)
