"""Model↔migration parity for the things the column checks do NOT cover.

Audit finding A8-05. Two suites already compare the migrated schema to the model:
one on column type, length and nullability, one on CHECK constraint names and the
values each admits. Between them they miss three structural facts, and each has a
different failure mode when it drifts:

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
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from app.db.base import Base
from app.models.bi import BI_TABLES, MONTHLY_PARTITIONED_TABLES, YEARLY_PARTITIONED_TABLES
from app.models.bi_commentary import BI_COMMENTARY_TABLES
from app.models.bi_content import BI_CONTENT_TABLES
from app.models.bi_notifications import BI_NOTIFICATION_TABLES
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    migrated_postgres_schema,
    postgres_schema_url,
)

__all__ = ["migrated_postgres_schema", "postgres_schema_url"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

#: Every tenant table of the BI plane. ``BI_COMMENTARY_TABLES`` is listed
#: separately from the rest because it is the one that is NOT ``bi_``-prefixed
#: (see that constant's own comment for why the name is correct), and a subject
#: derived from the prefix therefore left ``ai_commentary_drafts`` — its RLS, its
#: composite parent key and its indexes — unverified against the migrated schema
#: (audit A9-08).
ALL_BI_TABLES: tuple[str, ...] = (
    *BI_TABLES,
    *BI_CONTENT_TABLES,
    *BI_NOTIFICATION_TABLES,
    *BI_COMMENTARY_TABLES,
)

#: The half of the subject the prefix rule governs. Kept separate so the
#: completeness check below stays exact instead of becoming a ``>=``.
PREFIXED_BI_TABLES: frozenset[str] = frozenset(ALL_BI_TABLES) - frozenset(BI_COMMENTARY_TABLES)
PARTITIONED: frozenset[str] = frozenset((*MONTHLY_PARTITIONED_TABLES, *YEARLY_PARTITIONED_TABLES))


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


def test_primary_key_columns_and_their_ORDER_match_the_model(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in ALL_BI_TABLES:
        model = [column.name for column in Base.metadata.tables[table_name].primary_key]
        migrated = list(
            inspector.get_pk_constraint(
                table_name, schema=migrated_postgres_schema.schema_name
            ).get("constrained_columns")
            or []
        )
        if model != migrated:
            drift.append(f"{table_name}: model {model} != migrated {migrated}")
    assert not drift, (
        "the primary key ORDER differs between the model and the migration. For a "
        "partitioned table the range column must LEAD the key or it prunes nothing, "
        "and for every table the leading columns are the access path: " + "; ".join(drift)
    )
    for table_name in PARTITIONED:
        key = [column.name for column in Base.metadata.tables[table_name].primary_key]
        range_column = {**MONTHLY_PARTITIONED_TABLES, **YEARLY_PARTITIONED_TABLES}[table_name]
        assert key[0] == range_column, f"{table_name}: {range_column} must lead {key}"


def test_every_foreign_key_is_the_composite_that_pins_the_tenant(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The composite is what stops a child pointing at another tenant's parent."""
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in ALL_BI_TABLES:
        table = Base.metadata.tables[table_name]
        model = {
            (
                tuple(element.parent.name for element in constraint.elements),
                tuple(element.column.table.name for element in constraint.elements),
            )
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
        migrated = {
            (
                tuple(fk["constrained_columns"]),
                (fk["referred_table"],) * len(fk["constrained_columns"]),
            )
            for fk in inspector.get_foreign_keys(
                table_name, schema=migrated_postgres_schema.schema_name
            )
        }
        if model != migrated:
            drift.append(f"{table_name}: model {sorted(model)} != migrated {sorted(migrated)}")
        for columns, _ in model:
            if len(columns) > 1:
                assert "organization_id" in columns, f"{table_name}: {columns} omits the tenant"
    assert not drift, (
        "the foreign keys differ between the model and the migration. A single-column "
        "key where the model declares a composite would let a child row point at "
        "another tenant's parent: " + "; ".join(drift)
    )


def test_every_declared_index_exists_in_the_migration(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """A missing index fails nothing and costs everything.

    It is not a correctness defect, so no test fails and no query errors — the
    planner simply does more work, which is how a twelve-month question came to
    scan sixty partitions this week.
    """
    inspector = inspect(migrated_postgres_schema.app_engine)
    missing: list[str] = []
    for table_name in ALL_BI_TABLES:
        declared = {index.name for index in Base.metadata.tables[table_name].indexes if index.name}
        if not declared:
            continue
        migrated = {
            index["name"]
            for index in inspector.get_indexes(
                table_name, schema=migrated_postgres_schema.schema_name
            )
            if index.get("name")
        }
        missing.extend(f"{table_name}.{name}" for name in sorted(declared - migrated))
    assert not missing, (
        "these indexes are declared on the model and absent from the migration, so "
        "the query path pays for them without having them: " + ", ".join(missing)
    )


def test_the_index_check_has_a_non_empty_subject() -> None:
    """Otherwise the check above passes on a plane with no indexes declared."""
    declared = sum(
        len([index for index in Base.metadata.tables[name].indexes if index.name])
        for name in ALL_BI_TABLES
    )
    assert declared >= 10, f"only {declared} named bi_* indexes found; the check is near-vacuous"
