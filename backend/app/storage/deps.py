"""Shared HTTP dependency for the process-wide storage client."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.storage.client import StorageClient, StorageValidationError
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client


def get_http_storage() -> StorageClient:
    """The storage engine, or 503 when it is unconfigured or retired."""
    try:
        return get_storage_client()
    except (StorageValidationError, StorageRetiredError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage is unavailable: {exc}",
        ) from exc


HttpStorage = Annotated[StorageClient, Depends(get_http_storage)]
