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
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.reporting.service.exports.text import xml_safe
from app.reporting.service.templates import (
    ColumnSpec,
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
_WORKING_COPY_BANNER = "WORKING COPY — FOR INTERNAL REVIEW · not a filing artifact"
#: The same workbook for a package generated since 2026-11-23, when the formula
#: workbook began to be filed with every return that has one. Chosen by the
#: snapshot's ``metadata.formula_workbook`` stamp, so a package generated before
#: keeps its original wording and re-exports byte for byte (D-021).
_FILED_WORKBOOK_BANNER = (
    "FORMULA WORKBOOK — live formulas over the return's sealed figures · "
    "filed with the return; the signed record holds the fixed values"
)
_FILED_WORKBOOK_NOTE = (
    "Inputs are copied from the sealed report sheets and the formulas recalculate "
    "from them. Editing a copy changes nothing in the filed return."
)


@dataclass(frozen=True)
class _SectionLocation:
    sheet_title: str
    first_data_row: int
    row_count: int
    columns: dict[str, int]
    #: Each data row's line code and the sheet row it sits on, so a formula can
    #: name a line ("12.3", "USD") instead of guessing its position.
    rows: dict[str, int] = field(default_factory=lambda: {})
    total_row: int | None = None


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
        # Control characters a workbook cannot hold are made printable here, at
        # render time (``exports.text``); the snapshot keeps the exact text.
        target = sheet.cell(row=row_idx, column=col_idx, value=xml_safe(str(cell.value)))
        # openpyxl stores any string starting with "=" as a FORMULA. A rendered
        # text cell is snapshot data — for a narrative, text a user typed — so it
        # is pinned as a string and can never be evaluated by the spreadsheet.
        target.data_type = "s"
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
    sheet: Worksheet,
    row_idx: int,
    rendered_row: RenderedRow,
    columns: tuple[ColumnSpec, ...],
    *,
    bold: bool = False,
) -> None:
    """One grid row on the human-facing sheet.

    A section total carries a snapshot key as its ``code``
    (``on_balance_mismatch_total_ghs``) because the ``equals_sum_of_rows``
    validation and ``_snapshot_total`` bind to it. Written into LMTD Table 2's
    "#" column, beneath BoG's own row numbers 1 to 17, it reads as an internal
    variable on a return — so it is left out here exactly as it is on the PDF,
    which this workbook is the audit twin of. The key itself is untouched in the
    snapshot and in the CSV, the machine interchange format.
    """
    for col_idx, (spec, cell) in enumerate(zip(columns, rendered_row.cells, strict=True), start=1):
        if not (rendered_row.is_total and spec.key == "code"):
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
        sheet.cell(row=row_idx, column=2, value=xml_safe(value))
    row_idx = len(rendered.metadata_pairs) + 2
    sheet.cell(row=row_idx, column=1, value="Attestation").font = Font(bold=True)
    for offset, line in enumerate(rendered.attestation_lines, start=1):
        sheet.cell(row=row_idx + offset, column=2, value=line)
    row_idx += len(rendered.attestation_lines) + 2
    if rendered.template.notes:
        sheet.cell(row=row_idx, column=1, value="Template notes").font = Font(bold=True)
        for offset, note in enumerate(rendered.template.notes, start=1):
            sheet.cell(row=row_idx + offset, column=2, value=note)
        row_idx += len(rendered.template.notes) + 2
    if rendered.report_notes:
        sheet.cell(row=row_idx, column=1, value="Report notes").font = Font(bold=True)
        for offset, note in enumerate(rendered.report_notes, start=1):
            sheet.cell(row=row_idx + offset, column=2, value=xml_safe(note)).data_type = "s"
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 110


def _section_sheets(
    workbook: Workbook, rendered: RenderedReturn, used_titles: set[str]
) -> dict[str, _SectionLocation]:
    locations: dict[str, _SectionLocation] = {}
    for section in rendered.sections:
        sheet_title = _sheet_title(section.title, used_titles)
        sheet = workbook.create_sheet(sheet_title)
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
            _write_table_row(sheet, row_idx, rendered_row, section.layout.columns)
            row_idx += 1
        code_index = next(
            (index for index, column in enumerate(section.layout.columns) if column.key == "code"),
            None,
        )
        locations[section.layout.section_code] = _SectionLocation(
            sheet_title=sheet_title,
            first_data_row=header_row + 1,
            row_count=len(section.rows),
            columns={
                column.key: column_idx
                for column_idx, column in enumerate(section.layout.columns, start=1)
            },
            rows={
                str(rendered_row.cells[code_index].value): header_row + 1 + offset
                for offset, rendered_row in enumerate(section.rows)
                if code_index is not None and rendered_row.cells[code_index].value is not None
            },
            total_row=row_idx if section.total_row is not None else None,
        )
        if section.total_row is not None:
            _write_table_row(sheet, row_idx, section.total_row, section.layout.columns, bold=True)
            row_idx += 1
        if section.layout.notes:
            row_idx += 1
            for note in section.layout.notes:
                note_cell = sheet.cell(row=row_idx, column=1, value=f"Note: {note}")
                note_cell.font = Font(italic=True, size=9)
                row_idx += 1
        _autosize(sheet, len(section.layout.columns))
    return locations


def _quote_sheet_title(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _report_cell(location: _SectionLocation, key: str, row_offset: int) -> str | None:
    column = location.columns.get(key)
    if column is None or row_offset >= location.row_count:
        return None
    sheet = _quote_sheet_title(location.sheet_title)
    return f"{sheet}!{get_column_letter(column)}{location.first_data_row + row_offset}"


def _snapshot_total(snapshot: dict[str, Any], code: str) -> Decimal | None:
    for row in snapshot.get("totals", []):
        if row.get("code") != code or row.get("value") in (None, ""):
            continue
        try:
            return Decimal(str(row["value"])) / Decimal("1000")
        except (InvalidOperation, TypeError, ValueError):
            return None
    return None


def _working_header(sheet: Worksheet, title: str) -> None:
    sheet.cell(row=1, column=1, value=_WORKING_COPY_BANNER).font = Font(bold=True, color="9C0006")
    sheet.cell(row=2, column=1, value=title).font = Font(bold=True, size=12)
    sheet.cell(
        row=3,
        column=1,
        value=(
            "Inputs are copied from the sealed report sheets. Edit only this workbook for "
            "review; it does not change the immutable package or any filing artifact."
        ),
    ).font = Font(italic=True, size=9)


def _working_lmt(sheet: Worksheet, locations: dict[str, _SectionLocation]) -> bool:
    inputs = locations.get("prudential_ratio_inputs")
    reported = locations.get("prudential_ratio_percentages")
    if inputs is None or reported is None or "value" not in inputs.columns:
        return False
    _working_header(sheet, "SDI LMTD Table 1 — Working Ratio Calculations")
    sheet.append([])
    headers = ("Input", "Sealed report value", "Calculation", "Reported ratio", "Check")
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=5, column=column, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    input_names = (
        "Narrow liquid assets",
        "Broad liquid assets",
        "Volatile liabilities",
        "Total deposits",
        "Short-term liabilities",
        "Total assets",
    )
    for offset, label in enumerate(input_names):
        row = 6 + offset
        source = _report_cell(inputs, "value", offset)
        sheet.cell(row=row, column=1, value=label)
        if source is not None:
            sheet.cell(row=row, column=2, value=f"={source}").number_format = _GHS_FORMAT

    ratios = (
        ("Narrow liquid assets / volatile liabilities", 6, 8, 0),
        ("Broad liquid assets / volatile liabilities", 7, 8, 1),
        ("Narrow liquid assets / short-term liabilities", 6, 10, 2),
        ("Broad liquid assets / short-term liabilities", 7, 10, 3),
        ("Narrow liquid assets / total deposits", 6, 9, 4),
        ("Broad liquid assets / total deposits", 7, 9, 5),
        ("Narrow liquid assets / total assets", 6, 11, 6),
        ("Broad liquid assets / total assets", 7, 11, 7),
    )
    for offset, (label, numerator, denominator, reported_offset) in enumerate(ratios, start=13):
        sheet.cell(row=offset, column=1, value=label)
        calculated = sheet.cell(
            row=offset, column=3, value=f'=IFERROR(B{numerator}/B{denominator}*100,"")'
        )
        calculated.number_format = _PCT_FORMAT
        source = _report_cell(reported, "value", reported_offset)
        if source is not None:
            reported_cell = sheet.cell(row=offset, column=4, value=f"={source}")
            reported_cell.number_format = _PCT_FORMAT
            sheet.cell(
                row=offset,
                column=5,
                value=f'=IF(ABS(C{offset}-D{offset})<0.0001,"MATCH","CHECK")',
            )
    return True


def _working_large_exposures(
    sheet: Worksheet, locations: dict[str, _SectionLocation], snapshot: dict[str, Any]
) -> bool:
    nof = _snapshot_total(snapshot, "nof_ghs")
    sections = [
        locations.get(code) for code in ("template_1", "template_2", "template_3", "template_4")
    ]
    available = [location for location in sections if location is not None and location.row_count]
    if nof is None or not available:
        return False
    _working_header(sheet, "SDI Large Exposures — Working % of Net Own Funds")
    sheet.cell(row=5, column=1, value="Net Own Funds (GHS '000)").font = Font(bold=True)
    sheet.cell(row=5, column=2, value=nof).number_format = _GHS_FORMAT
    sheet.cell(row=6, column=1, value="Large-exposure threshold (10% of NOF)").font = Font(
        bold=True
    )
    sheet.cell(row=6, column=2, value="=B5*10%").number_format = _GHS_FORMAT
    row_idx = 8
    for location in available:
        if "value" not in location.columns or "pct_nof" not in location.columns:
            continue
        sheet.cell(row=row_idx, column=1, value=location.sheet_title).font = Font(bold=True)
        row_idx += 1
        for column, header in enumerate(
            ("Counterparty key", "Exposure", "Calculated % NOF", "Reported % NOF", "Check"), start=1
        ):
            cell = sheet.cell(row=row_idx, column=column, value=header)
            cell.font = Font(bold=True)
            cell.fill = _HEADER_FILL
        row_idx += 1
        for offset in range(location.row_count):
            code = _report_cell(location, "code", offset)
            exposure = _report_cell(location, "value", offset)
            reported = _report_cell(location, "pct_nof", offset)
            if code is not None:
                sheet.cell(row=row_idx, column=1, value=f"={code}")
            if exposure is not None:
                sheet.cell(row=row_idx, column=2, value=f"={exposure}").number_format = _GHS_FORMAT
                sheet.cell(
                    row=row_idx, column=3, value=f'=IFERROR(B{row_idx}/$B$5*100,"")'
                ).number_format = _PCT_FORMAT
            if reported is not None:
                sheet.cell(row=row_idx, column=4, value=f"={reported}").number_format = _PCT_FORMAT
                sheet.cell(
                    row=row_idx,
                    column=5,
                    value=f'=IF(ABS(C{row_idx}-D{row_idx})<0.0001,"MATCH","CHECK")',
                )
            row_idx += 1
        row_idx += 1
    return True


def _working_stress(sheet: Worksheet, locations: dict[str, _SectionLocation]) -> bool:
    positions = locations.get("t1_summary_positions")
    if positions is None or not positions.row_count:
        return False
    _working_header(sheet, "SDI Annual Stress — Working CAR Calculations")
    headers = (
        "Period",
        "Net Own Funds",
        "Risk-Weighted Assets",
        "Calculated CAR %",
        "Reported CAR %",
        "Check",
    )
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=5, column=column, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    for offset in range(positions.row_count):
        row_idx = 6 + offset
        period = _report_cell(positions, "description", offset)
        nof = _report_cell(positions, "value", offset)
        rwa = _report_cell(positions, "total_rwa", offset)
        reported = _report_cell(positions, "car_pct", offset)
        if period is not None:
            sheet.cell(row=row_idx, column=1, value=f"={period}")
        if nof is not None:
            sheet.cell(row=row_idx, column=2, value=f"={nof}").number_format = _NUMBER_FORMAT
        if rwa is not None:
            sheet.cell(row=row_idx, column=3, value=f"={rwa}").number_format = _NUMBER_FORMAT
            sheet.cell(
                row=row_idx, column=4, value=f'=IFERROR(B{row_idx}/C{row_idx}*100,"")'
            ).number_format = _PCT_FORMAT
        if reported is not None:
            sheet.cell(row=row_idx, column=5, value=f"={reported}").number_format = _PCT_FORMAT
            sheet.cell(
                row=row_idx,
                column=6,
                value=f'=IF(ABS(D{row_idx}-E{row_idx})<0.0001,"MATCH","CHECK")',
            )
    return True


def _working_irrbb(sheet: Worksheet, locations: dict[str, _SectionLocation]) -> bool:
    gap = locations.get("repricing_gap")
    if gap is None or not gap.row_count:
        return False
    _working_header(sheet, "SDI IRRBB — Working Repricing-Gap Calculations")
    headers = (
        "Time Bucket",
        "Rate-Sensitive Assets",
        "Rate-Sensitive Liabilities",
        "Calculated Gap",
        "Reported Gap",
        "Check",
    )
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=5, column=column, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    for offset in range(gap.row_count):
        row_idx = 6 + offset
        bucket = _report_cell(gap, "code", offset)
        rsa = _report_cell(gap, "rsa_ghs", offset)
        rsl = _report_cell(gap, "rsl_ghs", offset)
        reported = _report_cell(gap, "value", offset)
        if bucket is not None:
            sheet.cell(row=row_idx, column=1, value=f"={bucket}")
        if rsa is not None:
            sheet.cell(row=row_idx, column=2, value=f"={rsa}").number_format = _GHS_FORMAT
        if rsl is not None:
            sheet.cell(row=row_idx, column=3, value=f"={rsl}").number_format = _GHS_FORMAT
        if rsa is not None and rsl is not None:
            sheet.cell(
                row=row_idx, column=4, value=f"=B{row_idx}-C{row_idx}"
            ).number_format = _GHS_FORMAT
        if reported is not None:
            sheet.cell(row=row_idx, column=5, value=f"={reported}").number_format = _GHS_FORMAT
            sheet.cell(
                row=row_idx,
                column=6,
                value=f'=IF(ABS(D{row_idx}-E{row_idx})<0.0001,"MATCH","CHECK")',
            )
    return True


# --- Capital Adequacy (CAR-RWA) and Net Open Position (FX-NOP) -------------------
#
# Every figure is recomputed by a live formula from the cells of the report sheets
# in this workbook, and checked against the figure the return reports. The formulas
# keep to SUM, IF, ABS, MAX and arithmetic (the vocabulary
# ``bog_forms.formulas.WorkbookEvaluator`` evaluates), so a test can prove that
# every check reads MATCH.

_MONEY_TOLERANCE = "0.001"  # GHS '000, i.e. one cedi
_RAW_MONEY_TOLERANCE = "1"  # whole cedis, for figures the return states unscaled
_RATIO_TOLERANCE = "0.0001"  # percentage points


def _ref_at(location: _SectionLocation, key: str, row: int) -> str | None:
    column = location.columns.get(key)
    if column is None:
        return None
    return f"{_quote_sheet_title(location.sheet_title)}!{get_column_letter(column)}{row}"


def _line_ref(location: _SectionLocation | None, code: str, key: str = "value") -> str | None:
    if location is None:
        return None
    row = location.rows.get(code)
    return None if row is None else _ref_at(location, key, row)


def _data_rows(location: _SectionLocation | None) -> list[int]:
    if location is None:
        return []
    return [location.first_data_row + offset for offset in range(location.row_count)]


def _sum_of(location: _SectionLocation | None, key: str = "value") -> str:
    refs = (
        [ref for row in _data_rows(location) if (ref := _ref_at(location, key, row))]
        if location
        else []
    )
    return f"SUM({','.join(refs)})" if refs else "0"


class _CheckSheet:
    """Writes ``Line | Calculation | Recalculated | Reported | Check`` rows."""

    def __init__(self, sheet: Worksheet, first_row: int) -> None:
        self.sheet = sheet
        self.row = first_row

    def headers(self, labels: tuple[str, ...]) -> None:
        for column, label in enumerate(labels, start=1):
            cell = self.sheet.cell(row=self.row, column=column, value=label)
            cell.font = Font(bold=True)
            cell.fill = _HEADER_FILL
        self.row += 1

    def heading(self, text: str) -> None:
        self.row += 1
        self.sheet.cell(row=self.row, column=1, value=text).font = Font(bold=True, size=11)
        self.row += 1

    def line(  # noqa: PLR0913 - one row, every cell of it
        self,
        label: str,
        how: str,
        recalculated: str | None,
        reported: str | None,
        *,
        number_format: str = _GHS_FORMAT,
        tolerance: str = _MONEY_TOLERANCE,
    ) -> int:
        row = self.row
        self.sheet.cell(row=row, column=1, value=label)
        self.sheet.cell(row=row, column=2, value=how).font = Font(italic=True, size=9)
        if recalculated is not None:
            self.sheet.cell(
                row=row, column=3, value=f"={recalculated}"
            ).number_format = number_format
        if reported is not None:
            self.sheet.cell(row=row, column=4, value=f"={reported}").number_format = number_format
        if recalculated is not None and reported is not None:
            self.sheet.cell(
                row=row, column=5, value=f'=IF(ABS(C{row}-D{row})<{tolerance},"MATCH","CHECK")'
            )
        self.row += 1
        return row

    def widths(self, widths: tuple[int, ...]) -> None:
        for column, width in enumerate(widths, start=1):
            self.sheet.column_dimensions[get_column_letter(column)].width = width


def _working_capital(
    sheet: Worksheet, locations: dict[str, _SectionLocation], snapshot: dict[str, Any]
) -> bool:
    cet1 = locations.get("cet1")
    credit = locations.get("credit_rwa")
    ratios = locations.get("capital_ratios")
    if cet1 is None or credit is None or ratios is None:
        return False
    headline = locations.get("headline_comparative")
    _working_header(sheet, "Capital Adequacy — Working Calculations")
    out = _CheckSheet(sheet, 5)
    out.headers(
        ("Line", "Calculation", "Recalculated", "Reported", "Check", "Minimum (%)", "Meets minimum")
    )

    def reported_total(code: str) -> str | None:
        ref = _line_ref(headline, code)
        if ref is not None:
            return ref
        value = _snapshot_total(snapshot, code)
        return None if value is None else str(value)

    out.heading("Regulatory capital (GHS '000)")
    cet1_reported = (
        _ref_at(cet1, "value", cet1.total_row) if cet1.total_row is not None else None
    ) or reported_total("cet1_total_ghs")
    r_cet1 = out.line("CET1 capital", "Sum of the CET1 lines", _sum_of(cet1), cet1_reported)
    r_at1 = out.line(
        "Additional Tier 1", "Sum of the AT1 lines", _sum_of(locations.get("at1")), None
    )
    r_t1 = out.line(
        "Tier 1 capital", "CET1 + AT1", f"C{r_cet1}+C{r_at1}", reported_total("tier1_total_ghs")
    )
    r_t2 = out.line(
        "Tier 2 capital", "Sum of the Tier 2 lines", _sum_of(locations.get("tier2")), None
    )
    r_capital = out.line(
        "Total regulatory capital",
        "Tier 1 + Tier 2",
        f"C{r_t1}+C{r_t2}",
        reported_total("total_capital_ghs"),
    )

    out.heading("Credit risk-weighted assets (GHS '000)")
    credit_rows: list[int] = []
    for row in _data_rows(credit):
        exposure = _ref_at(credit, "balance", row)
        weight = _ref_at(credit, "rate_pct", row)
        code = _ref_at(credit, "code", row)
        description = _ref_at(credit, "description", row)
        if exposure is None or weight is None:
            continue
        written = out.line(
            "", "Exposure × risk weight", f"{exposure}*{weight}/100", _ref_at(credit, "value", row)
        )
        if code is not None:
            sheet.cell(row=written, column=1, value=f"={code}")
        if description is not None:
            sheet.cell(row=written, column=2, value=f"={description}")
        credit_rows.append(written)
    r_credit = out.line(
        "Credit RWA",
        "Sum of the lines above",
        f"SUM(C{credit_rows[0]}:C{credit_rows[-1]})" if credit_rows else "0",
        _sum_of(credit),
    )

    out.heading("Market and operational risk-weighted assets (GHS '000)")
    r_market = _market_lines(out, locations.get("market_rwa"), r_credit)
    r_operational = _operational_lines(out, locations.get("operational_rwa"), r_credit)
    r_rwa = out.line(
        "Total RWA",
        "Credit + market + operational",
        "+".join(f"C{row}" for row in (r_credit, r_market, r_operational)),
        reported_total("total_rwa_ghs"),
    )

    _ratio_lines(out, ratios, r_cet1, r_t1, r_capital, r_rwa)
    out.widths((34, 58, 18, 18, 10, 14, 14))
    return True


def _ratio_lines(  # noqa: PLR0913 - the ratios and the four lines they divide
    out: _CheckSheet,
    ratios: _SectionLocation,
    r_cet1: int,
    r_t1: int,
    r_capital: int,
    r_rwa: int,
) -> None:
    sheet = out.sheet
    out.heading("Capital adequacy ratios (%)")
    for code, label, numerator in (
        ("12.1", "CET1 ratio", r_cet1),
        ("12.2", "Tier 1 ratio", r_t1),
        ("12.3", "Capital adequacy ratio (CAR)", r_capital),
    ):
        reported = _line_ref(ratios, code)
        if reported is None:
            continue
        row = out.line(
            label,
            "Capital ÷ total RWA × 100",
            f"C{numerator}/C{r_rwa}*100",
            reported,
            number_format=_PCT_FORMAT,
            tolerance=_RATIO_TOLERANCE,
        )
        minimum = _line_ref(ratios, code, "minimum_pct")
        if minimum is not None:
            sheet.cell(row=row, column=6, value=f"={minimum}").number_format = _PCT_FORMAT
            sheet.cell(row=row, column=7, value=f'=IF(C{row}>=F{row},"Yes","No")')
    leverage = _line_ref(ratios, "12.4")
    if leverage is not None:
        row = out.line(
            "Leverage ratio",
            "Tier 1 ÷ leverage exposure measure; the exposure measure is not part of this "
            "return, so the reported ratio is carried as filed",
            None,
            leverage,
            number_format=_PCT_FORMAT,
        )
        minimum = _line_ref(ratios, "12.4", "minimum_pct")
        if minimum is not None:
            sheet.cell(row=row, column=6, value=f"={minimum}").number_format = _PCT_FORMAT
            sheet.cell(row=row, column=7, value=f'=IF(D{row}>=F{row},"Yes","No")')


def _market_lines(out: _CheckSheet, market: _SectionLocation | None, r_credit: int) -> int:
    rows = _data_rows(market)
    if market is None or len(rows) < 3:
        return out.line("Market RWA", "No market risk lines in this return", "0", None)
    rwa = rows[-1]
    if len(rows) == 3:
        # Prescribed as a percentage of credit RWA (SDI s.29).
        return out.line(
            "Market RWA",
            "Credit RWA × prescribed percentage",
            f"C{r_credit}*{_ref_at(market, 'rate_pct', rwa)}/100",
            _ref_at(market, "value", rwa),
        )
    long_, short, charge = rows[0], rows[1], rows[-2]
    r_open = out.line(
        "Larger open FX position",
        "Larger of net long and net short",
        f"MAX({_ref_at(market, 'balance', long_)},{_ref_at(market, 'balance', short)})",
        _ref_at(market, "balance", charge),
    )
    r_charge = out.line(
        "FX capital charge",
        "Larger open position × charge rate",
        f"C{r_open}*{_ref_at(market, 'rate_pct', charge)}/100",
        _ref_at(market, "value", charge),
    )
    r_multiplier = out.line(
        "RWA multiplier",
        "As applied by the return: market RWA ÷ FX charge",
        f"{_ref_at(market, 'value', rwa)}/{_ref_at(market, 'balance', rwa)}",
        None,
        number_format=_NUMBER_FORMAT,
    )
    return out.line(
        "Market RWA",
        "FX charge × RWA multiplier",
        f"C{r_charge}*C{r_multiplier}",
        _ref_at(market, "value", rwa),
    )


def _operational_lines(
    out: _CheckSheet, operational: _SectionLocation | None, r_credit: int
) -> int:
    rows = _data_rows(operational)
    if operational is None or not rows:
        return out.line("Operational RWA", "No operational risk lines in this return", "0", None)
    rwa = rows[-1]
    if len(rows) == 1:
        return out.line(
            "Operational RWA",
            "Credit RWA × prescribed percentage",
            f"C{r_credit}*{_ref_at(operational, 'rate_pct', rwa)}/100",
            _ref_at(operational, "value", rwa),
        )
    charge = rows[-2]
    income = [_ref_at(operational, "value", row) for row in rows[:-2]]
    counted = [ref for ref in income if ref is not None]
    r_income = out.line(
        "Gross income counted",
        "Positive gross income in the three-year window",
        f"SUM({','.join(counted)})" if counted else "0",
        _ref_at(operational, "balance", charge),
    )
    r_years = out.line(
        "Years counted",
        "Years in the window with positive gross income",
        "+".join(f"IF({ref}>0,1,0)" for ref in counted) if counted else "0",
        None,
        number_format="0",
    )
    r_charge = out.line(
        "BIA capital charge",
        "Gross income counted × alpha ÷ years counted",
        f"C{r_income}*{_ref_at(operational, 'rate_pct', charge)}/100/C{r_years}",
        _ref_at(operational, "value", charge),
    )
    r_multiplier = out.line(
        "RWA multiplier",
        "As applied by the return: operational RWA ÷ BIA charge",
        f"{_ref_at(operational, 'value', rwa)}/{_ref_at(operational, 'balance', rwa)}",
        None,
        number_format=_NUMBER_FORMAT,
    )
    return out.line(
        "Operational RWA",
        "BIA charge × RWA multiplier",
        f"C{r_charge}*C{r_multiplier}",
        _ref_at(operational, "value", rwa),
    )


def _fx_currency_table(sheet: Worksheet, positions: _SectionLocation) -> tuple[int, int, int]:
    """One row per currency; returns its first and last rows and the next free row."""
    headers = (
        "Currency",
        "Net position (FCY)",
        "End-of-day rate",
        "At end-of-day rate (GHS '000)",
        "Reported (GHS '000)",
        "Difference (GHS '000)",
        "|Net| ÷ Tier 1 (%)",
        "Reported (%)",
        "Check",
        "Within single limit",
        "Long (GHS)",
        "Short (GHS)",
    )
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=9, column=column, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    first = 10
    row = first
    for report_row in _data_rows(positions):
        refs = {
            key: _ref_at(positions, key, report_row)
            for key in ("code", "net_ccy", "spot_ghs", "value", "abs_pct_tier1")
        }
        if refs["value"] is None:
            continue
        if refs["code"] is not None:
            sheet.cell(row=row, column=1, value=f"={refs['code']}")
        if refs["net_ccy"] is not None and refs["spot_ghs"] is not None:
            sheet.cell(
                row=row, column=2, value=f"={refs['net_ccy']}"
            ).number_format = _NUMBER_FORMAT
            sheet.cell(row=row, column=3, value=f"={refs['spot_ghs']}").number_format = "0.0000"
            sheet.cell(
                row=row, column=4, value=f"=B{row}*C{row}/1000"
            ).number_format = _NUMBER_FORMAT
            sheet.cell(row=row, column=6, value=f"=E{row}-D{row}").number_format = _NUMBER_FORMAT
        sheet.cell(row=row, column=5, value=f"={refs['value']}").number_format = _NUMBER_FORMAT
        sheet.cell(
            row=row, column=7, value=f"=ABS(E{row}*1000)/$C$5*100"
        ).number_format = _PCT_FORMAT
        if refs["abs_pct_tier1"] is not None:
            sheet.cell(
                row=row, column=8, value=f"={refs['abs_pct_tier1']}"
            ).number_format = _PCT_FORMAT
            sheet.cell(
                row=row,
                column=9,
                value=f'=IF(ABS(G{row}-H{row})<{_RATIO_TOLERANCE},"MATCH","CHECK")',
            )
        sheet.cell(row=row, column=10, value=f'=IF(G{row}<=$C$6,"Yes","No")')
        sheet.cell(
            row=row, column=11, value=f"=IF(E{row}>0,E{row}*1000,0)"
        ).number_format = _GHS_FORMAT
        sheet.cell(
            row=row, column=12, value=f"=IF(E{row}<0,-E{row}*1000,0)"
        ).number_format = _GHS_FORMAT
        row += 1
    last = row - 1
    note = sheet.cell(
        row=row,
        column=1,
        value=(
            "Difference: the return's GHS position is the booked reporting-currency balance "
            "plus hedges revalued at the rate; the column beside it converts the whole "
            "position at the end-of-day rate."
        ),
    )
    note.font = Font(italic=True, size=9)

    return first, last, row


def _working_fx_nop(
    sheet: Worksheet, locations: dict[str, _SectionLocation], snapshot: dict[str, Any]
) -> bool:
    positions = locations.get("currency_positions")
    summary = locations.get("nop_summary")
    tier1 = _line_ref(summary, "tier1_ghs")
    if positions is None or summary is None or tier1 is None or not positions.row_count:
        return False
    metadata = snapshot.get("metadata")
    limits = cast(dict[str, object], metadata) if isinstance(metadata, dict) else {}
    _working_header(sheet, "Net Open Position — Working Calculations")

    sheet.cell(row=5, column=1, value="Tier 1 capital (GHS)").font = Font(bold=True)
    sheet.cell(row=5, column=3, value=f"={tier1}").number_format = _GHS_FORMAT
    for row, label, key in (
        (6, "Single-currency limit (% of Tier 1)", "nop_single_limit_pct"),
        (7, "Aggregate limit (% of Tier 1)", "nop_aggregate_limit_pct"),
    ):
        sheet.cell(row=row, column=1, value=label).font = Font(bold=True)
        raw = limits.get(key)
        try:
            value = Decimal(str(raw)) if raw not in (None, "") else None
        except InvalidOperation:
            value = None
        if value is not None:
            sheet.cell(row=row, column=3, value=value).number_format = _PCT_FORMAT

    first, last, row = _fx_currency_table(sheet, positions)
    out = _CheckSheet(sheet, row + 2)
    out.headers(("Aggregate position", "Calculation", "Recalculated", "Reported", "Check"))
    r_long = out.line(
        "Sum of long positions (GHS)",
        "Sum of the long column",
        f"SUM(K{first}:K{last})",
        _line_ref(summary, "sum_long_ghs"),
        tolerance=_RAW_MONEY_TOLERANCE,
    )
    r_short = out.line(
        "Sum of short positions (GHS)",
        "Sum of the short column",
        f"SUM(L{first}:L{last})",
        _line_ref(summary, "sum_short_ghs"),
        tolerance=_RAW_MONEY_TOLERANCE,
    )
    r_nop = out.line(
        "Aggregate net open position (GHS)",
        "Larger of the long and short sums",
        f"MAX(C{r_long},C{r_short})",
        _line_ref(summary, "nop_ghs"),
        tolerance=_RAW_MONEY_TOLERANCE,
    )
    r_pct = out.line(
        "Aggregate NOP as % of Tier 1",
        "Aggregate NOP ÷ Tier 1 × 100",
        f"C{r_nop}/$C$5*100",
        _line_ref(summary, "nop_pct_tier1"),
        number_format=_PCT_FORMAT,
        tolerance=_RATIO_TOLERANCE,
    )
    sheet.cell(row=r_pct, column=6, value="Within aggregate limit").font = Font(bold=True)
    sheet.cell(row=r_pct, column=7, value=f'=IF(C{r_pct}<=$C$7,"Yes","No")')
    out.widths((34, 22, 20, 20, 18, 18, 18, 14, 10, 18, 16, 16))
    return True


def _formula_workbook_filed(snapshot: dict[str, Any]) -> bool:
    metadata = snapshot.get("metadata")
    return (
        isinstance(metadata, dict)
        and cast(dict[str, object], metadata).get("formula_workbook") == "filed"
    )


def _mark_filed(sheet: Worksheet) -> None:
    """Restate the header of a workbook that is filed with its return."""
    sheet.cell(row=1, column=1, value=_FILED_WORKBOOK_BANNER)
    sheet.cell(row=3, column=1, value=_FILED_WORKBOOK_NOTE)


def _working_calculations_sheet(
    workbook: Workbook,
    rendered: RenderedReturn,
    locations: dict[str, _SectionLocation],
    snapshot: dict[str, Any],
    used_titles: set[str],
) -> None:
    sheet = cast(
        Worksheet, workbook.create_sheet(_sheet_title("Working Calculations", used_titles))
    )
    code = rendered.template.return_code
    written = (
        _working_lmt(sheet, locations)
        if code == "SDI-LMT-MONTHLY"
        else _working_large_exposures(sheet, locations, snapshot)
        if code == "SDI-LE-MONTHLY"
        else _working_stress(sheet, locations)
        if code == "SDI-STRESS-ANNUAL"
        else _working_irrbb(sheet, locations)
        if code == "SDI-IRRBB-QUARTERLY"
        else _working_capital(sheet, locations, snapshot)
        if code == "CAR-RWA"
        else _working_fx_nop(sheet, locations, snapshot)
        if code == "FX-NOP"
        else False
    )
    if not written:
        workbook.remove(sheet)
        return
    if _formula_workbook_filed(snapshot):
        _mark_filed(sheet)
    for column in range(1, 7):
        sheet.column_dimensions[get_column_letter(column)].width = 28


def _provenance_sheet(workbook: Workbook, rendered: RenderedReturn, used_titles: set[str]) -> None:
    sheet = workbook.create_sheet(_sheet_title("Fidelity & Provenance", used_titles))
    row_idx = 1
    for line in rendered.provenance_lines:
        sheet.cell(row=row_idx, column=1, value=xml_safe(line)).font = Font(bold=(row_idx == 1))
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
            sheet.cell(row=row_idx, column=col_idx, value=xml_safe(value))
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


#: The same normalisation, under a public name. Every OOXML container has the
#: wall-clock problem this solves, so the ICAAP DOCX exports
#: (``icaap/service/render/docx.py``) reuse it rather than growing a second,
#: drifting copy: python-docx stamps zip entries at save time exactly as
#: openpyxl does.
normalize_zip = _normalize_zip


def render_xlsx(
    rendered: RenderedReturn,
    *,
    generated_at: datetime,
    working_copy: bool = False,
    snapshot: dict[str, Any] | None = None,
) -> bytes:
    workbook = Workbook()
    pinned = generated_at.replace(tzinfo=None, microsecond=0)
    workbook.properties.created = pinned
    workbook.properties.modified = pinned
    workbook.properties.creator = "AequorOS Regulatory Reporting"
    workbook.properties.lastModifiedBy = "AequorOS Regulatory Reporting"
    if working_copy:
        workbook.calculation.fullCalcOnLoad = True
        workbook.calculation.forceFullCalc = True

    used_titles: set[str] = {"Return Metadata"}
    _metadata_sheet(workbook, rendered)
    locations = _section_sheets(workbook, rendered, used_titles)
    if working_copy and snapshot is not None:
        _working_calculations_sheet(workbook, rendered, locations, snapshot, used_titles)
    _provenance_sheet(workbook, rendered, used_titles)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return _normalize_zip(buffer.getvalue(), pinned)


__all__ = ["normalize_zip", "render_xlsx"]
