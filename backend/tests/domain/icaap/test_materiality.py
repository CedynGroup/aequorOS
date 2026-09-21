"""Materiality: the matrix, the thresholds that are data, and the override."""

from __future__ import annotations

import pytest

from app.domain.icaap.materiality import (
    Level,
    MaterialityPolicy,
    MaterialityPolicyError,
    RatingBand,
    assess,
    build_policy,
    matrix_cells,
    overridden_down,
)
from app.domain.icaap.pillar2.types import MissingParameter

SCALE = tuple(Level(score=step, key=f"l{step}", label=f"Level {step}") for step in range(1, 6))
BANDS = (
    RatingBand("low", "Low", 1, 4),
    RatingBand("medium", "Medium", 5, 9),
    RatingBand("high", "High", 10, 16),
    RatingBand("very_high", "Very high", 17, 25),
)
DIGEST = "d0d0"


def policy(min_score: int = 10, min_impact: int = 4) -> MaterialityPolicy:
    return build_policy(
        SCALE, SCALE, BANDS, min_score=min_score, min_impact=min_impact, thresholds_digest=DIGEST
    )


@pytest.mark.parametrize(
    ("likelihood", "impact", "score", "rating", "material"),
    [
        (4, 3, 12, "high", True),
        (2, 4, 8, "medium", True),
        (3, 3, 9, "medium", False),
        (5, 5, 25, "very_high", True),
        (1, 1, 1, "low", False),
    ],
)
def test_the_design_goldens(
    likelihood: int, impact: int, score: int, rating: str, material: bool
) -> None:
    assessment = assess(policy(), likelihood, impact)
    assert assessment.score == score
    assert assessment.rating == rating
    assert assessment.matrix_verdict == ("material" if material else "not_material")
    assert assessment.verdict_source == "matrix"


def test_impact_alone_can_make_a_risk_material() -> None:
    """A rare catastrophe scores 8 and is still material, on impact."""
    assert assess(policy(), 2, 4).matrix_verdict == "material"
    assert assess(policy(), 4, 2).matrix_verdict == "not_material"


def test_changing_the_governed_threshold_changes_the_verdict() -> None:
    """D-024: the console moves this, not a release."""
    assert assess(policy(min_score=10), 3, 3).matrix_verdict == "not_material"
    assert assess(policy(min_score=9), 3, 3).matrix_verdict == "material"


def test_an_unscored_risk_is_unassessed_not_immaterial() -> None:
    assessment = assess(policy(), None, 4)
    assert assessment.matrix_verdict == "unassessed"
    assert assessment.score is None
    assert assessment.rating is None


def test_an_override_keeps_both_answers_visible() -> None:
    assessment = assess(policy(), 4, 3, override="not_material")
    assert assessment.matrix_verdict == "material"
    assert assessment.verdict == "not_material"
    assert assessment.verdict_source == "override"
    assert overridden_down(assessment)
    up = assess(policy(), 1, 1, override="material")
    assert up.verdict == "material"
    assert not overridden_down(up)


@pytest.mark.parametrize(
    ("bands", "code"),
    [
        (
            (RatingBand("low", "Low", 1, 4), RatingBand("high", "High", 6, 25)),
            "bands_not_contiguous",
        ),
        (
            (RatingBand("low", "Low", 1, 4), RatingBand("high", "High", 5, 20)),
            "bands_not_covering_scale",
        ),
        ((RatingBand("low", "Low", 4, 1),), "band_not_ascending"),
    ],
)
def test_a_matrix_that_cannot_rate_every_score_is_refused(
    bands: tuple[RatingBand, ...], code: str
) -> None:
    with pytest.raises(MaterialityPolicyError) as error:
        build_policy(SCALE, SCALE, bands, min_score=10, min_impact=4, thresholds_digest=DIGEST)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("min_score", "min_impact", "bands", "expected"),
    [
        (None, 4, BANDS, "icaap_materiality_material_min_score"),
        (10, None, BANDS, "icaap_materiality_material_min_impact"),
        (10, 4, None, "icaap_materiality_rating_bands"),
    ],
)
def test_an_unresolved_threshold_is_a_typed_refusal(
    min_score: int | None,
    min_impact: int | None,
    bands: tuple[RatingBand, ...] | None,
    expected: str,
) -> None:
    with pytest.raises(MissingParameter) as error:
        build_policy(
            SCALE,
            SCALE,
            bands,
            min_score=min_score,
            min_impact=min_impact,
            thresholds_digest=DIGEST,
        )
    assert error.value.param_code == expected


def test_a_score_outside_the_scale_is_a_programming_error() -> None:
    with pytest.raises(MaterialityPolicyError) as error:
        assess(policy(), 9, 1)
    assert error.value.code == "score_out_of_scale"


def test_the_heatmap_covers_the_whole_matrix() -> None:
    cells = matrix_cells(policy())
    assert len(cells) == len(SCALE) * len(SCALE)
    assert {cell.rating_key for cell in cells} == {band.key for band in BANDS}
    material = {(cell.likelihood, cell.impact) for cell in cells if cell.material}
    assert (2, 4) in material
    assert (3, 3) not in material
