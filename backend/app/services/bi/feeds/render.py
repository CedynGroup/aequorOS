"""The two wire formats a report server pulls: NDJSON and CSV.

Both are written a LINE AT A TIME and neither is ever assembled into one string:
a feed has no row cap to hide behind, so the cost of a pull must be one row of
memory rather than the whole payload.

**The column names are catalogue member ids** (``loans.balance_rc``,
``branch.code``), which is what makes a feed stable enough to build a report
model on: a member id is a wire key by the catalogue's own convention, renaming
one is a catalogue version bump, and nothing in the header depends on a label, a
locale or a currency. The reporting unit travels in the response metadata, not
in a column name — a header reading ``Balance (GHS)`` would put a currency in the
consumer's schema and break it the day the same feed served a second
jurisdiction.

**Flags are ``true``/``false``, in BOTH formats.** This is the one place the feed
deliberately differs from the governed export's ``machine_cell``, which writes
``Yes``/``No``: that is display copy, and it is English. A report server reads a
boolean.

**Amounts are exact.** A money value is written as its own decimal literal — in
CSV, and in NDJSON as a JSON NUMBER rather than a float conversion — so a balance
with fifteen significant digits survives the wire. That is why NDJSON is composed
with :func:`canonical_text` per value rather than handed to ``json.dumps``
wholesale: ``json.dumps`` cannot write a ``Decimal`` at all, and the obvious
repairs either quote it (turning every amount into text) or round it through a
float.

**Text cannot execute.** Dimension values are bank data: a product family may be
named ``=HYPERLINK(...)``. Text goes through ``exports.context.inert`` before it
is written to CSV, the same defence the governed exports and the regulatory CSV
exporter apply. NDJSON needs no such treatment (nothing opens a JSON document in
a spreadsheet) and deliberately does not get it, so the value a consumer parses
out of JSON is the value the mart holds.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from app.services.bi.compiler import ColumnSpec
from app.services.bi.exports.context import inert

#: The wire vocabulary. One tuple so a format cannot be half-added: the schema's
#: ``Literal``, the media-type map and the writer below are pinned against it.
FeedFormat = Literal["ndjson", "csv"]
FEED_FORMATS: Final[tuple[FeedFormat, ...]] = ("ndjson", "csv")

NDJSON_MEDIA_TYPE: Final = "application/x-ndjson; charset=utf-8"
CSV_MEDIA_TYPE: Final = "text/csv; charset=utf-8"
MEDIA_TYPES: Final[dict[FeedFormat, str]] = {
    "ndjson": NDJSON_MEDIA_TYPE,
    "csv": CSV_MEDIA_TYPE,
}
EXTENSIONS: Final[dict[FeedFormat, str]] = {"ndjson": "ndjson", "csv": "csv"}

#: One line ending on every platform, for the same reason the export CSV pins
#: one: a file whose line endings depend on the machine that produced it is not
#: comparable between pulls.
LINE_TERMINATOR: Final = "\n"

#: What a NULL reads as in CSV. Never a zero: a measure with no rows and a
#: measure that is zero are different statements (the marts' own rule).
BLANK: Final = ""

#: Column formats whose values are numbers, DERIVED from the catalogue's own
#: vocabulary so a value type added there and forgotten here falls through to
#: text rather than being written as a number it may not be.
_NUMERIC_FORMATS: Final[frozenset[str]] = frozenset(
    {"amount", "pct", "fraction", "index", "duration_years", "count", "int"}
)


def canonical_text(value: Any, column: ColumnSpec) -> str:  # noqa: PLR0911 - one branch per value shape
    """One value as the exact text both formats carry.

    The single definition of "what this cell says", so a CSV pull and an NDJSON
    pull of the same slice cannot disagree about a figure. CSV writes this
    verbatim (after inerting text); NDJSON writes it as a JSON number for the
    numeric formats and as a JSON string otherwise.
    """

    if value is None:
        return BLANK
    if column.format == "flag":
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        # A boolean that reached a non-flag column: still a boolean, not 1/0.
        return "true" if value else "false"
    return str(value)


def _json_field(value: Any, column: ColumnSpec) -> str:
    if value is None:
        return "null"
    text = canonical_text(value, column)
    if column.format == "flag" or isinstance(value, bool):
        return text
    if column.format in _NUMERIC_FORMATS:
        return text
    return json.dumps(text)


def ndjson_line(row: Sequence[Any], columns: Sequence[ColumnSpec]) -> str:
    """One row as one JSON object, keyed by member id, in the compiler's order."""

    body = ",".join(
        f"{json.dumps(column.id)}:{_json_field(value, column)}"
        for value, column in zip(row, columns, strict=True)
    )
    return "{" + body + "}" + LINE_TERMINATOR


def csv_line(*values: str) -> str:
    """One CSV record, quoted by the standard writer, with one line ending."""

    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=LINE_TERMINATOR).writerow(values)
    return buffer.getvalue()


def csv_header(columns: Sequence[ColumnSpec]) -> str:
    """The header row: member ids, so the consumer's schema is the catalogue's."""

    return csv_line(*(column.id for column in columns))


def csv_row(row: Sequence[Any], columns: Sequence[ColumnSpec]) -> str:
    """One row as CSV, amounts exact and text made inert."""

    cells: list[str] = []
    for value, column in zip(row, columns, strict=True):
        text = canonical_text(value, column)
        cells.append(text if column.format in _NUMERIC_FORMATS or not text else inert(text))
    return csv_line(*cells)


def iter_lines(
    rows: Iterable[Sequence[Any]], columns: Sequence[ColumnSpec], fmt: FeedFormat
) -> Iterator[str]:
    """The whole payload, one line at a time, in ``fmt``.

    CSV leads with a header row and NDJSON does not: an NDJSON stream is
    self-describing and a header object would make its records heterogeneous,
    which is exactly what breaks a consumer that maps one schema over the file.
    """

    if fmt == "csv":
        yield csv_header(columns)
        for row in rows:
            yield csv_row(row, columns)
        return
    if fmt == "ndjson":
        for row in rows:
            yield ndjson_line(row, columns)
        return
    # Unreachable through the API (the route's Literal is this vocabulary) and
    # kept as the local statement of it: a new format must be handled here.
    raise ValueError(f"unsupported BI feed format: {fmt}")


def iter_bytes(
    rows: Iterable[Sequence[Any]], columns: Sequence[ColumnSpec], fmt: FeedFormat
) -> Iterator[bytes]:
    """:func:`iter_lines` as UTF-8, which is what a streaming response takes."""

    for line in iter_lines(rows, columns, fmt):
        yield line.encode("utf-8")


__all__ = [
    "BLANK",
    "CSV_MEDIA_TYPE",
    "EXTENSIONS",
    "FEED_FORMATS",
    "LINE_TERMINATOR",
    "MEDIA_TYPES",
    "NDJSON_MEDIA_TYPE",
    "FeedFormat",
    "canonical_text",
    "csv_header",
    "csv_line",
    "csv_row",
    "iter_bytes",
    "iter_lines",
    "ndjson_line",
]
