"""POST /operator/auth/login — staff email+password sign-in (the primary path).

Mirrors the client-side credential flow (`app/services/authentication.py`)
point for point, on the staff table:

- lookup by lowercased email in ``operator_users`` (active, holding a hash);
- Argon2id verification (`app.core.security` — the SAME hash scheme as tenant
  accounts), with a dummy-hash burn on unknown emails so timing never says
  whether an address exists;
- one GENERIC 401 for every failure mode — wrong password, unknown email,
  deactivated account, SSO-only account — no user enumeration, ever;
- a DURABLE per-account lockout on the tenant plane's own primitive
  (``app/services/auth_throttle.py`` over ``operator_users``' two throttle
  columns), fronted by a process-local per-email guard so an address with no
  row is throttled identically and the 429 stays enumeration-safe. See
  ``services/operator_auth.py`` for why the old in-process ``(email, IP)``
  dict was the wrong control for a plane that mints cross-tenant BYPASSRLS
  sessions (audit finding D-25);
- success stamps ``last_login_at``, clears BOTH throttle layers, and mints the
  8-hour operator session JWT.

Unauthenticated by design (it IS the front door), mounted directly on the
operator app beside ``/operator/health`` — session issuance is not a v1
resource. The endpoint 503s with a clear message when ``OPERATOR_JWT_SECRET``
is unset: password auth is REQUIRED to be configured, never degraded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core import security
from app.core.config import get_operator_settings
from app.db.base import utc_now
from app.models import OperatorUser
from app.operator.deps import get_operator_sessionmaker
from app.operator.services import operator_auth
from app.schemas.operator import (
    OperatorIdentityRead,
    OperatorLoginRead,
    OperatorLoginRequest,
)

router = APIRouter(prefix="/operator/auth", tags=["operator-auth"])

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
)


def _throttled(minutes: int | None = None) -> HTTPException:
    """One 429 body for both throttle layers, so the response never reveals
    WHICH layer refused — and therefore never reveals whether the address has
    a staff account. The minute count is only ever added when the durable
    lockout is the reason, which by then the caller has already earned."""
    wait = "in a few minutes" if minutes is None else f"in {minutes} minutes"
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many failed sign-in attempts. Try again {wait}.",
    )


@router.post("/login", response_model=OperatorLoginRead)
def operator_login(payload: OperatorLoginRequest) -> OperatorLoginRead:
    settings = get_operator_settings()
    if settings.jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Operator password sign-in is not configured: set "
                "OPERATOR_JWT_SECRET on the operator API to enable it."
            ),
        )

    email = payload.email.strip().lower()
    if operator_auth.throttle_locked(email):
        raise _throttled()

    session = get_operator_sessionmaker()()
    try:
        user = session.scalar(select(OperatorUser).where(OperatorUser.email == email))
        # Uniform failure: unknown email, SSO-only account (no hash), and a
        # deactivated account all burn a hash check and answer the same 401.
        if user is None or not user.password_hash or not user.is_active:
            operator_auth.burn_password_check(payload.password)
            operator_auth.record_login_failure(email)
            raise _INVALID

        # The durable lockout outranks the password: a locked account cannot
        # sign in with the RIGHT password either, which is what makes the
        # attempt budget finite across workers, replicas, deploys and every
        # source address the attacker owns.
        expiry = operator_auth.account_lock_expiry(user)
        if expiry is not None:
            raise _throttled(operator_auth.lockout_minutes_remaining(expiry))

        if not security.verify_password(payload.password, user.password_hash):
            operator_auth.record_login_failure(email)
            # Commits the failure itself — a count a rollback erases is a count
            # that never happened. The attempt that trips the threshold still
            # answers 401: the 429 arrives on the NEXT try, so the boundary is
            # not an oracle for the threshold value.
            operator_auth.record_account_failure(session, user)
            raise _INVALID

        operator_auth.clear_login_failures(email)
        operator_auth.clear_account_failures(user)
        user.last_login_at = utc_now()
        session.commit()

        token, expires_at = operator_auth.mint_operator_token(
            email=user.email, role=user.role, secret=settings.jwt_secret
        )
        return OperatorLoginRead(
            access_token=token,
            expires_at=expires_at,
            operator=OperatorIdentityRead.model_validate(
                {
                    "email": user.email,
                    "display_name": user.display_name,
                    "role": user.role,
                }
            ),
        )
    finally:
        session.close()
