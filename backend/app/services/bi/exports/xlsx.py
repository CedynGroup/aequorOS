"""XLSX rendering of a governed export: write-only, two sheets, both protected.

``docs/bi.md`` §Exports is explicit about the mode — **openpyxl write-only** —
and the reason is the export row cap: the normal ``Workbook`` holds every cell
object in memory until ``save``, which at 100 000 rows × twenty columns is two
million objects. A write-only workbook streams each row to the archive as it is
appended and keeps one row alive at a time.

**Two sheets.** ``Data`` is the grid and nothing else: the header row in the
compiler's column order, then the rows, so the sheet can be read by a pivot
table or a Power BI query without a preamble to skip. ``Export metadata`` carries
the provenance — the six fields ``docs/bi.md`` requires, in its order, then the
supplementary ones — so the file explains itself when it is opened six months
later by someone who did not run the query.

**Both protected**, the way a sealed regulatory workbook is
(``bog_forms/render.py``: ``ws.protection.sheet = True``). The reason is the same
one: a downloaded figure that can be typed over in place, and then circulated,
is a figure nobody can trace back. Protection here is a statement of intent
rather than a cryptographic control — Excel lifts it without a password — but
the alternative is a sheet that invites the edit.

**The watermark rides the page header and footer** rather than a cell, so it
prints on every page and displaces no data. The header carries the attribution
(institution and recipient); the footer carries the standing note and the page
number.

The workbook's own document properties are left to openpyxl, which stamps the
real creation time. That is deliberate and is the one place these artifacts
differ from the PDF: a spreadsheet is a working copy, its creation time is true,
and the byte-determinism promise is made for the PDF alone (``pdf.py``).
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from app.services.bi.exports.context import (
    EXPORT_TITLE,
    STANDING_NOTE,
    ExportContext,
    ExportTable,
    native_cell,
)

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTENSION = "xlsx"

DATA_SHEET = "Data"
METADATA_SHEET = "Export metadata"

#: Number formats by catalogue value type. Amounts and ratios group thousands
#: and show two decimals; a count is an integer. No currency symbol: the unit is
#: in the column heading and in the metadata sheet, resolved from the bank.
_NUMBER_FORMATS: dict[str, str] = {
    "amount": "#,##0.00;(#,##0.00);-",
    "ratio": "#,##0.0000",
    "pct": "#,##0.00",
    "count": "#,##0",
    "int": "#,##0",
    "date": "yyyy-mm-dd",
}

#: What a write-only cell accepts. Stated because ``native_cell`` produces
#: exactly these and nothing wider.
CellValue = str | float | int | datetime | date | None

_HEADER_FONT = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=12)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _furnish(sheet: Worksheet, ctx: ExportContext) -> None:
    """Watermark every printed page, and lock the sheet against in-place edits.

    ``HeaderFooterItem`` is typed optional by openpyxl but is constructed for
    every worksheet, so the parts are addressed through ``HeaderFooterPart``
    directly rather than asserted — a worksheet with no header does not exist.
    """

    sheet.oddHeader.center.text = ctx.watermark  # pyright: ignore[reportOptionalMemberAccess]
    sheet.oddFooter.left.text = STANDING_NOTE  # pyright: ignore[reportOptionalMemberAccess]
    sheet.oddFooter.right.text = "Page &P of &N"  # pyright: ignore[reportOptionalMemberAccess]
    sheet.protection.sheet = True


def _cell(  # noqa: PLR0913 - one keyword per cell attribute the sheets set
    sheet: Worksheet,
    value: CellValue,
    *,
    font: Font | None = None,
    fmt: str | None = None,
    wrap: bool = False,
) -> Any:
    # openpyxl's own ``KNOWN_TYPES`` accepts ``datetime.date`` and writes it as a
    # date; only its type stub is narrower. A date column must stay a date — a
    # spreadsheet cannot sort or filter a date that arrived as text.
    target = WriteOnlyCell(sheet, value=cast("str | float | datetime | None", value))
    if font is not None:
        target.font = font
    if fmt is not None:
        target.number_format = fmt
    if wrap:
        target.alignment = _WRAP
    return target


def _metadata_sheet(workbook: Workbook, ctx: ExportContext, table: ExportTable) -> None:
    sheet = workbook.create_sheet(METADATA_SHEET)
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 110
    _furnish(sheet, ctx)
    sheet.append([_cell(sheet, EXPORT_TITLE, font=_TITLE_FONT)])
    sheet.append([])
    sheet.append(
        [_cell(sheet, "Field", font=_HEADER_FONT), _cell(sheet, "Value", font=_HEADER_FONT)]
    )
    for field, value in ctx.metadata_rows():
        sheet.append([_cell(sheet, field, font=_HEADER_FONT), _cell(sheet, value, wrap=True)])
    sheet.append([_cell(sheet, "Rows", font=_HEADER_FONT), _cell(sheet, table.row_count)])
    if table.truncated:
        sheet.append(
            [
                _cell(sheet, "Completeness", font=_HEADER_FONT),
                _cell(
                    sheet,
                    "Truncated at the export row cap. Narrow the window or add a "
                    "filter to export the remainder.",
                ),
            ]
        )


def _data_sheet(workbook: Workbook, ctx: ExportContext, table: ExportTable) -> None:
    sheet = workbook.create_sheet(DATA_SHEET)
    _furnish(sheet, ctx)
    sheet.append([_cell(sheet, column.label, font=_HEADER_FONT) for column in table.columns])
    sheet.freeze_panes = "A2"
    for row in table.rows:
        sheet.append(
            [
                _cell(
                    sheet,
                    native_cell(value, column),
                    fmt=_NUMBER_FORMATS.get(column.format),
                )
                for value, column in zip(row, table.columns, strict=False)
            ]
        )


def render_xlsx(table: ExportTable, ctx: ExportContext) -> bytes:
    """The workbook: the grid, the provenance, both sheets protected."""

    workbook = Workbook(write_only=True)
    workbook.properties.creator = EXPORT_TITLE
    workbook.properties.title = f"{EXPORT_TITLE} — {ctx.institution_name}"
    workbook.properties.description = STANDING_NOTE
    _data_sheet(workbook, ctx, table)
    _metadata_sheet(workbook, ctx, table)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = ["DATA_SHEET", "EXTENSION", "MEDIA_TYPE", "METADATA_SHEET", "render_xlsx"]
