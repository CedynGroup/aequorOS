"""Deterministic PDF export for a governed desk-methodology version."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import DeskMethodology

_NAVY = colors.HexColor("#18324B")
_BLUE = colors.HexColor("#2563EB")
_GRID = colors.HexColor("#CBD5E1")
_SURFACE = colors.HexColor("#F8FAFC")
_DRAFT = colors.HexColor("#B45309")
_APPROVED = colors.HexColor("#047857")
_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle(
    "MethodologyTitle", parent=_STYLES["Title"], fontSize=20, leading=25, textColor=_NAVY
)
_H2 = ParagraphStyle(
    "MethodologyHeading",
    parent=_STYLES["Heading2"],
    fontSize=12,
    leading=15,
    textColor=_NAVY,
    spaceBefore=10,
    spaceAfter=5,
)
_BODY = ParagraphStyle(
    "MethodologyBody",
    parent=_STYLES["BodyText"],
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#334155"),
    wordWrap="CJK",
)
_LABEL = ParagraphStyle(
    "MethodologyLabel", parent=_BODY, fontSize=7, leading=9, textColor=colors.HexColor("#64748B")
)
_VALUE = ParagraphStyle(
    "MethodologyValue", parent=_BODY, fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A")
)
_CODE = ParagraphStyle(
    "MethodologyCode", parent=_VALUE, fontName="Courier", fontSize=7.5, leading=9.5, wordWrap="CJK"
)


def _canvas(*args: Any, **kwargs: Any) -> pdf_canvas.Canvas:
    kwargs["invariant"] = 1
    return pdf_canvas.Canvas(*args, **kwargs)


class _Furniture:
    def __init__(self, footer: str) -> None:
        self.footer = footer

    def __call__(self, canvas: pdf_canvas.Canvas, _doc: SimpleDocTemplate) -> None:
        width, height = A4
        canvas.saveState()
        canvas.setStrokeColor(_BLUE)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(18 * mm, 10 * mm, self.footer)
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()


def _display(value: Any) -> str:
    if value is None:
        return "Not set"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parameter_rows(value: Any, *, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_parameter_rows(value[key], prefix=path))
        return rows
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            rows.extend(_parameter_rows(item, prefix=f"{prefix}[{index}]"))
        return rows or [(prefix, "[]")]
    return [(prefix, _display(value))]


def _metadata(row: DeskMethodology) -> Table:
    status_color = _APPROVED if row.status == "approved" else _DRAFT
    fields = (
        ("Methodology code", row.methodology_code),
        ("Version", f"v{row.version}"),
        ("Status", row.status.upper()),
        (
            "Effective from",
            row.effective_from.isoformat() if row.effective_from else "Not approved",
        ),
        ("Proposed by", row.proposed_by),
        ("Approved by", row.approved_by or "Not approved"),
        ("Approved at", row.approved_at.isoformat() if row.approved_at else "Not approved"),
        ("Registered", row.created_at.isoformat()),
    )
    cells = [
        [Paragraph(escape(label), _LABEL), Paragraph(escape(value), _VALUE)]
        for label, value in fields
    ]
    table = Table(cells, colWidths=[38 * mm, 124 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.5, _GRID),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, _GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TEXTCOLOR", (1, 2), (1, 2), status_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def render_methodology_pdf(row: DeskMethodology) -> bytes:
    """Render one registered version without mutating the governed record."""
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title=f"{row.methodology_code} v{row.version}",
        author="AequorOS Market Research Desk",
    )
    story: list[Any] = [
        Spacer(0, 11 * mm),
        Paragraph("AequorOS Market Research Desk", _LABEL),
        Paragraph(
            f"{escape(row.methodology_code)} <font color='#64748B'>v{row.version}</font>", _TITLE
        ),
        Spacer(0, 4 * mm),
        Paragraph(
            "Governed methodology record. This export is a read-only representation of the "
            "versioned register and does not alter its approval state.",
            _BODY,
        ),
        Spacer(0, 6 * mm),
        _metadata(row),
        Paragraph("Change rationale", _H2),
        Paragraph(escape(row.change_rationale), _BODY),
        Paragraph("Versioned parameters", _H2),
        Paragraph(
            "Parameters are shown in stable key order. Nested objects are flattened to their "
            "full paths so the document can be compared to the governed register precisely.",
            _BODY,
        ),
        Spacer(0, 3 * mm),
    ]
    parameter_rows = _parameter_rows(row.parameters)
    if parameter_rows:
        table_rows = [[Paragraph("Parameter", _LABEL), Paragraph("Value", _LABEL)]]
        table_rows.extend(
            [Paragraph(escape(key.replace("_", " ")), _CODE), Paragraph(escape(value), _CODE)]
            for key, value in parameter_rows
        )
        table = Table(table_rows, colWidths=[62 * mm, 100 * mm], repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("BOX", (0, 0), (-1, -1), 0.5, _GRID),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, _GRID),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("No versioned parameters are recorded.", _BODY))
    story.append(Spacer(0, 5 * mm))
    story.append(
        KeepTogether(
            [
                Paragraph("Export provenance", _H2),
                Paragraph(
                    f"Register identity: {escape(str(row.id))}. This document is rendered from "
                    "the immutable versioned register. For approval history and operator actions, "
                    "consult the operator audit log.",
                    _BODY,
                ),
            ]
        )
    )
    document.build(
        story,
        onFirstPage=_Furniture(
            f"AequorOS methodology register | {row.methodology_code} v{row.version}"
        ),
        onLaterPages=_Furniture(
            f"AequorOS methodology register | {row.methodology_code} v{row.version}"
        ),
        canvasmaker=_canvas,
    )
    return buffer.getvalue()


__all__ = ["render_methodology_pdf"]
