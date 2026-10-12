"""Generated canonical books pass public validation and preserve provenance."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.ingestion.contracts import CanonicalRecords
from app.domain.ingestion.validation import (
    ValidationContext,
    default_validation_config,
    run_validation,
)
from tests.data_engine.testing.assertions import (
    assert_canonical_links,
    assert_canonical_validation_unchanged,
)
from tests.data_engine.testing.generators import CanonicalBook, canonical_books


@given(book=canonical_books(), scale=st.integers(1, 10))
def test_books_are_valid_and_reordering_or_scaling_preserves_acceptance(
    book: CanonicalBook, scale: int
) -> None:
    assert_canonical_links(book)
    snapshot = book.snapshots[0]
    scope = snapshot.bank_id
    snapshot.bank_id = "BK-000000000002"
    with pytest.raises(AssertionError, match="Canonical: record escapes batch bank"):
        assert_canonical_links(book)
    snapshot.bank_id = scope
    context = ValidationContext(as_of_date=book.batch.as_of_date)
    config = default_validation_config()
    before = run_validation(book.records, config, context)
    assert not any(f.severity in ("ERROR", "BLOCKER", "WARNING") for f in before.findings), (
        f"Canonical: generated invalid instances: {before.findings}"
    )
    changed = CanonicalRecords(
        counterparties=book.records.counterparties,
        products=book.records.products,
        positions=[
            row.model_copy(update={"balance": row.balance * scale})
            for row in reversed(book.records.positions)
        ],
    )
    assert_canonical_validation_unchanged(run_validation(changed, config, context), before)
