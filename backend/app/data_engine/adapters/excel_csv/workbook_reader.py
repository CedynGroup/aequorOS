"""Read .xlsx/.csv/.tsv files into raw cell grids, tolerating bank realities.

The reader only gets values out of the file; recovering table structure is
``sheet_analyzer``'s job. Formulas are read as their last cached value when
the workbook has one (files saved by Excel do); a formula with no cached
value comes through as ``None`` and is surfaced downstream rather than
silently computed.
"""

from __future__ import annotations

import csv
import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from app.core import spreadsheet_limits
from app.core.spreadsheet_limits import SpreadsheetTooLarge, bounded_rows, inspect_archive
from app.data_engine.adapters.excel_csv.sheet_analyzer import MergedRange

SUPPORTED_SUFFIXES = (".xlsx", ".csv", ".tsv")

#: Namespaces of the spreadsheet part schema and of the relationship ids that
#: point a sheet at the worksheet part holding it.
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class WorkbookReadError(ValueError):
    pass


@dataclass(frozen=True)
class SheetGrid:
    name: str
    grid: list[list[Any]]
    merged_ranges: list[MergedRange]


def read_source(path: Path) -> list[SheetGrid]:
    if not path.exists():
        raise WorkbookReadError(f"Source file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx(path)
    if suffix in {".csv", ".tsv"}:
        return [_read_delimited(path, delimiter="\t" if suffix == ".tsv" else ",")]
    if suffix == ".xls":
        raise WorkbookReadError(
            "Legacy .xls workbooks are not supported; save the file as .xlsx and retry."
        )
    raise WorkbookReadError(
        f"Unsupported file type {suffix!r}; supported: {', '.join(SUPPORTED_SUFFIXES)}."
    )


def _read_xlsx(path: Path) -> list[SheetGrid]:
    # Imported lazily: openpyxl is only needed when an .xlsx workbook is read.
    import openpyxl  # noqa: PLC0415

    try:
        inspect_archive(path)
    except SpreadsheetTooLarge as exc:
        raise WorkbookReadError(f"Workbook {path.name}: {exc}") from exc
    try:
        # Stream worksheet cells instead of retaining openpyxl's cell objects.
        # Read-only mode omits ``merged_cells``, so recover merge declarations
        # separately before collecting the bounded grids.
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        message = str(exc)
        if "encrypted" in message.lower() or "password" in message.lower():
            raise WorkbookReadError(
                f"Workbook {path.name} is password-protected; provide an unprotected copy."
            ) from exc
        raise WorkbookReadError(f"Cannot open workbook {path.name}: {message}") from exc

    cells_used = [0]
    sheets: list[SheetGrid] = []
    try:
        merges = _merged_ranges_by_sheet(path)
        for worksheet in workbook.worksheets:
            grid = [list(row) for row in bounded_rows(worksheet, cells_used=cells_used)]
            sheet_merges = merges.get(worksheet.title, [])
            width = max((len(row) for row in grid), default=0)
            unmerged_cells = len(grid) * width
            width = max(width, max((right for _, _, _, right in sheet_merges), default=0))
            height = max(len(grid), max((bottom for _, _, bottom, _ in sheet_merges), default=0))
            grid.extend([] for _ in range(height - len(grid)))
            for row in grid:
                row.extend([None] * (width - len(row)))
            cells_used[0] += height * width - unmerged_cells
            sheets.append(
                SheetGrid(
                    name=worksheet.title,
                    grid=grid,
                    merged_ranges=sheet_merges,
                )
            )
    except SpreadsheetTooLarge as exc:
        raise WorkbookReadError(f"Workbook {path.name}: {exc}") from exc
    finally:
        workbook.close()
    return sheets


def _merged_ranges_by_sheet(path: Path) -> dict[str, list[MergedRange]]:
    """Each sheet's merge declarations, read straight from the workbook's XML.

    ``read_only=True`` is what keeps a hostile workbook from becoming a
    full-document parse, and it costs the ``merged_cells`` accessor that header
    recovery needs. The merges are one ``<mergeCell ref="A1:B2">`` element each,
    so they are collected from the sheet parts without building a single cell
    object. Values are unaffected: openpyxl reports the same ``None`` for the
    covered cells either way, and :func:`fill_merged_cells` is what spreads the
    anchor across them.

    Merges are part of the answer, not a nicety: without them
    :func:`fill_merged_cells` cannot spread a title banner, and the header row
    beneath it is misread as data. So a workbook whose sheet parts cannot be
    resolved is REFUSED rather than silently read without its merges — a
    workbook that reads with the wrong shape is worse than one that says it
    cannot be read. Only an archive that is not a zip at all is left to
    openpyxl's own "cannot open workbook" wording, which is better than ours.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            parts = _sheet_parts(archive)
            cells_used = [0]
            return {
                title: _merge_ranges(archive, part, cells_used=cells_used)
                for title, part in parts.items()
            }
    except zipfile.BadZipFile:
        return {}
    except (KeyError, OSError) as exc:
        raise WorkbookReadError(
            f"Workbook {path.name} declares a sheet part it does not contain: {exc}"
        ) from exc


def _sheet_parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Sheet title to the worksheet part that holds it.

    Titles live in ``xl/workbook.xml`` and point at parts through relationship
    ids resolved in ``xl/_rels/workbook.xml.rels``; neither is in document
    order, so this is the only reliable mapping.

    Targets are canonicalized against the package root the way openpyxl does it,
    because a producer may write ``worksheets/sheet1.xml``, ``./worksheets/…`` or
    ``/xl/worksheets/…`` and all three name the same part. Resolving only the
    first form silently lost every sheet's merges for the other two.
    """
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {element.get("Id", ""): element.get("Target", "") for element in relationships}
    available = set(archive.namelist())
    parts: dict[str, str] = {}
    for sheet in workbook.iter(f"{_MAIN_NS}sheet"):
        title = sheet.get("name")
        target = targets.get(sheet.get(f"{_REL_NS}id", ""), "")
        if not title or not target:
            continue
        parts[title] = _part_name(target, available)
    return parts


def _part_name(target: str, available: set[str]) -> str:
    """The archive entry a relationship target names, or ``KeyError``.

    A target is either package-absolute (``/xl/…``) or relative to the part that
    declares it, which for ``xl/workbook.xml`` means relative to ``xl/``.
    """
    if target.startswith("/"):
        candidate = posixpath.normpath(target.lstrip("/"))
    else:
        candidate = posixpath.normpath(posixpath.join("xl", target))
    if candidate in available:
        return candidate
    raise KeyError(target)


def _merge_ranges(
    archive: zipfile.ZipFile, part: str, *, cells_used: list[int]
) -> list[MergedRange]:
    """``(min_row, min_col, max_row, max_col)`` for every merge in ``part``."""
    ranges: list[MergedRange] = []
    parents: list[ElementTree.Element] = []
    previous_cells = cells_used[0]
    rows = max_row = max_col = cells = 0
    row_col = 0
    with archive.open(part) as handle:
        events = cast(
            "Iterator[tuple[str, ElementTree.Element]]",
            ElementTree.iterparse(handle, events=("start", "end")),
        )
        for event, element in events:
            if event == "start":
                parents.append(element)
                if element.tag == f"{_MAIN_NS}row":
                    rows += 1
                    max_row = max(max_row, rows, int(element.get("r", str(max_row + 1))))
                    row_col = 0
                elif element.tag == f"{_MAIN_NS}c":
                    cells += 1
                    reference = element.get("r")
                    cell_row, row_col = (
                        coordinate_to_tuple(reference) if reference else (max_row, row_col + 1)
                    )
                    max_row = max(max_row, cell_row)
                    max_col = max(max_col, row_col)
                elif element.tag == f"{_MAIN_NS}mergeCell":
                    reference = element.get("ref", "")
                    if ":" in reference:
                        corners = range_boundaries(reference)
                        if None not in corners:
                            left, top, right, bottom = cast("tuple[int, int, int, int]", corners)
                            ranges.append((top, left, bottom, right))
                            max_row = max(max_row, bottom)
                            max_col = max(max_col, right)
                if max_row > spreadsheet_limits.MAX_ROWS_PER_SHEET:
                    raise SpreadsheetTooLarge(
                        f"Sheet {part!r} has more than "
                        f"{spreadsheet_limits.MAX_ROWS_PER_SHEET} rows."
                    )
                cells_used[0] = previous_cells + max(cells, max_row * max_col)
                if cells_used[0] > spreadsheet_limits.MAX_CELLS_PER_WORKBOOK:
                    raise SpreadsheetTooLarge(
                        f"The workbook has more than "
                        f"{spreadsheet_limits.MAX_CELLS_PER_WORKBOOK} cells; "
                        "split it into one file per dataset."
                    )
                continue
            parents.pop()
            if parents:
                parents[-1].remove(element)
            element.clear()
    return ranges


def _read_delimited(path: Path, *, delimiter: str) -> SheetGrid:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            grid: list[list[Any]] = [list(row) for row in csv.reader(handle, delimiter=delimiter)]
    except UnicodeDecodeError as exc:
        raise WorkbookReadError(f"File {path.name} is not valid UTF-8 text.") from exc
    return SheetGrid(name=path.stem, grid=grid, merged_ranges=[])
