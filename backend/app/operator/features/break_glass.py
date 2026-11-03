"""POST /operator/auth/break-glass — audited emergency staff sign-in.

The third, narrowest door beside ordinary password and workforce OIDC. It is
mounted with the other auth routes (unauthenticated by design — it IS a front
door) and is **default-off**:

* ``OPERATOR_BREAK_GLASS_ENABLED`` unset/false answers 404, so a deployment
  that has not opted in behaves as if the route did not exist;
* a staff row with no enabled emergency credential, an inactive row and a
  wrong code all answer the same generic 401 — no user enumeration;
* the durable per-credential lockout answers 429 even for the right code;
* every attempt that reaches verification writes an ``operator_audit_log`` row
  (``auth_mode='break_glass'``) and emits the ``auth.break_glass`` warning log.

The response shape is the ordinary login response, but the token carries the
break-glass ``typ``, a 15-minute fixed lifetime with no refresh, and the bound
row's role — never a role of its own.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core import security
from app.core.config import get_operator_settings
from app.db.base import utc_now
from app.models import OperatorUser
from app.operator.deps import get_operator_sessionmaker
from app.operator.schemas import (
    OperatorBreakGlassRequest,
    OperatorIdentityRead,
    OperatorLoginRead,
)
from app.operator.services import break_glass

router = APIRouter(prefix="/operator/auth", tags=["operator-auth"])

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid emergency access credentials.",
)


def _throttled(minutes: int | None = None) -> HTTPException:
    wait = "in a few minutes" if minutes is None else f"in {minutes} minutes"
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many failed emergency sign-in attempts. Try again {wait}.",
    )


@router.post("/break-glass", response_model=OperatorLoginRead)
def operator_break_glass_login(payload: OperatorBreakGlassRequest) -> OperatorLoginRead:
    settings = get_operator_settings()
    if not settings.break_glass_enabled:
        # Same answer whether the switch is off or the route was never
        # configured: the door does not advertise itself.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Emergency sign-in is not available.",
        )
    if settings.jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Emergency sign-in is enabled but cannot mint a session: set "
                "OPERATOR_JWT_SECRET on the operator API."
            ),
        )

    email = payload.email.strip().lower()
    if break_glass.throttle_locked(email):
        raise _throttled()

    session = get_operator_sessionmaker()()
    try:
        user = session.scalar(select(OperatorUser).where(OperatorUser.email == email))
        credential = break_glass.credential_for_user(session, user.id) if user is not None else None
        # Uniform failure: unknown email, inactive row, undesignated row and a
        # disabled credential all burn a hash check and answer the same 401.
        if (
            user is None
            or not user.is_active
            or credential is None
            or not credential.is_enabled
            or not credential.code_hash
            or credential.rotated_at is None
        ):
            break_glass.burn_code_check(payload.code)
            break_glass.record_login_failure(email)
            break_glass.record_operator_attempt(
                session,
                email=email,
                success=False,
                reason="not_designated",
                credential_id=credential.id if credential is not None else None,
                operator_user_id=user.id if user is not None else None,
            )
            raise _INVALID

        expiry = break_glass.account_lock_expiry(credential)
        if expiry is not None:
            break_glass.record_operator_attempt(
                session,
                email=email,
                success=False,
                reason="credential_locked",
                credential_id=credential.id,
                operator_user_id=user.id,
            )
            raise _throttled(break_glass.lockout_minutes_remaining(expiry))

        if not security.verify_password(payload.code, credential.code_hash):
            break_glass.record_login_failure(email)
            # Commits the failure itself; the attempt row follows on the same
            # session. The attempt that trips the threshold still answers 401.
            break_glass.record_credential_failure(session, credential)
            break_glass.record_operator_attempt(
                session,
                email=email,
                success=False,
                reason="invalid_code",
                credential_id=credential.id,
                operator_user_id=user.id,
            )
            raise _INVALID

        break_glass.clear_login_failures(email)
        break_glass.clear_credential_failures(credential)
        credential.last_used_at = utc_now()
        user.last_login_at = utc_now()
        token, expires_at = break_glass.mint_token(
            email=user.email,
            role=user.role,
            credential_id=credential.id,
            rotated_at=credential.rotated_at,
            secret=settings.jwt_secret,
        )
        # Commits the success stamp and the audit row together, then logs.
        break_glass.record_operator_attempt(
            session,
            email=user.email,
            success=True,
            reason="verified",
            credential_id=credential.id,
            operator_user_id=user.id,
            expires_at=expires_at,
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
