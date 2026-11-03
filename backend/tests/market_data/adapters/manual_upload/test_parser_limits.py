"""A market-data workbook cannot buy more work than the platform agreed to do.

Why this exists (security audit, 2026-10-28 — issue #702)
---------------------------------------------------------
The manual-upload parser already opened workbooks with ``read_only=True``, which
bounds memory per row but not the number of rows: nothing stopped a crafted
``.xlsx`` from streaming millions of them, or from declaring a tiny entry that
decompresses to gigabytes. The row and cell caps are what end that parse, and
the archive check is what refuses an obvious bomb before any decompression.

Both are reported as ``ManualUploadParseError`` — the parser's own contract —
so an oversized file is a classified refusal the API can return, not a crash.
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.core import spreadsheet_limits
from app.market_data.adapters.manual_upload.parser import (
    ManualUploadParseError,
    parse_upload,
)
from tests.market_data.adapters.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_yield_curve_workbook,
)


def test_an_ordinary_template_still_parses() -> None:
    """The caps must not touch the workbook the bank is actually asked to fill in."""
    parsed = parse_upload(
        build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", FIXTURE_AS_OF.isoformat(), 3, 15.80]]),
        "curve.xlsx",
        expected_as_of=FIXTURE_AS_OF,
    )

    assert parsed.problems == []


def test_a_workbook_past_the_row_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS_PER_SHEET", 3)

    with pytest.raises(ManualUploadParseError, match="rows"):
        parse_upload(_wide_workbook(rows=10), "bomb.xlsx", expected_as_of=FIXTURE_AS_OF)


def test_a_workbook_that_expands_past_the_archive_cap_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spreadsheet_limits, "MAX_UNCOMPRESSED_BYTES", 1024)

    with pytest.raises(ManualUploadParseError, match="platform will read"):
        parse_upload(_wide_workbook(rows=50), "bomb.xlsx", expected_as_of=FIXTURE_AS_OF)


def test_the_refusal_is_still_a_classified_parse_error() -> None:
    """Callers catch one error type for "the file cannot be read"; that holds."""
    assert issubclass(ManualUploadParseError, ValueError)


def _wide_workbook(*, rows: int) -> bytes:
    """A workbook with no recognisable template header, just volume."""
    workbook = Workbook()
    sheet = _active_sheet(workbook)
    sheet.title = "yield_curve"
    sheet.append(["filler"] * 8)
    for index in range(rows):
        sheet.append([index] * 8)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _active_sheet(workbook: Workbook) -> Worksheet:
    sheet = workbook.active
    assert sheet is not None  # a fresh Workbook always has an active sheet
    return sheet
