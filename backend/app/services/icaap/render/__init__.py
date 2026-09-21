"""Renderers for the ICAAP document model.

One model in (``app/domain/icaap/document.IcaapDocument``), bytes out. Nothing
in this package touches the database, the request or the clock: everything it
prints was resolved by ``services/icaap/exports_draft.py`` (P1 drafts) or by
P3's snapshot reader, which is what lets a draft and a filed document of the
same cycle be compared line by line.

* ``format`` — stored payload values to display strings.
* ``pdf`` — the draft PDF (reportlab); P3's filing PDF reuses it with an
  embedded Unicode font and no watermark.
* ``docx`` — the editable working copy (python-docx).
"""

from __future__ import annotations

from app.services.icaap.render.docx import render_docx
from app.services.icaap.render.pdf import (
    HELVETICA,
    FontSet,
    IcaapPdfRenderer,
    render_pdf,
    render_pdf_with_report,
)

__all__ = [
    "HELVETICA",
    "FontSet",
    "IcaapPdfRenderer",
    "render_docx",
    "render_pdf",
    "render_pdf_with_report",
]
