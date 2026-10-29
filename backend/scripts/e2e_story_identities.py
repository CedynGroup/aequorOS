"""Provision and retire synthetic story principals in the disposable dashboard database."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.models import AttestationSignature, SignerIdentity
from app.attestation.service.identity import ensure_signer_identity
from app.attestation.service.keys import SignerKeyService
from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationAccessRequest, AuthorizationBinding
from app.identity.service import authentication, authorization, grant_administration, membership
from app.identity.service.organization_ownership import assign_initial_owner
from app.models import Bank, Job, MacroScenario, Organization, User
from app.policy.service.parameter_register import seed_tenant_register
from app.schemas.stress import MacroScenarioTransition
from app.services import macro_scenarios

SOURCE_ORG = "OR-DEM00001"
SOURCE_BANK = "BK-SAMP0001"
SOURCE_ADMIN = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
BASELINE_ROLES = frozenset(
    {
        "viewer",
        "grant_member",
        "invite_fresh",
        "forecast_member",
        "forecast_summary_member",
        "access_extra_member",
        "macro_viewer",
        "legacy_account_admin",
    }
)


class ActorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    user_id: UUID


class ScopedBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_role: str = "viewer"
    role_bundle: RoleBundle
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope
    data_scope_kind: DataScope = DataScope.ALL
    data_scope_values: list[str] = Field(default_factory=list)


class StoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    bank_id: str
    actors: list[ActorSpec]
    owned_tenant: bool = False
    signing: bool = False
    bindings: list[ScopedBinding] = Field(default_factory=list)
    owner_read_modules: list[ModuleScope] | None = None
    institution_type: str = "universal_bank"


class ActorRead(BaseModel):
    role: str
    user_id: UUID
    organization_id: str
    email: str
    display_name: str
    scalar_role: str
    authorization_version: int


class StoryRead(BaseModel):
    actors: list[ActorRead]


def _grant(
    db: Session,
    user: User,
    bundle: RoleBundle,
    module: ModuleScope = ModuleScope.ALL,
    sensitivity: SensitivityScope = SensitivityScope.ALL,
) -> None:
    _ = authorization.create_role_binding(
        db,
        organization_id=user.organization_id,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-story"),
        reason="Explicit authority for one synthetic dashboard story",
        commit=False,
    )


def _scalar_role(role: str, owned_tenant: bool) -> str:
    if role not in BASELINE_ROLES | {"admin", "analyst", "approver", "validator", "account_admin"}:
        raise ValueError(f"Unknown story profile: {role}")
    if role in {"admin", "analyst"}:
        return "account_admin" if owned_tenant and role == "admin" else "analyst"
    if role == "legacy_account_admin":
        return "account_admin"
    if role in {"approver", "account_admin"}:
        return role
    return "viewer"


def _apply_scoped_bindings(db: Session, request: StoryRequest, users: dict[str, User]) -> None:
    for binding in request.bindings:
        if binding.actor_role not in BASELINE_ROLES or binding.actor_role not in users:
            raise ValueError("Scoped story bindings require a baseline-only actor")
        if binding.role_bundle not in {RoleBundle.VIEWER, RoleBundle.ANALYST, RoleBundle.APPROVER}:
            raise ValueError("Scoped story bindings must use an operational human bundle")
        user = users[binding.actor_role]
        _ = authorization.create_role_binding(
            db,
            organization_id=request.organization_id,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=binding.role_bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                request.bank_id,
                binding.module_scope,
                binding.sensitivity_scope,
                binding.data_scope_kind,
                tuple(binding.data_scope_values),
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-story"),
            reason="Exact scoped authority for one synthetic dashboard story",
            commit=False,
        )


def _configure_owner_read(db: Session, request: StoryRequest, users: dict[str, User]) -> None:
    if request.owner_read_modules is None:
        return
    if not request.owned_tenant or "admin" not in users:
        raise ValueError("Owner read changes require the story's own initial owner")
    owner = users["admin"]
    defaults = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == owner.id,
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                AuthorizationBinding.role_bundle == RoleBundle.VIEWER.value,
                AuthorizationBinding.module_scope == ModuleScope.ALL.value,
            )
        )
    )
    assert len(defaults) == 1
    _ = grant_administration.revoke_scoped_grant(
        db,
        organization_id=request.organization_id,
        binding_id=defaults[0].id,
        actor_user_id=owner.id,
        reason="Replace the story owner's broad read sentence with exact module grants",
        commit=False,
    )
    for module in request.owner_read_modules:
        _grant(db, owner, RoleBundle.VIEWER, module)


def _prepare_story_bank(db: Session, request: StoryRequest) -> None:
    if not request.owned_tenant and (
        request.organization_id != SOURCE_ORG or request.bank_id != SOURCE_BANK
    ):
        raise ValueError("Shared story actors must target the disposable sample bank")
    if not request.owned_tenant and request.institution_type != "universal_bank":
        raise ValueError("Institution variants require a fresh story tenant")
    if request.owned_tenant:
        if request.organization_id == SOURCE_ORG or request.bank_id == SOURCE_BANK:
            raise ValueError("A story tenant must have distinct institution identities")
        db.add(Organization(id=request.organization_id, name="Story Tenant"))
        db.flush()
        bank = Bank(
            id=request.bank_id,
            organization_id=request.organization_id,
            name="Sample Bank Ltd",
            short_name="Sample",
            currency="GHS",
            jurisdiction_code="GH",
            license_type=(
                "universal"
                if request.institution_type == "universal_bank"
                else request.institution_type
            ),
            institution_type=request.institution_type,
        )
        db.add(bank)
        db.flush()
        seed_tenant_register(db, bank=bank, approved_by="e2e-story", approved_at=utc_now())


def provision(db: Session, request: StoryRequest) -> StoryRead:
    """Create identities and setup only; never prepare financial facts."""
    source = db.get(User, SOURCE_ADMIN)
    if source is None or source.organization_id != SOURCE_ORG or source.password_hash is None:
        raise ValueError("The synthetic bootstrap identity is required")
    _prepare_story_bank(db, request)

    users: list[tuple[ActorSpec, User]] = []
    # The sole owner must exist before another account administrator is added.
    for spec in sorted(request.actors, key=lambda item: item.role != "admin"):
        if spec.user_id == SOURCE_ADMIN or db.get(User, spec.user_id) is not None:
            raise ValueError("A story must use a fresh principal identity")
        role = spec.role
        scalar = _scalar_role(role, request.owned_tenant)
        name = (
            f"E2E {role.replace('_', ' ').title()}"
            if request.owned_tenant
            else f"Story {spec.user_id.hex[:8]} {role.replace('_', ' ').title()}"
        )
        user = User(
            id=spec.user_id,
            organization_id=request.organization_id,
            email=f"journey.{role}.{spec.user_id.hex}@aequoros.example",
            display_name=name,
            role=scalar,
            password_hash=source.password_hash,
            auth_provider="password",
        )
        db.add(user)
        db.flush()
        _ = membership.ensure_baseline_membership(
            db, user=user, granted_by_id="e2e-story", commit=False
        )
        if request.owned_tenant and role == "admin":
            _ = assign_initial_owner(
                db,
                organization_id=request.organization_id,
                candidate=user,
                granted_by_id="e2e-story",
                commit=False,
            )
        if role in {"admin", "analyst", "approver"} and not (
            request.owned_tenant and role == "admin"
        ):
            _grant(db, user, RoleBundle.APPROVER if role == "approver" else RoleBundle.ANALYST)
        elif role == "validator":
            _grant(db, user, RoleBundle.VIEWER)
            _grant(
                db, user, RoleBundle.VALIDATOR, ModuleScope.REGULATORY, SensitivityScope.RESTRICTED
            )
        elif role == "account_admin":
            _grant(
                db, user, RoleBundle.ACCOUNT_ADMIN, ModuleScope.ACCOUNT, SensitivityScope.RESTRICTED
            )
        users.append((spec, user))
    by_role = {spec.role: user for spec, user in users}
    _apply_scoped_bindings(db, request, by_role)
    _configure_owner_read(db, request, by_role)
    db.commit()
    if request.signing:
        ctx = TenantContext(organization_id=request.organization_id)
        service = SignerKeyService(db, ctx)
        for _, user in users:
            signer = ensure_signer_identity(db, ctx, user.id)
            _ = service.issue(
                signer_id=signer.signer_id,
                display_name=user.display_name or user.email,
                organization_name="aequorOS Synthetic Story",
            )
            db.commit()
    return StoryRead(
        actors=[
            ActorRead(
                role=spec.role,
                user_id=user.id,
                organization_id=user.organization_id,
                email=user.email,
                display_name=user.display_name or user.email,
                scalar_role=user.role,
                authorization_version=user.authorization_version,
            )
            for spec, user in users
        ]
    )


def retire(db: Session, request: StoryRequest) -> None:
    """End story authority while retaining principal, audit and signed evidence history."""
    ids = [spec.user_id for spec in request.actors]
    if SOURCE_ADMIN in ids:
        raise ValueError("The bootstrap operator is not a story principal")
    users = list(
        db.scalars(
            select(User).where(User.organization_id == request.organization_id, User.id.in_(ids))
        )
    )
    if any(not user.email.startswith("journey.") for user in users):
        raise ValueError("Refusing to retire a principal outside the story namespace")
    signer_ids = list(
        db.scalars(
            select(SignerIdentity.signer_id).where(
                SignerIdentity.organization_id == request.organization_id,
                SignerIdentity.user_id.in_(ids),
            )
        )
    )
    signatures = set(
        db.scalars(
            select(AttestationSignature.id).where(AttestationSignature.signer_id.in_(signer_ids))
        )
    )
    scenarios = list(
        db.scalars(
            select(MacroScenario).where(
                MacroScenario.organization_id == request.organization_id,
                MacroScenario.created_by.in_(ids),
                MacroScenario.status != "archived",
            )
        )
    )
    try:
        for scenario in scenarios:
            _ = macro_scenarios.archive_scenario(
                db,
                TenantContext(
                    organization_id=request.organization_id, actor_user_id=scenario.created_by
                ),
                scenario.id,
                MacroScenarioTransition(reason="Retire the synthetic story's governed clone"),
            )
    finally:
        db.rollback()
        _end_authority(db, request, users)
    assert not list(
        db.scalars(
            select(AuthorizationAccessRequest.id).where(
                AuthorizationAccessRequest.organization_id == request.organization_id,
                AuthorizationAccessRequest.requester_user_id.in_(ids),
                AuthorizationAccessRequest.status == "pending",
            )
        )
    )
    assert signatures == set(
        db.scalars(
            select(AttestationSignature.id).where(AttestationSignature.signer_id.in_(signer_ids))
        )
    )


def _end_authority(db: Session, request: StoryRequest, users: list[User]) -> None:
    ids = [user.id for user in users]
    for user in users:
        for binding in db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == request.organization_id,
                AuthorizationBinding.principal_user_id == user.id,
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                AuthorizationBinding.role_bundle != RoleBundle.MEMBER.value,
            )
        ):
            # Only these fresh principals are suspended. Deactivation below
            # ends membership and invalidates all sessions in this transaction.
            binding.status = BindingStatus.SUSPENDED.value
        authentication.deactivate_user(db, user, commit=False)
    if request.owned_tenant:
        for job in db.scalars(
            select(Job).where(
                Job.organization_id == request.organization_id, Job.status == "queued"
            )
        ):
            job.status = "failed"
            job.error = "Synthetic story retired before queued work was consumed"
            job.completed_at = utc_now()
    db.commit()
    assert not list(db.scalars(select(User.id).where(User.id.in_(ids), User.is_active.is_(True))))
    assert not list(
        db.scalars(
            select(AuthorizationBinding.id).where(
                AuthorizationBinding.principal_user_id.in_(ids),
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
            )
        )
    )


def main() -> None:
    action: Literal["provision", "retire"]
    if len(sys.argv) != 4 or sys.argv[1] not in {"provision", "retire"}:
        raise ValueError("Expected provision|retire, disposable database path and story JSON")
    action = "provision" if sys.argv[1] == "provision" else "retire"
    database = Path(sys.argv[2]).resolve()
    expected = Path(__file__).resolve().parent.parent / "dashboard/e2e/.tmp/e2e.db"
    if database != expected or not database.is_file():
        raise ValueError("Only the disposable dashboard database may be changed")
    request = StoryRequest.model_validate_json(sys.argv[3])
    with Session(create_engine(f"sqlite+pysqlite:///{database}")) as db:
        if action == "provision":
            print(provision(db, request).model_dump_json())
        else:
            retire(db, request)


if __name__ == "__main__":
    main()
