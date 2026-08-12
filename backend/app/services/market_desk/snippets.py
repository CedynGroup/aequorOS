"""Capture content decoding + field-level source snippets for the desk.

Silver captures store raw bytes as base64 in ``payload.content_base64`` (or
omit when over the inline cap). Analysts need to see the source of an
extracted observation — HTML context, PDF text window, or JSON excerpt.
"""

from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from app.models import DeskSourceCapture

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_CONTEXT_CHARS = 240
_MAX_RETURN_CHARS = 12_000


def _decode_payload(capture: DeskSourceCapture) -> tuple[bytes | None, dict[str, Any]]:
    payload = capture.payload or {}
    meta = dict(payload.get("meta") or {})
    b64 = payload.get("content_base64")
    if isinstance(b64, str) and b64:
        try:
            return base64.b64decode(b64), meta
        except Exception as exc:  # noqa: BLE001 - surface as 422
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Capture payload is not valid base64: {exc}",
            ) from exc
    return None, meta


def _kind_hint(meta: dict[str, Any], content: bytes | None) -> str:
    kind = str(meta.get("kind") or "")
    if kind:
        return kind
    if content is None:
        return "empty"
    if content[:4] == b"%PDF":
        return "pdf"
    head = content[:200].lstrip().lower()
    if head.startswith(b"{") or head.startswith(b"["):
        return "json"
    if b"<html" in head or b"<!doctype" in head or b"<table" in head:
        return "html"
    return "text"


def _window_around(text: str, needle: str, *, radius: int = _CONTEXT_CHARS) -> str | None:
    if not needle or not text:
        return None
    # Prefer exact, then digit-stripped float-ish match.
    idx = text.find(needle)
    if idx < 0:
        # Try without trailing zeros: "15.00" -> "15"
        compact = needle.rstrip("0").rstrip(".")
        if compact and compact != needle:
            idx = text.find(compact)
    if idx < 0:
        return None
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    # Cheap strip — enough for BoG table pages; not a full browser.
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _pdf_to_text(raw: bytes) -> str:
    try:
        from app.services.market_desk.sources.pdf_text import extract_text  # type: ignore
    except Exception:
        extract_text = None
    if extract_text is not None:
        try:
            return str(extract_text(raw) or "")
        except Exception:  # noqa: BLE001
            pass
    # Fallback: latin-1 dump of extractable strings (low quality but present).
    try:
        return raw.decode("latin-1", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def capture_content_view(
    db: Session,
    capture_id: Any,
    *,
    needle: str | None = None,
    max_chars: int = _MAX_RETURN_CHARS,
) -> dict[str, Any]:
    """Return decoded capture content (truncated) and optional value snippet."""
    capture = db.get(DeskSourceCapture, capture_id)
    if capture is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Capture does not exist.",
        )
    content, meta = _decode_payload(capture)
    kind = _kind_hint(meta, content)
    omitted = (capture.payload or {}).get("content_omitted")

    text: str | None = None
    if content is not None:
        if kind in {"html", "publication_page", "tender_page"} or (
            kind == "text" and b"<" in content[:500]
        ):
            text = _html_to_text(content)
            kind = "html" if kind == "text" else kind
        elif kind in {"pdf", "result_pdf", "publication_pdf", "report_file"} or content[
            :4
        ] == b"%PDF":
            text = _pdf_to_text(content)
            kind = "pdf"
        elif kind in {"json", "pxweb_data"} or (
            content.lstrip()[:1] in (b"{", b"[")
        ):
            try:
                parsed = json.loads(content.decode("utf-8"))
                text = json.dumps(parsed, indent=2, ensure_ascii=False)
                kind = "json"
            except Exception:  # noqa: BLE001
                text = content.decode("utf-8", errors="replace")
        else:
            text = content.decode("utf-8", errors="replace")

    truncated = False
    if text is not None and len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"
        truncated = True

    snippet = None
    if text and needle:
        snippet = _window_around(text, needle)

    return {
        "capture_id": str(capture.id),
        "source_key": capture.source_key,
        "source_url": capture.source_url,
        "as_of_date": capture.as_of_date.isoformat(),
        "status": capture.status,
        "content_sha256": capture.content_sha256,
        "parser_version": capture.parser_version,
        "kind": kind,
        "content_omitted": omitted,
        "content_available": content is not None,
        "content_bytes": len(content) if content is not None else 0,
        "text": text,
        "truncated": truncated,
        "snippet": snippet,
        "needle": needle,
        "meta": meta,
    }


def snippet_for_observation(
    db: Session,
    *,
    capture_id: Any,
    value: str | None,
) -> dict[str, Any]:
    """Focused snippet around an observation value for field-level review."""
    view = capture_content_view(db, capture_id, needle=value)
    return {
        "capture_id": view["capture_id"],
        "source_key": view["source_key"],
        "source_url": view["source_url"],
        "kind": view["kind"],
        "content_available": view["content_available"],
        "content_omitted": view["content_omitted"],
        "snippet": view["snippet"],
        "needle": value,
        "hint": (
            None
            if view["snippet"]
            else (
                "Value not found in capture text — open full content or use source URL."
                if view["content_available"]
                else "Raw content not stored inline (over size cap) — use source URL."
            )
        ),
    }
