"""State-machine coverage for session invalidation and scoped grant administration.

The session machine covers issue, rotation, and atomic invalidation. The grant
machine drives the real create/revoke API over arbitrary scalar scope tuples and
checks both persisted rows and the evaluator's exact effective union after every
transition.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

import jwt
import pytest
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

from app.core.authorization import (
    ROLE_PERMISSIONS,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, RefreshToken, User
from app.services import authentication, authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, headers


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


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/market-data/templates/yield_curve",
        "/api/v1/regulatory-reporting/templates",
    ),
)
def test_authenticated_read_surfaces_accept_current_active_users(
    db_client: TestClient,
    path: str,
) -> None:
    response = db_client.get(path, headers=headers())

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/market-data/templates/yield_curve",
        "/api/v1/regulatory-reporting/templates",
    ),
)
def test_authenticated_read_surfaces_reject_stale_access_tokens(
    db_client: TestClient,
    path: str,
) -> None:
    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        user.authorization_version = 2
        session.commit()

    response = db_client.get(path, headers=headers(authorization_version=1))

    assert response.status_code == 401
    assert response.json()["error"]["message"] == ("Session authorization is stale. Sign in again.")


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/market-data/templates/yield_curve",
        "/api/v1/regulatory-reporting/templates",
    ),
)
def test_authenticated_read_surfaces_reject_inactive_users(
    db_client: TestClient,
    path: str,
) -> None:
    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        user.is_active = False
        session.commit()

    response = db_client.get(path, headers=headers())

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Tenant context is not valid."


_ADMIN_TARGET = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
_ADMIN_BANKS = ("BK-PROP0001", "BK-PROP0002")
_ADMIN_MODULES = (Module.LIQUIDITY, Module.REGULATORY, Module.FX)
_ADMIN_SENSITIVITIES = (
    Sensitivity.PUBLISHED,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
)
_ADMIN_ROLES = (
    RoleBundle.VIEWER,
    RoleBundle.AUDITOR,
    RoleBundle.ANALYST,
    RoleBundle.APPROVER,
)


@dataclass(frozen=True)
class _RequestedGrant:
    binding_id: UUID
    role_bundle: RoleBundle
    institution_id: str
    module: Module
    sensitivity: Sensitivity


def _seed_grant_administration_surface() -> None:
    with _session() as session:
        owner = session.get(User, USER_1)
        assert owner is not None
        owner.role = "account_admin"
        session.add(
            User(
                id=_ADMIN_TARGET,
                organization_id=ORG_1,
                email="property.grantee@example.test",
                display_name="Property Grantee",
                role="viewer",
            )
        )
        for index, bank_id in enumerate(_ADMIN_BANKS, start=1):
            session.add(
                Bank(
                    id=bank_id,
                    organization_id=ORG_1,
                    name=f"Property Bank {index}",
                    short_name=f"Prop {index}",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=owner.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ORG_OWNER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "property-test"),
            reason="property-test Org Owner authority",
        )


def _grant_admin_headers() -> dict[str, str]:
    return headers(roles=("account_admin",), authorization_version=2)


def _target_binding_rows(client: TestClient) -> list[dict[str, object]]:
    response = client.get(
        "/api/v1/authorization/bindings",
        headers=_grant_admin_headers(),
        params={"principal_user_id": str(_ADMIN_TARGET)},
    )
    assert response.status_code == 200, response.text
    return response.json()["bindings"]


def test_grant_administration_state_machine_preserves_exact_union(
    db_client: TestClient,
) -> None:
    """Generative API invariant for create/revoke, including sensitivity.

    Every active row must be one request the machine made.  Authority is checked
    over the complete finite resource/permission grid, so independently chosen
    dimensions can never compose into an unrequested combination.  Revocation
    may change only its selected row.
    """

    _seed_grant_administration_surface()

    class GrantAdministrationMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            with _session() as session:
                session.execute(
                    delete(AuthorizationBinding).where(
                        AuthorizationBinding.principal_user_id == _ADMIN_TARGET
                    )
                )
                session.execute(delete(RefreshToken).where(RefreshToken.user_id == _ADMIN_TARGET))
                user = session.get(User, _ADMIN_TARGET)
                assert user is not None
                user.authorization_version = 1
                user.is_active = True
                session.commit()
            self.requested: dict[UUID, _RequestedGrant] = {}
            self.revoked: set[UUID] = set()

        @rule(
            role=st.sampled_from(_ADMIN_ROLES),
            bank_id=st.sampled_from(_ADMIN_BANKS),
            module=st.sampled_from(_ADMIN_MODULES),
            sensitivity=st.sampled_from(_ADMIN_SENSITIVITIES),
        )
        def create_one_complete_binding(
            self,
            role: RoleBundle,
            bank_id: str,
            module: Module,
            sensitivity: Sensitivity,
        ) -> None:
            response = db_client.post(
                "/api/v1/authorization/bindings",
                headers=_grant_admin_headers(),
                json={
                    "principal_user_id": str(_ADMIN_TARGET),
                    "role_bundle": role.value,
                    "institution_scope": "institution",
                    "institution_id": bank_id,
                    "module_scope": module.value,
                    "sensitivity_scope": sensitivity.value,
                    "reason": "state-machine complete scoped grant",
                },
            )
            assert response.status_code == 201, response.text
            row = response.json()["binding"]
            assert row["sensitivity_scope"] == sensitivity.value
            binding_id = UUID(row["id"])
            self.requested[binding_id] = _RequestedGrant(
                binding_id,
                role,
                bank_id,
                module,
                sensitivity,
            )

        @precondition(lambda self: bool(set(self.requested) - self.revoked))
        @rule(slot=st.integers(min_value=0, max_value=31))
        def revoke_exactly_one_binding(self, slot: int) -> None:
            active_ids = sorted(set(self.requested) - self.revoked, key=str)
            binding_id = active_ids[slot % len(active_ids)]
            before = {UUID(str(row["id"])): row for row in _target_binding_rows(db_client)}
            response = db_client.post(
                f"/api/v1/authorization/bindings/{binding_id}/revoke",
                headers=_grant_admin_headers(),
                json={"reason": "state-machine selected revocation"},
            )
            assert response.status_code == 200, response.text
            assert (
                response.json()["sensitivity_scope"] == self.requested[binding_id].sensitivity.value
            )
            after = {UUID(str(row["id"])): row for row in _target_binding_rows(db_client)}
            assert after[binding_id]["status"] == "revoked"
            for remaining_id in set(before) - {binding_id}:
                assert after[remaining_id] == before[remaining_id]
            self.revoked.add(binding_id)

        @invariant()
        def active_rows_and_authority_are_exactly_the_requested_union(self) -> None:
            rows = _target_binding_rows(db_client)
            by_id = {UUID(str(row["id"])): row for row in rows}
            assert set(by_id) == set(self.requested)
            active_ids = {
                binding_id for binding_id, row in by_id.items() if row["effective"] is True
            }
            assert active_ids == set(self.requested) - self.revoked
            for binding_id, requested in self.requested.items():
                row = by_id[binding_id]
                assert (
                    row["role_bundle"],
                    row["institution_id"],
                    row["module_scope"],
                    row["sensitivity_scope"],
                ) == (
                    requested.role_bundle.value,
                    requested.institution_id,
                    requested.module.value,
                    requested.sensitivity.value,
                )

            with _session() as session:
                principal = PrincipalLocator(ORG_1, _ADMIN_TARGET, PrincipalType.HUMAN)
                for bank_id in _ADMIN_BANKS:
                    for module in _ADMIN_MODULES:
                        for sensitivity in _ADMIN_SENSITIVITIES:
                            resource = ResourceLocator(
                                ORG_1,
                                InstitutionScope.INSTITUTION,
                                bank_id,
                                module,
                                sensitivity,
                            )
                            for permission in Permission:
                                expected = any(
                                    requested.binding_id not in self.revoked
                                    and requested.institution_id == bank_id
                                    and requested.module is module
                                    and requested.sensitivity is sensitivity
                                    and permission in ROLE_PERMISSIONS[requested.role_bundle]
                                    for requested in self.requested.values()
                                )
                                decision = authorization.evaluate_permission(
                                    session,
                                    principal,
                                    permission,
                                    resource,
                                )
                                assert decision.allowed is expected

    run_state_machine_as_test(
        GrantAdministrationMachine,
        settings=settings(max_examples=15, stateful_step_count=10, deadline=None),
    )
