"""Integration keys: generate-once, revocable API credentials for bank IT.

An admin issues a key from the API Push console; the platform creates a
dedicated SERVICE ACCOUNT (a machine user with the ``analyst`` role — enough
to push data, nothing more) and returns the raw key exactly once. Only the
SHA-256 hash is persisted. Middleware sends the key as a plain bearer value;
authentication resolves the hash to the service account and the request then
flows through the exact same tenant-context and role checks as any user.

Revocation is immediate (key row stamped + service account deactivated) and
issuance/revocation are audited. Rotation = issue a second key, switch the
middleware, revoke the first.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.security import utc_now
from app.models import IntegrationKey, User
from app.schemas.integration_keys import (
    IntegrationKeyIssued,
    IntegrationKeyListRead,
    IntegrationKeyRead,
)
from app.services.audit import record_event

KEY_PREFIX = "aeq_live_"
_KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
_KEY_LENGTH = 40
_LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)
_MAX_ACTIVE_KEYS = 10


def _generate_key() -> str:
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))
    return f"{KEY_PREFIX}{body}"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def looks_like_integration_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


def _read(key: IntegrationKey) -> IntegrationKeyRead:
    return IntegrationKeyRead(
        id=key.id,
        label=key.label,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        created_by=key.created_by,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def issue_key(db: Session, ctx: TenantContext, label: str) -> IntegrationKeyIssued:
    active = db.scalars(
        select(IntegrationKey).where(
            IntegrationKey.organization_id == ctx.organization_id,
            IntegrationKey.revoked_at.is_(None),
        )
    ).all()
    if len(active) >= _MAX_ACTIVE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Active key limit reached ({_MAX_ACTIVE_KEYS}). Revoke unused keys first.",
        )

    raw = _generate_key()
    service_user = User(
        organization_id=ctx.organization_id,
        email=f"integration.{secrets.token_hex(6)}@service.aequoros.invalid",
        display_name=f"Integration — {label}",
        role="analyst",
        auth_provider="service",
        is_active=True,
    )
    db.add(service_user)
    db.flush()
    key = IntegrationKey(
        organization_id=ctx.organization_id,
        service_user_id=service_user.id,
        label=label,
        key_prefix=raw[: len(KEY_PREFIX) + 4] + "…",
        key_hash=hash_key(raw),
        created_by=ctx.actor_user_id,
    )
    db.add(key)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="integration_key.issued",
        entity_type="integration_key",
        entity_id=key.id,
        details={"label": label, "service_user_id": str(service_user.id)},
    )
    db.commit()
    return IntegrationKeyIssued(key=raw, record=_read(key))


def list_keys(db: Session, ctx: TenantContext) -> IntegrationKeyListRead:
    keys = db.scalars(
        select(IntegrationKey)
        .where(IntegrationKey.organization_id == ctx.organization_id)
        .order_by(IntegrationKey.created_at.desc())
    ).all()
    return IntegrationKeyListRead(keys=[_read(key) for key in keys])


def revoke_key(db: Session, ctx: TenantContext, key_id: UUID, reason: str) -> IntegrationKeyRead:
    key = db.scalar(
        select(IntegrationKey).where(
            IntegrationKey.id == key_id,
            IntegrationKey.organization_id == ctx.organization_id,
        )
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found.")
    if key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Key is already revoked."
        )
    key.revoked_at = utc_now()
    service_user = db.get(User, key.service_user_id)
    if service_user is not None:
        service_user.is_active = False
    record_event(
        db,
        ctx,
        event_type="integration_key.revoked",
        entity_type="integration_key",
        entity_id=key.id,
        details={"label": key.label, "reason": reason},
    )
    db.commit()
    return _read(key)


def authenticate_key(db: Session, raw: str) -> TenantContext:
    """Resolve a raw integration key to a tenant principal.

    Pre-auth path: the key-hash lookup is global (integration_keys is not
    RLS-forced — hashes and metadata only); the returned context then goes
    through the same validate_tenant_context as every request, which
    re-checks the service account exists and is active under RLS.
    """
    key = db.scalar(
        select(IntegrationKey).where(IntegrationKey.key_hash == hash_key(raw))
    )
    if key is None or key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked integration key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = utc_now()
    last_used = key.last_used_at
    if last_used is not None and last_used.tzinfo is None:  # sqlite returns naive
        last_used = last_used.replace(tzinfo=UTC)
    if last_used is None or now - last_used > _LAST_USED_WRITE_INTERVAL:
        key.last_used_at = now
        db.commit()
    return TenantContext(
        organization_id=key.organization_id,
        actor_user_id=key.service_user_id,
        roles=("analyst",),
    )
