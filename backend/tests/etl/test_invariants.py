"""Cross-cutting ML-ETL governance invariants (build brief §INVARIANTS).

These verify the guarantees the whole layer must uphold regardless of which stage
produced an op:
  * dedup never destroys source records (all source ids retained on the linkage);
  * reversibility — the original record is always retrievable via lineage;
  * a regulatory-critical value is never silently modified (flag-not-modify);
  * every operation carries confidence + provenance (lineage) evidence.
"""

from __future__ import annotations

import pytest

from app.etl.contracts import (
    REGULATORY_CRITICAL_FIELDS,
    Disposition,
    ETLOperation,
    ETLOperationType,
    ETLProvenance,
    ETLValidationError,
)
from app.etl.deduplication.counterparty_matcher import CounterpartyMatcher
from app.etl.deduplication.fingerprint_detector import FingerprintAnomalyDetector
from app.etl.deduplication.position_deduplicator import PositionDeduplicator
from tests.etl._factories import counterparty, position


def test_regulatory_critical_field_cannot_be_sanctioned_rewrite() -> None:
    # A sanctioned op that tries to rewrite a regulatory-critical field must raise.
    for field in ("balance", "interest_rate", "counterparty_id"):
        assert field in REGULATORY_CRITICAL_FIELDS
        with pytest.raises(ETLValidationError):
            ETLOperation(
                record_id="r1",
                field_name=field,
                disposition=Disposition.SANCTIONED,
                before="100",
                after="200",
                provenance=ETLProvenance(
                    operation_type=ETLOperationType.NORMALIZE,
                    operation_ref="test",
                ),
            )


def test_regulatory_issue_is_flagged_with_after_none() -> None:
    # The permitted path for a regulatory-critical concern: FLAG it, never modify it.
    op = ETLOperation(
        record_id="r1",
        field_name="balance",
        disposition=Disposition.FLAGGED,
        before="100",
        after=None,
        provenance=ETLProvenance(
            operation_type=ETLOperationType.ANOMALY_FLAG,
            operation_ref="test",
            confidence=0.9,
        ),
        reason="balance 3 orders of magnitude off peers",
    )
    assert op.after is None
    assert op.disposition is Disposition.FLAGGED


def test_dedup_preserves_all_source_records_and_is_reversible() -> None:
    recs = [
        counterparty("C1", "ACME TRADING LTD", source="t24", national_id="GHA-1"),
        counterparty("C2", "Acme Trading Limited", source="upload", national_id="GHA-1"),
        counterparty("C3", "ACME TRADING CO LTD", source="los"),
    ]
    by_id = {r.data["counterparty_id"]: r for r in recs}
    links = CounterpartyMatcher().link(recs)
    assert links

    for link in links:
        # Winner + every subsumed id is a real source record we can retrieve (reverse).
        assert link.canonical_winner_id in by_id
        for sid in link.linked_source_ids:
            assert sid in by_id, "source record must remain retrievable via lineage"
        # The winner is one of the linked ids (nothing invented).
        assert link.canonical_winner_id in link.linked_source_ids


def test_fingerprint_flags_never_modify_regulatory_values() -> None:
    records = [
        position(
            f"n{i}", source_reference=f"ARR/{i}", as_of_date="2026-04-30", balance_ghs=str(100 + i)
        )
        for i in range(12)
    ]
    records.append(
        position(
            "BAD",
            source_reference="ARR/BAD",
            as_of_date="2026-04-30",
            balance_ghs="10000000000000",
            junk="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz aaaa bbbb cccc",
        )
    )
    ops = FingerprintAnomalyDetector().score(records)
    assert ops
    for op in ops:
        assert op.disposition is Disposition.FLAGGED
        assert op.after is None  # regulatory values on the record are untouched


def test_every_dedup_op_carries_confidence_and_provenance() -> None:
    cp_links = CounterpartyMatcher().link(
        [
            counterparty("C1", "ACME TRADING LTD", national_id="GHA-1"),
            counterparty("C2", "Acme Trading Limited", national_id="GHA-1"),
        ]
    )
    pos_links = PositionDeduplicator().link(
        [
            position("p1", source_reference="ARR/1", as_of_date="2026-03-31", balance_ghs="1000"),
            position("p2", source_reference="ARR/1", as_of_date="2026-04-30", balance_ghs="900"),
        ]
    )
    for link in [*cp_links, *pos_links]:
        assert 0.0 <= link.combined_confidence <= 1.0
        assert link.provenance.confidence is not None
        assert link.provenance.operation_type is ETLOperationType.DEDUP_LINK
        assert link.provenance.operation_ref
