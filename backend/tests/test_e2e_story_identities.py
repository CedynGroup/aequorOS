"""Story principals retain real authority and end access even after a failed story."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import BindingStatus
from app.core.security import hash_password
from app.identity.models.authorization import AuthorizationBinding
from app.identity.schemas.auth import MeResponse, TokenResponse
from app.identity.service import authorization
from app.models import BankFinancialFact, User
from scripts.e2e_story_identities import (
    SOURCE_ADMIN,
    SOURCE_BANK,
    SOURCE_ORG,
    ActorSpec,
    StoryRequest,
    provision,
    retire,
)

PASSWORD = "synthetic-story-step-up-not-production"


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
            raise RuntimeError("synthetic body failure")
        finally:
            retire(db_session, request)
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
