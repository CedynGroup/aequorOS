"""Materiality: likelihood × impact, against thresholds nobody hard-codes.

Which risks are material decides what the rest of the ICAAP has to do — a
material risk needs an appetite metric, a treatment decision and, if it is to
be quantified, a Pillar 2 item. So the scoring has to be legible and the
thresholds have to be editable, because the guideline prescribes neither.

The ORDINAL SCALES (1–5 likelihood, 1–5 impact) are structural: they come from
the framework JSON, which is data. The THRESHOLDS — the score at which a risk
becomes material, the impact level that makes it material on its own, and the
rating bands — are governed parameters resolved from the console (D-024). This
module receives all of them and holds none.

Two rules that are easy to get wrong:

* materiality is ``score ≥ min_score`` **OR** ``impact ≥ min_impact``. A
  low-likelihood, catastrophic-impact risk is material however small the
  product is. That is the whole point of the impact leg.
* an **override** is a judgement, not a correction. Both the matrix verdict and
  the override survive in the assessment, the reason is mandatory at the
  service boundary, and overriding a material risk DOWN raises a readiness
  warning. Nobody gets to quietly delete a risk from the report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.domain.icaap.pillar2.types import MissingParameter

PARAM_MIN_SCORE = "icaap_materiality_material_min_score"
PARAM_MIN_IMPACT = "icaap_materiality_material_min_impact"
PARAM_RATING_BANDS = "icaap_materiality_rating_bands"

Verdict = Literal["material", "not_material", "unassessed"]
VerdictSource = Literal["matrix", "override"]

MaterialityErrorCode = Literal[
    "scale_empty",
    "bands_empty",
    "bands_not_contiguous",
    "bands_not_covering_scale",
    "band_not_ascending",
    "score_out_of_scale",
]


class MaterialityPolicyError(ValueError):
    """A matrix that cannot classify every score it can produce."""

    def __init__(self, code: MaterialityErrorCode, *, detail: str | None = None):
        self.code: MaterialityErrorCode = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


@dataclass(frozen=True)
class Level:
    """One step on an ordinal scale — structural, from the framework."""

    score: int
    key: str
    label: str


@dataclass(frozen=True)
class RatingBand:
    """A governed score range and the rating it reads as."""

    key: str
    label: str
    min_score: int
    max_score: int


@dataclass(frozen=True)
class MaterialityPolicy:
    """Everything needed to score a risk, all of it supplied by the caller."""

    likelihood: tuple[Level, ...]
    impact: tuple[Level, ...]
    bands: tuple[RatingBand, ...]
    material_min_score: int
    material_min_impact: int
    #: Digest of the governed thresholds, so a stored assessment can be told
    #: that the rules moved under it.
    thresholds_digest: str

    @property
    def min_score(self) -> int:
        return min(level.score for level in self.likelihood) * min(
            level.score for level in self.impact
        )

    @property
    def max_score(self) -> int:
        return max(level.score for level in self.likelihood) * max(
            level.score for level in self.impact
        )


@dataclass(frozen=True)
class MaterialityAssessment:
    """What the matrix said, what the bank said, and which one governs."""

    score: int | None
    rating: str | None
    matrix_verdict: Verdict
    verdict: Verdict
    verdict_source: VerdictSource


@dataclass(frozen=True)
class MatrixCell:
    likelihood: int
    impact: int
    score: int
    rating_key: str | None
    material: bool


def build_policy(  # noqa: PLR0913 - the two scales, the bands and three governed thresholds
    likelihood: Sequence[Level],
    impact: Sequence[Level],
    bands: Sequence[RatingBand] | None,
    *,
    min_score: int | None,
    min_impact: int | None,
    thresholds_digest: str,
) -> MaterialityPolicy:
    """Check a matrix and its governed thresholds, or refuse."""
    if min_score is None:
        raise MissingParameter(PARAM_MIN_SCORE)
    if min_impact is None:
        raise MissingParameter(PARAM_MIN_IMPACT)
    if bands is None or not bands:
        raise MissingParameter(PARAM_RATING_BANDS)
    if not likelihood or not impact:
        raise MaterialityPolicyError("scale_empty")

    ordered = sorted(bands, key=lambda band: band.min_score)
    lowest = min(level.score for level in likelihood) * min(level.score for level in impact)
    highest = max(level.score for level in likelihood) * max(level.score for level in impact)

    previous: RatingBand | None = None
    for band in ordered:
        if band.max_score < band.min_score:
            raise MaterialityPolicyError("band_not_ascending", detail=band.key)
        if previous is not None and band.min_score != previous.max_score + 1:
            raise MaterialityPolicyError("bands_not_contiguous", detail=band.key)
        previous = band
    if ordered[0].min_score > lowest or ordered[-1].max_score < highest:
        raise MaterialityPolicyError("bands_not_covering_scale")

    return MaterialityPolicy(
        likelihood=tuple(likelihood),
        impact=tuple(impact),
        bands=tuple(ordered),
        material_min_score=min_score,
        material_min_impact=min_impact,
        thresholds_digest=thresholds_digest,
    )


def rating_for(policy: MaterialityPolicy, score: int) -> str:
    """The band a score falls in."""
    for band in policy.bands:
        if band.min_score <= score <= band.max_score:
            return band.key
    raise MaterialityPolicyError("score_out_of_scale", detail=str(score))


def is_material(policy: MaterialityPolicy, score: int, impact: int) -> bool:
    """Score OR impact — a rare catastrophe is material on impact alone."""
    return score >= policy.material_min_score or impact >= policy.material_min_impact


def assess(
    policy: MaterialityPolicy,
    likelihood: int | None,
    impact: int | None,
    *,
    override: Literal["material", "not_material"] | None = None,
) -> MaterialityAssessment:
    """Score a risk, apply any override, and keep both answers visible."""
    if likelihood is None or impact is None:
        return MaterialityAssessment(
            score=None,
            rating=None,
            matrix_verdict="unassessed",
            verdict=override if override is not None else "unassessed",
            verdict_source="override" if override is not None else "matrix",
        )

    scores_l = {level.score for level in policy.likelihood}
    scores_i = {level.score for level in policy.impact}
    if likelihood not in scores_l:
        raise MaterialityPolicyError("score_out_of_scale", detail=f"likelihood:{likelihood}")
    if impact not in scores_i:
        raise MaterialityPolicyError("score_out_of_scale", detail=f"impact:{impact}")

    score = likelihood * impact
    matrix_verdict: Verdict = "material" if is_material(policy, score, impact) else "not_material"
    return MaterialityAssessment(
        score=score,
        rating=rating_for(policy, score),
        matrix_verdict=matrix_verdict,
        verdict=override if override is not None else matrix_verdict,
        verdict_source="override" if override is not None else "matrix",
    )


def overridden_down(assessment: MaterialityAssessment) -> bool:
    """A matrix-material risk the bank called not material — a warning."""
    return (
        assessment.verdict_source == "override"
        and assessment.matrix_verdict == "material"
        and assessment.verdict == "not_material"
    )


def matrix_cells(policy: MaterialityPolicy) -> tuple[MatrixCell, ...]:
    """Every cell of the heatmap, for the register UI."""
    cells: list[MatrixCell] = []
    for likelihood in sorted(level.score for level in policy.likelihood):
        for impact in sorted(level.score for level in policy.impact):
            score = likelihood * impact
            cells.append(
                MatrixCell(
                    likelihood=likelihood,
                    impact=impact,
                    score=score,
                    rating_key=rating_for(policy, score),
                    material=is_material(policy, score, impact),
                )
            )
    return tuple(cells)


__all__ = [
    "PARAM_MIN_IMPACT",
    "PARAM_MIN_SCORE",
    "PARAM_RATING_BANDS",
    "Level",
    "MaterialityAssessment",
    "MaterialityErrorCode",
    "MaterialityPolicy",
    "MaterialityPolicyError",
    "MatrixCell",
    "RatingBand",
    "Verdict",
    "VerdictSource",
    "assess",
    "build_policy",
    "is_material",
    "matrix_cells",
    "overridden_down",
    "rating_for",
]
