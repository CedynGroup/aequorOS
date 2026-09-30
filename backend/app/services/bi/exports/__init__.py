"""Governed exports: the three artifacts, and the policy that decides who gets them.

``docs/bi.md`` §Exports in four parts, one module each:

* :mod:`~app.services.bi.exports.policy` — **who may.** A summary export needs
  ``view``; a record-level or confidential one needs ``export`` as well, and
  which of the two a request IS is read from the catalogue's own sensitivity
  declarations, never from a flag on the body.
* :mod:`~app.services.bi.exports.context` — **what travels with the file.** The
  six provenance fields the spec names, the watermark, and the cell formatting.
* :mod:`~app.services.bi.exports.csv`, ``xlsx``, ``pdf`` — **the artifacts.**
  Streamed CSV, a write-only workbook with a protected metadata sheet, and a
  byte-deterministic PDF.
* :mod:`~app.services.bi.exports.jobs` — **the asynchronous path.** Over the
  async threshold the request becomes a ``bi_export`` job that renders in the
  ``bi`` worker lane, writes to the storage temp tier and hands back a presigned
  GET link.

What is NOT here, deliberately: authorization itself (``services/bi/
authorization.py`` makes the decision, this package only says which permissions
to ask it for), the query log and the audit event (the feature and the job own
those — ``app/services/bi`` may write ``bi_*`` tables and nothing else), and the
route (``app/features/export_bi.py``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Literal

from app.services.bi.exports import csv as csv_export
from app.services.bi.exports import pdf as pdf_export
from app.services.bi.exports import xlsx as xlsx_export
from app.services.bi.exports.context import (
    EXPORT_TITLE,
    METADATA_FIELDS,
    STANDING_NOTE,
    ExportColumn,
    ExportContext,
    ExportTable,
    query_lines,
    table_from,
    window_label,
)
from app.services.bi.exports.policy import (
    CLASS_LABELS,
    RECORD_LEVEL,
    SUMMARY,
    ExportClass,
    classify,
    classify_ids,
    permissions_for,
    record_level_members,
)

#: The wire vocabulary. Kept here rather than in ``app/schemas/bi.py`` so the
#: renderer that implements a format and the name a client asks for it by cannot
#: drift apart; the schema's Literal is asserted against this by a test.
ExportFormat = Literal["csv", "xlsx", "pdf"]
EXPORT_FORMATS: tuple[ExportFormat, ...] = ("csv", "xlsx", "pdf")

#: The ``bi_query_log`` surface every export is recorded under, for the paths
#: that cannot see ``read_bi``'s constants — the worker handler. A test pins the
#: two against each other and against ``models.bi.QUERY_LOG_SURFACES``.
QUERY_LOG_SURFACE = "export"

MEDIA_TYPES: Mapping[ExportFormat, str] = {
    "csv": csv_export.MEDIA_TYPE,
    "xlsx": xlsx_export.MEDIA_TYPE,
    "pdf": pdf_export.MEDIA_TYPE,
}

EXTENSIONS: Mapping[ExportFormat, str] = {
    "csv": csv_export.EXTENSION,
    "xlsx": xlsx_export.EXTENSION,
    "pdf": pdf_export.EXTENSION,
}


def render(fmt: ExportFormat, table: ExportTable, ctx: ExportContext) -> bytes:
    """The artifact, as bytes. One entry point so a format cannot be half-added."""

    if fmt == "csv":
        return csv_export.render_csv(table, ctx)
    if fmt == "xlsx":
        return xlsx_export.render_xlsx(table, ctx)
    if fmt == "pdf":
        return pdf_export.render_pdf(table, ctx)
    # Unreachable through the API (the schema's Literal is this vocabulary) and
    # kept as the local statement of it: a new format must be handled here.
    raise ValueError(f"unsupported BI export format: {fmt}")


def iter_bytes(fmt: ExportFormat, table: ExportTable, ctx: ExportContext) -> Iterator[bytes]:
    """The artifact as a stream. CSV streams row by row; the others are one chunk.

    The PDF and the workbook are produced whole by their libraries — reportlab
    lays out a document and openpyxl finalises a zip archive — so streaming them
    would be a lie about memory. CSV genuinely streams, which is the format a
    100 000-row export uses.
    """

    if fmt == "csv":
        yield from csv_export.iter_csv_bytes(table, ctx)
        return
    yield render(fmt, table, ctx)


def filename_for(fmt: ExportFormat, *, bank_id: str, as_of_label: str) -> str:
    """A stable, safe download name: institution, window, format. No clock."""

    window = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in as_of_label
    ).strip("-")
    return f"{bank_id}-analytics-{window}.{EXTENSIONS[fmt]}"


__all__ = [
    "CLASS_LABELS",
    "EXPORT_FORMATS",
    "EXPORT_TITLE",
    "EXTENSIONS",
    "MEDIA_TYPES",
    "METADATA_FIELDS",
    "QUERY_LOG_SURFACE",
    "RECORD_LEVEL",
    "STANDING_NOTE",
    "SUMMARY",
    "ExportClass",
    "ExportColumn",
    "ExportContext",
    "ExportFormat",
    "ExportTable",
    "classify",
    "classify_ids",
    "filename_for",
    "iter_bytes",
    "permissions_for",
    "query_lines",
    "record_level_members",
    "render",
    "table_from",
    "window_label",
]
