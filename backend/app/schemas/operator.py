"""Operator control-plane API contracts (docs/internal/developer.md §2, §3).

These schemas serve ONLY the operator app (``app.operator``); nothing here is
mounted on — or importable into — the tenant API surface.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import JsonObject


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- provisioning --------------------------------------------------------------
type ProvisioningStepStatus = Literal["succeeded", "failed", "skipped", "rolled_back"]
type ProvisioningStepName = Literal[
    "organization",
    "bank",
    "storage",
    "kms",
    "sso_stub",
    "first_admin",
    "readiness",
    "cleanup",
]


class TenantProvisionCreate(ClosedModel):
    """The onboarding form. Every field is REQUIRED — most deliberately.

    ``currency`` has no default and never will: the no-defaults rule exists
    because independently-defaulted currency/jurisdiction silently disagreed
    once (a Nigerian bank reporting in cedis). The console derives a currency
    suggestion from the jurisdiction; the operator must confirm it here.
    """

    organization_name: str = Field(min_length=1, max_length=255)
    bank_name: str = Field(min_length=1, max_length=255)
    license_type: str = Field(min_length=1, max_length=40)
    # Validated against the jurisdictions registry in the saga (a registry
    # lookup needs the DB; the format gate lives here).
    jurisdiction_code: str = Field(min_length=2, max_length=8)
    # ISO-4217, uppercase, exactly three letters — the schema-level 422.
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    admin_email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    admin_full_name: str = Field(min_length=1, max_length=255)


class ProvisioningStepRead(ClosedModel):
    step: ProvisioningStepName
    status: ProvisioningStepStatus
    detail: str


class ProvisioningResultRead(ClosedModel):
    """The saga's explicit step-by-step outcome — partial failure is never
    silent (developer.md §2). ``admin_one_time_password`` follows the
    integration-key precedent: plaintext shown exactly once, only the Argon2id
    hash is stored."""

    succeeded: bool
    organization_id: str | None
    bank_id: str | None
    admin_email: str | None
    admin_one_time_password: str | None
    #: The two OIDC redirect URIs bank IT must register (sign-in AND signing
    #: step-up — registering only the first is the documented foot-gun).
    sso_redirect_uris: list[str]
    steps: list[ProvisioningStepRead]
    warnings: list[str]


# -- tenants list ----------------------------------------------------------------
class TenantFreshnessSummaryRead(ClosedModel):
    is_stale: bool
    stale_modules: list[str]
    modules_reported: int
    latest_computed_at: datetime | None


class TenantIngestionSummaryRead(ClosedModel):
    batch_id: str
    status: str
    source_system: str
    as_of_date: date
    completed_at: datetime | None


class TenantRead(ClosedModel):
    organization_id: str
    organization_name: str
    organization_created_at: datetime
    # Null for an organization with no bank yet (should not happen through the
    # saga, but the view never hides a half-provisioned tenant).
    bank_id: str | None
    bank_name: str | None
    jurisdiction_code: str | None
    currency: str | None
    license_type: str | None
    bank_created_at: datetime | None
    period_count: int
    latest_period_end: date | None
    freshness: TenantFreshnessSummaryRead | None
    last_ingestion: TenantIngestionSummaryRead | None
    sso_configured: bool
    sso_enabled: bool
    storage_provider: str | None


class TenantsListRead(ClosedModel):
    tenants: list[TenantRead]


# -- activity feed ----------------------------------------------------------------
type ActivityKind = Literal[
    "ingestion_batch", "job", "official_run", "package", "audit_event"
]


class ActivityItemRead(ClosedModel):
    ts: datetime
    kind: ActivityKind
    summary: str
    status: str


class TenantActivityRead(ClosedModel):
    organization_id: str
    items: list[ActivityItemRead]


# -- data engines ----------------------------------------------------------------
type DataEngineKind = Literal["market_data", "database_direct", "t24"]


class DataEngineConnectionRead(ClosedModel):
    """Connection health metadata ONLY — no credential material, ever.

    Ciphertexts, fingerprints, and vault paths stay server-side; the operator
    wall shows lifecycle status, last activity, and credential expiry.
    """

    organization_id: str
    bank_id: str
    engine: DataEngineKind
    #: Vendor (market data), backend (database-direct), or core system (T24).
    system: str
    display_name: str
    status: str
    last_activity_at: datetime | None
    last_activity_status: str | None
    credential_expires_at: datetime | None
    created_at: datetime


class DataEnginesRead(ClosedModel):
    connections: list[DataEngineConnectionRead]


# -- audit -----------------------------------------------------------------------
class OperatorAuditLogRead(ClosedModel):
    id: str
    operator_email: str
    auth_mode: Literal["dev", "oidc"]
    action: str
    target_org: str | None
    detail: JsonObject
    created_at: datetime
