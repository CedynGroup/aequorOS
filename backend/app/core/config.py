from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
StorageBackend = Literal["s3"]

SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    # Allow constructing settings by field name (e.g. AuthSettings(jwt_secret=...) in
    # tests) in addition to the env alias; env loading still uses the alias.
    populate_by_name=True,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASHFLOW_ARTIFACTS_DIR = BACKEND_ROOT / "artifacts" / "cashflow"
DEFAULT_BEHAVIORAL_ARTIFACTS_DIR = BACKEND_ROOT / "artifacts" / "behavioral"


class AppSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app_env: AppEnv = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="risk-service", alias="APP_NAME")


class DatabaseSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    @field_validator("database_url", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        """DATABASE_URL="" means unconfigured.

        Environment variables take priority over the .env file in
        pydantic-settings, so setting the variable to an empty string is the
        only way a caller (notably the test suite) can neutralize a developer's
        .env database without editing the file.
        """
        if value is not None and not value.strip():
            return None
        return value


class CorsSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.origins_raw.split(",") if origin.strip()]


class StorageSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    # Consolidated onto the single MinIO credential set (S3_* / STORAGE_*): the
    # document-upload, presigned-URL, and storage-health paths share the same
    # object store as the Data Engine. There is no separate RISK_S3_* set.
    backend: StorageBackend = Field(default="s3")
    bucket: str = Field(default="aequoros", alias="S3_BUCKET")
    region: str = Field(default="us-east-1", alias="S3_REGION")
    endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT")
    access_key_id: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    secret_access_key: str | None = Field(default=None, alias="S3_SECRET_KEY")
    force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    presign_expires_seconds: int = Field(default=900, alias="STORAGE_PRESIGN_EXPIRES_SECONDS")
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


class BehavioralSettings(BaseSettings):
    """Per-tenant behavioral ML models (``app/ml/behavioral``) settings.

    ``BEHAVIORAL_FAST_TEST=1`` lowers the min-data gate for tests;
    ``BEHAVIORAL_ARTIFACTS_DIR`` relocates the per-(org,bank) model artifacts.
    """

    model_config = SETTINGS_CONFIG

    fast_test: bool = Field(default=False, alias="BEHAVIORAL_FAST_TEST")
    artifacts_dir: Path = Field(
        default=DEFAULT_BEHAVIORAL_ARTIFACTS_DIR, alias="BEHAVIORAL_ARTIFACTS_DIR"
    )


class MarketDataSettings(BaseSettings):
    """Market data adapter framework settings (docs/market_data_adapter.md).

    ``CREDENTIAL_VAULT_MASTER_KEY`` is the application-layer AES-256-GCM
    master key material for the MVP encrypted-DB credential vault
    (app/adapters/market_data/credential_manager.py); unset means the vault
    refuses to operate. ``MARKET_DATA_PULL_ENABLED`` gates live vendor pulls
    (off by default — MVP ships fixture-tested adapters without production
    pulls, per market_data_adapter.md §14.1).
    """

    model_config = SETTINGS_CONFIG

    credential_vault_master_key: str | None = Field(
        default=None, alias="CREDENTIAL_VAULT_MASTER_KEY"
    )
    market_data_pull_enabled: bool = Field(default=False, alias="MARKET_DATA_PULL_ENABLED")


class TemenosSettings(BaseSettings):
    """Temenos T24 core-banking adapter settings (docs/temenos_adapter.md).

    Reuses the market-data ``CREDENTIAL_VAULT_MASTER_KEY`` for the encrypted
    credential vault. ``TEMENOS_PULL_ENABLED`` gates scheduled EOD/COB pulls
    (off by default — MVP ships fixture-tested adapters + a portal-gated live
    transport, so no environment auto-connects to a bank's core).
    """

    model_config = SETTINGS_CONFIG

    temenos_pull_enabled: bool = Field(default=False, alias="TEMENOS_PULL_ENABLED")


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
    # A job stuck in ``running`` longer than this is treated as orphaned by a
    # dead worker and reclaimed (see job_queue.reclaim_stale). Must exceed the
    # slowest handler's real runtime (etl_dedup / pipeline_refresh over a full
    # book run in minutes) so a slow-but-alive job is never reclaimed twice.
    worker_stale_job_seconds: float = Field(default=900.0, alias="WORKER_STALE_JOB_SECONDS")
    official_run_hour: int = Field(default=2, alias="OFFICIAL_RUN_HOUR")
    official_run_enabled: bool = Field(default=False, alias="OFFICIAL_RUN_ENABLED")
    # The worker claims and processes jobs across every tenant, so its DB
    # connection must see all rows. On an RLS-forced Postgres this requires a
    # BYPASSRLS role (the app role is deliberately tenant-scoped). When unset,
    # the worker falls back to DATABASE_URL (correct for SQLite tests and any
    # deployment whose main role already bypasses RLS).
    worker_database_url: str | None = Field(default=None, alias="WORKER_DATABASE_URL")

    @field_validator("worker_database_url", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        """WORKER_DATABASE_URL="" falls back to DATABASE_URL (same rule as
        DatabaseSettings: empty env values neutralize .env without edits)."""
        if value is not None and not value.strip():
            return None
        return value


class AuthSettings(BaseSettings):
    """JWT + password/SSO auth.

    ``jwt_secret`` signs and verifies the app access/refresh tokens (HS256; the
    backend is both issuer and verifier). It MUST be set to a strong secret in any
    real environment — a settings validator refuses to issue/verify tokens when it
    is unset, so the header-trust fallback can never silently re-appear.
    """

    model_config = SETTINGS_CONFIG

    jwt_secret: str | None = Field(default=None, alias="AUTH_JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="AUTH_JWT_ALGORITHM")
    jwt_issuer: str = Field(default="aequoros", alias="AUTH_JWT_ISSUER")
    jwt_audience: str = Field(default="aequoros-api", alias="AUTH_JWT_AUDIENCE")
    access_token_ttl_seconds: int = Field(default=900, alias="AUTH_ACCESS_TOKEN_TTL")
    refresh_token_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 14, alias="AUTH_REFRESH_TOKEN_TTL"
    )
    max_failed_logins: int = Field(default=5, alias="AUTH_MAX_FAILED_LOGINS")
    lockout_seconds: int = Field(default=900, alias="AUTH_LOCKOUT_SECONDS")
    # SSO via Auth0 (OIDC): the backend verifies the Auth0 id_token against Auth0's
    # JWKS (zero-trust) — issuer https://{domain}/, audience = the client id — then
    # mints its own app token. Unset domain/client_id disables the SSO endpoint.
    auth0_domain: str | None = Field(default=None, alias="AUTH0_DOMAIN")
    auth0_client_id: str | None = Field(default=None, alias="AUTH0_CLIENT_ID")

    @field_validator("auth0_domain", mode="before")
    @classmethod
    def blank_domain_is_unset(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def blank_secret_is_unset(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class Settings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    cashflow: CashflowSettings = Field(default_factory=CashflowSettings)
    behavioral: BehavioralSettings = Field(default_factory=BehavioralSettings)
    market_data: MarketDataSettings = Field(default_factory=MarketDataSettings)
    temenos: TemenosSettings = Field(default_factory=TemenosSettings)
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
