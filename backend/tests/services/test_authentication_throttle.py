from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import AuthSettings
from app.models import User
from app.services import auth_throttle, authentication
from tests.api.helpers import ORG_1, USER_1

PASSWORD = "correct-horse-battery-staple-0001"  # noqa: S105 - fixture only
WRONG_PASSWORD = "wrong-password-0002"  # noqa: S105 - fixture only


def _settings() -> AuthSettings:
    return AuthSettings.model_validate(
        {
            "AUTH_JWT_SECRET": "unit-test-signing-secret-please-rotate-000",
            "AUTH_MAX_FAILED_LOGINS": 2,
            "AUTH_LOCKOUT_SECONDS": 60,
        }
    )


def _user(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.id == USER_1))
    assert user is not None
    user.organization_id = ORG_1
    user.password_hash = security.hash_password(PASSWORD)
    user.failed_login_attempts = 0
    user.locked_until = None
    db_session.commit()
    return user


def test_password_login_uses_the_shared_durable_throttle(db_session: Session) -> None:
    user = _user(db_session)
    settings = _settings()

    with pytest.raises(HTTPException) as first_failure:
        authentication.login_with_password(
            db_session,
            email=user.email,
            password=WRONG_PASSWORD,
            organization_id=ORG_1,
            settings=settings,
        )
    assert first_failure.value.status_code == 401

    db_session.expire_all()
    persisted = db_session.scalar(select(User).where(User.id == USER_1))
    assert persisted is not None
    assert persisted.failed_login_attempts == 1

    with pytest.raises(HTTPException) as lockout:
        authentication.login_with_password(
            db_session,
            email=user.email,
            password=WRONG_PASSWORD,
            organization_id=ORG_1,
            settings=settings,
        )
    assert lockout.value.status_code == 423

    db_session.expire_all()
    persisted = db_session.scalar(select(User).where(User.id == USER_1))
    assert persisted is not None
    assert auth_throttle.is_locked(persisted)