"""Emergency-only staff sign-in: credential issuance, throttle, session, audit.

Break-glass is the answer to one scenario: the workforce IdP (Google
Workspace) is unreachable, or a staff account was suspended in error, and the
operator console would otherwise be unreachable. It is deliberately NOT a
second password:

* a **separate credential** (``operator_break_glass_credentials``) bound to an
  explicitly designated ``operator_users`` row — never a flag that widens every
  staff account;
* **off by default** at the API (``OPERATOR_BREAK_GLASS_ENABLED``) and absent
  until a credential is issued;
* a **short fixed session** (15 minutes, no refresh), minted with its own
  ``typ`` claim so an ordinary operator JWT can never pass as break-glass and
  vice versa;
* the **row's role and nothing more** — the credential grants no role of its
  own, and a disabled credential or deactivated row refuses immediately, even
  for a token already minted;
* a **durable per-credential lockout** on the tenant plane's own primitive
  (``app/identity/service/auth_throttle.py``), fronted by the process-local
  per-email guard that keeps unknown addresses indistinguishable;
* **every attempt audited and alerted** — ``operator_audit_log`` rows with
  ``auth_mode='break_glass'`` plus the ``auth.break_glass`` structured
  condition (see ``docs/rbac.md`` §11.3 for the operations handoff).

Ordinary staff password and Google sign-in are untouched: this module adds a
third, narrower door beside them.
"""

from __future__ import annotations

import datetime as dt
import secrets
import threading
from typing import Any, TypedDict, cast
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.observability import operator_break_glass
from app.db.base import utc_now
from app.identity import public as auth_throttle
from app.models import OperatorAuditLog, OperatorBreakGlassCredential, OperatorUser

#: Emergency sessions are deliberately short and never refresh. Fifteen minutes
#: is enough to restore the console and rotate the broken path; it is not a
#: working session.
BREAK_GLASS_TOKEN_TTL_SECONDS = 15 * 60

#: Consecutive failures per email before the process-local pre-lookup guard
#: trips, and for how long. Mirrors ``operator_auth`` so an address with no
#: credential row is throttled exactly like a designated one.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 5 * 60

BREAK_GLASS_SIGN_IN_ACTION = "auth.break_glass.sign_in"
BREAK_GLASS_DENIED_ACTION = "auth.break_glass.denied"

_TOKEN_TYP = "operator_break_glass"


class BreakGlassClaims(TypedDict):
    """A verified emergency session's claims (validated by :func:`verify_token`)."""

    sub: str
    role: str
    typ: str
    bg: str
    iat: int
    exp: int
    credential_id: str


# -- the code --------------------------------------------------------------------
def generate_code() -> str:
    """A high-entropy emergency code, shown once and stored only as a hash."""
    return secrets.token_urlsafe(32)


# A real Argon2id hash of a random unguessable string, so an unknown email or an
# undesignated row burns the same work as a real verification.
_DUMMY_HASH = security.hash_password("aequoros-break-glass-timing-equalizer")


def burn_code_check(code: str) -> None:
    """Spend one Argon2id verification without revealing anything."""
    security.verify_password(code, _DUMMY_HASH)


# -- login throttle, layer 1: process-local, unknown-principal guard --------------
_throttle_lock = threading.Lock()
#: email -> (consecutive_failures, locked_until | None)
_failures: dict[str, tuple[int, dt.datetime | None]] = {}


def throttle_locked(email: str, now: dt.datetime | None = None) -> bool:
    moment = now or utc_now()
    with _throttle_lock:
        entry = _failures.get(email)
        if entry is None:
            return False
        _count, locked_until = entry
        if locked_until is None or locked_until <= moment:
            if locked_until is not None:
                del _failures[email]
            return False
        return True


def record_login_failure(email: str, now: dt.datetime | None = None) -> None:
    moment = now or utc_now()
    with _throttle_lock:
        count, locked_until = _failures.get(email, (0, None))
        count += 1
        if count >= MAX_FAILED_ATTEMPTS:
            locked_until = moment + dt.timedelta(seconds=LOCKOUT_SECONDS)
        _failures[email] = (count, locked_until)


def clear_login_failures(email: str) -> None:
    with _throttle_lock:
        _failures.pop(email, None)


def reset_login_throttle() -> None:
    """Test hook: forget the process-local layer (the durable one is in the DB)."""
    with _throttle_lock:
        _failures.clear()


# -- login throttle, layer 2: the durable per-credential lockout ------------------
def account_lock_expiry(
    credential: OperatorBreakGlassCredential, *, now: dt.datetime | None = None
) -> dt.datetime | None:
    """When this emergency credential's lockout ends, or ``None`` if unlocked."""
    return auth_throttle.lock_expiry(credential, now=now)


def record_credential_failure(
    db: Session, credential: OperatorBreakGlassCredential, *, now: dt.datetime | None = None
) -> dt.datetime | None:
    """Count one wrong code against the CREDENTIAL row itself; commits.

    Separate from the staff row's password throttle on purpose: guessing at the
    emergency door must not lock the account out of its ordinary sign-in.
    """
    return auth_throttle.record_failure_for(
        db,
        credential,
        model=OperatorBreakGlassCredential,
        identity=(OperatorBreakGlassCredential.id == credential.id,),
        anomaly_fields={
            "plane": "operator",
            "surface": "break_glass",
            "operator_user_id": str(credential.operator_user_id),
            "credential_id": str(credential.id),
        },
        settings=get_settings().auth,
        now=now,
    )


def clear_credential_failures(credential: OperatorBreakGlassCredential) -> None:
    auth_throttle.clear_failures(credential)


def lockout_minutes_remaining(expiry: dt.datetime, *, now: dt.datetime | None = None) -> int:
    return auth_throttle.minutes_remaining(expiry, now=now)


# -- session tokens ---------------------------------------------------------------
def mint_token(
    *,
    email: str,
    role: str,
    credential_id: UUID,
    secret: str,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    """Sign a short break-glass session; returns ``(token, expires_at)``.

    The distinct ``typ`` claim is load-bearing: it keeps the ordinary operator
    session verifier and this one mutually exclusive.
    """
    moment = now or utc_now()
    expires_at = moment + dt.timedelta(seconds=BREAK_GLASS_TOKEN_TTL_SECONDS)
    payload = {
        "sub": email,
        "role": role,
        "typ": _TOKEN_TYP,
        "bg": str(credential_id),
        "iat": int(moment.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256"), expires_at


def verify_token(
    token: str, *, secret: str, now: dt.datetime | None = None
) -> BreakGlassClaims:
    """Verify signature, typ, claims and the fixed lifetime cap.

    Raises :class:`app.core.security.TokenInvalidError` on ANY failure — the
    caller collapses it into the generic 401. The lifetime check is what makes
    "short, no refresh" true regardless of who holds the signing secret.
    """
    try:
        decoded: Any = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "bg"]},
        )
    except jwt.PyJWTError as exc:
        raise security.TokenInvalidError(str(exc)) from exc
    raw = cast("dict[str, object]", decoded)
    if raw.get("typ") != _TOKEN_TYP:
        msg = f"expected a break-glass token, got typ={raw.get('typ')!r}"
        raise security.TokenInvalidError(msg)
    subject = raw.get("sub")
    credential = raw.get("bg")
    role = raw.get("role")
    issued_at = raw.get("iat")
    expires_at = raw.get("exp")
    if not isinstance(subject, str) or not subject:
        raise security.TokenInvalidError("break-glass token carries no subject email")
    if not isinstance(credential, str) or not credential:
        raise security.TokenInvalidError("break-glass token carries no credential id")
    if not isinstance(role, str) or not role:
        raise security.TokenInvalidError("break-glass token carries no role")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise security.TokenInvalidError("break-glass token lifetimes are not integers")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise security.TokenInvalidError("break-glass token lifetimes are not integers")
    try:
        credential_id = UUID(credential)
    except (TypeError, ValueError) as exc:
        raise security.TokenInvalidError(
            "break-glass token carries a malformed credential id"
        ) from exc
    moment = now or utc_now()
    if issued_at - int(moment.timestamp()) > 60:
        raise security.TokenInvalidError("break-glass token was issued in the future")
    if expires_at - issued_at > BREAK_GLASS_TOKEN_TTL_SECONDS:
        raise security.TokenInvalidError("break-glass token exceeds the fixed session lifetime")
    return BreakGlassClaims(
        sub=subject,
        role=role,
        typ=_TOKEN_TYP,
        bg=credential,
        iat=issued_at,
        exp=expires_at,
        credential_id=str(credential_id),
    )


# -- lookup ----------------------------------------------------------------------
def credential_for_user(db: Session, user_id: UUID) -> OperatorBreakGlassCredential | None:
    return db.scalar(
        select(OperatorBreakGlassCredential).where(
            OperatorBreakGlassCredential.operator_user_id == user_id
        )
    )


# -- issuance / revocation (console operator API or the bootstrap CLI) -------------
def issue_credential(
    db: Session, user: OperatorUser, *, now: dt.datetime | None = None
) -> tuple[OperatorBreakGlassCredential, str]:
    """Designate (or rotate) one staff row's emergency credential.

    Returns ``(row, code)`` with the plaintext code shown exactly once — only
    its Argon2id hash is stored. Rotation replaces the hash and clears the
    lockout, so the previous code stops working immediately. The caller
    commits.
    """
    if not user.is_active:
        msg = f"operator {user.email!r} is not active; reactivate it before designating break-glass"
        raise ValueError(msg)
    moment = now or utc_now()
    credential = credential_for_user(db, user.id)
    if credential is None:
        credential = OperatorBreakGlassCredential(operator_user_id=user.id)
        db.add(credential)
    code = generate_code()
    credential.code_hash = security.hash_password(code)
    credential.is_enabled = True
    credential.rotated_at = moment
    credential.failed_login_attempts = 0
    credential.locked_until = None
    return credential, code


def disable_credential(
    db: Session, credential: OperatorBreakGlassCredential, *, now: dt.datetime | None = None
) -> None:
    """Revoke the emergency credential; the caller commits.

    Every already-minted break-glass token is refused on its next request
    because the request boundary re-reads this row.
    """
    credential.is_enabled = False
    credential.code_hash = None
    credential.failed_login_attempts = 0
    credential.locked_until = None
    credential.rotated_at = now or utc_now()


# -- audit + alert ---------------------------------------------------------------
def record_operator_attempt(  # noqa: PLR0913 - one audit row carries actor, outcome and session evidence
    db: Session,
    *,
    email: str,
    success: bool,
    reason: str,
    credential_id: UUID | None = None,
    operator_user_id: UUID | None = None,
    expires_at: dt.datetime | None = None,
) -> None:
    """Write the audit row, commit, then emit the operational alert.

    Every attempt — success or failure — lands here. The alert is emitted only
    after the audit row is durable, so a rolled-back transaction never claims
    an emergency sign-in that did not happen.
    """
    detail: dict[str, Any] = {"policy": "break_glass", "reason": reason}
    if credential_id is not None:
        detail["credential_id"] = str(credential_id)
    if operator_user_id is not None:
        detail["operator_user_id"] = str(operator_user_id)
    if expires_at is not None:
        detail["session_expires_at"] = expires_at.isoformat()
    db.add(
        OperatorAuditLog(
            operator_email=email,
            auth_mode="break_glass",
            action=BREAK_GLASS_SIGN_IN_ACTION if success else BREAK_GLASS_DENIED_ACTION,
            detail=detail,
        )
    )
    db.commit()
    # Accounts are named by id in an operational log, never by email or code.
    fields: dict[str, object] = {}
    if operator_user_id is not None:
        fields["operator_user_id"] = str(operator_user_id)
    if credential_id is not None:
        fields["credential_id"] = str(credential_id)
    operator_break_glass(success=success, reason=reason, **fields)


__all__ = [
    "BREAK_GLASS_DENIED_ACTION",
    "BREAK_GLASS_SIGN_IN_ACTION",
    "BREAK_GLASS_TOKEN_TTL_SECONDS",
    "account_lock_expiry",
    "burn_code_check",
    "clear_credential_failures",
    "clear_login_failures",
    "credential_for_user",
    "disable_credential",
    "generate_code",
    "issue_credential",
    "lockout_minutes_remaining",
    "mint_token",
    "record_operator_attempt",
    "record_credential_failure",
    "record_login_failure",
    "reset_login_throttle",
    "throttle_locked",
    "verify_token",
]
