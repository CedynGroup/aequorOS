from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
StorageBackend = Literal["s3"]
SigningBackend = Literal["software", "pkcs11", "kms", "openbao"]

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
# Sealed soft-key store for the DEV/TEST signing backend only. Deliberately on
# disk and never in the database: signer_keys holds custody metadata, never key
# material (docs/attestation_esignature.md §3.4).
DEFAULT_SIGNING_KEY_DIR = BACKEND_ROOT / "artifacts" / "signing_keys"


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


class SmtpSettings(BaseSettings):
    """Outbound email for the notification mirror (plan GAP-5).

    Default OFF: the in-app feed is always on; email mirroring activates only
    when SMTP_HOST is set. Credentials live in the deployment environment,
    never in code or the database.
    """

    model_config = SETTINGS_CONFIG

    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, alias="SMTP_FROM")
    smtp_starttls: bool = Field(default=True, alias="SMTP_STARTTLS")

    @field_validator("smtp_host", "smtp_username", "smtp_password", "smtp_from", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @property
    def enabled(self) -> bool:
        return self.smtp_host is not None and self.smtp_from is not None


class AttestationSettings(BaseSettings):
    """Attestation & e-signature configuration (docs/attestation_esignature.md).

    Default OFF end to end. ``SIGNER_ID_PEPPER`` is the only value required to
    provision signer identities; signing additionally needs a key backend and
    (for long-term validity) a TSA. An absent pepper is a hard failure at
    derivation rather than a silent fallback — a predictable signer identity
    would let anyone holding a filed document enumerate platform user ids.
    """

    model_config = SETTINGS_CONFIG

    signer_id_pepper: str | None = Field(default=None, alias="SIGNER_ID_PEPPER")
    #: openbao | pkcs11 | kms | software. "software" is refused when APP_ENV=prod;
    #: "openbao" is the production backend this deployment actually ships with.
    signing_backend: SigningBackend = Field(default="software", alias="SIGNING_BACKEND")
    pkcs11_module_path: str | None = Field(default=None, alias="PKCS11_MODULE_PATH")
    pkcs11_token_label: str | None = Field(default=None, alias="PKCS11_TOKEN_LABEL")
    pkcs11_slot: int | None = Field(default=None, alias="PKCS11_SLOT")
    pkcs11_user_pin: str | None = Field(default=None, alias="PKCS11_USER_PIN")
    #: Self-hosted OpenBao (Linux Foundation fork of HashiCorp Vault) reached over
    #: its HTTP API — the Transit engine signs, the key never leaves the server
    #: (app/services/attestation/openbao.py).
    openbao_addr: str | None = Field(default=None, alias="OPENBAO_ADDR")
    #: AppRole credentials. The secret id is a CREDENTIAL: it is never logged,
    #: never put in an exception message, and never written to an audit event.
    openbao_role_id: str | None = Field(default=None, alias="OPENBAO_ROLE_ID")
    openbao_secret_id: str | None = Field(default=None, alias="OPENBAO_SECRET_ID")
    openbao_transit_mount: str = Field(default="transit", alias="OPENBAO_TRANSIT_MOUNT")
    #: The ISSUING PKI mount (the intermediate CA) every officer certificate is
    #: signed by, and its role. There is no self-signing fallback: a deployment
    #: whose mount or role is absent fails enrolment loudly rather than storing a
    #: certificate that chains to nothing. Both are created by
    #: scripts/bootstrap_openbao_pki.py, whose defaults these match.
    openbao_pki_mount: str = Field(default="pki-int", alias="OPENBAO_PKI_MOUNT")
    openbao_pki_role: str = Field(default="aequoros-signer", alias="OPENBAO_PKI_ROLE")
    #: Sent as X-Vault-Namespace when set. OpenBao 2.x has namespaces, so an
    #: unset-but-named namespace is a 404 rather than a silently ignored header —
    #: leave this unset unless the server actually defines one.
    openbao_namespace: str | None = Field(default=None, alias="OPENBAO_NAMESPACE")
    #: PEM CA bundle used to verify the OpenBao server's TLS certificate. A
    #: self-hosted OpenBao normally presents a private CA's certificate, and the
    #: alternative to configuring the CA is disabling verification — which would
    #: make the signing channel interceptable. There is deliberately no
    #: "insecure" switch.
    openbao_ca_cert: str | None = Field(default=None, alias="OPENBAO_CA_CERT")
    #: Explicit and finite: a hung OpenBao must not hold a signing request open.
    openbao_timeout_seconds: float = Field(default=10.0, alias="OPENBAO_TIMEOUT_SECONDS")
    #: Sealed soft-key store for the DEV/TEST backend. On disk, never in the
    #: database: signer_keys holds custody metadata only (§3.4).
    software_key_dir: Path = Field(
        default=DEFAULT_SIGNING_KEY_DIR, alias="SIGNING_SOFTWARE_KEY_DIR"
    )
    #: Cloud KMS selection for the (unbuilt) kms backend — see signers.py.
    kms_provider: str | None = Field(default=None, alias="SIGNING_KMS_PROVIDER")
    kms_key_id: str | None = Field(default=None, alias="SIGNING_KMS_KEY_ID")
    #: Seconds a single-use signing authorisation stays valid after step-up.
    authorization_ttl_seconds: int = Field(default=120, alias="SIGNING_AUTHORIZATION_TTL")
    #: Master switch for the signing surfaces. Identity provisioning and the
    #: evidential hardening are always on; producing signatures is not.
    signing_enabled: bool = Field(default=False, alias="ATTESTATION_SIGNING_ENABLED")
    #: Deployment-wide e-sign REQUIREMENT switch — orthogonal to
    #: ``signing_enabled``, which says whether this deployment CAN sign. True
    #: (the default): signing is required per the resolved policy. False: a
    #: kill-switch — NO return may demand a signature, even under a configured
    #: mandatory ``ReturnSigningPolicy`` row; every return follows the
    #: signature-optional workflow (bare maker-checker approval, no ceremony).
    #: Rows go dormant, not deleted: re-enabling restores them unchanged. See
    #: ``attestation.policy._apply_esign_kill_switch``.
    esign_required: bool = Field(default=True, alias="ATTESTATION_ESIGN_REQUIRED")
    #: PEM trust anchors for verification, as a filesystem path (file or
    #: directory). WITHOUT these, verification can only anchor on the
    #: certificate chain the signature itself carries — which proves the
    #: signature is internally consistent but NOT that the signer was issued a
    #: certificate by an authority the institution recognises. The verifier
    #: reports that distinction rather than implying trust it cannot establish.
    trust_roots_path: str | None = Field(default=None, alias="ATTESTATION_TRUST_ROOTS")

    @field_validator(
        "signer_id_pepper",
        "trust_roots_path",
        "pkcs11_module_path",
        "pkcs11_token_label",
        "pkcs11_user_pin",
        "kms_provider",
        "kms_key_id",
        "openbao_addr",
        "openbao_role_id",
        "openbao_secret_id",
        "openbao_namespace",
        "openbao_ca_cert",
        mode="before",
    )
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("openbao_transit_mount", mode="before")
    @classmethod
    def normalize_transit_mount(cls, value: str | None) -> str:
        """An empty mount would build ``/v1//sign/…`` and 404 at signing time."""
        if value is None or not str(value).strip():
            return "transit"
        return str(value).strip().strip("/")

    @field_validator("openbao_pki_mount", "openbao_pki_role", mode="before")
    @classmethod
    def normalize_pki_path(cls, value: str | None, info: ValidationInfo) -> str:
        """Blank reads as "unset", which means the documented default.

        Same reason as the transit mount: an empty string would build a URL that
        404s at enrolment, and a deployment that left the variable in its .env
        with no value means "I did not configure this", not "use no mount".
        """
        if value is None or not str(value).strip():
            return "pki-int" if info.field_name == "openbao_pki_mount" else "aequoros-signer"
        return str(value).strip().strip("/")

    @property
    def identities_available(self) -> bool:
        return self.signer_id_pepper is not None

    @property
    def signing_ready(self) -> bool:
        """True when this deployment can actually produce a signature.

        Signing is REQUIRED by default (attestation.policy.default_policy), so a
        deployment that cannot sign cannot file — the two settings below are the
        difference between a working filing pipeline and a locked one. They are
        reported together because separately they are not actionable: an
        operator needs to be told "this deployment cannot sign", not "one of two
        environment variables you have not heard of is unset".
        """
        return self.signing_enabled and self.identities_available

    def signing_readiness_gaps(self) -> list[str]:
        """The specific settings that must be supplied before signing works.

        Backend credentials belong here for the same reason ``SIGNER_ID_PEPPER``
        does: with ``SIGNING_BACKEND=openbao`` and no address or AppRole, the
        ceremony fails at the moment an officer presses sign, which is the
        latest and worst moment to learn it. ``/health/ready`` and
        ``ensure_signing_configured`` both read this list, so a deploy surfaces
        the gap instead of a filing deadline doing it.

        Only the SELECTED backend is checked. An unset ``OPENBAO_ADDR`` on a
        pkcs11 deployment is not a gap, it is an unused setting.
        """
        gaps: list[str] = []
        if not self.signing_enabled:
            gaps.append("ATTESTATION_SIGNING_ENABLED")
        if not self.identities_available:
            gaps.append("SIGNER_ID_PEPPER")
        if self.signing_backend == "openbao":
            if self.openbao_addr is None:
                gaps.append("OPENBAO_ADDR")
            if self.openbao_role_id is None:
                gaps.append("OPENBAO_ROLE_ID")
            if self.openbao_secret_id is None:
                gaps.append("OPENBAO_SECRET_ID")
        return gaps

    @property
    def trust_roots_configured(self) -> bool:
        """Whether an institutional anchor was named at all.

        Deliberately not "whether it loads": callers that need the anchors call
        :meth:`load_trust_roots`, which fails loudly on a bad path. This answers
        the different question the startup warning asks — has anybody told this
        deployment what its root is — without touching the filesystem.
        """
        return self.trust_roots_path is not None

    def load_trust_roots(self) -> list[bytes]:
        """Configured PEM trust anchors, or an empty list.

        An unreadable configured path is a hard failure, not a silent fallback:
        verifying against the embedded chain while an operator believes
        institutional roots are in force would overstate the result.
        """
        if self.trust_roots_path is None:
            return []
        root = Path(self.trust_roots_path)
        if not root.exists():
            msg = f"ATTESTATION_TRUST_ROOTS path does not exist: {root}"
            raise ValueError(msg)
        files = sorted(root.glob("*.pem")) if root.is_dir() else [root]
        anchors = [path.read_bytes() for path in files if path.is_file()]
        if not anchors:
            msg = f"ATTESTATION_TRUST_ROOTS contains no PEM files: {root}"
            raise ValueError(msg)
        return anchors


class TsaSettings(BaseSettings):
    """RFC 3161 trusted time (docs/attestation_esignature.md §3.6, gap G4).

    Default OFF: with no ``TSA_URL`` the attestation path has no trusted clock
    and must fail closed rather than fall back to the app host's wall clock —
    an unmonitored host clock is precisely the gap the TSA exists to close.

    Only a HASH is ever sent to the URL configured here (RFC 3161 message
    imprint), never document content — the property that makes an external,
    possibly offshore, timestamping authority compatible with Act 930 banking
    secrecy (§5.2). ``TSA_HASH_ALGORITHM`` is the imprint digest and, per
    RFC 8933, must be the algorithm that produced the digest we submit.
    """

    model_config = SETTINGS_CONFIG

    tsa_url: str | None = Field(default=None, alias="TSA_URL")
    tsa_username: str | None = Field(default=None, alias="TSA_USERNAME")
    tsa_password: str | None = Field(default=None, alias="TSA_PASSWORD")
    # Explicit and finite: a hung TSA must not hold a signing request open.
    tsa_timeout_seconds: float = Field(default=10.0, alias="TSA_TIMEOUT_SECONDS")
    tsa_hash_algorithm: str = Field(default="sha256", alias="TSA_HASH_ALGORITHM")

    @field_validator("tsa_url", "tsa_username", "tsa_password", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("tsa_hash_algorithm", mode="before")
    @classmethod
    def normalize_hash_algorithm(cls, value: str | None) -> str:
        if value is None or not str(value).strip():
            return "sha256"
        return str(value).strip().lower()

    @property
    def enabled(self) -> bool:
        return self.tsa_url is not None

    @property
    def basic_auth(self) -> tuple[str, str] | None:
        """HTTP basic credentials, or None when the TSA is open/IP-allowlisted."""
        if self.tsa_username is None or self.tsa_password is None:
            return None
        return (self.tsa_username, self.tsa_password)


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


class DatabaseDirectSettings(BaseSettings):
    """Database-Direct adapter scheduling settings.

    ``DATABASE_DIRECT_HEALTH_ENABLED`` gates the scheduled daily connection
    health probes (off by default). A probe is the existing live connection
    test — connect, authenticate, read the data dictionary — so it monitors a
    bank's reporting replica and, as genuine database activity, keeps
    idle-stopping test cores awake (OCI stops an Always Free Oracle ADB after
    seven consecutive idle days).
    """

    model_config = SETTINGS_CONFIG

    database_direct_health_enabled: bool = Field(
        default=False, alias="DATABASE_DIRECT_HEALTH_ENABLED"
    )


class DeskSettings(BaseSettings):
    """Market research desk scheduling settings (app/services/market_desk).

    ``DESK_CAPTURE_ENABLED`` gates the nightly scheduled capture job that
    scrapes the desk's Tier-1 Ghana sources (BoG/GFIM/GSS) after publication
    hours, stages observations, and — when an APPROVED methodology exists —
    computes and submits the day's draft determination for review, so the
    desk publishes next morning with one maker-checker action. Off by default:
    no environment scrapes external sites unless it opts in.
    ``DESK_CAPTURE_HOUR_UTC`` is the earliest UTC hour of the daily run
    (Accra is UTC, so 18 = after Ghana business hours).
    ``DESK_CAPTURE_SOURCES`` optionally narrows the run to a comma-separated
    allow-list of SOURCE_REGISTRY keys (unset/empty = all scheduled sources).
    """

    model_config = SETTINGS_CONFIG

    desk_capture_enabled: bool = Field(default=False, alias="DESK_CAPTURE_ENABLED")
    desk_capture_hour_utc: int = Field(default=18, alias="DESK_CAPTURE_HOUR_UTC")
    desk_capture_sources: str | None = Field(default=None, alias="DESK_CAPTURE_SOURCES")

    @field_validator("desk_capture_sources", mode="before")
    @classmethod
    def blank_sources_means_all(cls, value: str | None) -> str | None:
        """DESK_CAPTURE_SOURCES="" means no allow-list (all sources), the same
        empty-neutralizes rule as the database URL settings."""
        if value is not None and not value.strip():
            return None
        return value

    @property
    def capture_source_allowlist(self) -> tuple[str, ...] | None:
        """The parsed allow-list, or None when every scheduled source runs."""
        if self.desk_capture_sources is None:
            return None
        keys = tuple(
            key.strip() for key in self.desk_capture_sources.split(",") if key.strip()
        )
        return keys or None


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
    #: Hourly scheduled live refresh: keeps the live tier's computed_at current
    #: even when no new data arrives, so "Live" genuinely means today. Cheap
    #: (pipeline_refresh writes zero RegulatoryRun rows). Default off — the
    #: scheduler stays inert unless a deployment opts in.
    live_refresh_enabled: bool = Field(default=False, alias="LIVE_REFRESH_ENABLED")
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
    # SSO (own OIDC relying party — no third-party broker): connections live in
    # the sso_connections table (issuer/client_id per org, vaulted secret) and the
    # backend verifies every id_token against the issuer's JWKS (zero-trust).
    # SSO_INTERNAL_KEY authenticates the dashboard's server-to-server fetch of the
    # OIDC client config (the only path that ever reads the secret back). Unset =
    # that internal endpoint is disabled, so browser SSO cannot start.
    sso_internal_key: str | None = Field(default=None, alias="SSO_INTERNAL_KEY")

    @field_validator("sso_internal_key", mode="before")
    @classmethod
    def blank_internal_key_is_unset(cls, value: str | None) -> str | None:
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
    database_direct: DatabaseDirectSettings = Field(default_factory=DatabaseDirectSettings)
    desk: DeskSettings = Field(default_factory=DeskSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    smtp: SmtpSettings = Field(default_factory=SmtpSettings)
    attestation: AttestationSettings = Field(default_factory=AttestationSettings)
    tsa: TsaSettings = Field(default_factory=TsaSettings)

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


class OperatorSettings(BaseSettings):
    """Operator control-plane API settings (docs/internal/developer.md §4).

    The operator app (``app.operator.main``) is a SEPARATE ASGI app — never
    mounted on the tenant API — deployed behind an allowlist/VPN with
    workforce OIDC login. These settings are deliberately kept out of the
    tenant :class:`Settings` aggregate: tenant-plane code has no reason to
    read them, and the operator entrypoint resolves them independently via
    :func:`get_operator_settings`.
    """

    model_config = SETTINGS_CONFIG

    #: Port the operator uvicorn binds (its own Coolify app / local process).
    operator_port: int = Field(default=8100, alias="OPERATOR_PORT")
    #: Comma-separated allowed origins for the staff console frontend.
    operator_cors_origins_raw: str = Field(default="", alias="OPERATOR_CORS_ORIGINS")
    #: Dedicated DB URL for the operator role. Cross-tenant reads on the
    #: RLS-forced primary need a BYPASSRLS role (the worker precedent) — the
    #: use site falls back to WORKER_DATABASE_URL then DATABASE_URL, so a
    #: local/hermetic run works without extra configuration.
    operator_database_url: str | None = Field(default=None, alias="OPERATOR_DATABASE_URL")
    #: Development bearer-token auth. NEVER valid in production: the operator
    #: app refuses to boot when this is on with APP_ENV=production.
    dev_auth_enabled: bool = Field(default=False, alias="OPERATOR_DEV_AUTH_ENABLED")
    dev_token: str | None = Field(default=None, alias="OPERATOR_DEV_TOKEN")
    dev_email: str = Field(default="dev@aequoros.com", alias="OPERATOR_DEV_EMAIL")
    #: Signing secret for operator password-session JWTs (HS256, dedicated —
    #: never the tenant AUTH_JWT_SECRET). REQUIRED for the email+password
    #: primary path to function: unset, POST /operator/auth/login answers 503
    #: with a clear message rather than degrade.
    jwt_secret: str | None = Field(default=None, alias="OPERATOR_JWT_SECRET")
    #: Workforce OIDC (Google Workspace / Okta issuer). Verified with the same
    #: zero-trust machinery as customer SSO (`verify_oidc_id_token`); tokens
    #: must carry a verified email under the allowed domain.
    oidc_issuer: str | None = Field(default=None, alias="OPERATOR_OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, alias="OPERATOR_OIDC_CLIENT_ID")
    oidc_allowed_domain: str = Field(default="aequoros.com", alias="OPERATOR_OIDC_ALLOWED_DOMAIN")
    #: Per-tenant KMS keys + SSE-KMS bucket encryption during provisioning
    #: (developer.md §2a). Off by default: MinIO deployments have no KMS, and
    #: the saga records the step as honestly skipped rather than pretending.
    aws_kms_enabled: bool = Field(default=False, alias="OPERATOR_AWS_KMS_ENABLED")

    @field_validator(
        "operator_database_url",
        "dev_token",
        "jwt_secret",
        "oidc_issuer",
        "oidc_client_id",
        mode="before",
    )
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.operator_cors_origins_raw.split(",")
            if origin.strip()
        ]


@lru_cache
def get_operator_settings() -> OperatorSettings:
    return OperatorSettings()
