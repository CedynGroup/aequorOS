"""Operator control-plane API contracts (docs/internal/developer.md §2, §3).

These schemas serve ONLY the operator app (``app.operator``); nothing here is
mounted on — or importable into — the tenant API surface.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import JsonObject
from app.schemas.market_desk import DeskEntitlementRead


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
    "first_owner",
    # The institution's own board register (``param_*``). Without it a tenant is
    # provisioned, ingests its whole book, and still cannot produce a single
    # successful calculation run (founder review 2026-08-23).
    "parameters",
    "readiness",
    "desk_market_data",
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
    # The typed institution discriminator (docs/sdi.md §1) — REQUIRED with no
    # default, the same fail-loud discipline as currency/jurisdiction. Validated
    # against the institution_types registry in the saga (DB lookup); selecting
    # an SDI class here is what scopes the tenant's modules/requirements/returns
    # in later phases. The format gate lives here.
    institution_type: str = Field(min_length=1, max_length=40)
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
    # The typed institution discriminator (docs/sdi.md §1); null only for an
    # organization with no bank yet.
    institution_type: str | None
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


# -- jobs ------------------------------------------------------------------------
class OperatorJobRead(ClosedModel):
    """Cross-tenant job metadata for the Operations board.

    ``claimed_by`` is the runtime that most recently moved the job from queued
    to running. It remains visible after completion or a retry so an operator
    can identify a stale or failed worker generation.
    """

    id: str
    organization_id: str
    bank_id: str | None
    job_type: str
    status: str
    claimed_by: str | None
    attempts: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    run_after: datetime | None
    error: str | None


class OperatorJobsRead(ClosedModel):
    jobs: list[OperatorJobRead]


class StuckDedupBatchRead(ClosedModel):
    """One ingestion batch whose out-of-band dedup pass can no longer run itself.

    ``dedup_status`` is the batch's own marker (``deferred`` = queued/never ran,
    ``failed`` = ran and could not complete). ``job_status`` / ``job_error`` come
    from the most recent ``etl_dedup`` job for the batch: when it has exhausted
    ``max_attempts`` the queue will never touch the batch again, and the pass
    exists only if an operator re-drives it.
    """

    batch_id: UUID
    organization_id: str
    bank_id: str
    source_system: str
    as_of_date: date
    records_extracted: int
    dedup_status: str
    job_id: UUID | None
    job_status: str | None
    job_attempts: int | None
    job_max_attempts: int | None
    job_error: str | None
    job_completed_at: datetime | None


class StuckDedupBatchesRead(ClosedModel):
    """The fleet board of exhausted dedup passes, newest batch first.

    Deliberately a BOARD, not a timer: the four batches that stranded on the
    primary failed for three different reasons (a severed connection, a live job
    reclaimed as dead, a code/schema skew since healed), and an automatic retry
    loop would have masked all three. An operator reads the reason, then
    re-drives.
    """

    batches: list[StuckDedupBatchRead]


# -- worker health --------------------------------------------------------------
class WorkerHeartbeatRead(ClosedModel):
    worker_id: str
    status: Literal["healthy", "stale"]
    started_at: datetime
    last_seen_at: datetime
    last_job_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None


class WorkerHealthRead(ClosedModel):
    """Authenticated operator evidence for the cross-tenant worker fleet."""

    ready: bool
    stale_after_seconds: float
    observed_at: datetime
    workers: list[WorkerHeartbeatRead]


# -- audit -----------------------------------------------------------------------
class OperatorAuditLogRead(ClosedModel):
    id: str
    operator_email: str
    auth_mode: Literal["dev", "oidc", "password"]
    action: str
    target_org: str | None
    detail: JsonObject
    created_at: datetime


class OperatorAuditLogListRead(ClosedModel):
    """The operator's OWN cross-tenant action log (distinct from a tenant's
    activity feed). Filtered, paginated, newest-first."""

    items: list[OperatorAuditLogRead]
    #: Total rows matching the filters (before ``limit``/``offset``).
    total: int


# -- fleet overview --------------------------------------------------------------
type NeedsAttentionSeverity = Literal["crit", "warn"]


class OverviewTenantsRead(ClosedModel):
    total: int
    banks_live: int
    banks_empty: int
    stale_count: int


class OverviewIngestionRead(ClosedModel):
    failed_24h: int


class OverviewJobsRead(ClosedModel):
    failed_24h: int
    running: int


class OverviewConnectionsRead(ClosedModel):
    ok: int
    warn: int
    crit: int


class OverviewDeskRead(ClosedModel):
    pending_determinations: int
    pending_curve_determinations: int
    pending_oe_assessments: int


class NeedsAttentionItemRead(ClosedModel):
    kind: str
    summary: str
    severity: NeedsAttentionSeverity
    href: str | None = None


class FleetOverviewRead(ClosedModel):
    """Cross-tenant rollup for the console home — counts only, no per-tenant
    detail leak. Every underlying query is a deliberate fleet aggregate or
    reuses an explicitly org-scoped read view."""

    tenants: OverviewTenantsRead
    ingestion: OverviewIngestionRead
    jobs: OverviewJobsRead
    connections: OverviewConnectionsRead
    desk: OverviewDeskRead
    needs_attention: list[NeedsAttentionItemRead]


# -- single-tenant detail --------------------------------------------------------
class TenantUserRead(ClosedModel):
    email: str
    full_name: str | None
    role: str
    auth_provider: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class TenantUsersListRead(ClosedModel):
    users: list[TenantUserRead]


class TenantEntitlementsRead(ClosedModel):
    """Per-tenant desk entitlements + the dataset/tier catalog (the desk
    entitlement query filtered to one org)."""

    entitlements: list[DeskEntitlementRead]
    catalog: JsonObject


class TenantStorageRead(ClosedModel):
    """Best-effort per-tenant object-storage view. Metrics are optional by
    design: the MinIO deployment sits behind a Cloudflare WAF that blocks HEAD
    and exposes no KES, so object_count/bytes/kms_key_state are frequently
    unavailable — ``note`` explains why rather than the endpoint failing."""

    provider: str | None = None
    bucket: str | None = None
    object_count: int | None = None
    bytes: int | None = None
    kms_key_state: str | None = None
    note: str | None = None


# -- tenant inspector (READ-ONLY session tracking) -------------------------------
type InspectorMode = Literal["consent", "break_glass"]


class InspectorSessionCreate(ClosedModel):
    organization_id: str = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=1, max_length=2000)
    mode: InspectorMode
    #: Session lifetime in minutes (15–60). The session is a viewing window;
    #: it grants no access on its own — the read endpoints are what render data.
    ttl_minutes: int = Field(default=30, ge=15, le=60)


class InspectorSessionRead(ClosedModel):
    session_id: str
    organization_id: str
    mode: InspectorMode
    #: Always true this wave — no act-as-user token is ever minted.
    read_only: bool
    started_by: str
    started_at: datetime
    expires_at: datetime
    reason: str
    ended_at: datetime | None = None
    ended_by: str | None = None


class InspectorSessionListRead(ClosedModel):
    sessions: list[InspectorSessionRead]
    total: int


class InspectorActTokenRead(ClosedModel):
    """The act-as-examiner handoff: a short-lived, read-only impersonation token
    scoped to ONE tenant and to the examiner role, plus where the console renders
    it. The token is a bearer credential — it is never logged or re-listed."""

    act_token: str
    expires_at: datetime
    #: Origin of the authenticated bank product; the console hands the operator
    #: off there with ``act_token`` to open the read-only examiner view.
    dashboard_url: str


# -- tenant diagnostics (session-gated deep reads) -------------------------------
# Every schema below serves an endpoint that requires an ACTIVE Tenant Inspector
# session (app.operator.inspection.require_active_inspection). Nothing here ever
# carries credential material: SSO exposes only whether a secret is configured,
# integration keys expose only the display prefix + lifecycle metadata, and no
# vault path / ciphertext / fingerprint / key hash is ever a field.
class TenantLiveMetricRead(ClosedModel):
    bank_id: str
    module: str
    status: str
    metrics: JsonObject
    reporting_period_id: str
    computed_from_input_hash: str | None
    computed_at: datetime


class TenantRegulatoryRunRead(ClosedModel):
    """Latest regulatory-engine run summary. ``module`` is the run's family axis
    — ``regulatory_runs`` carries no separate 'family' column; the module IS the
    grouping key (liquidity / capital / irr / fx / ftp / forecast / …)."""

    run_id: str
    bank_id: str
    module: str
    scenario_code: str
    status: str
    input_hash: str
    reporting_period_id: str
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


class TenantMetricsRead(ClosedModel):
    organization_id: str
    #: Latest LiveMetric per (bank, module) — the always-fresh baseline view.
    live_metrics: list[TenantLiveMetricRead]
    #: Latest immutable RegulatoryRun per (bank, module/family).
    regulatory_runs: list[TenantRegulatoryRunRead]


class TenantFindingRead(ClosedModel):
    bank_id: str
    module: str
    rule_id: str
    severity: str
    status: str
    message: str
    metric: str | None
    reporting_period_id: str
    created_at: datetime
    updated_at: datetime


class TenantFindingsRead(ClosedModel):
    organization_id: str
    #: Active findings (open / needs_review, severity-ranked) followed by the
    #: most recently superseded — capped by the endpoint's ``limit``.
    findings: list[TenantFindingRead]
    open_count: int


class TenantIngestionBatchRead(ClosedModel):
    batch_id: str
    bank_id: str
    source_system: str
    adapter_version: str
    extraction_mode: str
    status: str
    as_of_date: date
    records_extracted: int
    records_translated: int
    records_accepted: int
    records_warning: int
    records_error: int
    records_blocked: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class TenantIngestionListRead(ClosedModel):
    organization_id: str
    batches: list[TenantIngestionBatchRead]


class TenantTranslationFailureRead(ClosedModel):
    """One raw source record that failed translation — the per-row error detail
    used to diagnose a failed upload. ``raw_record`` is the tenant's own source
    data (business data, not credentials)."""

    entity_type: str
    source_locator: str
    error_code: str
    error_message: str
    raw_record: JsonObject
    created_at: datetime


class TenantIngestionBatchDetailRead(ClosedModel):
    batch: TenantIngestionBatchRead
    validation_report: JsonObject
    etl_report: JsonObject | None
    translation_failures: list[TenantTranslationFailureRead]


class TenantConfigBankRead(ClosedModel):
    bank_id: str
    name: str
    jurisdiction_code: str
    currency: str
    license_type: str
    # The typed institution discriminator (docs/sdi.md §1).
    institution_type: str


class TenantMappingConfigRead(ClosedModel):
    id: str
    bank_id: str
    source_system: str
    source_ref: str
    version: int
    name: str
    status: str
    config: JsonObject


class TenantLiquidityThresholdRead(ClosedModel):
    id: str
    institution_class: str
    threshold_code: str
    threshold_pct: float
    effective_from: date
    effective_to: date | None
    approved_by: str


class TenantCapitalThresholdRead(ClosedModel):
    id: str
    threshold_code: str
    value_pct: float
    effective_from: date
    effective_to: date | None
    approved_by: str


class TenantThresholdRegisterRead(ClosedModel):
    """Current (open-ended, ``effective_to IS NULL``) Board-approved internal
    threshold register — liquidity (LMTD ¶11) and capital generations."""

    liquidity: list[TenantLiquidityThresholdRead]
    capital: list[TenantCapitalThresholdRead]


class TenantSsoConfigRead(ClosedModel):
    """OIDC connection diagnostics — NEVER the client secret. Only whether one
    is configured (``secret_configured``) is exposed; the sealed ciphertext and
    its fingerprint stay server-side."""

    issuer: str
    client_id: str
    allowed_email_domains: list[str]
    enabled: bool
    jit_enabled: bool
    secret_configured: bool


class TenantIntegrationKeyRead(ClosedModel):
    """Integration-key metadata only — the SHA-256 ``key_hash`` and the raw key
    are NEVER returned. ``key_prefix`` is the display fragment ("aeq_live_AB12…")
    shown once at issuance."""

    label: str
    key_prefix: str
    status: Literal["active", "revoked"]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TenantConfigRead(ClosedModel):
    """Non-secret diagnostic configuration for one tenant. No secret, credential,
    password hash, or vault material is ever included."""

    organization_id: str
    banks: list[TenantConfigBankRead]
    active_mappings: list[TenantMappingConfigRead]
    threshold_register: TenantThresholdRegisterRead
    sso: TenantSsoConfigRead | None
    integration_keys: list[TenantIntegrationKeyRead]


# -- tenant inspector: fix (remediation) actions ---------------------------------
# The WRITE side of the Tenant Inspector. Every fix is performed on the
# operator's cross-tenant BYPASSRLS session (NEVER a tenant-auth impersonation
# token), gated on an ACTIVE inspection session, and audited. ``note`` is
# required and non-empty on every action — it lands in operator_audit_log.


class FixRecomputeRequest(ClosedModel):
    """Recompute the bank's live metrics (enqueues a ``pipeline_refresh``)."""

    note: str = Field(min_length=1, max_length=2000)
    # Optional: unnecessary when the org has exactly one bank (the common case),
    # required to disambiguate a multi-bank org.
    bank_id: str | None = None


class FixOfficialRunRequest(ClosedModel):
    """Mint an immutable official filing run (enqueues an ``official_run``)."""

    note: str = Field(min_length=1, max_length=2000)
    # Defaults to the bank's latest reporting-period end when omitted.
    as_of_date: date | None = None
    bank_id: str | None = None


class FixRerunIngestionRequest(ClosedModel):
    """Re-process a specific ingestion batch."""

    batch_id: UUID
    note: str = Field(min_length=1, max_length=2000)


class FixRedriveDedupRequest(ClosedModel):
    """Re-drive the out-of-band ML-ETL dedup pass for one ingestion batch."""

    batch_id: UUID
    note: str = Field(min_length=1, max_length=2000)


class FixRedriveDedupRead(ClosedModel):
    job_id: UUID
    job_type: str
    status: str
    batch_id: UUID
    #: The batch's ``dedup_status`` at the moment of re-drive, so the audit
    #: detail and the console both record what was being recovered from.
    previous_dedup_status: str
    #: The exhausted job this re-drive replaces, when one exists.
    previous_job_id: UUID | None
    previous_job_error: str | None


class FixConfigRequest(ClosedModel):
    """A scoped, reversible configuration change.

    ``mapping_active`` toggles one ``MappingConfigRecord``'s active status
    (``value`` is a bool); ``threshold_value`` supersedes the current Board
    threshold with a new effective-dated generation (``value`` is a number). No
    other config kind is mutable through this seam — regulatory figures are never
    editable here.
    """

    kind: Literal["mapping_active", "threshold_value"]
    target_id: str
    value: bool | float
    note: str = Field(min_length=1, max_length=2000)


class FixJobEnqueuedRead(ClosedModel):
    job_id: UUID
    job_type: str
    status: str


class FixRerunIngestionRead(ClosedModel):
    job_id: UUID
    job_type: str
    status: str
    batch_id: UUID
    # Plain-language note on what re-processing actually did, so the console can
    # set expectations (e.g. that a full re-upload may still be required).
    detail: str


class FixConfigChangeRead(ClosedModel):
    """The applied config change, carrying before/after for the operator audit."""

    kind: Literal["mapping_active", "threshold_value"]
    # The record the change now points at: the toggled mapping, or the NEW
    # effective threshold generation.
    target_id: str
    before: JsonObject
    after: JsonObject


# -- staff identity (email+password primary; SSO secondary) -----------------------
type OperatorRole = Literal["developer", "operator_admin", "super_admin"]

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class OperatorLoginRequest(ClosedModel):
    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=1024)


class OperatorIdentityRead(ClosedModel):
    email: str
    display_name: str
    role: OperatorRole


class OperatorLoginRead(ClosedModel):
    access_token: str
    expires_at: datetime
    operator: OperatorIdentityRead


class OperatorUserRead(ClosedModel):
    email: str
    display_name: str
    role: OperatorRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class OperatorUsersListRead(ClosedModel):
    operators: list[OperatorUserRead]


class OperatorUserCreate(ClosedModel):
    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL_PATTERN)
    display_name: str = Field(min_length=1, max_length=255)
    role: OperatorRole


class OperatorUserCreatedRead(ClosedModel):
    operator: OperatorUserRead
    #: Plaintext shown exactly once (the provisioning-saga OTP idiom); only
    #: the Argon2id hash is stored server-side.
    one_time_password: str


class OperatorPasswordResetRead(ClosedModel):
    operator: OperatorUserRead
    #: Plaintext shown exactly once; replaces the previous credential.
    one_time_password: str


# ---------------------------------------------------------------------------
# Regulatory-parameter control plane (docs/sdi.md §7 Phase C)
# ---------------------------------------------------------------------------


class RegulatoryParameterRead(ClosedModel):
    """One effective-dated generation of a regulatory parameter, with provenance."""

    id: UUID
    scope_type: Literal["institution_class", "institution_type"]
    scope_key: str
    param_code: str
    jurisdiction_code: str
    value_numeric: Decimal | None
    value_json: JsonObject | None
    unit: str
    source_citation: str
    confirmation_status: Literal["confirmed", "pending"]
    effective_from: date
    effective_to: date | None
    status: Literal["draft", "approved"]
    proposed_by: str
    approved_by: str | None
    approved_at: datetime | None
    change_rationale: str | None
    created_at: datetime
    updated_at: datetime


class RegulatoryParameterListRead(ClosedModel):
    parameters: list[RegulatoryParameterRead]
    total: int


class RegulatoryParameterProposeRequest(ClosedModel):
    """Maker step: propose a new effective-dated generation (lands as ``draft``)."""

    scope_type: Literal["institution_class", "institution_type"]
    scope_key: str = Field(min_length=1, max_length=40)
    param_code: str = Field(min_length=1, max_length=64)
    # REQUIRED, no default (enterprise audit 2026-08-20 §6): a governed value is
    # scoped BY jurisdiction, so proposing one without naming it filed it as
    # Ghanaian. Both console call sites already send it explicitly.
    jurisdiction_code: str = Field(min_length=1, max_length=8)
    # Regulatory floors/limits/rates/amounts are non-negative; a negative value
    # would silently disable a prudential floor for every tenant of the scope.
    value_numeric: Decimal | None = Field(
        default=None, ge=0, description="The scalar value (e.g. a percentage)."
    )
    # STRUCTURAL governed parameters carry a mapping, not a scalar — the s.29
    # ``sdi_rwa_composition`` (risk class -> measurement) and ``sdi_rwa_bucket_map``
    # are the live examples. The console could READ these from the day they
    # existed but had no way to WRITE one (``propose`` hardcoded ``value_json=None``),
    # so the only governed declaration an SDI's capital ratio waits on was
    # unreachable from the control plane it names (founder review 2026-08-23).
    value_json: JsonObject | None = Field(
        default=None, description="The structural value, for parameters that carry a mapping."
    )
    unit: str = Field(min_length=1, max_length=24)
    source_citation: str = Field(min_length=1, max_length=240)
    confirmation_status: Literal["confirmed", "pending"] = "pending"
    effective_from: date
    change_rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _exactly_one_value(self) -> RegulatoryParameterProposeRequest:
        """A parameter carries a scalar OR a mapping — never both, never neither.

        Both would leave the resolver a choice it has no rule for; neither would
        create a governed row that resolves to nothing.
        """
        has_numeric = self.value_numeric is not None
        has_json = self.value_json is not None
        if has_numeric == has_json:
            msg = (
                "Provide exactly one of value_numeric (a scalar floor/limit/rate) or "
                "value_json (a structural mapping such as sdi_rwa_composition)."
            )
            raise ValueError(msg)
        return self


class RegulatoryParameterApproveRequest(ClosedModel):
    """Checker step: approve a draft (four-eyes — approver must differ from proposer)."""

    change_rationale: str | None = Field(default=None, max_length=500)
