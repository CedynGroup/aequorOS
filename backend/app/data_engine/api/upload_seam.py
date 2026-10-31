"""The upload seam shared by the ingestion APIs and every attachment surface.

Kept free of the ingestion service's import graph: ``public`` re-exports these
names, and re-exporting them from ``api.ingest_data`` instead would pull the
ingestion/live chain into ``public``'s own initialization (an import cycle in
fresh interpreters).
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import DbSession
from app.storage.client import StorageClient, StorageValidationError
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client

MAX_UPLOAD_BYTES = 50_000_000


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
