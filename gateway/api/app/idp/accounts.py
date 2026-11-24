"""Supervisor accounts of the gateway's own identity provider.

Passwords are held only as Argon2id hashes (RFC 9106 parameters from argon2-cffi).
A wrong password counts against the account; ``idp_max_failed_attempts`` in a row lock
it for ``idp_lockout_minutes``. Every sign-in, successful or refused, is written to the
tamper-evident audit log. Accounts are disabled, never deleted.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.chain import Actor, append
from app.auth.staff import short_name
from app.core.context import AppContext
from app.core.errors import ApiError
from app.db.models import StaffAccount

_HASHER = PasswordHasher()
#: Verified against when the email is unknown, so a miss costs the same as a hit.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 12
SIGN_IN_VIA = "AequorOS Portal sign-in"
IDENTITY_PROVIDER = Actor("system", "identity-provider", "Identity provider", SIGN_IN_VIA)

Outcome = Literal["signed_in", "incorrect", "locked", "disabled"]


@dataclass(frozen=True, slots=True)
class SignInResult:
    outcome: Outcome
    account: StaffAccount | None = None


def normalise_email(email: str) -> str:
    return email.strip().lower()


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(
            "invalid_request", f"A password needs at least {MIN_PASSWORD_LENGTH} characters."
        )
    classes = sum(
        bool(re.search(pattern, password)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]")
    )
    if classes < 3:
        raise ApiError(
            "invalid_request",
            "A password needs three of: lower-case letters, upper-case letters, digits, symbols.",
        )


def account_by_email(session: Session, email: str) -> StaffAccount | None:
    return session.scalars(
        select(StaffAccount).where(func.lower(StaffAccount.email) == normalise_email(email))
    ).first()


def _actor(account: StaffAccount) -> Actor:
    return Actor(
        "staff",
        account.subject,
        short_name(account.name, account.given_name, account.family_name),
        SIGN_IN_VIA,
    )


def create_account(
    session: Session,
    ctx: AppContext,
    *,
    email: str,
    name: str,
    given_name: str | None,
    family_name: str | None,
    groups: list[str],
    password: str,
    created_by: str,
) -> StaffAccount:
    address = normalise_email(email)
    if not _EMAIL.match(address):
        raise ApiError("invalid_request", f"{email!r} is not an email address.")
    unknown = sorted(set(groups) - set(ctx.settings.group_roles))
    if not groups or unknown:
        raise ApiError(
            "invalid_request",
            "Groups must be among: " + ", ".join(sorted(ctx.settings.group_roles)) + ".",
        )
    check_password_policy(password)
    if account_by_email(session, address) is not None:
        raise ApiError("conflict", f"An account for {address} already exists.")
    now = ctx.clock.now()
    account = StaffAccount(
        subject="stf-" + secrets.token_hex(8),
        email=address,
        name=name.strip(),
        given_name=(given_name or "").strip() or None,
        family_name=(family_name or "").strip() or None,
        groups=sorted(set(groups)),
        password_hash=_HASHER.hash(password),
        status="active",
        failed_attempts=0,
        locked_until=None,
        password_changed_at=now,
        last_sign_in_at=None,
        created_at=now,
        created_by=created_by,
    )
    session.add(account)
    session.flush()
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=IDENTITY_PROVIDER,
        category="credential",
        action="Created staff account",
        object_type="staff_account",
        object_ref=account.subject,
        detail={"email": address, "groups": list(account.groups), "created_by": created_by},
    )
    return account


def set_status(
    session: Session, ctx: AppContext, account: StaffAccount, status: str, *, by: str
) -> None:
    if status not in ("active", "disabled"):
        raise ApiError("invalid_request", "Status is active or disabled.")
    account.status = status
    account.failed_attempts = 0
    account.locked_until = None
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=IDENTITY_PROVIDER,
        category="credential",
        action="Enabled staff account" if status == "active" else "Disabled staff account",
        object_type="staff_account",
        object_ref=account.subject,
        detail={"email": account.email, "by": by},
    )


def set_password(
    session: Session, ctx: AppContext, account: StaffAccount, password: str, *, by: str
) -> None:
    check_password_policy(password)
    account.password_hash = _HASHER.hash(password)
    account.password_changed_at = ctx.clock.now()
    account.failed_attempts = 0
    account.locked_until = None
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=IDENTITY_PROVIDER,
        category="credential",
        action="Set staff account password",
        object_type="staff_account",
        object_ref=account.subject,
        detail={"email": account.email, "by": by},
    )


def _verify(stored: str, password: str) -> bool:
    try:
        return _HASHER.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def sign_in(session: Session, ctx: AppContext, email: str, password: str) -> SignInResult:
    """Check one sign-in attempt and record it. The caller commits."""
    settings = ctx.settings
    now = ctx.clock.now()
    account = account_by_email(session, email)
    if account is None:
        _ = _verify(_DUMMY_HASH, password)
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=IDENTITY_PROVIDER,
            category="sign_in",
            action="Sign-in refused",
            object_type="staff_account",
            object_ref=normalise_email(email)[:120] or "(blank)",
            detail={"reason": "unknown_account"},
        )
        return SignInResult("incorrect")
    if account.locked_until is not None and account.locked_until > now:
        _ = _verify(_DUMMY_HASH, password)
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=_actor(account),
            category="sign_in",
            action="Sign-in refused",
            object_type="staff_account",
            object_ref=account.subject,
            detail={"reason": "locked"},
        )
        return SignInResult("locked", account)
    if not _verify(account.password_hash, password):
        account.failed_attempts += 1
        locked = account.failed_attempts >= settings.idp_max_failed_attempts
        if locked:
            account.locked_until = now + timedelta(minutes=settings.idp_lockout_minutes)
            account.failed_attempts = 0
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=_actor(account),
            category="sign_in",
            action="Account locked" if locked else "Sign-in refused",
            object_type="staff_account",
            object_ref=account.subject,
            detail={"reason": "incorrect_password", "locked": locked},
        )
        return SignInResult("locked" if locked else "incorrect", account)
    if account.status != "active":
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=_actor(account),
            category="sign_in",
            action="Sign-in refused",
            object_type="staff_account",
            object_ref=account.subject,
            detail={"reason": "disabled"},
        )
        return SignInResult("disabled", account)
    if _HASHER.check_needs_rehash(account.password_hash):
        account.password_hash = _HASHER.hash(password)
    account.failed_attempts = 0
    account.locked_until = None
    account.last_sign_in_at = now
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=_actor(account),
        category="sign_in",
        action="Signed in",
        object_type="staff_account",
        object_ref=account.subject,
        detail={"amr": ["pwd"]},
    )
    return SignInResult("signed_in", account)
