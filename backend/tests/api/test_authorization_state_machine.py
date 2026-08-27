"""Bounded state-machine coverage for authorization-version invalidation.

The machine deliberately exposes only three operations the foundation owns:
issue a family, rotate a current refresh token, and atomically invalidate all
sessions.  It does not invent grant CRUD or workflow state that does not exist.
After every transition it checks the API boundary and persisted family state,
so arbitrary interleavings cannot leave a stale access or refresh credential
usable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import RefreshToken, User
from app.services import authentication, authorization
from tests.api.helpers import ORG_1, USER_1


@contextmanager
def _session() -> Iterator[Session]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        yield session
    finally:
        session.close()


def _claims(token: str) -> dict[str, object]:
    return jwt.decode(token, options={"verify_signature": False})


@dataclass(frozen=True)
class _AccessCredential:
    token: str
    authorization_version: int


@dataclass(frozen=True)
class _RefreshCredential:
    token: str
    authorization_version: int
    token_id: UUID
    family_id: UUID


def test_authorization_version_session_state_machine(db_client: TestClient) -> None:
    class AuthorizationSessionMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            with _session() as session:
                session.execute(delete(RefreshToken).where(RefreshToken.user_id == USER_1))
                user = session.get(User, USER_1)
                assert user is not None
                user.authorization_version = 1
                user.is_active = True
                session.commit()
            self.authorization_version = 1
            self.access_credentials: list[_AccessCredential] = []
            self.refresh_credentials: list[_RefreshCredential] = []
            self.live_refresh_credentials: list[_RefreshCredential] = []

        def _record_pair(
            self,
            *,
            access_token: str,
            refresh_token: str,
            family_id: UUID | None = None,
        ) -> _RefreshCredential:
            access_claims = _claims(access_token)
            refresh_claims = _claims(refresh_token)
            access_version = access_claims["authv"]
            refresh_version = refresh_claims["authv"]
            assert isinstance(access_version, int) and not isinstance(access_version, bool)
            assert isinstance(refresh_version, int) and not isinstance(refresh_version, bool)
            token_id = UUID(str(refresh_claims["jti"]))
            assert access_version == self.authorization_version
            assert refresh_version == self.authorization_version
            access = _AccessCredential(access_token, access_version)
            refresh = _RefreshCredential(
                refresh_token,
                refresh_version,
                token_id,
                family_id or token_id,
            )
            self.access_credentials.append(access)
            self.refresh_credentials.append(refresh)
            self.live_refresh_credentials.append(refresh)
            return refresh

        @rule()
        def issue_family(self) -> None:
            with _session() as session:
                user = session.get(User, USER_1)
                assert user is not None
                issued = authentication.issue_tokens(session, user)
            self._record_pair(
                access_token=issued.access_token,
                refresh_token=issued.refresh_token,
            )

        @precondition(lambda self: bool(self.live_refresh_credentials))
        @rule(slot=st.integers(min_value=0, max_value=7))
        def rotate_current_family(self, slot: int) -> None:
            old = self.live_refresh_credentials.pop(slot % len(self.live_refresh_credentials))
            response = db_client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old.token},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            self._record_pair(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                family_id=old.family_id,
            )

        @rule()
        def invalidate_sessions(self) -> None:
            with _session() as session:
                next_version = authorization.invalidate_user_authorization(
                    session,
                    organization_id=ORG_1,
                    user_id=USER_1,
                    reason="state-machine security change",
                )
            self.authorization_version += 1
            assert next_version == self.authorization_version
            self.live_refresh_credentials.clear()

        @invariant()
        def persisted_and_presented_authority_matches_model(self) -> None:
            with _session() as session:
                user = session.get(User, USER_1)
                assert user is not None
                assert user.authorization_version == self.authorization_version

                rows = list(
                    session.scalars(select(RefreshToken).where(RefreshToken.user_id == USER_1))
                )
                rows_by_id = {row.id: row for row in rows}
                stale_families = {
                    credential.family_id
                    for credential in self.refresh_credentials
                    if credential.authorization_version < self.authorization_version
                }
                for row in rows:
                    if row.family_id in stale_families:
                        assert row.revoked_at is not None
                        assert row.revoked_reason == "authorization_changed"
                for credential in self.live_refresh_credentials:
                    row = rows_by_id[credential.token_id]
                    assert row.revoked_at is None

            for credential in self.access_credentials:
                response = db_client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {credential.token}"},
                )
                expected = (
                    200 if credential.authorization_version == self.authorization_version else 401
                )
                assert response.status_code == expected, response.text

            for credential in self.refresh_credentials:
                if credential.authorization_version >= self.authorization_version:
                    continue
                response = db_client.post(
                    "/api/v1/auth/refresh",
                    json={"refresh_token": credential.token},
                )
                assert response.status_code == 401, response.text

    run_state_machine_as_test(
        AuthorizationSessionMachine,
        settings=settings(
            max_examples=12,
            stateful_step_count=8,
            deadline=None,
        ),
    )
