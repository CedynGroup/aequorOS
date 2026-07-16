from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
StorageBackend = Literal["s3"]

SETTINGS_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASHFLOW_ARTIFACTS_DIR = BACKEND_ROOT / "artifacts" / "cashflow"


class AppSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app_env: AppEnv = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="risk-service", alias="APP_NAME")


class DatabaseSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    database_url: str | None = Field(default=None, alias="DATABASE_URL")


class CorsSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.origins_raw.split(",") if origin.strip()]


class StorageSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    backend: StorageBackend = Field(default="s3", alias="RISK_STORAGE_BACKEND")
    bucket: str = Field(default="risk-local", alias="RISK_S3_BUCKET")
    region: str = Field(default="us-east-1", alias="RISK_S3_REGION")
    endpoint_url: str | None = Field(default="http://localhost:9000", alias="RISK_S3_ENDPOINT_URL")
    access_key_id: str | None = Field(default="minioadmin", alias="RISK_S3_ACCESS_KEY_ID")
    secret_access_key: str | None = Field(default="minioadmin", alias="RISK_S3_SECRET_ACCESS_KEY")
    force_path_style: bool = Field(default=True, alias="RISK_S3_FORCE_PATH_STYLE")
    presign_expires_seconds: int = Field(default=900, alias="RISK_S3_PRESIGN_EXPIRES_SECONDS")
    max_upload_bytes: int = Field(default=25_000_000, alias="RISK_MAX_UPLOAD_BYTES")

    @property
    def configured(self) -> bool:
        if self.backend != "s3":
            return False
        if not self.bucket or not self.region:
            return False
        if self.endpoint_url:
            return bool(self.access_key_id and self.secret_access_key)
        return True


class LoggingSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, log_level: str) -> str:
        return log_level.upper()


class CashflowSettings(BaseSettings):
    """In-process cash-flow ML module (``app/ml``) settings.

    ``CASHFLOW_FAST_TEST=1`` selects the reduced training config used by tests;
    ``CASHFLOW_ARTIFACTS_DIR`` relocates the saved model artifacts.
    """

    model_config = SETTINGS_CONFIG

    fast_test: bool = Field(default=False, alias="CASHFLOW_FAST_TEST")
    artifacts_dir: Path = Field(
        default=DEFAULT_CASHFLOW_ARTIFACTS_DIR, alias="CASHFLOW_ARTIFACTS_DIR"
    )


class WorkerSettings(BaseSettings):
    """Live-engine background worker and scheduler settings.

    ``RUN_INPROCESS_WORKER`` starts a daemon poll loop inside the API process
    (off by default so tests/dev drive handlers synchronously). The scheduler is
    inert unless ``OFFICIAL_RUN_ENABLED`` so no environment auto-mints the heavy
    22-scenario official runs.
    """

    model_config = SETTINGS_CONFIG

    run_inprocess_worker: bool = Field(default=False, alias="RUN_INPROCESS_WORKER")
    pipeline_debounce_seconds: int = Field(default=15, alias="PIPELINE_DEBOUNCE_SECONDS")
    worker_poll_seconds: float = Field(default=2.0, alias="WORKER_POLL_SECONDS")
    official_run_hour: int = Field(default=2, alias="OFFICIAL_RUN_HOUR")
    official_run_enabled: bool = Field(default=False, alias="OFFICIAL_RUN_ENABLED")
    # The worker claims and processes jobs across every tenant, so its DB
    # connection must see all rows. On an RLS-forced Postgres this requires a
    # BYPASSRLS role (the app role is deliberately tenant-scoped). When unset,
    # the worker falls back to DATABASE_URL (correct for SQLite tests and any
    # deployment whose main role already bypasses RLS).
    worker_database_url: str | None = Field(default=None, alias="WORKER_DATABASE_URL")


class Settings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    cashflow: CashflowSettings = Field(default_factory=CashflowSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)

    @property
    def storage_configured(self) -> bool:
        return self.storage.configured

    @property
    def risk_storage_backend(self) -> StorageBackend:
        return self.storage.backend

    @property
    def risk_s3_bucket(self) -> str:
        return self.storage.bucket

    @property
    def risk_s3_region(self) -> str:
        return self.storage.region

    @property
    def risk_s3_endpoint_url(self) -> str | None:
        return self.storage.endpoint_url

    @property
    def risk_s3_access_key_id(self) -> str | None:
        return self.storage.access_key_id

    @property
    def risk_s3_secret_access_key(self) -> str | None:
        return self.storage.secret_access_key

    @property
    def risk_s3_force_path_style(self) -> bool:
        return self.storage.force_path_style

    @property
    def risk_s3_presign_expires_seconds(self) -> int:
        return self.storage.presign_expires_seconds

    @property
    def risk_max_upload_bytes(self) -> int:
        return self.storage.max_upload_bytes


@lru_cache
def get_settings() -> Settings:
    return Settings()
