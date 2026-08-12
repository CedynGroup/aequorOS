"""POST /operator/auth/login — staff email+password sign-in (the primary path).

Mirrors the client-side credential flow (`app/services/authentication.py`)
point for point, on the staff table:

- lookup by lowercased email in ``operator_users`` (active, holding a hash);
- Argon2id verification (`app.core.security` — the SAME hash scheme as tenant
  accounts), with a dummy-hash burn on unknown emails so timing never says
  whether an address exists;
- one GENERIC 401 for every failure mode — wrong password, unknown email,
  deactivated account, SSO-only account — no user enumeration, ever;
- a minimal in-process throttle: 5 consecutive failures per (email, IP) lock
  the pair out for 5 minutes with a 429 (see services/operator_auth.py for
  why in-process is the honest right-size here);
- success stamps ``last_login_at`` and mints the 8-hour operator session JWT.

Unauthenticated by design (it IS the front door), mounted directly on the
operator app beside ``/operator/health`` — session issuance is not a v1
resource. The endpoint 503s with a clear message when ``OPERATOR_JWT_SECRET``
is unset: password auth is REQUIRED to be configured, never degraded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
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


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=OperatorLoginRead)
def operator_login(payload: OperatorLoginRequest, request: Request) -> OperatorLoginRead:
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
    ip = _client_ip(request)
    if operator_auth.throttle_locked(email, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again in a few minutes.",
        )

    session = get_operator_sessionmaker()()
    try:
        user = session.scalar(select(OperatorUser).where(OperatorUser.email == email))
        # Uniform failure: unknown email, SSO-only account (no hash), and a
        # deactivated account all burn a hash check and answer the same 401.
        if user is None or not user.password_hash or not user.is_active:
            operator_auth.burn_password_check(payload.password)
            operator_auth.record_login_failure(email, ip)
            raise _INVALID
        if not security.verify_password(payload.password, user.password_hash):
            operator_auth.record_login_failure(email, ip)
            raise _INVALID

        operator_auth.clear_login_failures(email, ip)
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
