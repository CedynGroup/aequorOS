"""Draft ICAAP exports: the PDF to read and the DOCX to redline.

Both routes stream. A draft is not an artifact — it is not stored, not
versioned and never signed — so there is no object to download later and
nothing for object storage to be unavailable for. The filed instrument is P3's
signed package; these two say so on every page.

Authorization is the export permission on the ICAAP surface
(``require_icaap_export``: a complete scoped binding for this institution), and
the flag/tenant/class checks inside that dependency answer 404 before anything
about the cycle is revealed.
"""

from __future__ import annotations

import io
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, IcaapExport
from app.services.icaap import exports_draft

router = APIRouter(tags=["icaap"])


def _binary_response(media_type: str) -> dict[int | str, dict[str, Any]]:
    """Both routes return a file, so the generated client must describe a
    binary body rather than the default JSON one."""
    return {200: {"content": {media_type: {"schema": {"type": "string", "format": "binary"}}}}}


_PDF_RESPONSES = _binary_response(exports_draft.PDF_MEDIA_TYPE)
_DOCX_RESPONSES = _binary_response(exports_draft.DOCX_MEDIA_TYPE)

ContentQuery = Annotated[
    Literal["working", "committed"],
    Query(
        description=(
            "Which text to print: the live working draft, or the latest committed "
            "version of each section. 'committed' never falls back to working text."
        )
    ),
]


def _stream(payload: bytes, *, filename: str, media_type: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/draft.pdf",
    response_class=StreamingResponse,
    operation_id="exportIcaapDraftPdf",
    responses=_PDF_RESPONSES,
)
def export_icaap_draft_pdf(
    bank_id: str,
    cycle_id: UUID,
    db: DbSession,
    access: IcaapExport,
    content: ContentQuery = "working",
) -> StreamingResponse:
    """The cycle as a watermarked draft PDF. Not a filing, and not stored."""
    _ = bank_id  # resolved and authorized by the dependency
    payload, filename = exports_draft.export_draft_pdf(db, access, cycle_id, content=content)
    return _stream(payload, filename=filename, media_type=exports_draft.PDF_MEDIA_TYPE)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/draft.docx",
    response_class=StreamingResponse,
    operation_id="exportIcaapDraftDocx",
    responses=_DOCX_RESPONSES,
)
def export_icaap_draft_docx(
    bank_id: str,
    cycle_id: UUID,
    db: DbSession,
    access: IcaapExport,
    content: ContentQuery = "working",
) -> StreamingResponse:
    """The cycle as an editable working copy. Never the filed document."""
    _ = bank_id  # resolved and authorized by the dependency
    payload, filename = exports_draft.export_draft_docx(db, access, cycle_id, content=content)
    return _stream(payload, filename=filename, media_type=exports_draft.DOCX_MEDIA_TYPE)


__all__ = ["router"]
