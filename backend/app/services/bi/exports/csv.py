"""CSV rendering of a governed export: streamed, provenance first, then the grid.

Shape, which follows the regulatory CSV exporter's own convention so the two
families of artifact read alike:

1. a ``field,value`` provenance block — the six fields ``docs/bi.md`` §Exports
   requires, then the supplementary ones;
2. one blank line;
3. the header row, in the compiler's column order;
4. the rows.

**Streamed.** The rows are yielded a line at a time and never assembled into one
string, so a 100 000-row export costs one row of memory rather than the whole
file. ``iter_csv`` is therefore what the route hands to a streaming response and
what the worker feeds to the object store; :func:`render_csv` exists for the
tests and for callers that genuinely need the bytes.

**Machine-readable cells.** Plain decimal strings, a minus sign for negatives, no
thousands separators and no unit symbols — the unit is stated once, in the
provenance block and in the amount columns' own headings. Display conventions
belong to the PDF.

**Text cannot execute.** Dimension values are bank data: a counterparty may be
named ``=HYPERLINK(...)``. Every text cell goes through ``context.inert`` before
it is written, which is the same defence the regulatory exporter applies to
narrative cells.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

from app.services.bi.exports.context import (
    EXPORT_TITLE,
    ExportContext,
    ExportTable,
    machine_cell,
)

#: One line ending, everywhere, on every platform. A CSV whose line endings
#: depend on the machine that produced it is not comparable between exports.
LINE_TERMINATOR = "\n"

MEDIA_TYPE = "text/csv; charset=utf-8"
EXTENSION = "csv"


def _line(*values: str) -> str:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=LINE_TERMINATOR).writerow(values)
    return buffer.getvalue()


def iter_csv(table: ExportTable, ctx: ExportContext) -> Iterator[str]:
    """The whole file, one line at a time, provenance first."""

    yield _line("field", "value")
    yield _line("Export", EXPORT_TITLE)
    for field, value in ctx.metadata_rows():
        yield _line(field, value)
    if table.truncated:
        yield _line(
            "Completeness",
            f"Truncated at the export row cap: {table.row_count} rows shown. "
            "Narrow the window or add a filter to export the remainder.",
        )
    yield LINE_TERMINATOR
    yield _line(*(column.label for column in table.columns))
    for row in table.rows:
        yield _line(
            *(
                machine_cell(value, column)
                for value, column in zip(row, table.columns, strict=False)
            )
        )


def iter_csv_bytes(table: ExportTable, ctx: ExportContext) -> Iterator[bytes]:
    """:func:`iter_csv` as UTF-8, which is what a response and the store take."""

    for line in iter_csv(table, ctx):
        yield line.encode("utf-8")


def render_csv(table: ExportTable, ctx: ExportContext) -> bytes:
    """The whole file as bytes. Equivalent to joining :func:`iter_csv_bytes`."""

    return b"".join(iter_csv_bytes(table, ctx))


__all__ = ["EXTENSION", "LINE_TERMINATOR", "MEDIA_TYPE", "iter_csv", "iter_csv_bytes", "render_csv"]
