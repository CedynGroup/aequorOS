"""Layout-faithful PDF text reconstruction for BoG templated PDFs.

Why not pypdf: on the captured auction-result PDFs pypdf emits each table
COLUMN as a contiguous block (all ISINs, then all security names, then all
amounts...), destroying row association. And on the captured APR PDF, pages
4/6/8 position every glyph individually, so even pdfplumber's
``extract_text(layout=True)`` shatters them ("T a b le 3" with the numeric
columns dropped). Reconstructing lines directly from character geometry —
cluster by ``top``, sort by ``x0``, split words on x-gaps — recovers every
page of every captured fixture faithfully; pdfplumber is the dependency that
exposes that geometry.

The BoG "PUBLIC" watermark is rendered as stray letters that land INSIDE
data lines (the captured SEFD has ``15L.56`` and ``B16.87``);
``clean_numeric_token`` strips watermark letters out of otherwise-numeric
tokens and reports that it did.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation

import pdfplumber

_WATERMARK_LETTERS = set("PUBLIC")
_NUMERIC_CORE_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def page_lines(raw: bytes, *, y_tolerance: float = 2.0, gap: float = 2.0) -> list[list[str]]:
    """Reconstruct each page as a list of text lines from char geometry."""
    pages: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            rows: list[tuple[float, list[dict]]] = []
            for char in sorted(page.chars, key=lambda c: (c["top"], c["x0"])):
                if rows and char["top"] - rows[-1][0] <= y_tolerance:
                    rows[-1][1].append(char)
                else:
                    rows.append((char["top"], [char]))
            lines: list[str] = []
            for _top, chars in rows:
                chars.sort(key=lambda c: c["x0"])
                buf: list[str] = []
                prev_x1: float | None = None
                for char in chars:
                    if prev_x1 is not None and char["x0"] - prev_x1 > gap:
                        buf.append(" ")
                    buf.append(char["text"])
                    prev_x1 = char["x1"]
                lines.append("".join(buf).strip())
            pages.append(lines)
    return pages


def clean_numeric_token(token: str) -> tuple[Decimal | None, bool]:
    """Parse a table cell that should be a number, tolerating the watermark.

    Returns ``(value, watermark_cleaned)``; ``(None, False)`` when the token
    is not a number even after cleaning (``N/A``, ``NL``, labels...).
    """
    stripped = token.strip().rstrip("%")
    if not stripped:
        return None, False
    if _NUMERIC_CORE_RE.match(stripped):
        return _to_decimal(stripped), False
    if any(ch in _WATERMARK_LETTERS for ch in stripped):
        cleaned = "".join(ch for ch in stripped if ch not in _WATERMARK_LETTERS)
        if cleaned and _NUMERIC_CORE_RE.match(cleaned):
            return _to_decimal(cleaned), True
    return None, False


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
