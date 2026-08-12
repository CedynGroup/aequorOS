"""Operator account management (/operator/v1/operators) — operator_admin only.

Staff accounts follow the client model's provisioning shape: an admin creates
the account and hands over a generated ONE-TIME password (shown exactly once,
the tenant provisioning-saga idiom — only the Argon2id hash is stored), the
operator signs in with it. No self-serve signup, no committed seed.

Bootstrap: locally, dev-token sessions carry ``operator_admin`` (dev auth is
hard-refused in production), so the first operator is created straight from
the console/curl. In production the bootstrap is ``scripts/create_operator.py``
run inside the backend container — see that script's docstring.

Every mutation lands an ``operator_audit_log`` row in the same transaction.
One-time passwords NEVER appear in audit detail (append-only, staff-readable).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core import security
from app.models import OperatorUser
from app.models.operator import OPERATOR_ROLE_RANK
from app.operator.deps import OperatorAdmin, OperatorDb, record_operator_action
from app.schemas.operator import (
    OperatorPasswordResetRead,
    OperatorUserCreate,
    OperatorUserCreatedRead,
    OperatorUserRead,
    OperatorUsersListRead,
)

router = APIRouter(prefix="/operators", tags=["operator-accounts"])


def _read(user: OperatorUser) -> OperatorUserRead:
    return OperatorUserRead.model_validate(
        {
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
            "last_login_at": user.last_login_at,
            "created_at": user.created_at,
        }
    )


def _get_or_404(db: OperatorDb, email: str) -> OperatorUser:
    user = db.scalar(select(OperatorUser).where(OperatorUser.email == email.lower()))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No operator account for {email.lower()!r}.",
        )
    return user


def _one_time_password() -> str:
    # The tenant provisioning-saga idiom (tenant_provisioning.py): a strong
    # generated credential, returned once, stored only as its hash.
    return secrets.token_urlsafe(24)


def _require_rank(admin: object, target_role: str, action: str) -> None:
    """Hierarchy rule: an admin may only manage accounts AT OR BELOW their
    own rank (developer < operator_admin < super_admin) — so touching a
    super_admin account, or minting one, requires a super_admin."""
    admin_role = getattr(admin, "role", "developer")
    if OPERATOR_ROLE_RANK[admin_role] < OPERATOR_ROLE_RANK[target_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{action} a {target_role!r} account requires a role of at "
            f"least {target_role!r} (you are {admin_role!r}).",
        )


@router.get("", response_model=OperatorUsersListRead)
def list_operators(db: OperatorDb, _admin: OperatorAdmin) -> OperatorUsersListRead:
    users = db.scalars(select(OperatorUser).order_by(OperatorUser.email)).all()
    return OperatorUsersListRead(operators=[_read(user) for user in users])


@router.post("", response_model=OperatorUserCreatedRead, status_code=status.HTTP_201_CREATED)
def create_operator(
    payload: OperatorUserCreate, db: OperatorDb, admin: OperatorAdmin
) -> OperatorUserCreatedRead:
    email = payload.email.strip().lower()
    if db.scalar(select(OperatorUser.id).where(OperatorUser.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An operator account for {email!r} already exists.",
        )
    _require_rank(admin, payload.role, "Creating")
    one_time_password = _one_time_password()
    user = OperatorUser(
        email=email,
        display_name=payload.display_name.strip(),
        role=payload.role,
        password_hash=security.hash_password(one_time_password),
        is_active=True,
    )
    db.add(user)
    record_operator_action(
        db,
        admin,
        action="operators.create",
        detail={"email": email, "role": payload.role},
    )
    db.commit()
    db.refresh(user)
    return OperatorUserCreatedRead(operator=_read(user), one_time_password=one_time_password)


@router.post("/{email}/reset-password", response_model=OperatorPasswordResetRead)
def reset_operator_password(
    email: str, db: OperatorDb, admin: OperatorAdmin
) -> OperatorPasswordResetRead:
    user = _get_or_404(db, email)
    _require_rank(admin, user.role, "Resetting the password of")
    one_time_password = _one_time_password()
    user.password_hash = security.hash_password(one_time_password)
    record_operator_action(
        db,
        admin,
        action="operators.reset_password",
        detail={"email": user.email},
    )
    db.commit()
    db.refresh(user)
    return OperatorPasswordResetRead(operator=_read(user), one_time_password=one_time_password)


@router.post("/{email}/deactivate", response_model=OperatorUserRead)
def deactivate_operator(email: str, db: OperatorDb, admin: OperatorAdmin) -> OperatorUserRead:
    user = _get_or_404(db, email)
    _require_rank(admin, user.role, "Deactivating")
    if user.email == admin.email.lower():
        # Refusing self-deactivation keeps at least the acting admin able to
        # undo mistakes — the last-admin lockout foot-gun.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot deactivate your own operator account.",
        )
    user.is_active = False
    record_operator_action(
        db,
        admin,
        action="operators.deactivate",
        detail={"email": user.email},
    )
    db.commit()
    db.refresh(user)
    return _read(user)


@router.post("/{email}/reactivate", response_model=OperatorUserRead)
def reactivate_operator(email: str, db: OperatorDb, admin: OperatorAdmin) -> OperatorUserRead:
    user = _get_or_404(db, email)
    _require_rank(admin, user.role, "Reactivating")
    user.is_active = True
    record_operator_action(
        db,
        admin,
        action="operators.reactivate",
        detail={"email": user.email},
    )
    db.commit()
    db.refresh(user)
    return _read(user)
