from __future__ import annotations

import io
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, MarketsTemplateView, MarketsUpload
from app.data_engine.public import IngestionStorage, read_upload_within_cap
from app.market_data.adapters.manual_upload import service
from app.market_data.adapters.manual_upload.templates import (
    TemplateKind,
    build_template,
    template_filename,
    template_media_type,
)
from app.market_data.schemas.market_data_upload import MarketDataUploadRead

router = APIRouter(tags=["market-data"])


@router.get(
    "/market-data/templates/{kind}",
    response_class=StreamingResponse,
    operation_id="getMarketDataTemplate",
)
def get_market_data_template(kind: TemplateKind, access: MarketsTemplateView) -> StreamingResponse:
    """Download the .xlsx upload template for one scope category (§8.2).

    The workbook is bank-neutral; the ``bank_id`` query names the institution
    whose Markets view grant authorizes the download.
    """
    _ = access
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
    bank_id: str,
    db: DbSession,
    access: MarketsUpload,
    storage: IngestionStorage,
    file: UploadFile,
    as_of_date: Annotated[date, Form()],
) -> MarketDataUploadRead:
    """Run an uploaded template file as a manual market data pull (§8.3)."""
    content = await read_upload_within_cap(file)
    return service.upload_market_data(
        db,
        access.ctx,
        access.bank.id,
        storage,
        filename=file.filename or "upload",
        content=content,
        as_of_date=as_of_date,
    )
