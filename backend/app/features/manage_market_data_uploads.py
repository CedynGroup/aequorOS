from __future__ import annotations

import io
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.adapters.market_data.manual_upload import service
from app.adapters.market_data.manual_upload.templates import (
    TemplateKind,
    build_template,
    template_filename,
    template_media_type,
)
from app.api.deps import DbSession, MutationTenant, Tenant
from app.features.ingest_data import MAX_UPLOAD_BYTES, IngestionStorage
from app.schemas.market_data_upload import MarketDataUploadRead

router = APIRouter(tags=["market-data"])


@router.get(
    "/market-data/templates/{kind}",
    response_class=StreamingResponse,
    operation_id="getMarketDataTemplate",
)
def get_market_data_template(kind: TemplateKind, ctx: Tenant) -> StreamingResponse:
    """Download the .xlsx upload template for one scope category (§8.2)."""
    _ = ctx
    content = build_template(kind)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=template_media_type(),
        headers={"Content-Disposition": f'attachment; filename="{template_filename(kind)}"'},
    )


@router.post(
    "/banks/{bank_id}/market-data/uploads",
    response_model=MarketDataUploadRead,
    operation_id="uploadMarketData",
)
async def upload_market_data(  # noqa: PLR0913 - route wiring: tenant, storage, file, form field
    bank_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
    file: UploadFile,
    as_of_date: Annotated[date, Form()],
) -> MarketDataUploadRead:
    """Run an uploaded template file as a manual market data pull (§8.3)."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload exceeds the {MAX_UPLOAD_BYTES // 1_000_000} MB limit.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded file is empty.",
        )
    return service.upload_market_data(
        db,
        ctx,
        bank_id,
        storage,
        filename=file.filename or "upload",
        content=content,
        as_of_date=as_of_date,
    )
