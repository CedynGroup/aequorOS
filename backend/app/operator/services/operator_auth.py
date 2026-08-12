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

The login throttle is an honest, minimal, IN-PROCESS attempt counter per
(email, client-IP): 5 consecutive failures lock that pair out for 5 minutes.
Deliberately not distributed — the operator API is a single small deployment,
and a per-process counter is exactly as strong as the deployment shape needs
while keeping zero infrastructure. Successes clear the counter.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import jwt

from app.core import security
from app.db.base import utc_now

#: Operator sessions live 8 hours — a staff work day, same order as tenant sessions.
OPERATOR_TOKEN_TTL_SECONDS = 8 * 3600

#: Consecutive failures per (email, ip) before the pair is locked out …
MAX_FAILED_ATTEMPTS = 5
#: … and for how long.
LOCKOUT_SECONDS = 5 * 60

_TOKEN_TYP = "operator"


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


# -- login throttle ------------------------------------------------------------
_throttle_lock = threading.Lock()
#: (email, ip) -> (consecutive_failures, locked_until | None)
_failures: dict[tuple[str, str], tuple[int, dt.datetime | None]] = {}


def throttle_locked(email: str, ip: str, now: dt.datetime | None = None) -> bool:
    moment = now or utc_now()
    with _throttle_lock:
        entry = _failures.get((email, ip))
        if entry is None:
            return False
        _count, locked_until = entry
        if locked_until is None:
            return False
        if locked_until <= moment:
            # Lockout served — forget the history entirely.
            del _failures[(email, ip)]
            return False
        return True


def record_login_failure(email: str, ip: str, now: dt.datetime | None = None) -> None:
    moment = now or utc_now()
    with _throttle_lock:
        count, locked_until = _failures.get((email, ip), (0, None))
        count += 1
        if count >= MAX_FAILED_ATTEMPTS:
            locked_until = moment + dt.timedelta(seconds=LOCKOUT_SECONDS)
        _failures[(email, ip)] = (count, locked_until)


def clear_login_failures(email: str, ip: str) -> None:
    with _throttle_lock:
        _failures.pop((email, ip), None)


def reset_login_throttle() -> None:
    """Test hook: forget all throttle state (the counter is process-global)."""
    with _throttle_lock:
        _failures.clear()


# -- constant-time helpers -------------------------------------------------------
# A real Argon2id hash of a random unguessable string; verifying a candidate
# password against it burns the same work as a real check, so "email exists"
# is not observable from response timing.
_DUMMY_HASH = security.hash_password("aequoros-operator-timing-equalizer")


def burn_password_check(password: str) -> None:
    """Spend one Argon2id verification without revealing anything."""
    security.verify_password(password, _DUMMY_HASH)
