from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import StreamingResponse

from app.storage.client import StorageAccessError, StorageClient, StorageError
from app.storage.downloads import verify
from app.storage.factory import get_storage_client

router = APIRouter(tags=["storage"])


def _chunks(body: BinaryIO) -> Iterator[bytes]:
    try:
        yield from iter(lambda: body.read(1024 * 1024), b"")
    finally:
        body.close()


@router.get(
    "/storage/download", response_class=StreamingResponse, operation_id="downloadBankObject"
)
def download_bank_object(
    token: Annotated[str, Query(min_length=1, max_length=16384)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> StreamingResponse:
    try:
        capability = verify(token)
    except StorageAccessError as exc:
        raise HTTPException(
            status_code=403, detail="The download link is invalid or expired."
        ) from exc
    try:
        descriptor, body = storage.read(capability.location(), capability.version)
    except StorageError as exc:
        raise HTTPException(
            status_code=503, detail="The bank key or encrypted object is unavailable."
        ) from exc
    return StreamingResponse(
        _chunks(body),
        media_type=descriptor.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )
