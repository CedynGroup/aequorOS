"""Story principals retain real authority and end access even after a failed story."""

from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    InstitutionScope,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.core.security import hash_password
from app.identity.models.authorization import AuthorizationBinding
from app.identity.schemas.auth import MeResponse, TokenResponse
from app.identity.service import authorization
from app.models import BankFinancialFact, MacroScenario, User
from app.stress.schemas.stress import MacroScenarioListRead, MacroScenarioRead
from scripts.e2e_story_identities import (
    RETURN_EXPORT_ROLES,
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
VAULT_KEY = "story-export-vault-key-not-production-0001"


class StoryAuthority(NamedTuple):
    """One story actor's projected authority, read back from ``/auth/me``."""

    #: institution id -> (module, sensitivity, permission) it is granted.
    capabilities: dict[str, set[tuple[str, str, str]]]
    #: every bundle held, whatever its scope.
    bundles: set[str]
    #: (bundle, institution_scope, institution_id) for the export bundle only.
    export_rows: set[tuple[str, str, str | None]]
    #: (bundle, institution_scope, institution_id) for the ceremony's
    #: exact-bank SIGNER and APPROVER duty bindings (#185).
    ceremony_rows: set[tuple[str, str, str | None]]


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


def test_signing_story_officers_hold_return_export_and_other_stories_do_not(
    db_session: Session,
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a signing story grants the export its ceremony requires.

    The 2026-11-03 cutover serves a return's files only to a REG/restricted
    EXPORT holder, and the signing workspace refuses to certify a page the
    officer was never served, so the officers who sign a story must hold it.
    Every other story keeps exactly the grants it always had: an approver-only
    identity stays denied, which is the production rule the fixture may not
    paper over. ``_grant`` binds at organization scope, so the extra binding
    covers the story's institution without narrowing the officer's own bundle.
    """
    _ = prepare_source(db_session)
    # A signing story also issues each officer a signer identity, so the suite's
    # blanked signing configuration is switched on for this story alone.
    monkeypatch.setenv("SIGNER_ID_PEPPER", "story-export-test-pepper-not-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", VAULT_KEY)
    get_settings.cache_clear()

    def story_authority(signing: bool, tag: str) -> dict[str, StoryAuthority]:
        # A story's own tenant is what gives ``/auth/me`` an institution to
        # project over; the shared sample tenant carries no bank rows here.
        request = StoryRequest(
            organization_id=f"OR-{tag}",
            bank_id=f"BK-{tag}",
            owned_tenant=True,
            signing=signing,
            actors=[
                ActorSpec(role="approver", user_id=uuid4()),
                ActorSpec(role="validator", user_id=uuid4()),
                ActorSpec(role="viewer", user_id=uuid4()),
            ],
        )
        try:
            actors = provision(db_session, request).actors
            authority: dict[str, StoryAuthority] = {}
            for actor in actors:
                bindings = list(
                    db_session.scalars(
                        select(AuthorizationBinding).where(
                            AuthorizationBinding.principal_user_id == actor.user_id,
                            AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                        )
                    )
                )
                login = db_client.post(
                    "/api/v1/auth/login",
                    json={"email": actor.email, "password": PASSWORD},
                )
                assert login.status_code == 200, login.text
                token = TokenResponse.model_validate_json(login.text)
                me = db_client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token.access_token}"},
                )
                assert me.status_code == 200, me.text
                effective = MeResponse.model_validate_json(me.text).effective_authority
                capabilities = {
                    institution.institution_id: {
                        (
                            capability.module.value,
                            capability.sensitivity.value,
                            capability.permission.value,
                        )
                        for capability in institution.capabilities
                    }
                    for institution in effective.institution_capabilities
                }
                authority[actor.role] = StoryAuthority(
                    capabilities,
                    {binding.role_bundle for binding in bindings},
                    {
                        (
                            binding.role_bundle,
                            binding.institution_scope,
                            binding.institution_id,
                        )
                        for binding in bindings
                        if binding.role_bundle == RoleBundle.EXPORTER.value
                    },
                    {
                        (
                            binding.role_bundle,
                            binding.institution_scope,
                            binding.institution_id,
                        )
                        for binding in bindings
                        if binding.role_bundle
                        in {RoleBundle.SIGNER.value, RoleBundle.APPROVER.value}
                        and binding.institution_scope == InstitutionScope.INSTITUTION.value
                    },
                )
            return authority
        finally:
            retire(db_session, request)

    signed = story_authority(True, "SGN00001")
    unsigned = story_authority(False, "PLN00001")
    signed_bank = "BK-SGN00001"
    unsigned_bank = "BK-PLN00001"

    # The officers the ceremony and the downtime bundle depend on hold exactly
    # the return export the policy requires, and the backend actually serves it.
    for role in RETURN_EXPORT_ROLES:
        held = signed[role]
        capabilities, bundles, export_rows = held.capabilities, held.bundles, held.export_rows
        assert ("reg", "restricted", "export") in capabilities[signed_bank], role
        assert RoleBundle.EXPORTER.value in bundles, role
        # Confined to the story's own bank: an organization-scoped binding
        # matches every institution in the tenant, so the grant names one.
        assert export_rows == {("exporter", InstitutionScope.INSTITUTION.value, signed_bank)}, role
        assert set(capabilities) == {signed_bank}, role
    # A plain approver keeps its own bundle and stays refused: no analyst row,
    # and no export the ceremony could read a page with. The #185 ceremony
    # contract names its signing duty exactly: an exact-bank REG/restricted
    # SIGNER binding for the signing step, and an exact-bank APPROVER binding
    # so the checker slot keeps its APPROVE duty confined to the story's bank.
    approver = signed["approver"]
    assert approver.bundles == {
        RoleBundle.MEMBER.value,
        RoleBundle.APPROVER.value,
        RoleBundle.EXPORTER.value,
        RoleBundle.SIGNER.value,
    }
    assert approver.ceremony_rows == {
        ("signer", InstitutionScope.INSTITUTION.value, signed_bank),
        ("approver", InstitutionScope.INSTITUTION.value, signed_bank),
    }
    assert ("reg", "restricted", "export") not in unsigned["approver"].capabilities.get(
        unsigned_bank, set()
    )
    assert unsigned["approver"].bundles == {RoleBundle.MEMBER.value, RoleBundle.APPROVER.value}
    # Without a signing story there is no ceremony, so no exact-bank duty
    # binding is issued at all.
    assert unsigned["approver"].ceremony_rows == set()
    assert unsigned["validator"].bundles == {
        RoleBundle.MEMBER.value,
        RoleBundle.VIEWER.value,
        RoleBundle.VALIDATOR.value,
    }
    assert ("reg", "restricted", "export") not in unsigned["validator"].capabilities.get(
        unsigned_bank, set()
    )
    # No story role outside the signing set receives an analyst row at all.
    assert unsigned["approver"].export_rows == unsigned["validator"].export_rows == set()
    # The validator exports but is not a ceremony slot: it holds no SIGNER or
    # exact-bank APPROVER duty in either story.
    assert signed["validator"].ceremony_rows == unsigned["validator"].ceremony_rows == set()
    # An unrelated story role is untouched by the signing rule.
    assert unsigned["viewer"].bundles == signed["viewer"].bundles == {RoleBundle.MEMBER.value}


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
