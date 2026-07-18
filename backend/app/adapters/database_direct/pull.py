"""Live pull orchestration: the one place a driver session is opened and read.

``stage_pull`` binds together the config-driven pieces — a backend
:class:`DatabaseDriver`, a :class:`ConnectionConfig`, transient
:class:`DbCredentials`, an :class:`ExtractionSpec`, and a
:class:`NormalizationPolicy` — into a single read-only pass that produces a
:class:`StagedBundle`. It NEVER writes to the source: it opens one session
(replica-preferred inside the driver), runs the parameterized ``SELECT`` the
query builder emits for each configured table, normalizes every result row, and
records the bundle.

Passing the offline fixture driver here runs the entire pull path — query
construction, normalization, bundle assembly — with no live database, which is
how the contract suite exercises it. The credential vault seam supplies
:class:`DbCredentials` for exactly one cycle; the caller discards them after.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app.adapters.database_direct.config import (
    ConnectionConfig,
    ExtractionSpec,
    TableExtraction,
)
from app.adapters.database_direct.drivers.base import DatabaseDriver, DbCredentials
from app.adapters.database_direct.extraction import StagedBundle, StagedTable, write_bundle
from app.adapters.database_direct.normalization import NormalizationPolicy, normalize_row
from app.adapters.database_direct.query_builder import build_select
from app.domain.ingestion.constants import ExtractionMode


def stage_pull(  # noqa: PLR0913 - a pull binds driver, connection, creds, spec, and options
    driver: DatabaseDriver,
    connection: ConnectionConfig,
    credentials: DbCredentials,
    spec: ExtractionSpec,
    *,
    as_of: date,
    mode: ExtractionMode | None = None,
    normalization: NormalizationPolicy | None = None,
    incremental_cursors: dict[str, Any] | None = None,
) -> StagedBundle:
    """Run one read-only pull and return a staged bundle.

    ``mode`` overrides the spec default. In ``incremental`` mode a table with an
    ``incremental_column`` is pulled with a ``cursor > :since`` predicate seeded
    from ``incremental_cursors``; the cursor reached is recorded for the next
    run. Tables without a cursor are always full-refresh (§5.3: not every source
    supports incremental).
    """
    resolved_mode: ExtractionMode = mode or spec.default_mode
    policy = normalization or NormalizationPolicy()
    cursors = dict(incremental_cursors or {})
    warnings: list[str] = []
    staged_tables: list[StagedTable] = []
    next_cursors: dict[str, str] = {}

    with driver.connect(connection, credentials) as session:
        for extraction in spec.tables:
            table_mode = _table_mode(resolved_mode, extraction, warnings)
            since = cursors.get(extraction.table) if table_mode == "incremental" else None
            query = build_select(extraction, connection.backend, incremental_since=since)
            result = session.fetch(query)
            rows = [normalize_row(row, policy) for row in result.as_dicts()]
            if extraction.constant_fields:
                # Inject table-implied constants (e.g. position_type) as synthetic
                # columns on every row so the mapping reads them like any other column.
                for row in rows:
                    row.update(extraction.constant_fields)
            base_columns = result.columns or tuple(extraction.columns)
            columns = base_columns + tuple(
                name for name in extraction.constant_fields if name not in base_columns
            )
            staged_tables.append(
                StagedTable(
                    name=extraction.table,
                    record_kind=extraction.record_kind,
                    dataset_kind=extraction.dataset_kind,
                    columns=columns,
                    rows=rows,
                    extraction_mode=table_mode,
                )
            )
            cursor_value = _cursor_reached(extraction, rows)
            if cursor_value is not None:
                next_cursors[extraction.table] = cursor_value

    return StagedBundle(
        backend=connection.backend,
        as_of_date=as_of.isoformat(),
        source_database=connection.database or connection.service_name or "",
        extraction_mode=resolved_mode,
        tables=staged_tables,
        warnings=warnings,
        incremental_cursors=next_cursors,
    )


def stage_pull_to_path(  # noqa: PLR0913 - thin wrapper over stage_pull plus a path
    driver: DatabaseDriver,
    connection: ConnectionConfig,
    credentials: DbCredentials,
    spec: ExtractionSpec,
    path: Path,
    *,
    as_of: date,
    mode: ExtractionMode | None = None,
    normalization: NormalizationPolicy | None = None,
    incremental_cursors: dict[str, Any] | None = None,
) -> StagedBundle:
    """Run a pull and write the staged bundle to ``path`` (the adapter's source)."""
    bundle = stage_pull(
        driver,
        connection,
        credentials,
        spec,
        as_of=as_of,
        mode=mode,
        normalization=normalization,
        incremental_cursors=incremental_cursors,
    )
    write_bundle(bundle, path)
    return bundle


def _table_mode(
    requested: ExtractionMode, extraction: TableExtraction, warnings: list[str]
) -> ExtractionMode:
    """Resolve the effective mode for one table.

    A table with no cursor column cannot be pulled incrementally; it degrades to
    a full refresh with a recorded warning rather than silently returning an
    empty or wrong delta.
    """
    if requested == "incremental" and not extraction.incremental_column:
        warnings.append(
            f"table {extraction.table!r} has no incremental_column; pulled full-refresh."
        )
        return "full"
    return requested


def _cursor_reached(extraction: TableExtraction, rows: list[dict[str, Any]]) -> str | None:
    """The high-water cursor value this pull reached, for the next incremental run.

    Rows are ordered by the cursor ascending (the query builder adds the ORDER
    BY), so the last row carries the maximum. Values are already normalized to
    strings, giving a stable, JSON-safe cursor to persist.
    """
    if not extraction.incremental_column or not rows:
        return None
    last = rows[-1].get(extraction.incremental_column)
    return None if last is None else str(last)
