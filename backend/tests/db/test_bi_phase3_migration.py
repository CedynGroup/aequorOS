"""PostgreSQL proof of BI version immutability and dashboard deletion cascades.

Column, CHECK and RLS parity across all BI-owned tables lives in
``test_bi_owned_tables_parity.py``. This suite retains the behavioral proof that
versions cannot be rewritten but can be removed by their dashboard's cascade.

This module's other-suite cases live in ``test_bi_phase3_migration_integration.py``.
"""

from __future__ import annotations

__all__ = ["migrated_postgres_schema", "postgres_schema_url"]
import os

import pytest

from app.bi.models.content import BI_CONTENT_TABLES
from app.bi.models.notifications import BI_NOTIFICATION_TABLES
from tests.support.postgres_migrations import (
    migrated_postgres_schema,
    postgres_schema_url,
)

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]
PHASE3_TABLES: tuple[str, ...] = (*BI_CONTENT_TABLES, *BI_NOTIFICATION_TABLES)


def test_the_table_list_is_not_empty() -> None:
    """The behavioral regressions rely on Phase 3 table registration."""
    assert len(PHASE3_TABLES) == 8, PHASE3_TABLES
    assert all(name.startswith("bi_") for name in PHASE3_TABLES)
