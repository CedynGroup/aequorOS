"""The staged bundle: the offline artifact that decouples live pull from ingest.

Exactly like the Temenos adapter's stage-then-ingest split, a live pull writes a
single JSON *staged bundle* (one block per configured table, rows already
charset/timezone/locale-normalized), and the adapter's ``extract`` reads that
bundle OFFLINE and flattens it into :class:`RawRecord` data. Everything the
adapter and its whole contract suite exercise is reproducible from the recorded
bundle plus the mapping version — no live database is ever touched during
extraction, translation, or testing.

This module owns the pure, side-effect-free half:

- :class:`StagedBundle` / :class:`StagedTable` — the serialized artifact.
- :func:`parse_bundle` — bundle -> ``RawRecord`` list + per-table summaries.
- :func:`read_bundle` / :func:`write_bundle` — JSON (de)serialization.

The live half (opening a driver session, running the query builder, normalizing
result rows into a bundle) lives in ``pull.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.database_direct.config import Backend
from app.domain.ingestion.constants import ExtractionMode
from app.domain.ingestion.contracts import (
    RawRecord,
    RecordKind,
    SourceTableSummary,
)


class StagedTableError(ValueError):
    """The staged bundle is unreadable or not shaped as this adapter expects."""


class StagedTable(BaseModel):
    """One configured table's staged rows, native-column-keyed and normalized."""

    model_config = ConfigDict(frozen=True)

    name: str
    record_kind: RecordKind
    dataset_kind: str | None = None
    columns: tuple[str, ...] = ()
    rows: list[dict[str, Any]] = Field(default_factory=list)
    extraction_mode: ExtractionMode = "full"

    @property
    def row_count(self) -> int:
        return len(self.rows)


class StagedBundle(BaseModel):
    """The whole staged pull: metadata plus one block per configured table."""

    model_config = ConfigDict(frozen=True)

    backend: Backend
    as_of_date: str
    source_database: str = ""
    extraction_mode: ExtractionMode = "full"
    tables: list[StagedTable] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Cursor values reached per table this pull, for the next incremental run.
    incremental_cursors: dict[str, str] = Field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize deterministically so identical pulls stage identical bytes."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def write_bundle(bundle: StagedBundle, path: Path) -> None:
    path.write_text(bundle.to_json(), encoding="utf-8")


def read_bundle(path: Path) -> StagedBundle:
    """Read a staged bundle document from disk, classifying malformed input."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"cannot read staged database-direct bundle {path.name}: {exc}"
        raise StagedTableError(msg) from exc
    if not isinstance(document, dict):
        raise StagedTableError("staged database-direct bundle must be a JSON object.")
    try:
        return StagedBundle.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - re-raised as the adapter's read error
        raise StagedTableError(f"staged database-direct bundle is malformed: {exc}") from exc


def parse_bundle(
    bundle: StagedBundle,
    *,
    source_name: str,
    entity_types: set[str] | None = None,
) -> tuple[list[RawRecord], list[SourceTableSummary]]:
    """Flatten a staged bundle into raw records + per-table summaries.

    ``entity_types`` (when given) filters entity tables to the requested kinds;
    reference tables are always kept (they are consumed as-is downstream). Each
    record's ``source_locator`` binds the physical table and 1-based row index
    for lineage: ``COREBANK.DBO.GL_ACCOUNTS#R14``.
    """
    records: list[RawRecord] = []
    summaries: list[SourceTableSummary] = []
    prefix = f"{bundle.source_database}." if bundle.source_database else ""

    for table in bundle.tables:
        summaries.append(SourceTableSummary(name=table.name, row_count=table.row_count))
        is_reference = table.record_kind == "reference"
        if not is_reference and entity_types is not None and table.record_kind not in entity_types:
            continue
        for index, row in enumerate(table.rows, start=1):
            records.append(
                RawRecord(
                    entity_type="reference" if is_reference else table.record_kind,
                    source_locator=f"{prefix}{table.name}#R{index}",
                    data=row,
                    dataset_kind=table.dataset_kind if is_reference else None,
                    source_table=table.name,
                )
            )
    return records, summaries
