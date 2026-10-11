"""Bank-scoped integration keys for middleware machine principals.

Issuance creates one service identity, one generate-once credential, and one
exact machine binding in a single transaction. The raw key is returned once and
only its SHA-256 hash is persisted. Authentication proves the credential; each
machine route separately requires the complete binding before any side effect.

Only data push (``writer``) credentials may be issued. Analytics reader
issuance is retired pending a governed BI delegation flow. Purpose is derived
from the machine binding; retired or unrecognized bundles report no purpose.

Revocation stamps the credential, revokes every machine binding for its
dedicated service identity — every bundle, not a named one — deactivates that
identity, and invalidates its authorization state in one transaction.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.security import utc_now
from app.core.tenancy import TenantContext
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.integration_key import IntegrationKey
from app.identity.models.user import User
from app.identity.schemas.integration_keys import (
    WRITER_PURPOSE,
    IntegrationKeyIssued,
    IntegrationKeyListRead,
    IntegrationKeyPurpose,
    IntegrationKeyRead,
)
from app.identity.service import authorization
from app.services.audit import record_event
from app.services.public_ids import normalize_public_id

KEY_PREFIX = "aeq_live_"
# Letters + digits minus visually ambiguous characters (0/O, 1/l/I, U/V-adjacent
# confusables are kept simple: drop 0O1lIoU). Built programmatically — a long
# literal here trips secret scanners' entropy rules.
_KEY_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lIoU")
_KEY_LENGTH = 40
_LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)
_MAX_ACTIVE_KEYS = 10

PURPOSE_BY_BUNDLE: dict[str, IntegrationKeyPurpose] = {
    RoleBundle.INTEGRATION_WRITER.value: WRITER_PURPOSE,
}


def _generate_key() -> str:
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))
    return f"{KEY_PREFIX}{body}"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def looks_like_integration_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


@dataclass(frozen=True, slots=True)
class _Authority:
    """What a key's dedicated service identity actually holds."""

    purpose: IntegrationKeyPurpose
    data_scope_kind: DataScope | None
    data_scope_values: tuple[str, ...]


def _authority(binding: AuthorizationBinding) -> _Authority | None:
    """One machine binding as the purpose and slice a listing reports.

    A bundle this mapping does not know returns ``None`` rather than a guess: a
    new machine bundle must be named here deliberately, and until it is, a key
    holding it reports no purpose instead of the wrong one.
    """

    purpose = PURPOSE_BY_BUNDLE.get(binding.role_bundle)
    if purpose is None:
        return None
    return _Authority(purpose=purpose, data_scope_kind=None, data_scope_values=())


def _authorities(
    db: Session, organization_id: str, service_user_ids: Sequence[UUID]
) -> dict[UUID, _Authority]:
    """The authority each service identity holds, in one org-scoped query.

    ``integration_keys`` is deliberately NOT RLS-forced (the pre-auth global hash
    lookup needs to see every row), so every lifecycle read has to carry its own
    organization predicate. This one carries it too, on the BINDING side, even
    though ``authorization_bindings`` is RLS-forced: the list it is keyed by came
    from the un-forced table, and a query that depends on someone else's
    predicate is a query that breaks when that predicate moves.
    """

    if not service_user_ids:
        return {}
    rows = db.scalars(
        select(AuthorizationBinding)
        .where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.principal_user_id.in_(set(service_user_ids)),
            AuthorizationBinding.principal_type == PrincipalType.MACHINE.value,
        )
        .order_by(AuthorizationBinding.created_at)
    ).all()
    found: dict[UUID, _Authority] = {}
    for binding in rows:
        authority = _authority(binding)
        if authority is not None:
            # Last write wins: issuance creates exactly one machine binding per
            # identity, so a second row can only be a re-grant, and the newest is
            # the one the evaluator would match.
            found[binding.principal_user_id] = authority
    return found


def _read(key: IntegrationKey, authority: _Authority | None) -> IntegrationKeyRead:
    return IntegrationKeyRead(
        id=key.id,
        bank_id=key.bank_id,
        label=key.label,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        created_by=key.created_by,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        purpose=None if authority is None else authority.purpose,
        data_scope_kind=None if authority is None else authority.data_scope_kind,
        data_scope_values=[] if authority is None else list(authority.data_scope_values),
    )


def issue_key(  # noqa: PLR0913 - one credential, its target and its whole scope
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    label: str,
    *,
    purpose: IntegrationKeyPurpose = WRITER_PURPOSE,
    data_scope: DataScope = DataScope.ALL,
    data_scope_values: Sequence[str] = (),
) -> IntegrationKeyIssued:
    """Create a data push identity, credential and exact binding atomically."""
    # Enforce this at the service boundary too, including calls bypassing Pydantic.
    if purpose != WRITER_PURPOSE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only data push keys may be issued.",
        )
    normalized_bank_id = normalize_public_id(bank_id)
    bank = db.scalar(
        select(Bank).where(
            Bank.id == normalized_bank_id,
            Bank.organization_id == ctx.organization_id,
        )
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    active = db.scalar(
        select(func.count(IntegrationKey.id)).where(
            IntegrationKey.organization_id == ctx.organization_id,
            IntegrationKey.bank_id == bank.id,
            IntegrationKey.revoked_at.is_(None),
        )
    )
    if active is not None and active >= _MAX_ACTIVE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Active key limit reached for {bank.id} ({_MAX_ACTIVE_KEYS}). "
                "Revoke unused keys first."
            ),
        )
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Integration keys must be issued by a human account administrator.",
        )

    if data_scope is not DataScope.ALL or tuple(data_scope_values):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A data push key writes rather than reads, so it cannot be limited "
                "to selected branches or regions."
            ),
        )
    raw = _generate_key()
    service_user = User(
        organization_id=ctx.organization_id,
        email=f"integration.{secrets.token_hex(6)}@service.aequoros.invalid",
        display_name=f"Integration — {label}",
        # Machine authority comes only from its one machine binding. Keeping the
        # scalar role non-operational prevents this credential from entering
        # legacy Analyst mutation surfaces during the wider cutover.
        role="viewer",
        auth_provider="service",
        is_active=True,
    )
    db.add(service_user)
    db.flush()
    key = IntegrationKey(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        service_user_id=service_user.id,
        label=label,
        key_prefix=raw[: len(KEY_PREFIX) + 4] + "…",
        key_hash=hash_key(raw),
        created_by=ctx.actor_user_id,
    )
    db.add(key)
    db.flush()
    binding = authorization.create_role_binding(
        db,
        organization_id=ctx.organization_id,
        principal_user_id=service_user.id,
        principal_type=PrincipalType.MACHINE,
        role_bundle=RoleBundle.INTEGRATION_WRITER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            bank.id,
            ModuleScope.DATA,
            SensitivityScope.RESTRICTED,
            data_scope,
            tuple(data_scope_values),
        ),
        grantor=authorization.GrantorRef(
            GrantorType.TENANT_USER,
            str(ctx.actor_user_id),
        ),
        reason=f"Integration key issued for {bank.id} ({purpose}): {label}",
        commit=False,
    )
    record_event(
        db,
        ctx,
        event_type="integration_key.issued",
        entity_type="integration_key",
        entity_id=key.id,
        details={
            "label": label,
            "bank_id": bank.id,
            "purpose": purpose,
            "role_bundle": RoleBundle.INTEGRATION_WRITER.value,
            "data_scope_kind": data_scope.value,
            "data_scope_values": list(data_scope_values),
            "service_user_id": str(service_user.id),
            "authorization_binding_id": str(binding.id),
        },
    )
    db.commit()
    return IntegrationKeyIssued(key=raw, record=_read(key, _authority(binding)))


def list_keys(db: Session, ctx: TenantContext) -> IntegrationKeyListRead:
    keys = db.scalars(
        select(IntegrationKey)
        .where(IntegrationKey.organization_id == ctx.organization_id)
        .order_by(IntegrationKey.created_at.desc())
    ).all()
    authorities = _authorities(
        db, ctx.organization_id, [key.service_user_id for key in keys if key.service_user_id]
    )
    return IntegrationKeyListRead(
        keys=[_read(key, authorities.get(key.service_user_id)) for key in keys]
    )


def revoke_key(db: Session, ctx: TenantContext, key_id: UUID, reason: str) -> IntegrationKeyRead:
    key = db.scalar(
        select(IntegrationKey)
        .where(
            IntegrationKey.id == key_id,
            IntegrationKey.organization_id == ctx.organization_id,
        )
        .with_for_update()
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found.")
    if key.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Key is already revoked.")
    moment = utc_now()
    service_user = db.scalar(
        select(User)
        .where(
            User.id == key.service_user_id,
            User.organization_id == ctx.organization_id,
        )
        .with_for_update(key_share=True)
    )
    if service_user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The key's machine identity is unavailable.",
        )
    # Revoke every machine binding of the dedicated identity, including retired
    # bundles. Revocation must end all authority, regardless of the key's purpose.
    bindings = list(
        db.scalars(
            select(AuthorizationBinding)
            .where(
                AuthorizationBinding.organization_id == ctx.organization_id,
                AuthorizationBinding.principal_user_id == key.service_user_id,
                AuthorizationBinding.principal_type == PrincipalType.MACHINE.value,
                AuthorizationBinding.status != BindingStatus.REVOKED.value,
            )
            .with_for_update()
        )
    )
    key.revoked_at = moment
    revoked_binding_ids: list[str] = []
    for binding in bindings:
        binding.status = BindingStatus.REVOKED.value
        binding.revoked_at = moment
        binding.revoked_by_type = GrantorType.TENANT_USER.value
        binding.revoked_by_id = str(ctx.actor_user_id)
        binding.revoked_reason = reason
        revoked_binding_ids.append(str(binding.id))
    service_user.is_active = False
    authorization.invalidate_user_authorization(
        db,
        organization_id=ctx.organization_id,
        user_id=service_user.id,
        reason=f"integration key revoked: {reason}",
        commit=False,
        locked_user=service_user,
    )
    authority = _authorities(db, ctx.organization_id, [key.service_user_id]).get(
        key.service_user_id
    )
    record_event(
        db,
        ctx,
        event_type="integration_key.revoked",
        entity_type="integration_key",
        entity_id=key.id,
        details={
            "label": key.label,
            "bank_id": key.bank_id,
            "purpose": None if authority is None else authority.purpose,
            "service_user_id": str(service_user.id),
            "authorization_binding_ids": revoked_binding_ids,
            "revoked_role_bundles": sorted({binding.role_bundle for binding in bindings}),
            "reason": reason,
        },
    )
    db.commit()
    return _read(key, authority)


def authenticate_key(db: Session, raw: str) -> TenantContext:
    """Resolve a raw integration key to a tenant principal.

    Pre-auth path: the key-hash lookup is global (integration_keys is not
    RLS-forced — hashes and metadata only); the returned context then goes
    through the same validate_tenant_context as every request, which
    re-checks the service account exists and is active under RLS.
    """
    key = db.scalar(select(IntegrationKey).where(IntegrationKey.key_hash == hash_key(raw)))
    if key is None or key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked integration key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TenantContext(
        organization_id=key.organization_id,
        actor_user_id=key.service_user_id,
        roles=(),
        integration_key_id=key.id,
        integration_key_bank_id=key.bank_id,
    )


def lock_authenticated_key(db: Session, ctx: TenantContext) -> IntegrationKey:
    """Lock the active credential for authorization and the following push."""

    if (
        ctx.integration_key_id is None
        or ctx.integration_key_bank_id is None
        or ctx.actor_user_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked integration key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key = db.scalar(
        select(IntegrationKey)
        .where(
            IntegrationKey.id == ctx.integration_key_id,
            IntegrationKey.organization_id == ctx.organization_id,
            IntegrationKey.bank_id == ctx.integration_key_bank_id,
            IntegrationKey.service_user_id == ctx.actor_user_id,
            IntegrationKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if key is None:
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
        db.flush()
    return key
