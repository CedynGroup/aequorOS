"""HTTP model of scoped requests, resolutions, refiling and grant revocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi.testclient import TestClient
from hypothesis import strategies as st
from hypothesis.stateful import initialize, invariant, rule
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.schemas.authorization import (
    AccessRequestListRead,
    AccessRequestRead,
    BindingCreateResponse,
    BindingPreviewRead,
    BindingRead,
)
from app.identity.service import authorization
from app.models import AuthorizationAccessRequest, AuthorizationBinding, User
from tests.identity.api.test_grant_administration import BANK_A, BANK_B, GRANTEE
from tests.identity.api.test_grant_administration import (
    grant_client as grant_client,  # noqa: PLC0414 - shared pytest fixture
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.support.stateful import DatabaseMachine, run_database_machine, success_events

ROOT = "/api/v1/authorization"
ROUTES = ("/fx", "/liquidity/forecast", "/liquidity/monitoring")
BANKS = (BANK_A, BANK_B)
type Actor = Literal["owner", "member", "outsider"]


@dataclass
class Request:
    bank: str
    route: str
    original: str
    status: str = "pending"
    binding_id: UUID | None = None

    @property
    def module(self) -> str:
        return "fx" if self.route == "/fx" else "liq"

    @property
    def sensitivity(self) -> str:
        return "aggregated" if self.route == "/fx" else "confidential"


@dataclass
class Grant:
    bank: str
    module: str
    active: bool = True


class AccessRequestMachine(DatabaseMachine):
    def __init__(self, fixture: Session, client: TestClient) -> None:
        super().__init__(fixture)
        self.client = client
        self.requests: dict[UUID, Request] = {}
        self.grants: dict[UUID, Grant] = {}

    def credentials(self, actor: Actor = "owner") -> dict[str, str]:
        user_id = {"owner": USER_1, "member": GRANTEE, "outsider": USER_2}[actor]
        self.db.expire_all()
        user = self.db.get(User, user_id)
        assert user is not None
        result = headers(
            org_id=ORG_2 if actor == "outsider" else ORG_1,
            user_id=user_id,
            authorization_version=user.authorization_version,
            roles=("viewer",),
        )
        # End read transactions before HTTP sessions create their savepoints.
        self.db.commit()
        return result

    def mine(self) -> list[AccessRequestRead]:
        response = self.client.get(
            f"{ROOT}/access-requests/mine", headers=self.credentials("member")
        )
        assert response.status_code == 200, response.text
        return AccessRequestListRead.model_validate_json(response.text).requests

    def evidence(self) -> tuple[object, ...]:
        public = tuple(sorted(row.model_dump_json() for row in self.mine()))
        self.db.expire_all()
        grants = tuple(
            (row.id, row.status, row.institution_id, row.module_scope, row.sensitivity_scope)
            for row in self.db.scalars(
                select(AuthorizationBinding)
                .where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                )
                .order_by(AuthorizationBinding.id)
            )
        )
        events = tuple(
            (key, success_events(self.db, key, "authorization.access_request"))
            for key in self.requests
        )
        user = self.db.get(User, GRANTEE)
        assert user is not None
        version = user.authorization_version
        self.db.commit()
        return public, grants, events, version

    @initialize(
        checkpoint=st.sampled_from(
            ("pending", "approved", "rejected", "revoked", "refiled", "shared")
        )
    )
    def reach_checkpoint(self, checkpoint: str) -> None:
        self.file(0, 0, "Initial request")
        key = next(iter(self.requests))
        if checkpoint == "shared":
            self.file(0, 1, "First liquidity route")
            self.file(0, 2, "Second liquidity route")
            self.approve(1, "none", "owner")
            self.grant_from_composer(1, 0)
            return
        if checkpoint == "pending":
            return
        if checkpoint == "rejected":
            self.reject(0, "owner")
            return
        self.approve(0, "none", "owner")
        if checkpoint in {"revoked", "refiled"}:
            self.revoke(0)
        if checkpoint == "refiled":
            self.file(0, 0, "New request after revocation")
            assert len(self.requests) == 2 and self.requests[key].status == "approved"

    @rule(
        bank=st.integers(0, 1),
        route=st.integers(0, 2),
        reason=st.text(alphabet="abc XYZ", min_size=1, max_size=30),
    )
    def file(self, bank: int, route: int, reason: str) -> None:
        bank_id, route_name = BANKS[bank], ROUTES[route]
        pending = next(
            (
                key
                for key, row in self.requests.items()
                if row.status == "pending" and row.bank == bank_id and row.route == route_name
            ),
            None,
        )
        payload: dict[str, object] = {
            "route": route_name,
            "institution_id": bank_id,
            "module_scope": "fx" if route == 0 else "liq",
            "sensitivity_scope": "aggregated" if route == 0 else "confidential",
            "permission": "view",
            "reason_category": "project_engagement",
            "reason_detail": reason,
            "reference": "STATE-MACHINE",
        }
        before = self.evidence() if pending is not None else None
        response = self.client.post(
            f"{ROOT}/access-requests", headers=self.credentials("member"), json=payload
        )
        assert response.status_code == 201, response.text
        row = AccessRequestRead.model_validate_json(response.text)
        if pending is not None:
            assert row.id == pending and self.evidence() == before
        else:
            assert row.id not in self.requests and row.status == "pending"
            self.requests[row.id] = Request(
                bank_id, route_name, row.model_dump_json(exclude={"status"})
            )

    def grant_payload(self, request: Request, wrong: str = "none") -> dict[str, object]:
        bank = BANK_B if request.bank == BANK_A else BANK_A
        module = "liq" if request.module == "fx" else "fx"
        payload: dict[str, object] = {
            "principal_user_id": str(GRANTEE),
            "role_bundle": "viewer",
            "institution_scope": "institution",
            "institution_id": bank if wrong == "bank" else request.bank,
            "module_scope": module if wrong == "module" else request.module,
            "sensitivity_scope": "all" if wrong == "sensitivity" else request.sensitivity,
            "reason_category": "project_engagement",
            "reason_detail": "Reviewing the institution's request",
        }
        preview = self.client.post(
            f"{ROOT}/bindings/preview", headers=self.credentials(), json=payload
        )
        assert preview.status_code == 200, preview.text
        payload["expected_authority_sentence"] = BindingPreviewRead.model_validate_json(
            preview.text
        ).authority_sentence
        return payload

    def resolve(self, binding: BindingRead, request_id: UUID | None = None) -> None:
        assert binding.principal_user_id == GRANTEE and binding.role_bundle == "viewer"
        assert binding.institution_id is not None
        self.grants[binding.id] = Grant(binding.institution_id, binding.module_scope.value)
        for key, request in self.requests.items():
            if request.status != "pending" or (request_id is not None and key != request_id):
                continue
            matching = next(
                (
                    grant_id
                    for grant_id, grant in self.grants.items()
                    if grant.active and (grant.bank, grant.module) == (request.bank, request.module)
                ),
                None,
            )
            if matching is not None:
                request.status, request.binding_id = "approved", matching

    @rule(
        index=st.integers(0, 20),
        wrong=st.sampled_from(("none", "bank", "module", "sensitivity")),
        actor=st.sampled_from(("owner", "member", "outsider")),
    )
    def approve(self, index: int, wrong: str, actor: Actor) -> None:
        key = list(self.requests)[index % len(self.requests)]
        expected = self.requests[key]
        before = self.evidence()
        payload = self.grant_payload(expected, wrong)
        payload.pop("principal_user_id")
        response = self.client.post(
            f"{ROOT}/access-requests/{key}/approve", headers=self.credentials(actor), json=payload
        )
        if actor != "owner" or expected.status != "pending" or wrong != "none":
            status = (
                403
                if actor == "member"
                else 404
                if actor == "outsider" or expected.status != "pending"
                else 409
            )
            assert response.status_code == status, response.text
            assert self.evidence() == before
        else:
            assert response.status_code == 200, response.text
            self.resolve(BindingCreateResponse.model_validate_json(response.text).binding, key)

    @rule(index=st.integers(0, 20), actor=st.sampled_from(("owner", "member", "outsider")))
    def reject(self, index: int, actor: Actor) -> None:
        key = list(self.requests)[index % len(self.requests)]
        expected = self.requests[key]
        before = self.evidence()
        response = self.client.post(
            f"{ROOT}/access-requests/{key}/reject",
            headers=self.credentials(actor),
            json={"reason_category": "other", "reason_detail": "Outside the agreed project scope"},
        )
        if actor != "owner" or expected.status != "pending":
            assert response.status_code == (403 if actor == "member" else 404), response.text
            assert self.evidence() == before
        else:
            assert response.status_code == 200, response.text
            assert AccessRequestRead.model_validate_json(response.text).status == "rejected"
            expected.status = "rejected"

    @rule(bank=st.integers(0, 1), route=st.integers(0, 2))
    def grant_from_composer(self, bank: int, route: int) -> None:
        request = Request(BANKS[bank], ROUTES[route], "")
        duplicate = any(
            grant.active and (grant.bank, grant.module) == (request.bank, request.module)
            for grant in self.grants.values()
        )
        before = self.evidence()
        payload = self.grant_payload(request)
        response = self.client.post(f"{ROOT}/bindings", headers=self.credentials(), json=payload)
        if duplicate:
            assert response.status_code == 409, response.text
            assert self.evidence() == before
        else:
            assert response.status_code == 201, response.text
            self.resolve(BindingCreateResponse.model_validate_json(response.text).binding)

    @rule(index=st.integers(0, 20))
    def revoke(self, index: int) -> None:
        if not self.grants:
            return
        key = list(self.grants)[index % len(self.grants)]
        expected = self.grants[key]
        before = self.evidence()
        response = self.client.post(
            f"{ROOT}/bindings/{key}/revoke",
            headers=self.credentials(),
            json={"reason": "Project engagement ended"},
        )
        if expected.active:
            assert response.status_code == 200, response.text
            assert BindingRead.model_validate_json(response.text).status.value == "revoked"
            expected.active = False
        else:
            assert response.status_code == 409, response.text
            assert self.evidence() == before

    @invariant()
    def complete_scope_and_resolution_history_are_preserved(self) -> None:
        mine = self.mine()
        assert {row.id for row in mine} == set(self.requests)
        pending_keys: set[tuple[str, str]] = set()
        for row in mine:
            expected = self.requests[row.id]
            assert row.status == expected.status
            assert row.model_dump_json(exclude={"status"}) == expected.original
            if row.status == "pending":
                key = (expected.bank, expected.route)
                assert key not in pending_keys
                pending_keys.add(key)
        pending = self.client.get(f"{ROOT}/access-requests", headers=self.credentials())
        assert pending.status_code == 200, pending.text
        assert {
            row.id for row in AccessRequestListRead.model_validate_json(pending.text).requests
        } == {key for key, row in self.requests.items() if row.status == "pending"}
        self.db.expire_all()
        for key, expected in self.requests.items():
            stored = self.db.get(AuthorizationAccessRequest, key)
            assert stored is not None and stored.binding_id == expected.binding_id
            events = success_events(self.db, key, "authorization.access_request")
            assert len(events) == (1 if expected.status == "pending" else 2)
            if expected.status != "pending":
                assert stored.resolved_at is not None and stored.resolved_by_user_id == USER_1
        for key, expected in self.grants.items():
            stored = self.db.get(AuthorizationBinding, key)
            assert stored is not None
            assert stored.status == ("active" if expected.active else "revoked")
            assert (stored.institution_id, stored.module_scope) == (expected.bank, expected.module)
        self.db.commit()


def test_access_requests_remain_scoped_and_resolved_history_is_terminal(
    db_session: Session,
    grant_client: TestClient,
) -> None:
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_2,
        principal_user_id=USER_2,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ORG_OWNER,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION, None, ModuleScope.ACCOUNT, SensitivityScope.ALL
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "state-machine-fixture"),
        reason="Explicit authority to test cross-tenant request isolation",
    )
    run_database_machine(lambda: AccessRequestMachine(db_session, grant_client))
