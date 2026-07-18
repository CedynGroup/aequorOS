"""The database-direct source adapter — one config-driven adapter, four backends.

Like the Temenos adapter, database-direct is stage-then-ingest, not live-query:
a pull (``pull.py``, over a backend :class:`DatabaseDriver`) connects read-only
to a bank's Oracle / SQL Server / JDBC- or ODBC-reachable core, runs the query
builder's parameterized ``SELECT`` statements, normalizes the rows, and stages
them as one JSON bundle. This adapter's ``extract`` then reads that staged bundle
OFFLINE and parses it into native records; ``translate`` maps those to canonical
record data via a generic, versioned :class:`MappingConfig`.

That split keeps every backend-specific concern (charset, timezone, locale
numbers, driver dialect, TLS, replica preference) confined to the driver + pull
layer, while extraction, translation, and the entire contract test suite run
with no live database. There is NO per-bank code anywhere: a bank is onboarded
through a :class:`ConnectionConfig`, an :class:`ExtractionSpec`, and a
:class:`MappingConfig`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from app.adapters.database_direct.extraction import (
    StagedBundle,
    StagedTableError,
    parse_bundle,
    read_bundle,
)
from app.adapters.database_direct.translate import translate as translate_records
from app.domain.ingestion.adapter import SourceAdapter, register_adapter
from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    EntityType,
    ExtractionResult,
    HealthStatus,
    MappingConfig,
    SourceColumn,
    SourceSchema,
    SourceTable,
)

ADAPTER_NAME = "database_direct"
ADAPTER_VERSION = "1.0"

# The four backends the driver abstraction serves, surfaced for documentation
# and registry introspection (spec §11.3: Oracle, SQL Server, plus the generic
# JDBC/ODBC bridges that also cover PostgreSQL and MySQL/MariaDB targets).
SUPPORTED_BACKENDS: tuple[str, ...] = ("oracle", "sqlserver", "jdbc", "odbc")


class DatabaseDirectAdapter(SourceAdapter):
    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(
            name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="DB_DIRECT"
        )

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        path = Path(config.location)
        if not path.is_file():
            return ConnectionStatus(
                ok=False, detail=f"Staged database-direct bundle {path} does not exist."
            )
        try:
            bundle = read_bundle(path)
        except StagedTableError as exc:
            return ConnectionStatus(ok=False, detail=str(exc))
        if not bundle.tables:
            return ConnectionStatus(ok=False, detail="Staged bundle contains no tables.")
        if not any(table.rows for table in bundle.tables):
            return ConnectionStatus(ok=False, detail="Staged bundle contains no rows.")
        return ConnectionStatus(ok=True)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        bundle = read_bundle(Path(config.location))
        tables: list[SourceTable] = []
        for table in bundle.tables:
            columns = _discover_columns(table.columns, table.rows)
            tables.append(SourceTable(name=table.name, columns=columns, row_count=table.row_count))
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        path = Path(config.location)
        content = path.read_bytes()
        bundle = read_bundle(path)
        records, summaries = parse_bundle(
            bundle,
            source_name=path.name,
            entity_types=set(entity_types),
        )
        return ExtractionResult(
            identity=self.identify(),
            as_of_date=as_of_date,
            extraction_mode=bundle.extraction_mode,
            content_hash=hashlib.sha256(content).hexdigest(),
            records=records,
            warnings=list(bundle.warnings),
            source_tables=summaries,
        )

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        return translate_records(raw_records, mapping_config)

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def _discover_columns(
    columns: tuple[str, ...], rows: list[dict[str, object]]
) -> tuple[SourceColumn, ...]:
    """Report a table's columns with up to three non-null sample values.

    The staged column list is authoritative (it is what the pull selected);
    rows without a value for a column simply contribute no sample.
    """
    names = columns or (tuple(rows[0].keys()) if rows else ())
    discovered: list[SourceColumn] = []
    for name in names:
        samples = tuple(str(row[name]) for row in rows[:3] if row.get(name) is not None)
        discovered.append(SourceColumn(name=name, sample_values=samples))
    return tuple(discovered)


def default_source_database(bundle: StagedBundle) -> str:
    """The source-database label a staged bundle carries (for lineage/reporting)."""
    return bundle.source_database


register_adapter("DB_DIRECT", DatabaseDirectAdapter)
