from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KeyManagementSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bank_key_required: bool = Field(default=False, alias="BANK_KEY_REQUIRED")

    platform_account: str | None = Field(
        default=None, alias="ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID", pattern=r"^\d{12}$"
    )

    backup_retention_days: int | None = Field(
        default=None, alias="ENCRYPTION_BACKUP_RETENTION_DAYS", ge=1
    )


@lru_cache
def get_key_settings() -> KeyManagementSettings:
    return KeyManagementSettings()
