"""Story principals retain real authority and end access even after a failed story."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import BindingStatus, ModuleScope, RoleBundle, SensitivityScope
from app.core.security import hash_password
from app.identity.models.authorization import AuthorizationBinding
from app.identity.schemas.auth import MeResponse, TokenResponse
from app.identity.service import authorization
from app.models import BankFinancialFact, MacroScenario, User
from app.stress.schemas.stress import MacroScenarioListRead, MacroScenarioRead
from scripts.e2e_story_identities import (
    SOURCE_ADMIN,
    SOURCE_BANK,
    SOURCE_ORG,
    ActorSpec,
    ScopedBinding,
    StoryRequest,
    provision,
    retire,
)

PASSWORD = "synthetic-story-step-up-not-production"


def test_scoped_story_credentials_enforce_real_backend_permission_boundaries(
    db_session: Session, db_client: TestClient
) -> None:
    _ = prepare_source(db_session)
    request = StoryRequest(
        organization_id="OR-STORY003",
        bank_id="BK-STORY003",
        owned_tenant=True,
        actors=[
            ActorSpec(role="admin", user_id=uuid4()),
            ActorSpec(role="viewer", user_id=uuid4()),
        ],
        bindings=[
            ScopedBinding(
                role_bundle=RoleBundle.VIEWER,
                module_scope=ModuleScope.FTP,
                sensitivity_scope=SensitivityScope.AGGREGATED,
            )
        ],
    )
    try:
        actor = next(
            actor for actor in provision(db_session, request).actors if actor.role == "viewer"
        )
        login = db_client.post(
            "/api/v1/auth/login", json={"email": actor.email, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        token = TokenResponse.model_validate_json(login.text)
        headers = {"Authorization": f"Bearer {token.access_token}"}
        response = db_client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200, response.text
        authority = MeResponse.model_validate_json(response.text).effective_authority
        assert authority.organization_capabilities == []
        assert len(authority.institution_capabilities) == 1
        institution = authority.institution_capabilities[0]
        assert institution.institution_id == request.bank_id
        assert [
            (capability.module, capability.sensitivity, capability.permission)
            for capability in institution.capabilities
        ] == [("ftp", "aggregated", "view")]
        denied = db_client.post(
            f"/api/v1/banks/{request.bank_id}/ftp/run-all-scenarios",
            headers=headers,
            json={"reporting_period_id": str(uuid4())},
        )
        assert denied.status_code == 403, denied.text
        denied = db_client.get(
            f"/api/v1/banks/{request.bank_id}/capital/dashboard", headers=headers
        )
        assert denied.status_code == 403, denied.text
    finally:
        retire(db_session, request)


def prepare_source(db: Session) -> User:
    source = db.get(User, SOURCE_ADMIN)
    assert source is not None
    source.password_hash = hash_password(PASSWORD)
    _ = authorization.invalidate_user_authorization(
        db,
        organization_id=SOURCE_ORG,
        user_id=source.id,
        reason="Prepare a synthetic story password",
        commit=False,
    )
    db.commit()
    return source


def test_failed_story_retires_fresh_actors_and_real_refresh_families(
    db_session: Session,
    db_client: TestClient,
) -> None:
    source = prepare_source(db_session)
    source_version = source.authorization_version
    original = {
        binding.id: binding.status
        for binding in db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == source.id)
        )
    }
    request = StoryRequest(
        organization_id=SOURCE_ORG,
        bank_id=SOURCE_BANK,
        actors=[
            ActorSpec(role="analyst", user_id=uuid4()),
            ActorSpec(role="approver", user_id=uuid4()),
        ],
    )
    credentials: list[TokenResponse] = []
    scenario_id: UUID | None = None
    with pytest.raises(RuntimeError, match="synthetic body failure"):
        try:
            actors = provision(db_session, request).actors
            assert len({actor.user_id for actor in actors}) == 2
            for actor in actors:
                response = db_client.post(
                    "/api/v1/auth/login", json={"email": actor.email, "password": PASSWORD}
                )
                assert response.status_code == 200, response.text
                credential = TokenResponse.model_validate_json(response.text)
                credentials.append(credential)
                me = db_client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {credential.access_token}"},
                )
                assert me.status_code == 200, me.text
                assert MeResponse.model_validate_json(me.text).user_id == actor.user_id
                if actor.role == "analyst":
                    headers = {"Authorization": f"Bearer {credential.access_token}"}
                    catalogue = db_client.get("/api/v1/macro-scenarios", headers=headers)
                    assert catalogue.status_code == 200, catalogue.text
                    system_id = (
                        MacroScenarioListRead.model_validate_json(catalogue.text).scenarios[0].id
                    )
                    clone = db_client.post(
                        f"/api/v1/macro-scenarios/{system_id}/clone",
                        headers=headers,
                        json={"reason": "Exercise governed cleanup after a failed story"},
                    )
                    assert clone.status_code == 201, clone.text
                    scenario_id = MacroScenarioRead.model_validate_json(clone.text).id
            raise RuntimeError("synthetic body failure")
        finally:
            retire(db_session, request)
    assert scenario_id is not None
    scenario = db_session.get(MacroScenario, scenario_id)
    assert scenario is not None
    assert scenario.status == "archived"
    for credential in credentials:
        assert (
            db_client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {credential.access_token}"}
            ).status_code
            == 401
        )
        assert (
            db_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": credential.refresh_token}
            ).status_code
            == 401
        )
    assert source.authorization_version == source_version
    assert original == {
        binding.id: binding.status
        for binding in db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == source.id)
        )
    }
    retire(db_session, request)


def test_owner_stories_have_distinct_tenants_and_no_financial_fixture_writes(
    db_session: Session,
) -> None:
    _ = prepare_source(db_session)
    identities: set[UUID] = set()
    for suffix in ("STORY001", "STORY002"):
        request = StoryRequest(
            organization_id=f"OR-{suffix}",
            bank_id=f"BK-{suffix}",
            owned_tenant=True,
            actors=[
                ActorSpec(role="admin", user_id=uuid4()),
                ActorSpec(role="grant_member", user_id=uuid4()),
            ],
        )
        try:
            actors = provision(db_session, request).actors
            current = {actor.user_id for actor in actors}
            assert not identities.intersection(current)
            identities.update(current)
            owner = next(actor for actor in actors if actor.role == "admin")
            held = list(
                db_session.scalars(
                    select(AuthorizationBinding).where(
                        AuthorizationBinding.principal_user_id == owner.user_id,
                        AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                    )
                )
            )
            assert {binding.role_bundle for binding in held} == {"member", "org_owner", "viewer"}
            assert not list(
                db_session.scalars(
                    select(BankFinancialFact.id).where(
                        BankFinancialFact.organization_id == request.organization_id
                    )
                )
            )
        finally:
            retire(db_session, request)
        assert not list(
            db_session.scalars(
                select(AuthorizationBinding.id).where(
                    AuthorizationBinding.organization_id == request.organization_id,
                    AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                )
            )
        )
