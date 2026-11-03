"""How much an uploaded spreadsheet is allowed to become.

An ``.xlsx`` is a zip of XML, and the 50 MB cap on an upload measures the
COMPRESSED bytes. A few kilobytes of shared strings and sheet rows can
therefore declare and hold tens of gigabytes once decompressed, which is why a
crafted workbook can exhaust the process that parses it (issue #702). openpyxl
has no bomb guard on either of the reader paths, so the platform imposes one
before and while it reads:

* :func:`inspect_archive` checks declared total uncompressed size and expansion
  ratio from the central directory before decompressing any entry;
* :func:`bounded_rows` limits the rectangular grids yielded by the worksheet
  iterator, rather than trusting declared worksheet dimensions.

These checks are not a bound on all parser allocations: workbook metadata is
loaded before row iteration, and each row is constructed before it is counted
here. The Data Engine also scans raw worksheet XML during merge extraction.
Neither layer decides what the workbook MEANS. Submission limits are documented
in ``backend/README.md`` under "Upload and workbook limits".

The caps are generous against every template the platform ships: the shipped
BoG layouts and the §8.2 market data templates are hundreds of rows, and a
full chart of accounts is tens of thousands.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO, Any, Protocol

from openpyxl.worksheet._read_only import ReadOnlyWorksheet

#: Rows in one worksheet. A bank's largest real sheet — a full general ledger
#: extract — is tens of thousands; a hundred thousand is not a misreading of a
#: template but a file nobody can review.
MAX_ROWS_PER_SHEET = 100_000

#: Cells across every sheet of one workbook, so splitting a bomb over many
#: small sheets does not evade the row cap.
MAX_CELLS_PER_WORKBOOK = 2_000_000

#: Uncompressed bytes across the whole archive. The 50 MB upload cap measures
#: compressed bytes; a legitimate workbook expands by roughly one order of
#: magnitude, and this leaves generous room over that.
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

#: Declared uncompressed bytes per compressed byte. Highly repetitive legitimate
#: files can also exceed this policy limit.
MAX_EXPANSION_RATIO = 100

#: Below this uncompressed size only the absolute cap applies. A few hundred
#: bytes of XML can compress arbitrarily well and mean nothing; the ratio is
#: evidence only once there is enough of it to be evidence.
MAX_EXPANSION_RATIO_MIN_BYTES = 1024 * 1024


class SpreadsheetTooLarge(ValueError):
    """An uploaded workbook expands past what the platform will read.

    Carries the cap that was passed so each caller can re-raise its own error
    type with the same sentence — the message is the bank-facing explanation
    and must not diverge between the upload routes.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def inspect_archive(source: Path | IO[bytes]) -> None:
    """Refuse ``source`` if its zip entries expand past the caps.

    Reads the central directory only: no entry is decompressed, though directory
    memory and read costs depend on the entry count. A source that is not a zip is
    left for the caller's own "not a valid .xlsx" path, which has better
    wording for that case.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        return
    uncompressed = sum(info.file_size for info in infos)
    compressed = sum(info.compress_size for info in infos)
    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise SpreadsheetTooLarge(
            f"The workbook expands to {_size(uncompressed)} of XML, past the "
            f"{_size(MAX_UNCOMPRESSED_BYTES)} the platform will read."
        )
    if (
        uncompressed >= MAX_EXPANSION_RATIO_MIN_BYTES
        and compressed > 0
        and uncompressed / compressed > MAX_EXPANSION_RATIO
    ):
        raise SpreadsheetTooLarge(
            f"The workbook expands {uncompressed // max(compressed, 1)} times its stored "
            f"size, past the {MAX_EXPANSION_RATIO}x the platform will read."
        )


def _size(byte_count: int) -> str:
    """``byte_count`` in the unit a person reads it in, without rounding it to zero."""
    if byte_count >= 1_000_000:
        return f"{byte_count // 1_000_000} MB"
    if byte_count >= 1_000:
        return f"{byte_count // 1_000} KB"
    return f"{byte_count} bytes"


class SheetRows(Protocol):
    """The part of a worksheet this reads: a title and a values-only row stream.

    Stated as a protocol so the guard does not depend on openpyxl's untyped
    worksheet class, and so a caller in a test can stand in for one.
    """

    @property
    def title(self) -> str: ...

    def iter_rows(self, *, values_only: bool) -> Iterator[tuple[Any, ...]]: ...


def bounded_rows(worksheet: SheetRows, *, cells_used: list[int]) -> Iterator[tuple[Any, ...]]:
    """``worksheet.iter_rows(values_only=True)`` under the row and cell caps.

    ``cells_used`` is a one-element counter shared across the sheets of one
    workbook, so a bomb split over many sheets is still counted as one
    workbook. It is a list rather than a return value because the caller is a
    generator loop, not a function that can hand back a total.
    """
    if isinstance(worksheet, ReadOnlyWorksheet):
        worksheet.reset_dimensions()
    previous_cells = cells_used[0]
    width = 0
    for rows, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        if rows > MAX_ROWS_PER_SHEET:
            raise SpreadsheetTooLarge(
                f"Sheet {worksheet.title!r} has more than {MAX_ROWS_PER_SHEET} rows."
            )
        width = max(width, len(row))
        cells_used[0] = previous_cells + rows * width
        if cells_used[0] > MAX_CELLS_PER_WORKBOOK:
            raise SpreadsheetTooLarge(
                f"The workbook has more than {MAX_CELLS_PER_WORKBOOK} cells; "
                "split it into one file per dataset."
            )
        yield row


__all__: Iterable[str] = (
    "MAX_CELLS_PER_WORKBOOK",
    "MAX_EXPANSION_RATIO",
    "MAX_EXPANSION_RATIO_MIN_BYTES",
    "MAX_ROWS_PER_SHEET",
    "MAX_UNCOMPRESSED_BYTES",
    "SheetRows",
    "SpreadsheetTooLarge",
    "bounded_rows",
    "inspect_archive",
)
