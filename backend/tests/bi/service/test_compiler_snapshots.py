"""Compiled SQL snapshots for representative queries (SQLite dialect).

Each query below compiles to exactly the text in ``snapshots/<name>.sql``,
rendered against the SQLite dialect with every bound parameter shown as ``?``
(so no value — tenant id, date, filter — is in the snapshot, only the shape).
A change in emitted SQL fails the test; when the change is deliberate,
regenerate with ``BI_SNAPSHOT_UPDATE=1`` and review the diff like code.

The snapshot text is the SQLite rendering: subtotals appear as the UNION ALL
emulation, not ``ROLLUP`` (``tests/db/test_bi_query_execution_integration.py`` proves the
Postgres form).

This module's other-suite cases live in ``test_compiler_snapshots_integration.py``.
"""

from __future__ import annotations

from tests.bi.service.test_compiler_snapshots_integration import QUERIES, SNAPSHOTS


def test_every_snapshot_file_belongs_to_a_query() -> None:
    on_disk = {path.stem for path in SNAPSHOTS.glob("*.sql")}
    assert on_disk == set(QUERIES), "a snapshot file has no query (or a query has no snapshot)"
