"""Database-Direct core-database connection management API on the ACTUAL primary.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do. The live test / discover /
sync endpoints run against an offline fixture driver patched over the service's
``driver_for`` seam, so no live database is ever reached. Invariants: activation
on valid credentials, TESTING on a bad shape, name conflicts (409), lifecycle
states, the sync routes through the ingestion spine (422 without a mapping,
202 + a batch with one), tenant isolation. The real bank already holds
connections; the vault key is set by the test (never read from .env). Opt-in via
REAL_DATA_DATABASE_URL, rolled back (tests/real_data.py).

This module's other-suite cases live in ``test_database_connections_integration.py``.
"""

from __future__ import annotations

from datetime import date

from app.data_engine.adapters.database_direct.config import ExtractionSpec
from app.data_engine.adapters.database_direct.extraction import StagedBundle, StagedTable
from app.data_engine.service.database_connections import _reconcile_as_of


class TestAsOfReconciliation:
    """The sync adopts the snapshot's own reporting date over a wrong request
    (pure — no database)."""

    def _bundle(self, as_of_value: str) -> StagedBundle:
        return StagedBundle(
            backend="oracle",
            as_of_date="2026-06-30",
            source_database="CORE",
            extraction_mode="full",
            tables=[
                StagedTable(
                    name="CORE.IFTB_DEPOSIT",
                    record_kind="position",
                    dataset_kind=None,
                    columns=("SOURCE_REFERENCE", "AS_OF_DATE"),
                    rows=[{"SOURCE_REFERENCE": "D1", "AS_OF_DATE": as_of_value}],
                    extraction_mode="full",
                ),
            ],
            warnings=[],
            incremental_cursors={},
        )

    def test_adopts_source_date_on_mismatch(self) -> None:
        spec = ExtractionSpec(as_of_column="AS_OF_DATE")
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-04-30T00:00:00"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 4, 30)
        assert note is not None and "2026-04-30" in note

    def test_no_change_when_dates_match(self) -> None:
        spec = ExtractionSpec(as_of_column="AS_OF_DATE")
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-06-30"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 6, 30)
        assert note is None

    def test_no_reconciliation_without_as_of_column(self) -> None:
        spec = ExtractionSpec()  # as_of_column unset -> requested date stands
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-04-30"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 6, 30)
        assert note is None
