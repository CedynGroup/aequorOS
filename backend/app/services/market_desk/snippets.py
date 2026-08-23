"""The capture payload contract: how raw bytes are stored, and how read.

This module is the ONE place that knows the shape of
``desk_source_captures.payload``. The nightly capture job writes through the
constants and :func:`find_payload_anchor` here; every reader of capture
content goes through :func:`capture_content_view`. Keeping both sides in one
file is deliberate — a writer and a reader that drift on this shape would
leave an observation whose lineage root reads empty.

Three payload shapes, all mutually exclusive:

``content_base64``
    The bytes ride inline (the common case).
``content_deferred_to`` (+ ``content_omitted``)
    These exact bytes were already stored by an EARLIER capture of the same
    source, so this row records the observation's lineage and skips the
    duplicate copy. It names the row that holds the bytes, and readers
    resolve that hop transparently. The pointer is always ONE hop — a new
    capture defers to the row that actually holds bytes, never to another
    deferral — which :func:`find_payload_anchor` guarantees at write time.
``content_omitted`` alone
    The artifact exceeded the inline cap and only its SHA-256 was kept.

Absence is always stated: a payload with no bytes says so in
``content_omitted`` and, when the bytes live elsewhere, names the row.

Analysts need to see the source of an extracted observation — HTML context,
PDF text window, or JSON excerpt — which is the rest of this module.
"""

from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.models import DeskSourceCapture

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_CONTEXT_CHARS = 240
_MAX_RETURN_CHARS = 12_000

#: Payload key: the artifact's bytes, base64-encoded.
CONTENT_BASE64 = "content_base64"
#: Payload key: why the bytes are not on this row (human-readable).
CONTENT_OMITTED = "content_omitted"
#: Payload key: the capture id that holds this row's bytes.
CONTENT_DEFERRED_TO = "content_deferred_to"


def deferred_payload(*, anchor_id: UUID, meta: dict[str, Any]) -> dict[str, Any]:
    """The payload of a capture whose bytes are already stored on ``anchor_id``.

    ``content_omitted`` deliberately repeats the fact in the sentinel shape
    this payload has always used for absent bytes, so even a reader that
    knows nothing about deferral states the absence — and names the row —
    rather than reporting an empty artifact.
    """
    return {
        "meta": meta,
        CONTENT_DEFERRED_TO: str(anchor_id),
        CONTENT_OMITTED: (
            f"byte-identical re-capture; payload stored on capture {anchor_id}"
        ),
    }


def _inline_bytes(payload: dict[str, Any] | None) -> bytes | None:
    """The bytes stored ON this payload, or None when it carries none."""
    b64 = (payload or {}).get(CONTENT_BASE64)
    if not isinstance(b64, str) or not b64:
        return None
    try:
        return base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001 - surface as 422
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Capture payload is not valid base64: {exc}",
        ) from exc


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def find_payload_anchor(
    db: Session, *, source_key: str, content: bytes, content_sha256: str
) -> UUID | None:
    """The capture already holding exactly ``content`` for this source, if any.

    Scoped to ``source_key``: a capture's lineage must resolve inside its own
    source, so an examiner following an observation never lands on an
    unrelated site's row.

    The digest is only the index probe — the anchor is confirmed by comparing
    the stored bytes to ``content`` itself, so a wrong or stale
    ``content_sha256`` on some earlier row can never make a later capture
    resolve to the wrong artifact. The returned row is always one that holds
    inline bytes, which is what keeps deferral a single hop.
    """
    candidate_id = db.scalar(
        select(DeskSourceCapture.id)
        .where(
            DeskSourceCapture.source_key == source_key,
            DeskSourceCapture.content_sha256 == content_sha256,
        )
        .order_by(DeskSourceCapture.captured_at.desc(), DeskSourceCapture.id.desc())
        .limit(1)
    )
    if candidate_id is None:
        return None
    for row_id in (candidate_id, _deferred_target(db, candidate_id)):
        if row_id is None:
            continue
        row = db.get(DeskSourceCapture, row_id)
        if row is not None and _inline_bytes(row.payload) == content:
            return row.id
    return None


def _deferred_target(db: Session, capture_id: UUID) -> UUID | None:
    row = db.get(DeskSourceCapture, capture_id)
    if row is None:
        return None
    return _as_uuid((row.payload or {}).get(CONTENT_DEFERRED_TO))


def _decode_payload(
    db: Session, capture: DeskSourceCapture
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """This capture's bytes, its meta, and the row it defers its bytes to.

    Deferral is resolved here so every content reader sees the same bytes
    whether or not this capture was the one that first stored them. The
    returned pointer is reported, never swallowed: an examiner is told where
    the artifact lives even when it resolved cleanly.
    """
    payload = capture.payload or {}
    meta = dict(payload.get("meta") or {})
    content = _inline_bytes(payload)
    if content is not None:
        return content, meta, None
    deferred_to = payload.get(CONTENT_DEFERRED_TO)
    anchor_id = _as_uuid(deferred_to)
    if anchor_id is None:
        return None, meta, deferred_to if isinstance(deferred_to, str) else None
    anchor = db.get(DeskSourceCapture, anchor_id)
    if anchor is None:
        return None, meta, str(anchor_id)
    return _inline_bytes(anchor.payload), meta, str(anchor_id)


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
        # Deferred on purpose: PDF extraction is an optional dependency, so a
        # module-level import would break every desk import where it is absent —
        # the except below is the whole point of importing here.
        from app.services.market_desk.sources.pdf_text import (  # noqa: PLC0415
            extract_text,  # type: ignore
        )
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
    content, meta, deferred_to = _decode_payload(db, capture)
    kind = _kind_hint(meta, content)
    omitted = (capture.payload or {}).get(CONTENT_OMITTED)
    if content is not None:
        # The bytes resolved. This row's omission note described where they
        # live, not that they are gone — repeating it beside the artifact
        # would tell the analyst the content is missing when it is right
        # there. ``content_deferred_to`` still names the row that holds it.
        omitted = None

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
        "content_deferred_to": deferred_to,
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
        "content_deferred_to": view["content_deferred_to"],
        "snippet": view["snippet"],
        "needle": value,
        "hint": _snippet_hint(view),
    }


def _snippet_hint(view: dict[str, Any]) -> str | None:
    if view["snippet"]:
        return None
    if view["content_available"]:
        return "Value not found in capture text — open full content or use source URL."
    deferred_to = view["content_deferred_to"]
    if deferred_to:
        # Only reachable if the named row has gone missing: state which one,
        # so the gap is investigable rather than silent.
        return (
            f"Raw content is stored on capture {deferred_to}, which could not be "
            "read — use source URL."
        )
    return "Raw content not stored inline (over size cap) — use source URL."
