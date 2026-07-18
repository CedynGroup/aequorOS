"""Contract tests for the position deduplicator (cross-time vs extraction-bug)."""

from __future__ import annotations

from app.etl.contracts import MatchType
from app.etl.deduplication.position_deduplicator import PositionDeduplicator
from tests.etl._factories import position


def test_legitimate_cross_time_snapshots_are_linked_not_flagged() -> None:
    recs = [
        position(
            "p1",
            source_reference="ARR/1",
            as_of_date="2026-03-31",
            balance_ghs="1000",
            ifrs9_stage="1",
        ),
        position(
            "p2",
            source_reference="ARR/1",
            as_of_date="2026-04-30",
            balance_ghs="950",
            ifrs9_stage="1",
        ),
    ]
    links = PositionDeduplicator().link(recs)
    assert len(links) == 1
    link = links[0]
    assert link.match_type is MatchType.CROSS_TIME
    assert link.signals["legitimate_evolution"] == 1.0
    assert link.auto_confirmed is True
    # Winner is the most recent snapshot; both rows preserved.
    assert link.canonical_winner_id == "p2"
    assert set(link.linked_source_ids) == {"p1", "p2"}


def test_extraction_bug_exact_duplicate_within_snapshot() -> None:
    recs = [
        position(
            "p3",
            source_reference="ARR/2",
            as_of_date="2026-04-30",
            balance_ghs="500",
            ifrs9_stage="1",
        ),
        position(
            "p4",
            source_reference="ARR/2",
            as_of_date="2026-04-30",
            balance_ghs="500",
            ifrs9_stage="1",
        ),
    ]
    links = PositionDeduplicator().link(recs)
    assert len(links) == 1
    link = links[0]
    assert link.match_type is MatchType.WITHIN_SOURCE
    assert link.signals["value_identical"] == 1.0
    assert link.auto_confirmed is True
    assert link.combined_confidence >= 0.95


def test_same_identity_same_snapshot_conflicting_values_needs_review() -> None:
    recs = [
        position(
            "p5",
            source_reference="ARR/3",
            as_of_date="2026-04-30",
            balance_ghs="700",
            ifrs9_stage="1",
        ),
        position(
            "p6",
            source_reference="ARR/3",
            as_of_date="2026-04-30",
            balance_ghs="777",
            ifrs9_stage="2",
        ),
    ]
    link = PositionDeduplicator().link(recs)[0]
    assert link.match_type is MatchType.WITHIN_SOURCE
    assert link.signals["value_identical"] == 0.0
    assert link.auto_confirmed is False  # human adjudicates divergent regulatory values


def test_same_balance_across_different_dates_is_suspected_misdated_dup() -> None:
    recs = [
        position("p7", source_reference="ARR/4", as_of_date="2026-03-31", balance_ghs="300"),
        position("p8", source_reference="ARR/4", as_of_date="2026-04-30", balance_ghs="300"),
    ]
    link = PositionDeduplicator().link(recs)[0]
    assert link.match_type is MatchType.CROSS_TIME
    assert link.signals["balance_moved"] == 0.0
    assert link.auto_confirmed is False


def test_distinct_positions_are_not_linked() -> None:
    recs = [
        position("p9", source_reference="ARR/5", as_of_date="2026-04-30", balance_ghs="100"),
        position("p10", source_reference="ARR/6", as_of_date="2026-04-30", balance_ghs="200"),
    ]
    assert PositionDeduplicator().link(recs) == []


def test_every_linkage_carries_confidence_and_lineage() -> None:
    recs = [
        position("p1", source_reference="ARR/1", as_of_date="2026-03-31", balance_ghs="1000"),
        position("p2", source_reference="ARR/1", as_of_date="2026-04-30", balance_ghs="950"),
    ]
    for link in PositionDeduplicator().link(recs):
        assert link.provenance.confidence is not None
        assert link.provenance.operation_ref == "position_deduplicator/v1"
        assert len(link.linked_source_ids) >= 2  # source rows preserved, never destroyed
