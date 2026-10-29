"""Bank administrator operations; all lookups bind organization and bank."""

import hmac
import secrets

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.key_management import reencryption, registry
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.schemas import BankKeyConnect, BankKeyRead, BankKeyRotate
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.models.bank import Bank
from app.identity.schemas.bank_encryption import (
    BankKeyScheduleConfirm,
    BankKeyStatusRead,
    BankKeyTestRead,
)
from app.services.audit import record_event


def _bank(db: Session, ctx: TenantContext, bank_id: str, *, lock: bool = False) -> Bank:
    if lock:
        registry.lock_bank_version(db, ctx.organization_id, bank_id)
    query = select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    bank = db.scalar(query.with_for_update() if lock else query)
    if bank is None:
        raise HTTPException(404, "Bank not found in this organization.")
    return bank


def status(db: Session, ctx: TenantContext, bank_id: str) -> BankKeyStatusRead:
    _bank(db, ctx, bank_id)
    row = registry.bank_key(db, ctx.organization_id, bank_id)
    result = BankKeyStatusRead(
        connected=row is not None, workload_role_arn=get_key_settings().workload_role_arn
    )
    if row is not None:
        result.key = BankKeyRead(
            bank_id=row.bank_id,
            provider=row.provider,
            key_id=row.key_id,
            region=row.region,
            owner_account=row.owner_account,
            status=KeyStatus(row.status),
            checked_at=row.checked_at,
        )
        result.rotation_period_days = row.rotation_period_days
        result.rotation_confirmed_at = row.rotation_confirmed_at
        result.current_version = row.current_version_id
        result.last_successful_use_at = registry.last_successful_use(db, row)
        result.last_rotated_at = row.last_rotated_at
    return result


def _audit(
    db: Session, ctx: TenantContext, bank_id: str, action: str, details: dict[str, str]
) -> None:
    _ = record_event(
        db,
        ctx,
        event_type=f"bank_key.{action}",
        entity_type="bank",
        entity_id=bank_id,
        details=details,
    )


def test_connection(
    db: Session, ctx: TenantContext, bank_id: str, payload: BankKeyConnect
) -> BankKeyTestRead:
    _bank(db, ctx, bank_id)
    key = payload.reference()
    provider = registry.provider_for(key.provider)
    context = {
        "purpose": "bank-key-connection-test",
        "bank_id": bank_id,
        "organization_id": ctx.organization_id,
    }
    observed = KeyStatus.UNAVAILABLE
    try:
        description = provider.describe(key)
        observed = description.status
        if description.status == KeyStatus.DISABLED:
            raise KeyUnavailableError(
                "The key is disabled or pending deletion. "
                "Enable the key in AWS KMS, then test again."
            )
        if description.status != KeyStatus.ACTIVE:
            raise KeyUnavailableError(
                "The key is unavailable. Verify the key ARN and region, then retry."
            )
        observed = KeyStatus.UNAVAILABLE
        plaintext = secrets.token_bytes(32)
        wrapped = provider.wrap(key, plaintext, context)
        if not hmac.compare_digest(provider.unwrap(key, wrapped, context), plaintext):
            raise KeyUnavailableError(
                "The key failed authentication. Check the key ARN and encryption context, "
                "then test again."
            )
        registry.lock_bank_version(db, ctx.organization_id, bank_id)
        connected = db.scalar(
            select(BankEncryptionKey)
            .where(
                BankEncryptionKey.bank_id == bank_id,
                BankEncryptionKey.organization_id == ctx.organization_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connected is not None and registry.reference(connected) == key:
            registry.initialize_version(db, connected, registry.provider_for)
        passed = True
        observed = KeyStatus.ACTIVE
        message = "Connection passed: aequorOS wrapped and unwrapped a synthetic data key."
    except KeyUnavailableError as exc:
        passed = False
        message = str(exc)
    registry.lock_bank_version(db, ctx.organization_id, bank_id)
    checked_at = utc_now()
    observation = (
        update(BankEncryptionKey)
        .where(
            BankEncryptionKey.organization_id == ctx.organization_id,
            BankEncryptionKey.bank_id == bank_id,
            BankEncryptionKey.provider == key.provider,
            BankEncryptionKey.key_id == key.key_id,
            BankEncryptionKey.region == key.region,
            BankEncryptionKey.owner_account == key.owner_account,
        )
        .values(checked_at=checked_at, status=observed.value)
    )
    if passed:
        observation = observation.values(last_successful_use_at=checked_at)
    _ = db.execute(observation.execution_options(synchronize_session=False))
    details = {"key_id": key.key_id}
    if not passed:
        details["message"] = message
    _audit(db, ctx, bank_id, "tested" if passed else "test_failed", details)
    db.commit()
    return BankKeyTestRead(passed=passed, message=message)


def connect(
    db: Session, ctx: TenantContext, bank_id: str, payload: BankKeyConnect
) -> BankKeyStatusRead:
    bank = _bank(db, ctx, bank_id, lock=True)
    if registry.bank_key(db, ctx.organization_id, bank_id) is not None:
        raise HTTPException(409, "Use rotation to replace the connected key.")
    if bank.storage_slug is None:
        raise HTTPException(
            409,
            "Bank storage must be provisioned before connecting a key. "
            "Contact your aequorOS administrator.",
        )
    try:
        _ = registry.register(
            db,
            bank_id=bank_id,
            organization_id=ctx.organization_id,
            storage_slug=bank.storage_slug,
            key=payload.reference(),
            providers=registry.provider_for,
        )
    except KeyUnavailableError as exc:
        db.rollback()
        raise HTTPException(503, str(exc)) from exc
    _ = reencryption.enqueue(db, ctx.organization_id, bank_id)
    _audit(db, ctx, bank_id, "connected", {"key_id": payload.key_id})
    db.commit()
    return status(db, ctx, bank_id)


def rotate(
    db: Session, ctx: TenantContext, bank_id: str, payload: BankKeyRotate
) -> BankKeyStatusRead:
    _bank(db, ctx, bank_id, lock=True)
    try:
        row = registry.scoped_key(db, bank_id=bank_id, organization_id=ctx.organization_id)
        previous = row.key_id
        _ = registry.rotate(db, row, payload.reference(), registry.provider_for)
    except KeyUnavailableError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    _audit(
        db,
        ctx,
        bank_id,
        "rotated",
        {"key_id": payload.key_id, "old_key_id": previous, "reason": payload.reason},
    )
    db.commit()
    return status(db, ctx, bank_id)


def confirm_schedule(
    db: Session, ctx: TenantContext, bank_id: str, payload: BankKeyScheduleConfirm
) -> BankKeyStatusRead:
    _bank(db, ctx, bank_id, lock=True)
    try:
        row = registry.scoped_key(db, bank_id=bank_id, organization_id=ctx.organization_id)
    except KeyUnavailableError as exc:
        raise HTTPException(409, str(exc)) from exc
    if row.key_id != payload.key_id:
        raise HTTPException(
            409,
            "The connected key changed. Refresh and configure the schedule for the current key.",
        )
    row.rotation_period_days = payload.period_days
    row.rotation_confirmed_at = utc_now()
    _audit(
        db,
        ctx,
        bank_id,
        "schedule_confirmed",
        {
            "key_id": row.key_id,
            "period_days": str(payload.period_days),
            "reason": payload.reason,
            "source": "bank_administrator_report",
        },
    )
    db.commit()
    return status(db, ctx, bank_id)
