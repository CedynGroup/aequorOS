"""The reader streams a workbook instead of materializing it, and still sees merges.

Why this exists (security audit, 2026-10-28 — issue #702)
---------------------------------------------------------
``workbook_reader`` loaded every sheet as a full openpyxl document, so a crafted
workbook's expansion became Python cell objects and killed the process. The fix
is ``read_only=True`` plus the shared caps, and the reason it is not a one-line
change is that ``read_only=True`` costs the ``merged_cells`` accessor — which
header recovery needs, because a title banner merged across the top is how a
bank distinguishes a heading from the table under it.

So two things are pinned here: a bomb is refused with the reader's own error
type, and the merge declarations come back exactly as the document model
reports them even though the document model is no longer loaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl
import pytest
from openpyxl.worksheet.worksheet import Worksheet

from app.core import spreadsheet_limits
from app.data_engine.adapters.excel_csv.workbook_reader import (
    WorkbookReadError,
    read_source,
)
from app.market_data.adapters.manual_upload.parser import read_grids
from tests.data_engine.adapters.excel_csv import fixtures as excel_fixtures


def test_the_merged_banner_survives_the_streaming_read(tmp_path: Path) -> None:
    """``read_only=True`` must not quietly lose a merge.

    The fixture merges ``A1:E1`` for its title banner, so the expected tuple is
    read off the fixture rather than inferred from the reader: four of the five
    cells covered by a merge must stay recorded as covered, or
    ``fill_merged_cells`` cannot spread the banner and the header row beneath it
    is misread as a table.
    """
    path = excel_fixtures.build_merged_headers(tmp_path / "merged.xlsx")

    read = {sheet.name: sheet.merged_ranges for sheet in read_source(path)}

    assert read == {"Loans": [(1, 1, 1, 5)]}


def test_the_grid_is_unchanged_by_the_streaming_read(tmp_path: Path) -> None:
    path = excel_fixtures.build_merged_headers(tmp_path / "merged.xlsx")

    (sheet,) = read_source(path)

    assert sheet.grid[0][0] == "Sample Bank Limited — Loan Book as of 30 June 2026"
    assert sheet.grid[2] == ["AccountRef", "Type", "Ccy", "Outstanding", "Rate"]
    # A merged range's covered cells are empty either way; fill_merged_cells
    # is what spreads the anchor across them afterwards.
    assert sheet.grid[0][1] is None


def test_every_sheet_keeps_its_own_merges(tmp_path: Path) -> None:
    """The title-to-part mapping is by relationship id, not by document order."""
    path = tmp_path / "two-sheets.xlsx"
    workbook = openpyxl.Workbook()
    first = _active_sheet(workbook)
    first.title = "Second alphabetically"
    first["A1"] = "banner"
    first.merge_cells("A1:C1")
    second = cast(Worksheet, workbook.create_sheet("Aaa"))
    second["B2"] = "other"
    second.merge_cells("B2:D4")
    workbook.save(path)

    read = {sheet.name: sheet.merged_ranges for sheet in read_source(path)}

    assert read == {"Second alphabetically": [(1, 1, 1, 3)], "Aaa": [(2, 2, 4, 4)]}


def test_a_workbook_past_the_row_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap ends the parse; the reader reports it as a read error, not a crash."""
    monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS_PER_SHEET", 2)
    path = _positions_workbook(tmp_path / "long.xlsx", rows=8)

    with pytest.raises(WorkbookReadError) as refusal:
        read_source(path)

    assert "rows" in str(refusal.value)


def test_a_workbook_that_expands_past_the_archive_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_UNCOMPRESSED_BYTES", 1024)
    path = _positions_workbook(tmp_path / "large.xlsx", rows=500)

    with pytest.raises(WorkbookReadError) as refusal:
        read_source(path)

    assert "the platform will read" in str(refusal.value)


def test_a_csv_is_untouched_by_the_spreadsheet_guard(tmp_path: Path) -> None:
    """The caps are about zip expansion; a delimited file has none."""
    path = tmp_path / "positions.csv"
    path.write_text("AccountRef,Balance\nLN-0001,100\n", encoding="utf-8")

    (sheet,) = read_source(path)

    assert sheet.grid[1] == ["LN-0001", "100"]
    assert sheet.merged_ranges == []


@pytest.mark.parametrize("reader", ["data_engine", "market_data"])
@pytest.mark.parametrize("dimension", ["A1:A1", "A1:B2", "A1:XFD1048576"])
def test_declared_dimensions_cannot_hide_or_invent_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, dimension: str
) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS_PER_SHEET", 5)
    monkeypatch.setattr(spreadsheet_limits, "MAX_CELLS_PER_WORKBOOK", 10)
    path = _positions_workbook(tmp_path / "dimensions.xlsx", rows=4)
    with ZipFile(path) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    declared = sheet.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}dimension")
    assert declared is not None
    declared.set("ref", dimension)
    _replace_sheet(path, ElementTree.tostring(sheet))

    grid = (
        read_source(path)[0].grid
        if reader == "data_engine"
        else read_grids(path.read_bytes(), path.name)[0][1]
    )

    assert grid == [["AccountRef", "Balance"], *[[f"LN-{i:05d}", i] for i in range(4)]]


@pytest.mark.parametrize(
    ("rows", "limit"),
    [
        ('<row r="1"/><row r="2"/><row r="3"/>', "rows"),
        ('<row r="3"/>', "rows"),
        ('<row r="1"><c r="A1"/><c r="B1"/><c r="C1"/></row>', "cells"),
        ('<row r="1"><c r="C1"/></row>', "cells"),
    ],
    ids=["empty-rows", "sparse-row", "cells", "sparse-column"],
)
def test_merge_extraction_refuses_excess_xml_before_reading_the_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: str, limit: str
) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS_PER_SHEET", 2)
    monkeypatch.setattr(spreadsheet_limits, "MAX_CELLS_PER_WORKBOOK", 2)
    path = _positions_workbook(tmp_path / "early-refusal.xlsx", rows=1)
    xml = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1"/><sheetData>'
        f"{rows}</sheetData>" + " " * 32_768 + "<malformed"
    )
    _replace_sheet(path, xml.encode())

    with pytest.raises(WorkbookReadError, match=limit):
        read_source(path)


def test_merge_extraction_counts_cells_across_sheets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_CELLS_PER_WORKBOOK", 3)
    path = tmp_path / "split.xlsx"
    workbook = openpyxl.Workbook()
    _active_sheet(workbook).append([1, 2])
    second = cast(Worksheet, workbook.create_sheet("Second"))
    second.append([3, 4])
    workbook.save(path)

    with pytest.raises(WorkbookReadError, match="cells"):
        read_source(path)


def _replace_sheet(path: Path, content: bytes) -> None:
    with ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["xl/worksheets/sheet1.xml"] = content
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)


def _active_sheet(workbook: openpyxl.Workbook) -> Worksheet:
    sheet = workbook.active
    assert sheet is not None  # a fresh Workbook always has an active sheet
    return sheet


def _positions_workbook(path: Path, *, rows: int) -> Path:
    workbook = openpyxl.Workbook()
    sheet = _active_sheet(workbook)
    sheet.title = "Positions"
    sheet.append(["AccountRef", "Balance"])
    for index in range(rows):
        sheet.append([f"LN-{index:05d}", index])
    workbook.save(path)
    return path
