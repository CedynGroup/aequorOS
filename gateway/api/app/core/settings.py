"""Service configuration, read from ``GATEWAY_*`` environment variables."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "sandbox", "production"]

API_ROOT = Path(__file__).resolve().parents[2]

# Placeholder due-date offsets, in business days after the reporting date. Bank of Ghana
# has not given us its due-date rule; these defaults only reproduce the dates the approved
# design shows (monthly returns for 31 Oct 2026 due 13 Nov, weekly returns due the
# following Monday). They are configuration, never presented as regulator policy.
DEFAULT_DUE_BUSINESS_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 1,
    "monthly": 10,
    "quarterly": 29,
    "semiannual": 30,
    "annual": 60,
}
DEFAULT_DUE_OVERRIDES: dict[str, int] = {"NPL-MONTHLY": 11, "IRRBB-PILOT": 43}

DEFAULT_GROUP_ROLES: dict[str, str] = {
    "srg-supervisors": "supervisor",
    "srg-senior-supervisors": "senior_supervisor",
    "srg-returns-policy": "returns_policy",
    "srg-access-approvers": "access_approver",
    "srg-internal-audit": "auditor",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=None, extra="ignore")

    env: Environment = "local"
    database_url: str = "sqlite+pysqlite:///./var/gateway-local.db"
    base_url: str = "http://localhost:8200"
    trusted_proxies: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = "srg-portal"
    oidc_jwks_url: str = ""
    keys_dir: Path = API_ROOT / ".keys"
    receipt_key_id: str = "gateway-receipt-2026-q4"
    audit_key_id: str = "gateway-audit-2026-q4"
    dev_oidc_key_id: str = "dev-oidc-2026-q4"
    retired_receipt_key_ids: str = ""
    operator_clients: str = ""
    client_certificate_days: int = 365
    # The gateway's own identity provider (``/idp``): supervisor accounts with Argon2id
    # password hashes. Serves sign-in whenever no external provider is federated through
    # ``oidc_issuer``. ``idp_redirect_prefixes`` lists the portal origins it may return to.
    idp_enabled: bool = True
    idp_redirect_prefixes: str = "http://localhost:3100/,http://127.0.0.1:3100/"
    idp_max_failed_attempts: int = 5
    idp_lockout_minutes: int = 15
    group_roles: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GROUP_ROLES))
    due_business_days: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_DUE_BUSINESS_DAYS)
    )
    due_business_days_overrides: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_DUE_OVERRIDES)
    )
    resubmission_business_days: int = 3
    access_token_seconds: int = 600
    rate_limit_per_minute: int = 600
    audit_verify_interval_seconds: int = 1800
    validation_sweep_seconds: int = 0
    scheduler_enabled: bool = True

    @property
    def is_dev(self) -> bool:
        """True where development conveniences exist (injected client certificates)."""
        return self.env in ("local", "test")

    @property
    def allows_seed(self) -> bool:
        return self.env in ("local", "test", "sandbox")

    @property
    def uses_local_keys(self) -> bool:
        return self.env in ("local", "test", "sandbox")

    @property
    def token_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/oauth/token"

    @property
    def issuer(self) -> str:
        """The gateway's own issuer identifier for access tokens."""
        return self.base_url.rstrip("/")

    @property
    def idp_issuer(self) -> str:
        """The gateway's own identity provider."""
        return self.base_url.rstrip("/") + "/idp"

    @property
    def uses_builtin_idp(self) -> bool:
        """Staff sign in through the gateway's own provider: no external one is federated.

        An issuer naming the gateway's own ``/idp`` IS the built-in provider. A deployment
        gives the API and the portal one env file, and the portal needs that issuer in
        ``GATEWAY_OIDC_ISSUER``; reading it as a federation would switch sign-in off.
        """
        own = not self.oidc_issuer or self.oidc_issuer.rstrip("/") == self.idp_issuer
        return self.idp_enabled and own

    @property
    def effective_oidc_issuer(self) -> str:
        if self.oidc_issuer:
            return self.oidc_issuer.rstrip("/")
        if self.idp_enabled:
            return self.idp_issuer
        return ""

    @property
    def problem_base(self) -> str:
        return self.base_url.rstrip("/") + "/errors/"

    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in self.trusted_proxies.split(","):
            item = raw.strip()
            if item:
                networks.append(ipaddress.ip_network(item, strict=False))
        return networks

    def redirect_prefixes(self) -> list[str]:
        return [p.strip() for p in self.idp_redirect_prefixes.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
