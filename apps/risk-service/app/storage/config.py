"""Storage layer configuration (storage.md §4.4, §5.2).

Backend selection is a config decision made once at startup, never runtime-
dynamic. The MVP MinIO deployment carries a mandatory ``retire_after`` date:
past that date the client refuses to initialize, so MVP infrastructure cannot
silently become production infrastructure (storage.md §14.9 — do not remove
or extend without approval from Dela or Eric).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.storage.client import StorageEnv

StorageBackend = Literal["minio", "s3", "gcs"]


class StorageEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    backend: StorageBackend = Field(default="minio", alias="STORAGE_BACKEND")
    env: StorageEnv = Field(default="mvp", alias="STORAGE_ENV")
    endpoint: str | None = Field(default=None, alias="S3_ENDPOINT")
    region: str = Field(default="us-east-1", alias="S3_REGION")
    access_key: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    secret_key: str | None = Field(default=None, alias="S3_SECRET_KEY")
    force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    # Mandatory for env=mvp: the date this MinIO deployment must be retired
    # or migrated to managed cloud (storage.md §5.2).
    retire_after: date | None = Field(default=None, alias="STORAGE_RETIRE_AFTER")
    presign_expires_seconds: int = Field(default=900, alias="STORAGE_PRESIGN_EXPIRES_SECONDS")
    # KES/KMS key applied to every write and as bucket-default encryption.
    # MVP uses one platform key; per-institution keys (storage.md §7.2) slot
    # in once KES key provisioning is automated — the write path and object
    # metadata are already keyed per institution.
    kms_key_id: str | None = Field(default=None, alias="STORAGE_KMS_KEY_ID")

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.access_key and self.secret_key)


class StorageRetiredError(RuntimeError):
    """The MVP storage deployment is past its mandated retirement date."""


def enforce_retirement(settings: StorageEngineSettings, *, today: date | None = None) -> None:
    if settings.env != "mvp":
        return
    if settings.retire_after is None:
        msg = (
            "STORAGE_RETIRE_AFTER must be set for env=mvp: self-hosted MinIO is "
            "sanctioned for synthetic MVP data only and must carry an explicit "
            "retirement date (storage.md §5.2)."
        )
        raise StorageRetiredError(msg)
    effective_today = today or date.today()
    if effective_today > settings.retire_after:
        msg = (
            f"MVP storage passed its retirement date ({settings.retire_after}). "
            "Migrate to a managed cloud backend (storage.md §11) or obtain an "
            "explicit extension from Dela or Eric."
        )
        raise StorageRetiredError(msg)


@lru_cache
def get_storage_settings() -> StorageEngineSettings:
    return StorageEngineSettings()
