"""XLSX rendering of a resolved return (docs/regulatory_reporting.md §5).

One metadata sheet, one sheet per template section, one fidelity/provenance
footer sheet. Styling is deliberately regulator-neutral: bold headers on a
light-grey fill, bold totals rows, ``#,##0;(#,##0)`` number formats (thousands
separators, parenthesised negatives per research §11) — no brand colors.

Byte output is deterministic for a given package so re-exports keep a stable
checksum: workbook document properties are pinned to the package's
``generated_at`` and the container zip is re-written with fixed entry
timestamps (openpyxl stamps entries with wall-clock time).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.services.regulatory_reporting.templates import (
    RenderedCell,
    RenderedReturn,
    RenderedRow,
)

_HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
_TOTAL_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
_THIN_BORDER = Border(bottom=Side(style="thin", color="BFBFBF"))
_GHS_FORMAT = "#,##0;(#,##0)"
_PCT_FORMAT = "0.00"
_NUMBER_FORMAT = "#,##0.00;(#,##0.00)"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_MAX_SHEET_TITLE = 31


def _sheet_title(title: str, used: set[str]) -> str:
    cleaned = "".join(ch for ch in title if ch not in "[]:*?/\\")[:_MAX_SHEET_TITLE]
    candidate = cleaned
    suffix = 2
    while candidate in used:
        stem = cleaned[: _MAX_SHEET_TITLE - len(f" ({suffix})")]
        candidate = f"{stem} ({suffix})"
        suffix += 1
    used.add(candidate)
    return candidate


def _write_cell(sheet: Worksheet, row_idx: int, col_idx: int, cell: RenderedCell) -> None:
    if cell.value is None:
        return
    if cell.kind == "bool":
        target = sheet.cell(row=row_idx, column=col_idx, value="Yes" if cell.value else "No")
        target.alignment = Alignment(horizontal="center")
        return
    if cell.kind == "text":
        sheet.cell(row=row_idx, column=col_idx, value=str(cell.value))
        return
    # Decimal — openpyxl stores it as a real number, formatting handles display.
    target = sheet.cell(row=row_idx, column=col_idx, value=cell.value)
    if cell.kind == "ghs":
        target.number_format = _GHS_FORMAT
    elif cell.kind == "pct":
        target.number_format = _PCT_FORMAT
    else:
        target.number_format = _NUMBER_FORMAT


def _write_table_row(
    sheet: Worksheet, row_idx: int, rendered_row: RenderedRow, *, bold: bool = False
) -> None:
    for col_idx, cell in enumerate(rendered_row.cells, start=1):
        _write_cell(sheet, row_idx, col_idx, cell)
        styled = sheet.cell(row=row_idx, column=col_idx)
        if bold:
            styled.font = Font(bold=True)
            styled.fill = _TOTAL_FILL


def _autosize(sheet: Worksheet, column_count: int) -> None:
    for col_idx in range(1, column_count + 1):
        longest = 0
        for row in sheet.iter_rows(min_col=col_idx, max_col=col_idx):
            value = row[0].value
            if value is not None:
                longest = max(longest, len(str(value)))
        sheet.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 2, 12), 60)


def _metadata_sheet(workbook: Workbook, rendered: RenderedReturn) -> None:
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Return Metadata"
    for row_idx, (label, value) in enumerate(rendered.metadata_pairs, start=1):
        label_cell = sheet.cell(row=row_idx, column=1, value=label)
        label_cell.font = Font(bold=True)
        sheet.cell(row=row_idx, column=2, value=value)
    row_idx = len(rendered.metadata_pairs) + 2
    sheet.cell(row=row_idx, column=1, value="Attestation").font = Font(bold=True)
    for offset, line in enumerate(rendered.attestation_lines, start=1):
        sheet.cell(row=row_idx + offset, column=2, value=line)
    row_idx += len(rendered.attestation_lines) + 2
    if rendered.template.notes:
        sheet.cell(row=row_idx, column=1, value="Template notes").font = Font(bold=True)
        for offset, note in enumerate(rendered.template.notes, start=1):
            sheet.cell(row=row_idx + offset, column=2, value=note)
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 110


def _section_sheets(workbook: Workbook, rendered: RenderedReturn, used_titles: set[str]) -> None:
    for section in rendered.sections:
        sheet = workbook.create_sheet(_sheet_title(section.title, used_titles))
        title_cell = sheet.cell(row=1, column=1, value=section.title)
        title_cell.font = Font(bold=True, size=12)
        unit_cell = sheet.cell(row=2, column=1, value=rendered.template.currency_unit)
        unit_cell.font = Font(italic=True)
        fidelity_cell = sheet.cell(
            row=3,
            column=1,
            value=(
                f"Fidelity: {section.layout.fidelity} · Layout: {section.layout.layout_id} · "
                f"Source: {section.layout.source_citation}"
            ),
        )
        fidelity_cell.font = Font(italic=True, size=9)
        header_row = 5
        for col_idx, column in enumerate(section.layout.columns, start=1):
            header = sheet.cell(row=header_row, column=col_idx, value=column.header)
            header.font = Font(bold=True)
            header.fill = _HEADER_FILL
            header.border = _THIN_BORDER
        row_idx = header_row + 1
        for rendered_row in section.rows:
            _write_table_row(sheet, row_idx, rendered_row)
            row_idx += 1
        if section.total_row is not None:
            _write_table_row(sheet, row_idx, section.total_row, bold=True)
            row_idx += 1
        if section.layout.notes:
            row_idx += 1
            for note in section.layout.notes:
                note_cell = sheet.cell(row=row_idx, column=1, value=f"Note: {note}")
                note_cell.font = Font(italic=True, size=9)
                row_idx += 1
        _autosize(sheet, len(section.layout.columns))


def _provenance_sheet(workbook: Workbook, rendered: RenderedReturn, used_titles: set[str]) -> None:
    sheet = workbook.create_sheet(_sheet_title("Fidelity & Provenance", used_titles))
    row_idx = 1
    for line in rendered.provenance_lines:
        sheet.cell(row=row_idx, column=1, value=line).font = Font(bold=(row_idx == 1))
        row_idx += 1
    row_idx += 1
    headers = ("Module", "Run ID", "Input Hash", "Engine Version")
    for col_idx, header in enumerate(headers, start=1):
        cell = sheet.cell(row=row_idx, column=col_idx, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    row_idx += 1
    for module, run_id, input_hash, engine_version in rendered.provenance_runs:
        for col_idx, value in enumerate((module, run_id, input_hash, engine_version), start=1):
            sheet.cell(row=row_idx, column=col_idx, value=value)
        row_idx += 1
    row_idx += 1
    sheet.cell(row=row_idx, column=1, value="Per-section fidelity").font = Font(bold=True)
    row_idx += 1
    for section in rendered.sections:
        sheet.cell(row=row_idx, column=1, value=section.layout.layout_id)
        sheet.cell(row=row_idx, column=2, value=section.layout.fidelity)
        sheet.cell(row=row_idx, column=3, value=section.layout.source_citation)
        row_idx += 1
    _autosize(sheet, 4)


def _normalize_zip(data: bytes, pinned: datetime) -> bytes:
    """Re-write the xlsx container with fixed entry timestamps — and pin the
    ``dcterms:modified`` document property, which openpyxl force-stamps with
    wall-clock time at save — so identical workbook content always yields
    identical bytes."""
    stamp = pinned.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    normalized = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(normalized, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in sorted(source.namelist()):
            payload = source.read(name)
            if name == "docProps/core.xml":
                payload = re.sub(
                    rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                    rb"\g<1>" + stamp + rb"\g<2>",
                    payload,
                )
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, payload)
    return normalized.getvalue()


def render_xlsx(rendered: RenderedReturn, *, generated_at: datetime) -> bytes:
    workbook = Workbook()
    pinned = generated_at.replace(tzinfo=None, microsecond=0)
    workbook.properties.created = pinned
    workbook.properties.modified = pinned
    workbook.properties.creator = "AequorOS Regulatory Reporting"
    workbook.properties.lastModifiedBy = "AequorOS Regulatory Reporting"

    used_titles: set[str] = {"Return Metadata"}
    _metadata_sheet(workbook, rendered)
    _section_sheets(workbook, rendered, used_titles)
    _provenance_sheet(workbook, rendered, used_titles)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return _normalize_zip(buffer.getvalue(), pinned)


__all__ = ["render_xlsx"]
