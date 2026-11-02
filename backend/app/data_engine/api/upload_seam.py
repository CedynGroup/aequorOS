"""The upload seam shared by the ingestion APIs and every attachment surface.

Kept free of the ingestion service's import graph: ``public`` re-exports these
names, and re-exporting them from ``api.ingest_data`` instead would pull the
ingestion/live chain into ``public``'s own initialization (an import cycle in
fresh interpreters).
"""

from typing import Annotated

from fastapi import Depends, HTTPException, UploadFile, status

from app.api.deps import DbSession
from app.storage.client import StorageClient, StorageValidationError
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client

#: File-content bytes accepted for one upload, compressed for an .xlsx file.
MAX_UPLOAD_BYTES = 50_000_000

#: How much of an upload is read between cap checks. Small enough that a body
#: far over the cap is refused after a bounded read, large enough that a real
#: 50 MB file is a few dozen iterations rather than fifty million.
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def read_upload_within_cap(file: UploadFile) -> bytes:
    """Read ``file`` in chunks, refusing it as soon as it passes the upload cap.

    ``await file.read()`` materializes the whole body first and compares the
    length afterwards, so the cap never bounded the memory the request used: an
    authenticated caller posting multi-gigabyte bodies drove the shared API
    process to exhaustion before a single byte was rejected (issue #701).

    An oversized file is refused after reading at most the cap plus one chunk.
    Accepted chunks remain in memory until the final join, which allocates a
    second copy of the accepted content. This bounds handler reads and copies,
    not proxy or multipart buffering before the handler runs.

    An empty upload is refused here too: every caller of this seam rejects it,
    and one place keeps the wording and the status identical.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Upload exceeds the {MAX_UPLOAD_BYTES // 1_000_000} MB limit.",
            )
        chunks.append(chunk)
    if not total:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded file is empty.",
        )
    return b"".join(chunks)


def get_ingestion_storage(db: DbSession) -> StorageClient:
    """The storage engine, or 503 when it is unconfigured or retired."""
    try:
        return get_storage_client().for_session(db)
    except (StorageValidationError, StorageRetiredError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage is unavailable: {exc}",
        ) from exc


IngestionStorage = Annotated[StorageClient, Depends(get_ingestion_storage)]


__all__ = [
    "MAX_UPLOAD_BYTES",
    "IngestionStorage",
    "get_ingestion_storage",
    "read_upload_within_cap",
]
