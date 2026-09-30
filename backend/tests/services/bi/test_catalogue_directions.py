"""Each engine measure's favourable direction equals ``report_comparison``'s.

The catalogue restates the direction per metric (``app/domain/bi/catalogue/
directions.py``) because ``app.domain`` may not import ``app.services``; this
test is the contract that keeps the two in step. It may import services.
"""

from __future__ import annotations

from app.domain.bi.authority import TEXT_VALUED_METRIC_IDS, engine_authorities
from app.domain.bi.catalogue import catalogue
from app.domain.bi.catalogue.directions import ENGINE_DIRECTIONS, engine_direction
from app.services.report_comparison import favorable_direction


def test_every_engine_measure_direction_matches_report_comparison() -> None:
    mismatches: list[str] = []
    for measure in catalogue().engine_measures():
        assert measure.engine_rule is not None
        metric_id = measure.engine_rule.metric_id
        expected = favorable_direction(metric_id)
        if measure.favourable_direction != expected:
            mismatches.append(
                f"{measure.id}: catalogue={measure.favourable_direction} vs {expected}"
            )
    assert not mismatches, "\n".join(mismatches)


def test_the_direction_table_names_only_engine_metrics_and_only_non_neutral_ones() -> None:
    engine_ids = {e.metric_id for e in engine_authorities()} - TEXT_VALUED_METRIC_IDS
    assert set(ENGINE_DIRECTIONS) <= engine_ids, sorted(set(ENGINE_DIRECTIONS) - engine_ids)
    assert "neutral" not in ENGINE_DIRECTIONS.values()
    for metric_id in engine_ids:
        assert engine_direction(metric_id) == favorable_direction(metric_id), metric_id


def test_signed_deltas_are_judged_on_magnitude() -> None:
    """D-013 / H-007: ΔEVE and earnings-at-risk are signed; smaller |Δ| is better."""
    for metric_id in (
        "worst_eve_change_pct_tier1",
        "ear_up_200_ghs",
        "ear_down_200_ghs",
        "ear_up_450_ghs",
        "ear_down_450_ghs",
    ):
        assert engine_direction(metric_id) == "magnitude_lower_better"
        assert favorable_direction(metric_id) == "magnitude_lower_better"
