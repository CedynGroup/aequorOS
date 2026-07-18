"""Position deduplicator: legitimate cross-time snapshots vs extraction-bug dups.

The hard problem (build brief): the same loan appearing twice may be *legitimate* (the
same position re-extracted across snapshots, its balance moved by real transactions) or
a *bug* (an extraction glitch duplicating a row within one snapshot). Conflating them
either double-counts exposure or silently drops a real position. This deduplicator
classifies using lineage the records already carry — ``source_reference`` (the stable
arrangement id), the ``as_of_date`` snapshot, and whether regulatory values moved:

  * **Same identity, one snapshot, identical regulatory fingerprint** →
    :class:`MatchType.WITHIN_SOURCE` extraction-bug duplicate (high confidence,
    auto-confirmable): the rows are byte-for-byte the same exposure counted twice.
  * **Same identity, one snapshot, *diverging* regulatory values** → a WITHIN_SOURCE
    *conflict*: two rows claim the same position with different balances/rates. Never
    auto-confirmed — a human must adjudicate which is authoritative.
  * **Same identity across multiple snapshots** → :class:`MatchType.CROSS_TIME`
    legitimate evolution when a balance actually moved; if balances are identical across
    *different* dates it is flagged as a suspected mis-dated duplicate (low confidence).

Every branch emits a :class:`LinkageRecord` — no source row is destroyed; the linkage
names the winner and preserves all subsumed ids for audit and reversibility.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.etl.contracts import (
    Deduplicator,
    ETLOperationType,
    ETLProvenance,
    LinkageRecord,
    MatchType,
)
from app.etl.deduplication._fields import get_field, record_id

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord

_OPERATION_REF = "position_deduplicator/v1"

# Regulatory fields whose equality decides "identical exposure" vs "conflict". These are
# the value-bearing measures of a position; identity fields (id/dates) are handled
# separately as the grouping key.
_FINGERPRINT_RAW_KEYS: tuple[str, ...] = (
    "balance_ghs",
    "balance_ccy",
    "notional_ccy",
    "interest_rate",
    "ifrs9_stage",
    "gl_code",
    "product_code",
)


class PositionDeduplicator(Deduplicator):
    """Deduplicator over positions; primary declared type is :class:`MatchType.CROSS_TIME`.

    Individual linkages carry their own ``match_type`` (CROSS_TIME or WITHIN_SOURCE)
    according to the case classified above.
    """

    match_type = MatchType.CROSS_TIME

    def __init__(self, *, conflict_confidence: float = 0.40, dup_confidence: float = 0.98) -> None:
        self.conflict_confidence = conflict_confidence
        self.dup_confidence = dup_confidence

    def link(self, records: list[RawRecord]) -> list[LinkageRecord]:
        positions = [r for r in records if r.entity_type == "position"]
        if len(positions) < 2:
            return []

        # Group by stable identity (arrangement/source_reference, else position id).
        groups: dict[str, list[RawRecord]] = defaultdict(list)
        for r in positions:
            groups[_identity_key(r)].append(r)

        linkages: list[LinkageRecord] = []
        for recs in groups.values():
            if len(recs) < 2:
                continue
            linkages.extend(self._classify_group(recs))
        return linkages

    def _classify_group(self, recs: list[RawRecord]) -> list[LinkageRecord]:
        out: list[LinkageRecord] = []
        by_date: dict[str, list[RawRecord]] = defaultdict(list)
        for r in recs:
            by_date[get_field(r, "as_of_date") or "__undated__"].append(r)

        # (1) Within-snapshot duplicates / conflicts.
        for date_recs in by_date.values():
            if len(date_recs) < 2:
                continue
            out.extend(self._classify_within_snapshot(date_recs))

        # (2) Cross-time evolution across distinct snapshots.
        if len(by_date) > 1:
            out.append(self._build_cross_time(recs, by_date))
        return out

    def _classify_within_snapshot(self, date_recs: list[RawRecord]) -> list[LinkageRecord]:
        # Partition records sharing one snapshot by their regulatory fingerprint.
        by_fingerprint: dict[tuple[str, ...], list[RawRecord]] = defaultdict(list)
        for r in date_recs:
            by_fingerprint[_fingerprint(r)].append(r)

        out: list[LinkageRecord] = []
        identical_groups = [g for g in by_fingerprint.values() if len(g) > 1]
        for group in identical_groups:
            out.append(self._build_within_source(group, is_conflict=False))

        # Distinct fingerprints under the same identity+snapshot = a conflict: two rows
        # claim the same position with different regulatory values.
        distinct_reps = [g[0] for g in by_fingerprint.values()]
        if len(distinct_reps) > 1:
            out.append(self._build_within_source(distinct_reps, is_conflict=True))
        return out

    def _build_within_source(self, group: list[RawRecord], *, is_conflict: bool) -> LinkageRecord:
        ids = [record_id(r) for r in group]
        winner = _select_position_winner(group)
        if is_conflict:
            signals = {"identity_match": 1.0, "same_snapshot": 1.0, "value_identical": 0.0}
            confidence = self.conflict_confidence
            auto_confirmed = False  # human must adjudicate divergent values
        else:
            signals = {"identity_match": 1.0, "same_snapshot": 1.0, "value_identical": 1.0}
            confidence = self.dup_confidence
            auto_confirmed = True
        return LinkageRecord(
            match_type=MatchType.WITHIN_SOURCE,
            canonical_winner_id=winner,
            linked_source_ids=tuple(sorted(ids)),
            signals=signals,
            combined_confidence=confidence,
            provenance=self._provenance(confidence),
            auto_confirmed=auto_confirmed,
        )

    def _build_cross_time(
        self, recs: list[RawRecord], by_date: dict[str, list[RawRecord]]
    ) -> LinkageRecord:
        ids = [record_id(r) for r in recs]
        # One representative balance per snapshot date to judge evolution.
        dated_balances: list[tuple[str, Decimal | None]] = []
        for date, date_recs in sorted(by_date.items()):
            dated_balances.append((date, _decimal(get_field(date_recs[0], "balance"))))

        known = [b for _, b in dated_balances if b is not None]
        distinct_balances = {b for b in known}
        balance_moved = len(distinct_balances) > 1

        n_dates = float(len(by_date))
        if balance_moved:
            # Legitimate: same arrangement observed over time with real balance movement.
            signals = {
                "identity_match": 1.0,
                "distinct_snapshots": n_dates,
                "balance_moved": 1.0,
                "legitimate_evolution": 1.0,
            }
            confidence = 0.95
            auto_confirmed = True
        else:
            # Same identity, different dates, but the balance never moved: a suspected
            # mis-dated duplicate rather than genuine evolution — leave for review.
            signals = {
                "identity_match": 1.0,
                "distinct_snapshots": n_dates,
                "balance_moved": 0.0,
                "legitimate_evolution": 0.0,
            }
            confidence = self.conflict_confidence
            auto_confirmed = False

        # The most recent snapshot is the canonical current-state winner.
        winner = _select_latest(recs)
        return LinkageRecord(
            match_type=MatchType.CROSS_TIME,
            canonical_winner_id=winner,
            linked_source_ids=tuple(sorted(ids)),
            signals=signals,
            combined_confidence=confidence,
            provenance=self._provenance(confidence),
            auto_confirmed=auto_confirmed,
        )

    def _provenance(self, confidence: float) -> ETLProvenance:
        # Deterministic rule-based classification — no ML model id/version.
        return ETLProvenance(
            operation_type=ETLOperationType.DEDUP_LINK,
            operation_ref=_OPERATION_REF,
            confidence=confidence,
        )


def _identity_key(record: RawRecord) -> str:
    """Stable identity for a position across snapshots: source_reference, else id."""
    return get_field(record, "source_reference") or record_id(record)


def _fingerprint(record: RawRecord) -> tuple[str, ...]:
    """Tuple of stringified regulatory values that defines an identical exposure."""
    parts: list[str] = []
    for key in _FINGERPRINT_RAW_KEYS:
        value = record.data.get(key)
        parts.append("" if value is None else str(value).strip())
    return tuple(parts)


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _select_position_winner(group: list[RawRecord]) -> str:
    """Winner within a same-snapshot group: most-complete row, then id for determinism."""

    def completeness(rec: RawRecord) -> tuple[int, str]:
        populated = sum(1 for k in _FINGERPRINT_RAW_KEYS if _is_populated(rec.data.get(k)))
        return (populated, record_id(rec))

    return record_id(max(group, key=completeness))


def _select_latest(recs: list[RawRecord]) -> str:
    """Winner across snapshots: the record with the maximum as_of_date (ISO-sortable)."""
    return record_id(max(recs, key=lambda r: get_field(r, "as_of_date") or ""))


def _is_populated(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() not in {"nan", "none", "null", "n/a"}
