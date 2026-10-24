from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import is_undeployed_environment


class KeyManagementSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kms_endpoint_url: str | None = Field(default=None, alias="BANK_KMS_ENDPOINT_URL")

    @field_validator("kms_endpoint_url", mode="before")
    @classmethod
    def local_kms_endpoint(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not is_undeployed_environment():
            raise ValueError("BANK_KMS_ENDPOINT_URL is permitted only in local/test.")
        endpoint = urlsplit(value)
        if (
            endpoint.scheme not in {"http", "https"}
            or endpoint.hostname not in {"127.0.0.1", "localhost", "::1", "risk-kms"}
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("BANK_KMS_ENDPOINT_URL must address the local KMS emulator.")
        return value

    demo_bank_key_file: Path | None = Field(default=None, alias="DEMO_BANK_KEY_FILE")

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
