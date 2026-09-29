from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, get_args

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
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")


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


#: Origin of the authenticated bank product (``bank.aequoros.com``). Read by TWO
#: settings classes from the SAME environment variable — the staff console needs
#: it to hand an operator off to the read-only examiner view, and BI
#: subscriptions need it for the sign-in link that stands in for an attachment
#: the platform will not email. Declared once here so the two cannot drift to
#: different hosts, which would send half the platform's links to the wrong
#: origin.
DEFAULT_BANK_APP_BASE_URL = "https://bank.aequoros.com"


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
    methods_raw: str = Field(default="GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS", alias="CORS_METHODS")
    headers_raw: str = Field(
        default="Accept,Authorization,Content-Type,X-Request-ID", alias="CORS_HEADERS"
    )

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.origins_raw.split(",") if origin.strip()]

    @property
    def methods(self) -> list[str]:
        return [method.strip().upper() for method in self.methods_raw.split(",") if method.strip()]

    @property
    def headers(self) -> list[str]:
        return [header.strip() for header in self.headers_raw.split(",") if header.strip()]


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


class IcaapSettings(BaseSettings):
    """ICAAP workspace settings.

    There is no on/off switch. ICAAP is part of the product for every bank
    tenant, and eligibility is decided by the things that genuinely decide it:
    the institution's licence class (``require_bank_class``) and the caller's
    capital/confidential authority. A deployment flag would have been a fourth
    answer to a question those two already answer.

    ``ICAAP_FRAMEWORKS_ENABLED`` is the per-framework switch: a comma-separated
    list of framework codes a tenant may pin. Reference frameworks can ship as
    data before the platform can file to that regulator.

    ``ICAAP_SIGNING_ENABLED`` is NOT that fourth answer either: it gates the
    SIGNING CEREMONY on the ICAAP report, never the workspace. See the field.
    """

    model_config = SETTINGS_CONFIG

    max_attachment_bytes: int = Field(default=25_000_000, alias="ICAAP_MAX_ATTACHMENT_BYTES")
    frameworks_enabled: str = Field(default="bog_icaap", alias="ICAAP_FRAMEWORKS_ENABLED")
    #: Whether the ICAAP report is signed at all. ``YES``/``NO`` (``1``/``0``
    #: and ``true``/``false`` are the same switch — Pydantic parses any of them).
    #:
    #: The founder's instruction: the ICAAP signing ceremony — Board slot
    #: included — is controlled from the environment the way the prudential
    #: returns' ceremony is controlled by ``ATTESTATION_SIGNING_ENABLED``, so it
    #: can be switched back on if a Board requires it after onboarding. It ships
    #: **NO** because no rule in the recovered BoG text requires the Board (or
    #: anyone) to e-sign the filed ICAAP PDF (D-043), and Board approval is
    #: evidenced regardless by the in-platform BRC/Board decisions, the challenge
    #: log and the mandatory board-resolution attachment.
    #:
    #: NO does not delete anything. It is applied AFTER policy resolution
    #: (``attestation.policy._apply_icaap_signing_switch``), so a configured
    #: ``ReturnSigningPolicy`` row for the ICAAP family goes DORMANT — the ICAAP
    #: return takes the bare maker-checker approval path — and flipping the
    #: switch to YES restores that row unchanged. It is scoped to the ``icaap``
    #: return family alone: no other family's signing is affected, which is
    #: exactly the difference between this and the deployment-wide
    #: ``ATTESTATION_ESIGN_REQUIRED`` kill-switch.
    #:
    #: This is NOT ``ICAAP_ENABLED``: D-046 removed the ICAAP availability flag
    #: on the founder's instruction and it is not coming back under another name.
    signing_enabled: bool = Field(default=False, alias="ICAAP_SIGNING_ENABLED")
    #: An ADDITIONAL directory of framework JSON, for tests and rehearsals only.
    #:
    #: It exists because 15 of the 17 Ghana sections are still
    #: ``pending_primary_text`` (D-006), so a non-rehearsal freeze cannot
    #: succeed against the real instrument and the filing path would otherwise
    #: have no end-to-end proof at all. A framework published this way is
    #: indistinguishable from a real one once loaded, which is exactly why a
    #: deployed environment must never load one: a bank could file a report
    #: whose checklist came from a file nobody reviewed.
    #:
    #: The refusal is an ALLOW-LIST (``local``/``test``), not "not production" —
    #: staging runs the same containers on a host somebody else can reach.
    extra_frameworks_dir: str | None = Field(default=None, alias="ICAAP_EXTRA_FRAMEWORKS_DIR")

    @property
    def enabled_framework_codes(self) -> frozenset[str]:
        return frozenset(
            code.strip() for code in self.frameworks_enabled.split(",") if code.strip()
        )

    @field_validator("extra_frameworks_dir", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: str | None) -> str | None:
        if value is not None and not str(value).strip():
            return None
        return value

    def extra_framework_roots(self, app_env: str) -> tuple[Path, ...]:
        """The extra roots this environment may load, refusing a deployed one.

        Raises rather than ignoring: a deployment that set the variable meant
        to load something, and silently loading nothing would be a framework
        that is present in the config and absent from the product.
        """
        if not self.extra_frameworks_dir:
            return ()
        if not is_undeployed_environment(app_env):
            msg = (
                "ICAAP_EXTRA_FRAMEWORKS_DIR loads ICAAP frameworks that no reviewer has "
                f"approved and is refused in the '{app_env}' environment. Publish the "
                "framework under app/domain/icaap/frameworks/ instead."
            )
            raise ValueError(msg)
        return tuple(
            Path(entry.strip()).expanduser()
            for entry in self.extra_frameworks_dir.split(",")
            if entry.strip()
        )


#: Every model vendor the platform can call, and the ONLY names
#: ``AI_PROVIDER_TIER`` accepts. Adding one means adding an adapter module under
#: ``app/services/ai/`` and an ``approved_configurations.json`` entry per feature.
AiVendor = Literal["anthropic", "openai", "google"]
AI_VENDORS: Final[tuple[AiVendor, ...]] = get_args(AiVendor)

#: The default failover order (D-053): Claude first, then OpenAI, then Gemini.
AI_DEFAULT_PROVIDER_TIER: Final = "anthropic,openai,google"

#: Effort levels each vendor NATIVELY accepts, ascending. The adapters map
#: ``AI_EFFORT`` onto these rather than naming a level themselves, so the
#: vocabulary stays here with every other AI tunable (D-024). An empty tuple
#: means the vendor exposes no effort control at all, which the adapter records
#: as a degraded capability rather than silently dropping.
AI_VENDOR_EFFORT_LEVELS: Final[dict[str, tuple[str, ...]]] = {
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
    "openai": ("low", "medium", "high"),
    "google": (),
}

#: What a vendor with no effort control records as its effort. Part of the
#: approval key, so it must be a stable, writable token rather than ``None``.
AI_EFFORT_UNSUPPORTED: Final = "unsupported"


#: Separator grammar of ``AI_FEATURE_MODELS``: ``feature:vendor=model``, comma
#: separated. One setting rather than a variable per (feature, vendor) because
#: ``extra="ignore"`` makes a mistyped variable name SILENT — the deployment
#: would fall back to the vendor default and nobody would know. Here a typo is a
#: boot-time error naming the offending entry.
AI_FEATURE_MODEL_SEPARATOR: Final = ":"
AI_FEATURE_MODEL_ASSIGN: Final = "="


def parse_ai_feature_models(value: str) -> dict[tuple[str, str], str]:
    """``"icaap_drafting:anthropic=claude-x"`` -> ``{("icaap_drafting", "anthropic"): "claude-x"}``.

    Validates the GRAMMAR and the VENDOR here, where it is cheap and where the
    settings object is built, so a malformed value cannot boot. The feature name
    and the per-feature pinning policy are checked in
    ``app.services.ai.model_selection``, which owns both — this module must not import
    from the services layer.
    """
    resolved: dict[tuple[str, str], str] = {}
    for entry in (item.strip() for item in value.split(",")):
        if not entry:
            continue
        key, separator, model = entry.partition(AI_FEATURE_MODEL_ASSIGN)
        if not separator or not model.strip():
            message = (
                f"AI_FEATURE_MODELS entry {entry!r} must read "
                f"feature{AI_FEATURE_MODEL_SEPARATOR}vendor{AI_FEATURE_MODEL_ASSIGN}model."
            )
            raise ValueError(message)
        feature, dot, vendor = key.strip().partition(AI_FEATURE_MODEL_SEPARATOR)
        if not dot or not feature.strip():
            message = (
                f"AI_FEATURE_MODELS entry {entry!r} must name a feature and a vendor "
                f"separated by {AI_FEATURE_MODEL_SEPARATOR!r}."
            )
            raise ValueError(message)
        vendor = vendor.strip().casefold()
        if vendor not in AI_VENDORS:
            message = (
                f"AI_FEATURE_MODELS entry {entry!r} names unknown vendor {vendor!r}; "
                f"permitted values are {list(AI_VENDORS)}."
            )
            raise ValueError(message)
        slot = (feature.strip().casefold(), vendor)
        if slot in resolved:
            message = f"AI_FEATURE_MODELS names {slot[0]}:{slot[1]} more than once."
            raise ValueError(message)
        resolved[slot] = model.strip()
    return resolved


def parse_ai_provider_tier(value: str) -> tuple[str, ...]:
    """``"anthropic,openai"`` -> ``("anthropic", "openai")``.

    Raises ``ValueError`` naming the offending token, so an unknown vendor is a
    boot-time configuration error rather than a vendor silently skipped at the
    moment a bank needed it.
    """
    names = [entry.strip().casefold() for entry in value.split(",") if entry.strip()]
    if not names:
        message = "AI_PROVIDER_TIER must name at least one vendor."
        raise ValueError(message)
    unknown = [name for name in names if name not in AI_VENDORS]
    if unknown:
        message = (
            f"AI_PROVIDER_TIER names unknown vendor(s) {unknown}; "
            f"permitted values are {list(AI_VENDORS)}."
        )
        raise ValueError(message)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        message = f"AI_PROVIDER_TIER lists {duplicates} more than once."
        raise ValueError(message)
    return tuple(names)


class AiSettings(BaseSettings):
    """Governed AI drafting and commentary (app/services/ai).

    This is the ONLY part of the platform that sends tenant data to an external
    service, so the switches here are egress gates, not feature flags — they are
    unrelated to ICAAP availability, which D-046 settled as "always on".

    ``AI_COMMENTARY_ENABLED`` is the deployment kill-switch, off by default, and
    it is checked at BOTH enqueue and run: a request can sit in the queue across
    a toggle change, and a job that started before someone pulled the switch must
    not be the one call that still goes out. With the recommended topology (D-026)
    the API and the AI worker read it from different env stores, so either one off
    stops model calls.

    ``AI_PRODUCTION_APPROVAL_REF`` plus the committed
    ``app/services/ai/approved_configurations.json`` are what make "not yet
    approved for production" enforceable in code rather than by memory: in any
    DEPLOYED environment a request is refused unless the ref is set AND the exact
    (feature, prompt version, model, effort, environment) tuple has been reviewed
    into that file. Changing the prompt, the model or the effort silently falls
    out of approval, which is the intended behaviour.

    Every number the AI path uses lives here (D-024). Logic reads
    ``get_settings().ai.*``; no module under ``app/domain/ai`` or
    ``app/services/ai`` may contain a numeric tunable.

    ``AI_PROVIDER_TIER`` is the failover order (D-053). Losing credit on one
    vendor must not stop AI features, so the tier is walked until one vendor
    answers — on AVAILABILITY failures only. A refused or ungrounded draft never
    advances the tier; it goes to the feature's deterministic fallback.

    ``AI_FEATURE_MODELS`` is per-FEATURE model selection (D-061), because
    reproducibility requirements differ by surface: an ICAAP narrative rides a
    FILED regulatory document and must pin an exact snapshot, while BI commentary
    is advisory and regenerable and may track a vendor's floating alias. The
    per-feature pinning policy lives with the resolution in
    ``app.services.ai.model_selection``; only the operator's value lives here.

    The API KEYS are deliberately NOT here — see the credential class local to
    each adapter module (``client.AiCredentialSettings``,
    ``openai_model.OpenAiCredentialSettings``,
    ``google_model.GoogleCredentialSettings``), each instantiated only when that
    vendor is actually prepared, so the API, the core worker and the operator
    process never parse a key into memory even if one were present in their env.
    """

    model_config = SETTINGS_CONFIG

    commentary_enabled: bool = Field(default=False, alias="AI_COMMENTARY_ENABLED")
    #: Failover order, first to last. Unknown or repeated names fail validation.
    provider_tier: str = Field(default=AI_DEFAULT_PROVIDER_TIER, alias="AI_PROVIDER_TIER")
    model: str = Field(default="claude-opus-5", alias="AI_MODEL")
    #: The other two vendors' model ids. Each was current at implementation time
    #: (2026-09-22) and is an operator setting for exactly that reason: an id the
    #: vendor has retired answers 404, which skips that tier with
    #: ``model_unavailable`` rather than failing the request.
    openai_model: str = Field(default="gpt-5.1", alias="AI_OPENAI_MODEL")
    google_model: str = Field(default="gemini-2.5-pro", alias="AI_GOOGLE_MODEL")
    #: PER-FEATURE overrides (D-061), ``feature:vendor=model`` comma separated.
    #: Empty by default, so a deployment that has only ever set the three
    #: vendor-level ids above keeps behaving exactly as it did. Resolution is
    #: ``feature override -> vendor default -> unset`` in
    #: ``app.services.ai.model_selection.resolve``, and the RESOLVED id is what the
    #: approved-configuration key is looked up with — an override cannot route
    #: around a review.
    feature_models: str = Field(default="", alias="AI_FEATURE_MODELS")
    effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="high", alias="AI_EFFORT"
    )
    #: Thinking is ON by default on Opus 5 and ``max_tokens`` caps thinking PLUS
    #: response, so this must leave room for both. Kept under the SDK's
    #: non-streaming comfort limit; a larger draft needs streaming, not a bigger
    #: number here.
    max_output_tokens: int = Field(default=16_000, alias="AI_MAX_OUTPUT_TOKENS")
    request_timeout_seconds: float = Field(default=600.0, alias="AI_REQUEST_TIMEOUT_SECONDS")
    max_retries: int = Field(default=2, alias="AI_MAX_RETRIES")
    #: ``default`` opts into server-side refusal fallbacks; ``off`` pins the
    #: request to one model, which counsel may require.
    fallbacks: Literal["default", "off"] = Field(default="default", alias="AI_FALLBACKS")
    prompt_cache_ttl: Literal["5m", "1h"] = Field(default="5m", alias="AI_PROMPT_CACHE_TTL")
    stale_margin_seconds: float = Field(default=120.0, alias="AI_STALE_MARGIN_SECONDS")
    #: One attempt: the SDK already retries timeouts and 5xx, and a job-level
    #: retry of an API outcome would double-spend and duplicate a sealed row.
    job_max_attempts: int = Field(default=1, alias="AI_JOB_MAX_ATTEMPTS")
    queue_expiry_seconds: int = Field(default=3600, alias="AI_QUEUE_EXPIRY_SECONDS")
    enqueue_debounce_seconds: int = Field(default=30, alias="AI_ENQUEUE_DEBOUNCE_SECONDS")
    daily_requests_per_org: int = Field(default=200, alias="AI_DAILY_REQUESTS_PER_ORG")
    daily_requests_per_user: int = Field(default=40, alias="AI_DAILY_REQUESTS_PER_USER")
    daily_output_tokens_per_org: int = Field(
        default=2_000_000, alias="AI_DAILY_OUTPUT_TOKENS_PER_ORG"
    )
    max_facts_per_sheet: int = Field(default=120, alias="AI_MAX_FACTS_PER_SHEET")
    max_paragraphs: int = Field(default=12, alias="AI_MAX_PARAGRAPHS")
    max_paragraph_chars: int = Field(default=2000, alias="AI_MAX_PARAGRAPH_CHARS")
    max_open_questions: int = Field(default=8, alias="AI_MAX_OPEN_QUESTIONS")
    max_manual_label_chars: int = Field(default=80, alias="AI_MAX_MANUAL_LABEL_CHARS")
    #: A single-token deny term shorter than this is dropped. A two-letter bank
    #: short name as a deny term would reject every draft containing that letter
    #: pair; a common-word short name ("Access", "First") costs some good drafts
    #: either way, which is why this is tunable rather than fixed.
    min_deny_term_chars: int = Field(default=4, alias="AI_MIN_DENY_TERM_CHARS")
    #: Served to the dashboard as ``poll_after_seconds`` so no interval literal
    #: lives in the browser bundle.
    client_poll_seconds: int = Field(default=5, alias="AI_CLIENT_POLL_SECONDS")
    consent_version: str = Field(default="ai-consent-2026-09-v1", alias="AI_CONSENT_VERSION")
    production_approval_ref: str | None = Field(default=None, alias="AI_PRODUCTION_APPROVAL_REF")
    #: ``tiered`` walks ``AI_PROVIDER_TIER``; ``anthropic`` pins every request to
    #: the one vendor (what counsel may require, and what the platform did before
    #: D-053); ``recorded`` replays fixtures and is REFUSED outside local/test.
    model_backend: Literal["tiered", "anthropic", "recorded"] = Field(
        default="tiered", alias="AI_MODEL_BACKEND"
    )
    recorded_fixture_path: str | None = Field(default=None, alias="AI_RECORDED_FIXTURE_PATH")

    @field_validator("production_approval_ref", "recorded_fixture_path", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("feature_models")
    @classmethod
    def feature_models_parse(cls, value: str) -> str:
        """Validate at BOOT. A typo here would otherwise be indistinguishable from
        "no override set", and the deployment would quietly file a report drafted
        by a model nobody chose."""
        parse_ai_feature_models(value)
        return value

    @property
    def feature_model_overrides(self) -> dict[tuple[str, str], str]:
        """The validated ``(feature, vendor) -> model`` overrides."""
        return parse_ai_feature_models(self.feature_models)

    @field_validator("provider_tier")
    @classmethod
    def tier_names_known_vendors(cls, value: str) -> str:
        """Validate at BOOT, not at the call.

        A typo here would otherwise surface as a vendor quietly missing from the
        chain on the day the first one ran out of credit — the exact failure the
        tier exists to prevent.
        """
        parse_ai_provider_tier(value)
        return value

    @property
    def provider_order(self) -> tuple[str, ...]:
        """The validated failover order. ``anthropic`` alone when pinned."""
        if self.model_backend == "anthropic":
            return (AI_VENDORS[0],)
        return parse_ai_provider_tier(self.provider_tier)

    @property
    def stale_after_seconds(self) -> float:
        """The reclaim window for an AI job.

        The SDK retries timeouts, so one ``generate`` can legitimately occupy a
        worker for ``timeout x (retries + 1)``. A reclaim window shorter than
        that reclaims a live job and runs it twice — the ``etl_dedup`` lesson,
        applied before it can happen rather than after.

        Multiplied by the TIER LENGTH since D-053: a handler that fails over
        Claude to OpenAI to Gemini legitimately spends that budget once per
        vendor, and a window sized for one would reclaim the job somewhere in the
        middle of the second.
        """
        per_vendor = self.request_timeout_seconds * (self.max_retries + 1)
        return per_vendor * len(self.provider_order) + self.stale_margin_seconds


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
        keys = tuple(key.strip() for key in self.desk_capture_sources.split(",") if key.strip())
        return keys or None


class BiSettings(BaseSettings):
    """Business-intelligence marts, query surface and the ``bi`` worker lane.

    EVERY switch here ships OFF and every limit ships at its safe value: a
    deployment that sets nothing has no BI routers mounted, enqueues no mart
    builds, runs no BI scheduler sweep, and serves nothing from a mart. Turning
    BI on is three explicit, separately reviewable acts, in this order:

    1. ``risk-worker-bi`` (``WORKER_JOB_TYPES=lane:bi``) is DEPLOYED and healthy.
       The three BI job types live in the ``bi`` lane, which the core worker and
       the API's in-process thread never claim by construction. The API and
       every worker share one ``jobs`` table, so a BI enqueue flag that flips
       before that process exists orphans every job it produces in ``queued`` —
       the exact ``notification_email_mirror`` failure this codebase already
       paid for (D-008). Nothing here may be enabled before step 1.
    2. ``BI_MART_ENQUEUE_ENABLED`` lets ingestion, withdrawals, the live and
       official pipelines and the register triggers enqueue ``bi_mart_refresh``;
       it is re-checked at run time by every BI handler (kill-switch idiom), so
       flipping it off also drains the backlog as ``skipped``.
    3. ``BI_ENABLED`` mounts the tenant-facing BI routers; ``BI_SCHEDULER_ENABLED``
       adds the recovery sweep and retention to the hourly tick, and
       ``BI_SUBSCRIPTIONS_ENABLED`` adds the subscription scan to it.
       ``BI_ALERTS_ENABLED`` needs no tick at all: alerts are evaluated by a
       succeeded mart build.

    ``GET /api/v1/feature-flags`` projects the three booleans to the dashboard;
    nothing else in this class is served. There is deliberately no grid licence
    key (D-030: AG Grid Community, grouping and pivot compiled server-side).

    ``BI_DATABASE_URL`` is optional: when set, BI queries run on their own small
    pool; unset (the default, and "" reads as unset like every other URL here)
    means the request's tenant session is used.

    ``BI_BACKFILL_HOP_SECONDS`` bounds ONE hop of the self-re-enqueuing backfill
    job, and :attr:`backfill_stale_after_seconds` derives that job's reclaim
    window from it, so the two cannot drift apart when the hop is tuned.
    """

    model_config = SETTINGS_CONFIG

    enabled: bool = Field(default=False, alias="BI_ENABLED")
    mart_enqueue_enabled: bool = Field(default=False, alias="BI_MART_ENQUEUE_ENABLED")
    scheduler_enabled: bool = Field(default=False, alias="BI_SCHEDULER_ENABLED")
    #: How many BI reads one principal may put inside the 60-second budget window.
    #: The default is the product's own figure and production should not change it:
    #: a dashboard pack opens about a dozen queries at once and a user may walk
    #: several packs in a minute, so what it bounds is a script, not a person.
    #:
    #: It is configurable for exactly one reason. The Playwright journeys ARE a
    #: script: they drive ~30 BI journeys as one identity inside a minute, so the
    #: suite trips its own product limit and the failure lands on whichever spec
    #: happens to run last — a red suite that says nothing about the code. The
    #: window is deliberately NOT configurable, so raising this cannot turn the
    #: budget off, only widen it.
    #: ``None`` means "use the product's own figure",
    #: ``query_log.RATE_LIMIT_MAX_QUERIES``. Left unset rather than mirroring 120
    #: here so there is ONE place the default lives, and so the six tests that
    #: monkeypatch that constant keep working — a second copy here would silently
    #: win over the patch and make those tests assert nothing.
    rate_limit_max_queries: int | None = Field(
        default=None, ge=1, alias="BI_RATE_LIMIT_MAX_QUERIES"
    )
    #: Threshold alerts (``docs/bi.md`` §Phase 3). Event-driven, not scheduled:
    #: an evaluation is enqueued by a SUCCEEDED ``bi_mart_refresh`` and by
    #: nothing else, which is why this flag is deliberately absent from
    #: ``scheduler.any_scheduling_enabled`` — it owns no branch of the tick. If
    #: an alert recovery sweep is ever added, it goes there in the same change.
    alerts_enabled: bool = Field(default=False, alias="BI_ALERTS_ENABLED")
    #: Scheduled subscriptions. This one DOES own a tick branch (the hourly
    #: ``bi_subscription_scan``), so it is named in ``any_scheduling_enabled``:
    #: a deployment that enabled only this would otherwise find the tick inert
    #: and the feature silently never running.
    subscriptions_enabled: bool = Field(default=False, alias="BI_SUBSCRIPTIONS_ENABLED")
    #: Natural-language questions (docs/bi.md §Phase 5). It owns no tick branch —
    #: a question is enqueued by a reader and by nobody else — so it is absent from
    #: ``scheduler.any_scheduling_enabled`` for the ``alerts_enabled`` reason above.
    #:
    #: It is a SEPARATE switch from ``enabled`` because it is the only BI surface
    #: that sends a reader's own words to an external vendor, and a deployment must
    #: be able to run every other BI surface without that. Three further gates sit
    #: behind it and all ship shut: ``AI_COMMENTARY_ENABLED`` (the deployment AI
    #: kill-switch), an approved configuration for ``bi_nlq`` in
    #: ``app/services/ai/approved_configurations.json`` (empty), and the tenant's own
    #: consented ``enabled_features``. It also needs the ``ai``-lane worker
    #: (``docker-compose.ai.prod.yml``) DEPLOYED before it is flipped, for the
    #: shared-``jobs``-table reason stated at the top of this class.
    nlq_enabled: bool = Field(default=False, alias="BI_NLQ_ENABLED")
    #: The largest artifact a scheduled delivery will attach. Above it the
    #: recipient is sent a sign-in link instead: an aggregated pack they are
    #: entitled to must not be dropped merely because the relay would refuse it,
    #: and a relay that bounces a 40 MB message fails the whole run. 5 MB is
    #: comfortably inside the default limit of every relay this ships against;
    #: a deployment whose relay allows more may raise it.
    subscription_attachment_max_bytes: int = Field(
        default=5_000_000, gt=0, alias="BI_SUBSCRIPTION_ATTACHMENT_MAX_BYTES"
    )
    #: Where a subscription's sign-in link points when the content may not be
    #: attached. The SAME environment variable the staff console reads
    #: (``BANK_APP_BASE_URL``, one constant above), read here rather than through
    #: ``OperatorSettings`` so a tenant-facing service does not depend on the
    #: staff control plane's configuration object. No host literal lives in the BI
    #: code; this is the only place the origin is named.
    bank_app_base_url: str = Field(default=DEFAULT_BANK_APP_BASE_URL, alias="BANK_APP_BASE_URL")
    daily_retention_days: int = Field(default=95, gt=0, alias="BI_DAILY_RETENTION_DAYS")
    database_url: str | None = Field(default=None, alias="BI_DATABASE_URL")
    interactive_timeout_ms: int = Field(default=10_000, gt=0, alias="BI_INTERACTIVE_TIMEOUT_MS")
    export_timeout_ms: int = Field(default=120_000, gt=0, alias="BI_EXPORT_TIMEOUT_MS")
    ui_row_cap: int = Field(default=5_000, gt=0, alias="BI_UI_ROW_CAP")
    grid_page_cap: int = Field(default=500, gt=0, alias="BI_GRID_PAGE_CAP")
    export_row_cap: int = Field(default=100_000, gt=0, alias="BI_EXPORT_ROW_CAP")
    export_async_threshold_rows: int = Field(
        default=10_000, gt=0, alias="BI_EXPORT_ASYNC_THRESHOLD_ROWS"
    )
    backfill_hop_seconds: int = Field(default=600, gt=0, alias="BI_BACKFILL_HOP_SECONDS")

    @field_validator("bank_app_base_url", mode="before")
    @classmethod
    def default_bank_app_base_url(cls, value: str | None) -> str:
        """Blank reads as "unset"; trailing slashes are trimmed so a path joins safely."""
        if value is None or not str(value).strip():
            return DEFAULT_BANK_APP_BASE_URL
        return str(value).strip().rstrip("/")

    @field_validator("database_url", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: str | None) -> str | None:
        """BI_DATABASE_URL="" means "use the tenant session" (the DatabaseSettings
        rule: an empty env value neutralizes a .env entry without editing it)."""
        if value is not None and not value.strip():
            return None
        return value

    @property
    def backfill_stale_after_seconds(self) -> float:
        """The reclaim window for one ``bi_mart_backfill`` hop.

        A hop stops starting new dates once ``BI_BACKFILL_HOP_SECONDS`` has
        elapsed, but the date already in flight runs to completion, so one hop
        can legitimately overrun its budget by a single day's build. Three
        budgets cover the hop, that overrun and a margin; a shorter window would
        reclaim a live hop and build the same dates twice (the ``etl_dedup``
        lesson, applied ahead of time as the AI lane does).
        """
        return float(self.backfill_hop_seconds * 3)


class WorkerSettings(BaseSettings):
    """Live-engine background worker and scheduler settings.

    ``RUN_INPROCESS_WORKER`` starts a daemon poll loop inside the API process
    (off by default so tests/dev drive handlers synchronously). The scheduler is
    inert unless ``OFFICIAL_RUN_ENABLED`` so no environment auto-mints the heavy
    22-scenario official runs.
    """

    model_config = SETTINGS_CONFIG

    run_inprocess_worker: bool = Field(default=False, alias="RUN_INPROCESS_WORKER")
    #: Stable runtime label written to each claimed job. Deployments should set
    #: this to a service/generation identifier; local workers fall back to host:PID.
    worker_id: str | None = Field(default=None, max_length=160, alias="WORKER_ID")
    pipeline_debounce_seconds: int = Field(default=15, alias="PIPELINE_DEBOUNCE_SECONDS")
    worker_poll_seconds: float = Field(default=2.0, alias="WORKER_POLL_SECONDS")
    # Operations treats a worker as unavailable only after this many seconds
    # without a durable heartbeat. Keep the threshold deploy-configured rather
    # than coupling it to the poll interval: deployment pauses and long hosts
    # need an explicit, reviewable operational decision.
    worker_heartbeat_stale_seconds: float = Field(
        default=120.0, gt=0, alias="WORKER_HEARTBEAT_STALE_SECONDS"
    )
    # A job stuck in ``running`` longer than this is treated as orphaned by a
    # dead worker and reclaimed (see job_queue.reclaim_stale).
    #
    # 900s is the FLEET DEFAULT, and setting it asserts that every job type NOT
    # listed in ``job_queue.STALE_AFTER_OVERRIDES_SECONDS`` completes inside 15
    # minutes: pipeline_refresh, official_run, market_data_pull, temenos_pull,
    # scheduled_tick, reporting_deadline_scan, notification_email_mirror,
    # database_direct_health, bi_mart_refresh and bi_retention (desk_capture
    # and etl_dedup have overrides; bi_mart_backfill derives its window from
    # BI_BACKFILL_HOP_SECONDS; the AI lane derives its own from AI_*). A handler
    # that outgrows that gets its own entry in the override map — NOT a bigger
    # global number, because
    # this value also governs how fast a genuinely dead worker's jobs come back,
    # so widening it fleet-wide to suit one long handler slows recovery for the
    # nine short ones.
    #
    # ``etl_dedup`` is the one that outgrew it: measured 2h02m on a 168k-record
    # batch, reclaimed as "worker presumed dead" at 900s while still running, and
    # requeued into concurrent copies of itself. It is now on a 4h override.
    worker_stale_job_seconds: float = Field(default=900.0, alias="WORKER_STALE_JOB_SECONDS")
    official_run_hour: int = Field(default=2, alias="OFFICIAL_RUN_HOUR")
    official_run_enabled: bool = Field(default=False, alias="OFFICIAL_RUN_ENABLED")
    #: Hourly live-refresh recovery net: enqueues only when accepted ingestion
    #: is newer than the bank's oldest live module (or no live rows exist).
    #: Unchanged and structurally unavailable rows are never refreshed by age.
    #: Default off; authoritative input mutations enqueue directly.
    live_refresh_enabled: bool = Field(default=False, alias="LIVE_REFRESH_ENABLED")
    # The worker claims and processes jobs across every tenant, so its DB
    # connection must see all rows. On an RLS-forced Postgres this requires a
    # BYPASSRLS role (the app role is deliberately tenant-scoped). When unset,
    # the worker falls back to DATABASE_URL (correct for SQLite tests and any
    # deployment whose main role already bypasses RLS).
    worker_database_url: str | None = Field(default=None, alias="WORKER_DATABASE_URL")
    # Which job types THIS worker process claims and reaps. Unset means "every
    # type in the default (core) lane" — the AI lane is never in the default,
    # so adding an AI job type to HANDLERS cannot make the core worker, or the
    # API's in-process thread, claim work that needs the model key.
    #
    # Tokens are comma-separated job types or ``lane:<name>``. Parsing and
    # validation live in ``app.worker.resolve_job_types`` because config must
    # not import the worker.
    worker_job_types: str | None = Field(default=None, alias="WORKER_JOB_TYPES")

    @field_validator("worker_database_url", "worker_id", "worker_job_types", mode="before")
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
    #: Signs + verifies the operator "act-as-examiner" impersonation token — a
    #: DEDICATED secret, never AUTH_JWT_SECRET or OPERATOR_JWT_SECRET, so an
    #: impersonation token is worthless on the normal access-token path and a
    #: normal access token can never masquerade as an impersonation token. Unset
    #: FAILS CLOSED: the operator mint endpoint answers 503 and the tenant API's
    #: accept branch is a no-op, so no impersonation is possible until it is set.
    impersonation_jwt_secret: str | None = Field(default=None, alias="IMPERSONATION_JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="AUTH_JWT_ALGORITHM")
    jwt_issuer: str = Field(default="aequoros", alias="AUTH_JWT_ISSUER")
    jwt_audience: str = Field(default="aequoros-api", alias="AUTH_JWT_AUDIENCE")
    access_token_ttl_seconds: int = Field(default=900, alias="AUTH_ACCESS_TOKEN_TTL")
    refresh_token_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 14, alias="AUTH_REFRESH_TOKEN_TTL"
    )
    #: How long after a refresh token is rotated re-presenting it still counts as
    #: a benign concurrent retry rather than theft. The dashboard's API client
    #: falls back to ``getSession()`` on every request once the cached access
    #: token is within 30s of expiry (dashboard/lib/api/token.ts), so several
    #: NextAuth refreshes can genuinely race with the SAME stored refresh token;
    #: without a window each of those would trip reuse detection and sign the
    #: user out. Outside the window a re-presented token revokes its whole
    #: family. Set to 0 for strict single-use rotation.
    refresh_rotation_grace_seconds: int = Field(default=30, alias="AUTH_REFRESH_ROTATION_GRACE")
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

    @field_validator("jwt_secret", "impersonation_jwt_secret", mode="before")
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
    icaap: IcaapSettings = Field(default_factory=IcaapSettings)
    ai: AiSettings = Field(default_factory=AiSettings)
    bi: BiSettings = Field(default_factory=BiSettings)
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


#: Environments where a developer's own machine IS the deployment. Everything
#: else — ``staging`` included — runs the same containers on a host somebody
#: else can reach.
#:
#: This is an ALLOW-LIST on purpose. Every guard that used to ask
#: ``app_env == "production"`` silently opened itself on ``staging`` and on any
#: environment name added later, because "not production" is an unbounded set
#: and the dangerous branch was the default. Asking "is this one of the two
#: environments that are definitionally undeployed?" inverts that: an
#: unrecognised value is treated as deployed, so a new environment name is safe
#: by construction and must be admitted here deliberately.
UNDEPLOYED_ENVS: frozenset[str] = frozenset({"local", "test"})


def is_undeployed_environment(app_env: str | None = None) -> bool:
    """True only on ``local``/``test`` — the environments a developer runs.

    The single authority for "may a never-in-production convenience apply
    here?". ``app.core.security._is_loopback_issuer_allowed`` (plain-http OIDC
    discovery), the operator dev-token bearer and the operator app's boot
    refusal all read it, and ``dashboard/lib/outbound.ts`` mirrors it for the
    Next.js runtime.
    """
    env = app_env if app_env is not None else get_settings().app.app_env
    return env in UNDEPLOYED_ENVS


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
    operator_cors_methods_raw: str = Field(
        default="GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS", alias="OPERATOR_CORS_METHODS"
    )
    operator_cors_headers_raw: str = Field(
        default="Accept,Authorization,Content-Type,X-Request-ID", alias="OPERATOR_CORS_HEADERS"
    )
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
    #: must carry a verified email under the allowed domain, and that email
    #: must identify an active, explicitly provisioned ``operator_users`` row.
    #: Domain membership alone grants no operator role.
    oidc_issuer: str | None = Field(default=None, alias="OPERATOR_OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, alias="OPERATOR_OIDC_CLIENT_ID")
    oidc_allowed_domain: str = Field(default="aequoros.com", alias="OPERATOR_OIDC_ALLOWED_DOMAIN")
    #: Origin of the authenticated bank product (bank.aequoros.com). Returned as
    #: ``dashboard_url`` by the act-as-examiner mint endpoint so the console knows
    #: where to hand the operator off with the impersonation token. Never a
    #: secret — just where the read-only examiner view is rendered.
    bank_app_base_url: str = Field(default=DEFAULT_BANK_APP_BASE_URL, alias="BANK_APP_BASE_URL")
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

    @field_validator("bank_app_base_url", mode="before")
    @classmethod
    def default_bank_app_base_url(cls, value: str | None) -> str:
        """Blank reads as "unset" (the documented default); trailing slashes are
        trimmed so the console can safely join a handoff path."""
        if value is None or not str(value).strip():
            return DEFAULT_BANK_APP_BASE_URL
        return str(value).strip().rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.operator_cors_origins_raw.split(",") if origin.strip()
        ]

    @property
    def cors_methods(self) -> list[str]:
        return [
            method.strip().upper()
            for method in self.operator_cors_methods_raw.split(",")
            if method.strip()
        ]

    @property
    def cors_headers(self) -> list[str]:
        return [
            header.strip() for header in self.operator_cors_headers_raw.split(",") if header.strip()
        ]


@lru_cache
def get_operator_settings() -> OperatorSettings:
    return OperatorSettings()
