"""Canonical identity, bank ownership and provenance use exact comparison."""

from decimal import Decimal

from app.data_engine.domain.ingestion.validation import ValidationOutcome
from tests.data_engine.testing.generators import CanonicalBook
from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(
    Decimal(0),
    "canonical identity / six-place amounts",
    "models/canonical.py composite bank FKs, NUMERIC(28,6); no rounding on identity",
)


def assert_canonical_links(book: CanonicalBook) -> None:
    TOLERANCE.assert_exact(
        book.batch.records_accepted,
        book.records.record_count,
        because="Canonical: accepted batch count differs from records",
    )
    scope = book.batch.organization_id, book.batch.bank_id
    for row in (book.lineage, book.counterparty, book.product, *book.positions, *book.snapshots):
        TOLERANCE.assert_exact(
            (row.organization_id, row.bank_id),
            scope,
            because="Canonical: record escapes batch bank",
        )
        TOLERANCE.assert_exact(
            row.ingestion_batch_id, book.batch.id, because="Canonical: dangling batch reference"
        )
    for row in (book.counterparty, book.product, *book.positions, *book.snapshots):
        TOLERANCE.assert_exact(
            row.lineage_id, book.lineage.id, because="Canonical: dangling lineage reference"
        )
        TOLERANCE.assert_exact(
            row.as_of_date,
            book.batch.as_of_date,
            because="Canonical: snapshot date differs from batch",
        )
    assert len(book.positions) == len(book.snapshots) == len(book.records.positions), (
        "Canonical: snapshot/position cardinality differs"
    )
    for position, snapshot, record in zip(
        book.positions, book.snapshots, book.records.positions, strict=True
    ):
        TOLERANCE.assert_exact(
            snapshot.position_id,
            position.id,
            because="Canonical: snapshot points at another position",
        )
        TOLERANCE.assert_exact(
            snapshot.counterparty_id,
            book.counterparty.id,
            because="Canonical: borrower link is dangling",
        )
        TOLERANCE.assert_exact(
            snapshot.product_id, book.product.id, because="Canonical: product link is dangling"
        )
        TOLERANCE.assert_exact(
            snapshot.balance, record.balance, because="Canonical: materialized balance changed"
        )
        TOLERANCE.assert_exact(
            position.source_reference,
            record.source_reference,
            because="Canonical: source identity changed",
        )


def assert_canonical_validation_unchanged(
    after: ValidationOutcome, before: ValidationOutcome
) -> None:
    TOLERANCE.assert_exact(
        after.record_statuses,
        before.record_statuses,
        because="Canonical: reordering or scaling changes record acceptance",
    )
    TOLERANCE.assert_exact(
        sorted((f.rule, f.source_reference, f.severity) for f in after.findings),
        sorted((f.rule, f.source_reference, f.severity) for f in before.findings),
        because="Canonical: reordering or scaling changes validation findings",
    )
