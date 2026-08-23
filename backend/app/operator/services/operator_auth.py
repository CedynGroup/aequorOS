"""Operator password-session primitives: JWT mint/verify, login throttle.

The staff auth model MATCHES the client-side one (founder's directive,
2026-08-11): email+password is the PRIMARY path, workforce SSO the secondary.
A successful ``POST /operator/auth/login`` mints a short-lived HS256 JWT over
the DEDICATED ``OPERATOR_JWT_SECRET`` (never the tenant ``AUTH_JWT_SECRET`` —
an operator token must be worthless on the tenant API and vice versa), signed
and verified with the same PyJWT library the platform uses everywhere.

Claims: ``{sub: email, role, typ: "operator", iat, exp: iat+8h}``. The
``typ`` claim is load-bearing: the verifier REQUIRES it, so no other HS256
token in the ecosystem can ever pass as an operator session.

**The login throttle (rebuilt 2026-08-23, audit finding D-25).** It used to be
an IN-PROCESS dict keyed on ``(email, client-IP)`` — 5 failures per pair, 5
minutes — which is the exact control the TENANT plane rejected as inadequate
and replaced with durable columns. The staff plane guards cross-tenant
BYPASSRLS access, so it cannot hold the weaker control. Two layers now:

1. **The durable per-ACCOUNT lockout** is the real control, and it is the
   tenant plane's own primitive (``app/services/auth_throttle.py``) running
   over ``operator_users.failed_login_attempts`` / ``.locked_until``: one
   implementation, one progressive backoff curve, one ``auth_anomaly``
   emission, shared by every worker and replica and surviving a deploy. The IP
   is gone from the key — rotating source addresses used to hand the attacker
   a fresh budget per address, which made the attempt budget unbounded.
2. **A process-local counter keyed on the EMAIL alone** stays in front of the
   row lookup. It is not the control; it covers the one case the durable
   counter cannot — an address with no ``operator_users`` row — so an unknown
   email is throttled exactly like a known one and the 429 never becomes a
   user-enumeration oracle. It is deliberately weak, and nothing depends on it.

Successes clear both.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import jwt
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import AuthSettings, get_settings
from app.db.base import utc_now
from app.models import OperatorUser
from app.services import auth_throttle

#: Operator sessions live 8 hours — a staff work day, same order as tenant sessions.
OPERATOR_TOKEN_TTL_SECONDS = 8 * 3600

#: Consecutive failures per email before the process-local pre-lookup guard
#: trips, and for how long. Deliberately the same numbers as the durable
#: control below so the two layers agree; the durable one is the authority.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 5 * 60

_TOKEN_TYP = "operator"


def throttle_settings() -> AuthSettings:
    """The lockout policy. ONE policy platform-wide: staff and tenant accounts
    share ``AUTH_MAX_FAILED_LOGINS`` / ``AUTH_LOCKOUT_SECONDS``, so an operator
    lockout can never be quietly looser than a bank user's."""
    return get_settings().auth


# -- JWT ---------------------------------------------------------------------
def mint_operator_token(
    *, email: str, role: str, secret: str, now: dt.datetime | None = None
) -> tuple[str, dt.datetime]:
    """Sign an operator session JWT; returns ``(token, expires_at)``."""
    moment = now or utc_now()
    expires_at = moment + dt.timedelta(seconds=OPERATOR_TOKEN_TTL_SECONDS)
    payload = {
        "sub": email,
        "role": role,
        "typ": _TOKEN_TYP,
        "iat": int(moment.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256"), expires_at


def verify_operator_token(token: str, *, secret: str) -> dict[str, Any]:
    """Verify signature, expiry, and the ``typ=operator`` claim; return claims.

    Raises :class:`app.core.security.TokenInvalidError` on ANY failure — the
    caller collapses that into the generic 401 (no oracle about which check
    failed).
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise security.TokenInvalidError(str(exc)) from exc
    if claims.get("typ") != _TOKEN_TYP:
        msg = f"expected an operator token, got typ={claims.get('typ')!r}"
        raise security.TokenInvalidError(msg)
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise security.TokenInvalidError("operator token carries no subject email")
    return claims


# -- login throttle, layer 1: process-local, unknown-principal guard -------------
# Keyed on the EMAIL alone. The client IP used to be part of the key, which is
# precisely what made the budget unbounded: an attacker rotating source
# addresses got MAX_FAILED_ATTEMPTS fresh guesses per address against the same
# account. Nothing security-critical rests on this layer — see the durable one
# below — but it must keep answering for addresses that have no row, so that a
# 429 says "slow down", never "this address exists".
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
        if locked_until is None:
            return False
        if locked_until <= moment:
            # Lockout served — forget the history entirely.
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


# -- login throttle, layer 2: the durable per-account lockout (the control) -------
def account_lock_expiry(
    user: OperatorUser, *, now: dt.datetime | None = None
) -> dt.datetime | None:
    """When this staff account's lockout ends, or ``None`` if it is not locked."""
    return auth_throttle.lock_expiry(user, now=now)


def record_account_failure(
    db: Session, user: OperatorUser, *, now: dt.datetime | None = None
) -> dt.datetime | None:
    """Count one wrong password against the staff row itself; commits.

    Straight through to the tenant plane's primitive — same atomic SQL
    increment, same progressive backoff, same ``auth_anomaly`` emission on
    lockout. The staff account is named by ``operator_user_id`` in that
    emission and never by email, matching the tenant rule.
    """
    return auth_throttle.record_failure_for(
        db,
        user,
        model=OperatorUser,
        identity=(OperatorUser.id == user.id,),
        anomaly_fields={"plane": "operator", "operator_user_id": str(user.id)},
        settings=throttle_settings(),
        now=now,
    )


def clear_account_failures(user: OperatorUser) -> None:
    """Zero the durable counter after a proven password (caller commits)."""
    auth_throttle.clear_failures(user)


def lockout_minutes_remaining(
    expiry: dt.datetime, *, now: dt.datetime | None = None
) -> int:
    """Whole minutes until a lockout ends — copy for the sign-in screen."""
    return auth_throttle.minutes_remaining(expiry, now=now)


# -- constant-time helpers -------------------------------------------------------
# A real Argon2id hash of a random unguessable string; verifying a candidate
# password against it burns the same work as a real check, so "email exists"
# is not observable from response timing.
_DUMMY_HASH = security.hash_password("aequoros-operator-timing-equalizer")


def burn_password_check(password: str) -> None:
    """Spend one Argon2id verification without revealing anything."""
    security.verify_password(password, _DUMMY_HASH)
