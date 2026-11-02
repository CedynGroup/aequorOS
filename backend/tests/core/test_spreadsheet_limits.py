"""A workbook cannot buy more work than the platform agreed to do.

Why this exists (security audit, 2026-10-28 — issue #702)
---------------------------------------------------------
An ``.xlsx`` is a zip of XML and the 50 MB upload cap measures the COMPRESSED
bytes, so a few kilobytes of sheet XML can hold tens of gigabytes once
decompressed. Both reader paths materialized every sheet with no expansion,
row-count or cell-count cap, so a crafted workbook OOM-killed the API or the
worker mid-request (openpyxl has no bomb guard on either path).

The guard is in two layers, and both are pinned here because either alone is
escapable: the archive header check ignores what a bomb actually declares, and
the streaming count is what holds when a zip under-declares its sizes.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.core import spreadsheet_limits
from app.core.spreadsheet_limits import (
    MAX_EXPANSION_RATIO,
    MAX_ROWS_PER_SHEET,
    SpreadsheetTooLarge,
    bounded_rows,
    inspect_archive,
)


def _archive_with(body_writer: Callable[[IO[bytes]], None], *, entries: int = 1) -> io.BytesIO:
    """A zip whose entries are written through ``body_writer(handle)``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(entries):
            with archive.open(
                f"xl/worksheets/sheet{index + 1}.xml", "w", force_zip64=True
            ) as handle:
                body_writer(handle)
    buffer.seek(0)
    return buffer


def _zeros(handle: IO[bytes]) -> None:
    """A megabyte of NULs per write: the shape a real decompression bomb has."""
    block = b"\0" * (1024 * 1024)
    for _ in range(8):
        handle.write(block)


def test_a_workbook_that_expands_past_the_ratio_is_refused() -> None:
    """Eight megabytes of NULs compress to almost nothing — a bomb, not a return."""
    archive = _archive_with(_zeros)

    with pytest.raises(SpreadsheetTooLarge) as refusal:
        inspect_archive(archive)

    assert "times its stored size" in str(refusal.value)


def test_the_ratio_refusal_names_the_ratio_it_found() -> None:
    archive = _archive_with(_zeros)

    with pytest.raises(SpreadsheetTooLarge) as refusal:
        inspect_archive(archive)

    assert str(MAX_EXPANSION_RATIO) in str(refusal.value)


def test_a_header_that_under_declares_its_size_is_caught_by_the_streaming_count() -> None:
    """The reason both layers exist: a zip may lie about how big an entry is.

    ``inspect_archive`` reads only the central directory, so a file that
    declares a small entry passes it. The row count is what refuses the parse,
    because it counts what openpyxl actually produces.
    """

    class LyingSheet:
        title = "Positions"

        def __init__(self) -> None:
            self.yielded = 0

        def iter_rows(self, *, values_only: bool) -> Iterator[tuple[str, int]]:
            assert values_only is True
            while True:
                self.yielded += 1
                if self.yielded > MAX_ROWS_PER_SHEET + 10:
                    return
                yield ("row", self.yielded)

    rows = bounded_rows(LyingSheet(), cells_used=[0])

    with pytest.raises(SpreadsheetTooLarge) as refusal:
        for _ in rows:
            pass

    assert "more than" in str(refusal.value)


def test_a_bomb_split_across_sheets_still_passes_the_cell_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-sheet caps alone would let one workbook smuggle N sheets of them."""
    monkeypatch.setattr(spreadsheet_limits, "MAX_CELLS_PER_WORKBOOK", 18)

    class Sheet:
        title = "Small"

        def iter_rows(self, *, values_only: bool) -> Iterator[tuple[int, int, int]]:
            assert values_only is True
            for _ in range(3):
                yield (1, 2, 3)

    cells_used = [0]
    for _sheet in range(2):
        for _row in bounded_rows(Sheet(), cells_used=cells_used):
            pass
    assert cells_used[0] == 18

    with pytest.raises(SpreadsheetTooLarge) as refusal:
        for _row in bounded_rows(Sheet(), cells_used=cells_used):
            pass

    assert "cells" in str(refusal.value)


def test_an_ordinary_workbook_passes_the_archive_check() -> None:
    """A template-shaped file must not be refused by a guard meant for bombs.

    Built through openpyxl rather than by hand: a real workbook's parts are
    verbose, which is why the ratio rule needs the headroom this pins.
    """
    workbook = Workbook()
    sheet = _active_sheet(workbook)
    sheet.append(["AccountRef", "Balance", "Ccy"])
    for index in range(2_000):
        sheet.append([f"LN-{index:05d}", index * 1_000, "GHS"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    inspect_archive(buffer)  # does not raise


def test_a_small_file_is_judged_by_the_absolute_cap_alone() -> None:
    """A few hundred repetitive bytes compress enormously and mean nothing."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>" * 50)
    buffer.seek(0)

    inspect_archive(buffer)  # does not raise


def test_a_file_that_is_not_a_zip_is_left_to_the_reader() -> None:
    """The readers have better wording for "not an .xlsx"; the guard stays out of it."""
    inspect_archive(io.BytesIO(b"this is a csv, honestly"))


def test_a_missing_file_is_left_to_the_reader(tmp_path: Path) -> None:
    inspect_archive(tmp_path / "absent.xlsx")


def _active_sheet(workbook: Workbook) -> Worksheet:
    sheet = workbook.active
    assert sheet is not None  # a fresh Workbook always has an active sheet
    return sheet
