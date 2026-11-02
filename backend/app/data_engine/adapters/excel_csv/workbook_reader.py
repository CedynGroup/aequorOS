"""Read .xlsx/.csv/.tsv files into raw cell grids, tolerating bank realities.

The reader only gets values out of the file; recovering table structure is
``sheet_analyzer``'s job. Formulas are read as their last cached value when
the workbook has one (files saved by Excel do); a formula with no cached
value comes through as ``None`` and is surfaced downstream rather than
silently computed.
"""

from __future__ import annotations

import csv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from openpyxl.utils.cell import range_boundaries

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
        # read_only: a tenant workbook is streamed a row at a time instead of
        # becoming a full openpyxl document in memory, which is the difference
        # between a bomb costing the worker its process and costing it a
        # refused upload (#702). It gives up the ``merged_cells`` accessor, so
        # the merge declarations are read from the archive separately.
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        message = str(exc)
        if "encrypted" in message.lower() or "password" in message.lower():
            raise WorkbookReadError(
                f"Workbook {path.name} is password-protected; provide an unprotected copy."
            ) from exc
        raise WorkbookReadError(f"Cannot open workbook {path.name}: {message}") from exc

    merges = _merged_ranges_by_sheet(path)
    cells_used = [0]
    sheets: list[SheetGrid] = []
    try:
        for worksheet in workbook.worksheets:
            grid = [list(row) for row in bounded_rows(worksheet, cells_used=cells_used)]
            sheets.append(
                SheetGrid(
                    name=worksheet.title,
                    grid=grid,
                    merged_ranges=merges.get(worksheet.title, []),
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

    Returns an empty mapping rather than raising when the archive is not shaped
    the way the specification says — the caller has already opened the same
    file through openpyxl, so a structure openpyxl accepted but this does not is
    a merged-range detail, not a reason to refuse a workbook that reads fine.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            parts = _sheet_parts(archive)
            return {title: _merge_ranges(archive, part) for title, part in parts.items() if part}
    except (zipfile.BadZipFile, KeyError, OSError):
        return {}


def _sheet_parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Sheet title to the worksheet part that holds it.

    Titles live in ``xl/workbook.xml`` and point at parts through relationship
    ids resolved in ``xl/_rels/workbook.xml.rels``; neither is in document
    order, so this is the only reliable mapping.
    """
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {element.get("Id", ""): element.get("Target", "") for element in relationships}
    parts: dict[str, str] = {}
    for sheet in workbook.iter(f"{_MAIN_NS}sheet"):
        title = sheet.get("name")
        target = targets.get(sheet.get(f"{_REL_NS}id", ""), "")
        if not title or not target:
            continue
        parts[title] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return parts


def _merge_ranges(archive: zipfile.ZipFile, part: str) -> list[MergedRange]:
    """``(min_row, min_col, max_row, max_col)`` for every merge in ``part``."""
    ranges: list[MergedRange] = []
    with archive.open(part) as handle:
        for event, element in ElementTree.iterparse(handle, events=("start", "end")):
            if event != "end":
                continue
            if element.tag == f"{_MAIN_NS}mergeCell":
                reference = element.get("ref", "")
                if ":" in reference:
                    corners = range_boundaries(reference)
                    if None in corners:
                        continue
                    min_col, min_row, max_col, max_row = cast("tuple[int, int, int, int]", corners)
                    ranges.append((min_row, min_col, max_row, max_col))
            # Cleared as it goes: the merge declarations sit at the end of a
            # sheet, after every row, so a retained tree would be the whole
            # worksheet again.
            element.clear()
    return ranges


def _read_delimited(path: Path, *, delimiter: str) -> SheetGrid:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            grid: list[list[Any]] = [list(row) for row in csv.reader(handle, delimiter=delimiter)]
    except UnicodeDecodeError as exc:
        raise WorkbookReadError(f"File {path.name} is not valid UTF-8 text.") from exc
    return SheetGrid(name=path.stem, grid=grid, merged_ranges=[])
