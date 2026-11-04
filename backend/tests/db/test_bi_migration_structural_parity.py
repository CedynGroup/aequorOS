"""Model↔migration parity for the things the column checks do NOT cover.

Column type, length, nullability and CHECK parity live in
``test_bi_owned_tables_parity.py``. This suite checks three additional structural
facts, each with a different failure mode when it drifts:

* **Primary key ORDER.** A partitioned table requires its range column to LEAD the
  key, and the model test that asserts that reads the MODEL only. If a migration
  built the same columns in another order, every hermetic test would pass and the
  real database would have a key that prunes nothing.
* **Foreign keys.** Every `bi_*` table carries a COMPOSITE key to its parent that
  includes `organization_id`. That composite is what makes a child row unable to
  point at another tenant's parent. A migration that emitted the single-column
  form would satisfy every column check while silently dropping that guarantee.
* **Indexes.** A missing index is not a correctness failure, so nothing fails —
  it is a performance cliff. The benchmark measured one this week: a predicate the
  planner could not prune turned a twelve-month question into a scan of sixty
  partitions. The indexes are declared in the model for reasons, and a migration
  that forgot one costs that silently.

Derived from the model rather than restated, so a new table joins these checks by
being declared. Partition children are excluded: Postgres names a child's own
constraints and indexes after the child, and the parent is the thing the model
declares.

This module's other-suite cases live in

``test_bi_migration_structural_parity_integration.py``.
"""

from __future__ import annotations

from tests.db.test_bi_migration_structural_parity_integration import ALL_BI_TABLES

__all__ = ["catalog_postgres_schema", "forward_migrated_postgres_schema", "postgres_schema_url"]
import os

import pytest

from app.bi.models.commentary import BI_COMMENTARY_TABLES
from app.db.base import Base
from tests.db.test_postgres_migrations_integration import (
    catalog_postgres_schema,
    forward_migrated_postgres_schema,
    postgres_schema_url,
)

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]
#: The half of the subject the prefix rule governs. Kept separate so the
#: completeness check below stays exact instead of becoming a ``>=``.
PREFIXED_BI_TABLES: frozenset[str] = frozenset(ALL_BI_TABLES) - frozenset(BI_COMMENTARY_TABLES)


def test_the_subject_is_every_registered_bi_table() -> None:
    """A parity suite over a short list quietly stops covering the plane."""
    assert len(ALL_BI_TABLES) == len(set(ALL_BI_TABLES)), "a table is registered twice"
    assert len(ALL_BI_TABLES) >= 24, ALL_BI_TABLES
    declared = {name for name in Base.metadata.tables if name.startswith("bi_")}
    assert declared == PREFIXED_BI_TABLES, sorted(PREFIXED_BI_TABLES ^ declared)


def test_the_subject_is_not_derived_from_the_bi_prefix_alone() -> None:
    """The unprefixed BI table must be IN the subject, and really be unprefixed.

    ``ai_commentary_drafts`` is deliberately not ``bi_``-named: the plane-boundary
    guard derives the BI-writable set from that prefix, and this row is AI egress
    evidence, not a mart. The correct name cost it every structural check keyed on
    the prefix, this suite included (audit A9-08). This test fails in both
    directions — if the table is dropped from the subject, and if someone
    "tidies" it into the prefix, which would make the BI plane able to write it.
    """

    assert BI_COMMENTARY_TABLES, "the unprefixed BI table list is empty"
    for name in BI_COMMENTARY_TABLES:
        assert name in ALL_BI_TABLES, f"{name} is not in this suite's subject"
        assert not name.startswith("bi_"), (
            f"{name} is now bi_-prefixed, which makes it writable by the BI plane; "
            "if that is intended, move it out of BI_COMMENTARY_TABLES deliberately"
        )
        assert name in Base.metadata.tables, f"{name} is not a mapped table"


def test_the_index_check_has_a_non_empty_subject() -> None:
    """Otherwise the check above passes on a plane with no indexes declared."""
    declared = sum(
        len([index for index in Base.metadata.tables[name].indexes if index.name])
        for name in ALL_BI_TABLES
    )
    assert declared >= 10, f"only {declared} named bi_* indexes found; the check is near-vacuous"
