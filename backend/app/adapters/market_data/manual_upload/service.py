"""Upload-flow orchestration for the Manual Upload adapter (§8.3).

The bank operator downloads a template, populates it, and uploads it through
the AequorOS UI. This module stages the bytes to the bank's temp tier
(through the same path ingestion uploads use), validates the file shape,
constructs the adapter with a resolved ``temp://`` handle, and runs the pull.
The result is one ingestion batch with lineage and audit records identical to
vendor pulls — and zero vendor quota.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.market_data.base import CredentialSet
from app.adapters.market_data.errors import MarketDataError
from app.adapters.market_data.manual_upload.adapter import VENDOR, ManualUploadAdapter
from app.adapters.market_data.manual_upload.parser import ManualUploadParseError, parse_upload
from app.db.base import utc_now
from app.models import Bank, IngestionBatch
from app.schemas.market_data_upload import MarketDataUploadRead
from app.services import ingestion

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext
    from app.storage.client import StorageClient


def upload_market_data(  # noqa: PLR0913 - one call carries the full upload context
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    storage: StorageClient,
    *,
    filename: str,
    content: bytes,
    as_of_date: date,
) -> MarketDataUploadRead:
    """Stage an uploaded template file and run it as a manual market data pull."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = ingestion.bank_slug(db, bank)

    try:
        parsed = parse_upload(content, filename, expected_as_of=as_of_date)
    except ManualUploadParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if not parsed.scopes:
        detail = "The uploaded file contains no recognizable market data rows."
        if parsed.problems:
            first = parsed.problems[0]
            detail += f" First problem: {first.sheet} row {first.row_number}: {first.message}."
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)

    staged = ingestion.upload_source(db, ctx, bank_id, storage, filename, content)
    adapter = ManualUploadAdapter(db, bank, slug, actor_user_id=ctx.actor_user_id, storage=storage)
    credentials = CredentialSet(
        institution_id=str(bank.id),
        vendor=VENDOR,
        credentials={"staged_location": staged.location},
        issued_at=utc_now(),
        expires_at=None,
    )
    scopes = sorted(parsed.scopes, key=lambda scope: scope.value)
    try:
        result = adapter.pull(credentials, scopes, as_of_date, str(bank.id), str(uuid4()))
    except MarketDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.bank_facing.message,
        ) from exc

    batch = db.get(IngestionBatch, UUID(result.batch_id))
    return MarketDataUploadRead(
        batch_id=UUID(result.batch_id),
        bank_id=bank.id,
        status=batch.status if batch is not None else "unknown",
        as_of_date=as_of_date,
        scopes=[scope.value for scope in result.scopes_pulled],
        canonical_records_produced=result.canonical_records_produced,
        quota_consumed=result.quota_consumed,
        warnings=list(result.warnings),
        errors=list(result.errors),
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
