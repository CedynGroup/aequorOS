"""Tenant provisioning saga (docs/internal/developer.md §2, §2a).

``provision_tenant`` creates everything a new bank tenant needs — org, bank,
storage buckets, (optionally) a per-tenant KMS key, the disabled SSO stub,
and the first admin — as an explicit saga: every step records
``succeeded | failed | skipped | rolled_back`` so partial failure never
leaves a half-tenant silently. On any failure the DB transaction rolls back
and freshly-created buckets are deleted (they are empty at that point; when
deletion itself fails the result says so — manual cleanup, named).

Rules this module enforces on purpose:

- NO financial data seeding, ever (standing order 2026-07-21). The tenant is
  born ``empty-but-wired``; it comes alive on its first ingestion.
- ``currency`` and ``jurisdiction_code`` are REQUIRED with no defaults; the
  jurisdiction must exist in the registry. Currency/jurisdiction mismatch is
  a warning, never a silent auto-couple.
- Storage goes through ``provision_institution`` — the single sanctioned
  bucket-creation path — with the institution slug derived from the bank
  platform ID and PERSISTED on ``banks.storage_slug`` so first ingestion
  reuses exactly these buckets instead of minting a second slug.
- The first admin's one-time password appears in the saga RESULT once
  (integration-key precedent); only the Argon2id hash is stored, and the
  audit log never sees the plaintext.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import OperatorSettings, get_operator_settings
from app.db.base import utc_now
from app.models import (
    Bank,
    BankReportingPeriod,
    InstitutionType,
    Jurisdiction,
    Organization,
    SsoConnection,
    TenantStorage,
    User,
)
from app.operator.deps import OperatorContext, record_operator_action
from app.schemas.operator import (
    ProvisioningResultRead,
    ProvisioningStepRead,
    TenantProvisionCreate,
)
from app.storage.client import StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.provisioning import provision_institution

logger = logging.getLogger(__name__)

#: The two redirect URIs bank IT must register at their IdP. Registering only
#: the first yields working sign-in with certification failing at step-up —
#: the documented foot-gun (CLAUDE.md SSO entry; docs/sso-onboarding.md).
SSO_REDIRECT_URI_PATHS: tuple[str, str] = (
    "/api/auth/callback/sso",
    "/api/attestation/step-up/callback",
)

#: Steps whose effects live in the DB transaction (rolled back wholesale).
_DB_STEPS = frozenset({"organization", "bank", "sso_stub", "first_admin"})


@dataclass(frozen=True)
class ProvisioningClients:
    """External clients the saga needs; injectable so tests stay hermetic.

    ``s3_client`` is None when object storage is unconfigured/unavailable —
    the storage step then FAILS with ``unavailable_reason`` (a tenant without
    buckets is not provisioned). ``kms_client`` is None unless
    OPERATOR_AWS_KMS_ENABLED and boto3 could build one.
    """

    s3_client: Any | None
    storage_settings: StorageEngineSettings
    kms_client: Any | None = None
    unavailable_reason: str | None = None


class _SagaAbort(Exception):
    """Internal: a step failed; stop, roll back, clean up."""


@dataclass
class _SagaState:
    steps: list[ProvisioningStepRead] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    organization_id: str | None = None
    bank_id: str | None = None
    created_buckets: list[str] = field(default_factory=list)
    bucket_names: list[str] = field(default_factory=list)
    kms_key_id: str | None = None
    one_time_password: str | None = None

    def record(self, step: str, status_: str, detail: str) -> None:
        self.steps.append(
            ProvisioningStepRead(step=step, status=status_, detail=detail)  # type: ignore[arg-type]
        )

    def fail(self, step: str, detail: str) -> _SagaAbort:
        self.record(step, "failed", detail)
        return _SagaAbort(detail)


def _validate(db: Session, payload: TenantProvisionCreate, state: _SagaState) -> Jurisdiction:
    jurisdiction = db.get(Jurisdiction, payload.jurisdiction_code)
    if jurisdiction is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown jurisdiction_code {payload.jurisdiction_code!r}: not in the "
                "jurisdictions registry. Onboarding a new country is a data exercise "
                "(registry row + parameter pack + return families), not a form field."
            ),
        )
    if db.get(InstitutionType, payload.institution_type) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown institution_type {payload.institution_type!r}: not in the "
                "institution_types registry. The licence class is the typed "
                "discriminator every SDI scoping keys off (docs/sdi.md §1) — it must "
                "resolve to a seeded registry row, not an arbitrary string."
            ),
        )
    if payload.currency != jurisdiction.currency_code:
        state.warnings.append(
            f"currency {payload.currency} differs from {jurisdiction.country_name}'s "
            f"registry currency {jurisdiction.currency_code} — confirm this is deliberate."
        )
    if db.scalar(select(Organization.id).where(Organization.name == payload.organization_name)):
        state.warnings.append(
            f"an organization named {payload.organization_name!r} already exists "
            "(duplicate names are allowed but usually a mistake)."
        )
    if db.scalar(select(Bank.id).where(Bank.name == payload.bank_name)):
        state.warnings.append(
            f"a bank named {payload.bank_name!r} already exists "
            "(duplicate names are allowed but usually a mistake)."
        )
    return jurisdiction


def _step_storage(
    db: Session,
    bank: Bank,
    organization_id: str,
    clients: ProvisioningClients,
    state: _SagaState,
) -> TenantStorage:
    if clients.s3_client is None:
        reason = clients.unavailable_reason or (
            "object storage is not configured (S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY)"
        )
        raise state.fail("storage", f"storage backend unavailable: {reason}")

    # The bank platform ID lowercased is already DNS-safe (Crockford base32 +
    # dash) and unique; persisting it as the bank's storage_slug means first
    # ingestion (services/ingestion.bank_slug) reuses these exact buckets.
    slug = bank.id.lower()
    bank.storage_slug = slug
    db.flush()

    result = provision_institution(clients.s3_client, clients.storage_settings, slug)
    state.created_buckets = list(result.created_buckets)
    state.bucket_names = result.bucket_names

    # Probe: put + GET + delete in the temp tier. GET, not HEAD — the managed
    # MinIO's Cloudflare WAF blocks HEAD (developer.md §2 step 3).
    temp_bucket = StorageLocation(
        institution_slug=slug, tier="temp", object_path=""
    ).bucket_name(clients.storage_settings.env)
    probe_key = f"provisioning-probe/{uuid4().hex}"
    probe_body = b"aequoros provisioning probe"
    clients.s3_client.put_object(Bucket=temp_bucket, Key=probe_key, Body=probe_body)
    fetched = clients.s3_client.get_object(Bucket=temp_bucket, Key=probe_key)["Body"].read()
    clients.s3_client.delete_object(Bucket=temp_bucket, Key=probe_key)
    if fetched != probe_body:
        raise state.fail(
            "storage",
            f"probe object read back different content from {temp_bucket} — "
            "storage backend is not trustworthy for this tenant.",
        )

    registry = TenantStorage(
        organization_id=organization_id,
        bucket_names=result.bucket_names,
        provider="minio",
        provisioned_at=utc_now(),
    )
    db.add(registry)
    db.flush()
    state.record(
        "storage",
        "succeeded",
        f"4 tier buckets ready ({len(result.created_buckets)} created, "
        f"{len(result.existing_buckets)} pre-existing); probe put+get+delete OK; "
        f"slug {slug!r} persisted on the bank.",
    )
    return registry


def _step_kms(
    registry: TenantStorage,
    organization_id: str,
    clients: ProvisioningClients,
    operator_settings: OperatorSettings,
    state: _SagaState,
) -> None:
    if not operator_settings.aws_kms_enabled:
        state.record(
            "kms",
            "skipped",
            "KMS disabled (OPERATOR_AWS_KMS_ENABLED=0) — SSE-KMS not applied",
        )
        return
    if clients.kms_client is None:
        raise state.fail(
            "kms",
            "OPERATOR_AWS_KMS_ENABLED=1 but no KMS client is available — refusing to "
            "report per-tenant encryption that was not applied.",
        )
    key = clients.kms_client.create_key(
        Description=f"AequorOS per-tenant storage key for {organization_id}",
        Tags=[{"TagKey": "aequoros-org", "TagValue": organization_id}],
    )
    key_metadata = key["KeyMetadata"]
    state.kms_key_id = key_metadata["KeyId"]
    clients.kms_client.create_alias(
        AliasName=f"alias/aequoros-{organization_id}",
        TargetKeyId=key_metadata["KeyId"],
    )
    for bucket in registry.bucket_names:
        clients.s3_client.put_bucket_encryption(  # type: ignore[union-attr] - storage step ran first
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": key_metadata["Arn"],
                        }
                    }
                ]
            },
        )
    registry.kms_key_arn = key_metadata["Arn"]
    registry.provider = "aws"
    state.record(
        "kms",
        "succeeded",
        f"per-tenant KMS key created (alias/aequoros-{organization_id}); SSE-KMS set as "
        f"default encryption on {len(registry.bucket_names)} buckets. Offboarding can now "
        "crypto-shred by scheduling key deletion.",
    )


def _step_sso_stub(db: Session, organization_id: str, state: _SagaState) -> None:
    # A disabled row with empty issuer/client_id and no secret is valid at the
    # model layer and inert at the auth layer (only enabled connections are
    # ever matched); bank IT completes it via Settings → Authentication.
    db.add(
        SsoConnection(
            organization_id=organization_id,
            issuer="",
            client_id="",
            allowed_email_domains=[],
            enabled=False,
            jit_enabled=False,
        )
    )
    db.flush()
    state.record(
        "sso_stub",
        "succeeded",
        "disabled OIDC connection stub created; bank IT fills issuer/client id/secret in "
        "Settings → Authentication (docs/sso-onboarding.md). Hand the bank BOTH redirect "
        f"URIs: {SSO_REDIRECT_URI_PATHS[0]} (sign-in) and {SSO_REDIRECT_URI_PATHS[1]} "
        "(signing step-up) — registering only the first breaks certification.",
    )


def _step_first_admin(
    db: Session, payload: TenantProvisionCreate, organization_id: str, state: _SagaState
) -> None:
    one_time_password = secrets.token_urlsafe(24)
    db.add(
        User(
            organization_id=organization_id,
            email=payload.admin_email.lower(),
            display_name=payload.admin_full_name,
            role="admin",
            auth_provider="password",
            is_active=True,
            password_hash=security.hash_password(one_time_password),
        )
    )
    db.flush()
    state.one_time_password = one_time_password
    state.record(
        "first_admin",
        "succeeded",
        f"admin {payload.admin_email.lower()} created (auth_provider=password). The "
        "one-time password is in this result ONLY — it is never stored or logged; "
        "hand it over out-of-band and have the admin change it at first sign-in.",
    )


def _step_readiness(db: Session, organization_id: str, bank_id: str, state: _SagaState) -> None:
    org_ok = (
        db.scalar(select(Organization.id).where(Organization.id == organization_id)) is not None
    )
    bank_ok = (
        db.scalar(
            select(Bank.id).where(
                Bank.id == bank_id, Bank.organization_id == organization_id
            )
        )
        is not None
    )
    if not (org_ok and bank_ok):
        raise state.fail(
            "readiness", "provisioned org/bank rows are not readable back — aborting."
        )
    period_count = db.scalar(
        select(func.count())
        .select_from(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == organization_id,
            BankReportingPeriod.bank_id == bank_id,
        )
    )
    if period_count != 0:
        raise state.fail(
            "readiness",
            f"expected zero reporting periods on a fresh tenant, found {period_count} — "
            "refusing to certify (no seeding path may exist).",
        )
    state.record(
        "readiness",
        "succeeded",
        "org/bank readable; storage probe OK; 0 reporting periods, 0 facts — "
        "empty-but-wired. The bank goes live on its first ingestion.",
    )


def _cleanup_external(clients: ProvisioningClients, state: _SagaState) -> None:
    """Best-effort external cleanup after a failed saga (DB already rolled back)."""
    notes: list[str] = []
    if state.kms_key_id is not None and clients.kms_client is not None:
        try:
            clients.kms_client.schedule_key_deletion(
                KeyId=state.kms_key_id, PendingWindowInDays=7
            )
            notes.append(f"KMS key {state.kms_key_id} scheduled for deletion (7 days)")
        except Exception as exc:  # noqa: BLE001 - cleanup must report, not raise
            notes.append(
                f"MANUAL CLEANUP NEEDED: KMS key {state.kms_key_id} could not be "
                f"scheduled for deletion ({exc})"
            )
    for bucket in state.created_buckets:
        try:
            clients.s3_client.delete_bucket(Bucket=bucket)  # type: ignore[union-attr]
            notes.append(f"deleted bucket {bucket}")
        except Exception as exc:  # noqa: BLE001 - cleanup must report, not raise
            notes.append(
                f"MANUAL CLEANUP NEEDED: bucket {bucket} could not be deleted ({exc})"
            )
    if notes:
        state.record("cleanup", "succeeded", "; ".join(notes))


def _mark_rolled_back(state: _SagaState) -> None:
    rolled: list[ProvisioningStepRead] = []
    for step in state.steps:
        if step.status == "succeeded" and (step.step in _DB_STEPS or step.step == "storage"):
            rolled.append(
                ProvisioningStepRead(
                    step=step.step,
                    status="rolled_back",
                    detail=f"{step.detail} [transaction rolled back]",
                )
            )
        else:
            rolled.append(step)
    state.steps = rolled


def provision_tenant(
    db: Session,
    operator: OperatorContext,
    payload: TenantProvisionCreate,
    clients: ProvisioningClients,
    *,
    operator_settings: OperatorSettings | None = None,
) -> ProvisioningResultRead:
    """Run the onboarding saga. Returns the explicit step-by-step result;
    the HTTP layer always answers 200 with ``succeeded`` telling the truth."""
    operator_settings = operator_settings or get_operator_settings()
    state = _SagaState()
    _validate(db, payload, state)

    try:
        # a. organization
        organization = Organization(name=payload.organization_name)
        db.add(organization)
        db.flush()
        state.organization_id = organization.id
        state.record("organization", "succeeded", f"organization {organization.id} created")

        # b. bank
        bank = Bank(
            organization_id=organization.id,
            name=payload.bank_name,
            short_name=payload.bank_name[:80],
            currency=payload.currency,
            jurisdiction_code=payload.jurisdiction_code,
            license_type=payload.license_type,
            institution_type=payload.institution_type,
        )
        db.add(bank)
        db.flush()
        state.bank_id = bank.id
        state.record(
            "bank",
            "succeeded",
            f"bank {bank.id} created ({payload.jurisdiction_code}, {payload.currency}, "
            f"{payload.license_type}, institution_type={payload.institution_type})",
        )

        # c. storage  d. kms  e. sso stub  f. first admin  g. readiness
        registry = _step_storage(db, bank, organization.id, clients, state)
        _step_kms(registry, organization.id, clients, operator_settings, state)
        _step_sso_stub(db, organization.id, state)
        _step_first_admin(db, payload, organization.id, state)
        _step_readiness(db, organization.id, bank.id, state)
    except _SagaAbort:
        db.rollback()
        _mark_rolled_back(state)
        _cleanup_external(clients, state)
        _audit(db, operator, payload, state, succeeded=False)
        return _result(payload, state, succeeded=False)
    except Exception as exc:  # unexpected — same guarantees, honest detail
        logger.exception("tenant provisioning failed unexpectedly")
        failing_step = _next_step_name(state)
        state.record(failing_step, "failed", f"unexpected error: {exc}")
        db.rollback()
        _mark_rolled_back(state)
        _cleanup_external(clients, state)
        _audit(db, operator, payload, state, succeeded=False)
        return _result(payload, state, succeeded=False)

    _audit(db, operator, payload, state, succeeded=True)
    db.commit()
    return _result(payload, state, succeeded=True)


_STEP_ORDER = ("organization", "bank", "storage", "kms", "sso_stub", "first_admin", "readiness")


def _next_step_name(state: _SagaState) -> str:
    done = {step.step for step in state.steps}
    for name in _STEP_ORDER:
        if name not in done:
            return name
    return "readiness"


def _audit(
    db: Session,
    operator: OperatorContext,
    payload: TenantProvisionCreate,
    state: _SagaState,
    *,
    succeeded: bool,
) -> None:
    """Audit the attempt. On failure the tx was rolled back, so this opens a
    fresh transaction — the failed attempt itself must never disappear.
    NEVER include the one-time password here."""
    record_operator_action(
        db,
        operator,
        action="tenants.provision",
        target_org=state.organization_id,
        detail={
            "succeeded": succeeded,
            "organization_name": payload.organization_name,
            "bank_name": payload.bank_name,
            "bank_id": state.bank_id,
            "jurisdiction_code": payload.jurisdiction_code,
            "currency": payload.currency,
            "institution_type": payload.institution_type,
            "admin_email": payload.admin_email.lower(),
            "steps": [{"step": s.step, "status": s.status} for s in state.steps],
            "warnings": state.warnings,
        },
    )
    if not succeeded:
        db.commit()


def _result(
    payload: TenantProvisionCreate, state: _SagaState, *, succeeded: bool
) -> ProvisioningResultRead:
    return ProvisioningResultRead(
        succeeded=succeeded,
        organization_id=state.organization_id if succeeded else None,
        bank_id=state.bank_id if succeeded else None,
        admin_email=payload.admin_email.lower() if succeeded else None,
        admin_one_time_password=state.one_time_password if succeeded else None,
        sso_redirect_uris=list(SSO_REDIRECT_URI_PATHS),
        steps=state.steps,
        warnings=state.warnings,
    )
