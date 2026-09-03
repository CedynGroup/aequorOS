"""Tenant-facing policy and lifecycle for indivisible scoped grants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.models import AuditEvent, AuthorizationBinding, Bank, Organization, User
from app.services import authentication, authorization


class GrantAdministrationError(ValueError):
    """A requested tenant grant mutation cannot be applied."""


class SodOutcome(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class SodFinding:
    code: str
    message: str


@dataclass(frozen=True)
class SodDecision:
    outcome: SodOutcome
    findings: tuple[SodFinding, ...] = ()


class SodPolicyBlocked(GrantAdministrationError):
    def __init__(self, decision: SodDecision) -> None:
        super().__init__("The scoped grant conflicts with separation-of-duties policy.")
        self.decision = decision


class AuthorityReviewChanged(GrantAdministrationError):
    def __init__(self) -> None:
        super().__init__("The selection changed and must be reviewed again.")


class DuplicateScopedGrant(GrantAdministrationError):
    def __init__(self, binding_id: UUID, authority_sentence: str) -> None:
        super().__init__(f"This authority already exists: {authority_sentence}")
        self.binding_id = binding_id
        self.authority_sentence = authority_sentence


@dataclass(frozen=True)
class GrantResult:
    binding: AuthorizationBinding
    sod_decision: SodDecision
    authority_sentence: str


_ROLE_LABELS = {
    RoleBundle.VIEWER: "Viewer",
    RoleBundle.AUDITOR: "Auditor",
    RoleBundle.ANALYST: "Analyst",
    RoleBundle.APPROVER: "Approver",
    RoleBundle.ACCOUNT_ADMIN: "Organization Administrator",
    RoleBundle.ORG_OWNER: "Organization Owner",
    RoleBundle.INTEGRATION_WRITER: "Integration Writer",
}

_MODULE_LABELS = {
    ModuleScope.ALL: "all modules",
    ModuleScope.LIQUIDITY: "Liquidity Monitoring",
    ModuleScope.CAPITAL: "Basel Capital",
    ModuleScope.IRRBB: "IRRBB",
    ModuleScope.FX: "Foreign Exchange",
    ModuleScope.FTP: "Funds Transfer Pricing",
    ModuleScope.FORECASTING: "Forecasting",
    ModuleScope.BEHAVIORAL: "Behavioral Models",
    ModuleScope.DATA: "Data Engine",
    ModuleScope.REGULATORY: "Regulatory Reporting",
    ModuleScope.RISK: "Risk & Limits",
    ModuleScope.MARKETS: "Markets",
    ModuleScope.ACCOUNT: "Account Administration",
    ModuleScope.AUDIT: "Audit",
}

_SENSITIVITY_LABELS = {
    SensitivityScope.ALL: "all sensitivity levels",
    SensitivityScope.PUBLISHED: "Published",
    SensitivityScope.AGGREGATED: "Aggregated",
    SensitivityScope.CONFIDENTIAL: "Confidential",
    SensitivityScope.RESTRICTED: "Restricted",
}

_OPERATIONAL_WRITE_BUNDLES = frozenset({RoleBundle.ANALYST, RoleBundle.APPROVER})
_ACCOUNT_ADMIN_BUNDLES = frozenset({RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def binding_is_effective(binding: AuthorizationBinding, *, now: datetime | None = None) -> bool:
    moment = _aware(now or utc_now())
    return (
        binding.status == BindingStatus.ACTIVE.value
        and binding.revoked_at is None
        and _aware(binding.valid_from) <= moment
        and (binding.valid_until is None or _aware(binding.valid_until) > moment)
    )


def _scope_overlaps(left: AuthorizationBinding, right: authorization.BindingScope) -> bool:
    institution_overlaps = (
        left.institution_scope == InstitutionScope.ORGANIZATION.value
        or right.institution_scope is InstitutionScope.ORGANIZATION
        or left.institution_id == right.institution_id
    )
    module_overlaps = (
        left.module_scope == ModuleScope.ALL.value
        or right.module_scope is ModuleScope.ALL
        or left.module_scope == right.module_scope.value
    )
    sensitivity_overlaps = (
        left.sensitivity_scope == SensitivityScope.ALL.value
        or right.sensitivity_scope is SensitivityScope.ALL
        or left.sensitivity_scope == right.sensitivity_scope.value
    )
    return institution_overlaps and module_overlaps and sensitivity_overlaps


def check_sod_policy(
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    role_bundle: RoleBundle,
    scope: authorization.BindingScope,
) -> SodDecision:
    """Return the server-authoritative assignment-time SoD decision.

    C9 is a hard block: an account administrator/owner cannot also receive an
    operational maker or checker bundle, and an operational maker/checker
    cannot be turned into an account administrator.  An overlapping
    Analyst/Approver pair is allowed because the engine deliberately unions
    bindings, but it is warned: maker-checker remains a non-bypassable
    per-object condition at action time.
    """

    rows = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == organization_id,
                AuthorizationBinding.principal_user_id == principal_user_id,
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
            )
        )
    )
    active = [row for row in rows if binding_is_effective(row)]
    existing_bundles = {RoleBundle(row.role_bundle) for row in active}
    findings: list[SodFinding] = []

    if role_bundle in _OPERATIONAL_WRITE_BUNDLES and existing_bundles & _ACCOUNT_ADMIN_BUNDLES:
        findings.append(
            SodFinding(
                code="c9_account_administration_operational_conflict",
                message=(
                    "Account administration and operational maker/checker authority "
                    "must remain separated for one identity."
                ),
            )
        )
    if role_bundle is RoleBundle.ACCOUNT_ADMIN and existing_bundles & _OPERATIONAL_WRITE_BUNDLES:
        findings.append(
            SodFinding(
                code="c9_account_administration_operational_conflict",
                message=(
                    "Account administration and operational maker/checker authority "
                    "must remain separated for one identity."
                ),
            )
        )

    counterpart = (
        RoleBundle.APPROVER
        if role_bundle is RoleBundle.ANALYST
        else RoleBundle.ANALYST
        if role_bundle is RoleBundle.APPROVER
        else None
    )
    if counterpart is not None and any(
        RoleBundle(row.role_bundle) is counterpart and _scope_overlaps(row, scope) for row in active
    ):
        findings.append(
            SodFinding(
                code="maker_checker_runtime_condition_required",
                message=(
                    "This identity will hold overlapping maker and checker grants. "
                    "A person still cannot approve work they prepared."
                ),
            )
        )

    blocked = any(finding.code.startswith("c9_") for finding in findings)
    if blocked:
        return SodDecision(SodOutcome.BLOCK, tuple(findings))
    if findings:
        return SodDecision(SodOutcome.WARN, tuple(findings))
    return SodDecision(SodOutcome.ALLOW)


def _scope_dict(binding: AuthorizationBinding) -> dict[str, str | None]:
    return {
        "institution_scope": binding.institution_scope,
        "institution_id": binding.institution_id,
        "module_scope": binding.module_scope,
        "sensitivity_scope": binding.sensitivity_scope,
    }


def compose_authority_sentence(
    *,
    principal_name: str,
    role_bundle: RoleBundle,
    institution_name: str,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
) -> str:
    role = _ROLE_LABELS[role_bundle]
    article = "an" if role[0].lower() in "aeiou" else "a"
    module = _MODULE_LABELS[module_scope]
    sensitivity = _SENSITIVITY_LABELS[sensitivity_scope]
    module_phrase = "across all modules" if module_scope is ModuleScope.ALL else f"in {module}"
    if sensitivity_scope is SensitivityScope.ALL:
        sensitivity_phrase = "covering all sensitivity levels"
    else:
        sensitivity_phrase = f"covering {sensitivity} data"
    return (
        f"{principal_name} is {article} {role} {module_phrase} for {institution_name}, "
        f"{sensitivity_phrase}."
    )


def authority_sentence(db: Session, binding: AuthorizationBinding) -> str:
    principal = db.scalar(
        select(User).where(
            User.id == binding.principal_user_id,
            User.organization_id == binding.organization_id,
        )
    )
    principal_name = (
        principal.display_name or principal.email if principal is not None else "This member"
    )
    if binding.institution_scope == InstitutionScope.ORGANIZATION.value:
        organization = db.get(Organization, binding.organization_id)
        institution_name = (
            f"every institution in {organization.name if organization else 'the organization'}"
        )
    else:
        bank = db.scalar(
            select(Bank).where(
                Bank.id == binding.institution_id,
                Bank.organization_id == binding.organization_id,
            )
        )
        institution_name = bank.name if bank is not None else str(binding.institution_id)
    return compose_authority_sentence(
        principal_name=principal_name,
        role_bundle=RoleBundle(binding.role_bundle),
        institution_name=institution_name,
        module_scope=ModuleScope(binding.module_scope),
        sensitivity_scope=SensitivityScope(binding.sensitivity_scope),
    )


def scoped_authority_sentence(  # noqa: PLR0913
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    role_bundle: RoleBundle,
    scope: authorization.BindingScope,
    lock_names: bool = False,
    locked_principal: User | None = None,
) -> str:
    principal = locked_principal
    if principal is None:
        principal_statement = select(User).where(
            User.id == principal_user_id,
            User.organization_id == organization_id,
        )
        if lock_names:
            principal_statement = principal_statement.with_for_update()
        principal = db.scalar(principal_statement)
    if principal is None:
        raise GrantAdministrationError("principal is not a member of the organization")

    if scope.institution_scope is InstitutionScope.ORGANIZATION:
        institution_statement = select(Organization).where(Organization.id == organization_id)
        if lock_names:
            institution_statement = institution_statement.with_for_update(read=True)
        organization = db.scalar(institution_statement)
        if organization is None:
            raise GrantAdministrationError("organization not found")
        institution_name = f"every institution in {organization.name}"
    else:
        institution_statement = select(Bank).where(
            Bank.id == scope.institution_id,
            Bank.organization_id == organization_id,
        )
        if lock_names:
            institution_statement = institution_statement.with_for_update(read=True)
        bank = db.scalar(institution_statement)
        if bank is None:
            raise GrantAdministrationError("institution is not part of the organization")
        institution_name = bank.name

    return compose_authority_sentence(
        principal_name=principal.display_name or principal.email,
        role_bundle=role_bundle,
        institution_name=institution_name,
        module_scope=scope.module_scope,
        sensitivity_scope=scope.sensitivity_scope,
    )


def _tenant_actor_id(actor: authorization.GrantorRef) -> UUID | None:
    if actor.kind is not GrantorType.TENANT_USER:
        return None
    return UUID(actor.identifier)


def _record_grant_audit(
    db: Session,
    binding: AuthorizationBinding,
    actor: authorization.GrantorRef,
    sentence: str,
) -> None:
    db.add(
        AuditEvent(
            organization_id=binding.organization_id,
            actor_user_id=_tenant_actor_id(actor),
            event_type="authorization.binding_granted",
            entity_type="authorization_binding",
            entity_id=str(binding.id),
            details={
                "grantor_type": actor.kind.value,
                "grantor_id": actor.identifier,
                "grantee_user_id": str(binding.principal_user_id),
                "role_bundle": binding.role_bundle,
                "scope": _scope_dict(binding),
                "occurred_at": binding.granted_at.isoformat(),
                "reason": binding.grant_reason,
                "authority_sentence": sentence,
            },
        )
    )


def validate_public_grant(role_bundle: RoleBundle, scope: authorization.BindingScope) -> None:
    if role_bundle in {RoleBundle.ORG_OWNER, RoleBundle.INTEGRATION_WRITER}:
        raise GrantAdministrationError("this role bundle is not grantable from Members")
    if role_bundle is RoleBundle.ACCOUNT_ADMIN and (
        scope.institution_scope is not InstitutionScope.ORGANIZATION
        or scope.institution_id is not None
        or scope.module_scope is not ModuleScope.ACCOUNT
        or scope.sensitivity_scope is not SensitivityScope.ALL
    ):
        raise GrantAdministrationError(
            "organization administrators require organization-wide Account Administration "
            "coverage at all sensitivity levels"
        )


def create_scoped_grant(  # noqa: PLR0913 - one complete binding is explicit
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    role_bundle: RoleBundle,
    scope: authorization.BindingScope,
    actor_user_id: UUID,
    reason: str,
    expected_authority_sentence: str,
    commit: bool = True,
) -> GrantResult:
    validate_public_grant(role_bundle, scope)
    principal = db.scalar(
        select(User)
        .where(
            User.id == principal_user_id,
            User.organization_id == organization_id,
        )
        .with_for_update()
    )
    if principal is None:
        raise GrantAdministrationError("principal is not a member of the organization")
    sentence = scoped_authority_sentence(
        db,
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        role_bundle=role_bundle,
        scope=scope,
        lock_names=True,
        locked_principal=principal,
    )
    if expected_authority_sentence != sentence:
        raise AuthorityReviewChanged

    active = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == organization_id,
                AuthorizationBinding.principal_user_id == principal_user_id,
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
            )
        )
    )
    duplicate = next(
        (
            binding
            for binding in active
            if binding_is_effective(binding)
            and binding.role_bundle == role_bundle.value
            and binding.institution_scope == scope.institution_scope.value
            and binding.institution_id == scope.institution_id
            and binding.module_scope == scope.module_scope.value
            and binding.sensitivity_scope == scope.sensitivity_scope.value
        ),
        None,
    )
    if duplicate is not None:
        raise DuplicateScopedGrant(duplicate.id, sentence)
    decision = check_sod_policy(
        db,
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        role_bundle=role_bundle,
        scope=scope,
    )
    if decision.outcome is SodOutcome.BLOCK:
        raise SodPolicyBlocked(decision)
    actor = authorization.GrantorRef(GrantorType.TENANT_USER, str(actor_user_id))
    try:
        binding = authorization.create_role_binding(
            db,
            organization_id=organization_id,
            principal_user_id=principal_user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=scope,
            grantor=actor,
            reason=reason,
            valid_from=utc_now(),
            valid_until=None,
            commit=False,
        )
    except authorization.AuthorizationInvariantError as exc:
        raise GrantAdministrationError(str(exc)) from exc
    _record_grant_audit(db, binding, actor, sentence)
    db.flush()
    if commit:
        db.commit()
        db.refresh(binding)
    return GrantResult(binding, decision, sentence)


def revoke_scoped_grant(  # noqa: PLR0913 - complete actor and target context is explicit
    db: Session,
    *,
    organization_id: str,
    binding_id: UUID,
    actor_user_id: UUID,
    reason: str,
    commit: bool = True,
) -> AuthorizationBinding:
    revoke_reason = reason.strip()
    if not revoke_reason:
        raise GrantAdministrationError("a revocation reason is required")
    binding = db.scalar(
        select(AuthorizationBinding)
        .where(
            AuthorizationBinding.id == binding_id,
            AuthorizationBinding.organization_id == organization_id,
        )
        .with_for_update()
    )
    if binding is None:
        raise GrantAdministrationError("scoped grant not found")
    if binding.role_bundle == RoleBundle.ORG_OWNER.value:
        raise GrantAdministrationError(
            "organization ownership cannot be revoked from the Members grant flow"
        )
    if binding.status != BindingStatus.ACTIVE.value:
        raise GrantAdministrationError("only an active scoped grant can be revoked")

    principal = db.scalar(
        select(User)
        .where(
            User.id == binding.principal_user_id,
            User.organization_id == organization_id,
        )
        .with_for_update(key_share=True)
    )
    if principal is None:
        raise GrantAdministrationError("grant principal is not a member of the organization")
    actor = db.scalar(
        select(User.id).where(
            User.id == actor_user_id,
            User.organization_id == organization_id,
            User.is_active.is_(True),
        )
    )
    if actor is None:
        raise GrantAdministrationError("revoker is not active in the organization")

    moment = utc_now()
    binding.status = BindingStatus.REVOKED.value
    binding.revoked_at = moment
    binding.revoked_by_type = GrantorType.TENANT_USER.value
    binding.revoked_by_id = str(actor_user_id)
    binding.revoked_reason = revoke_reason
    sentence = authority_sentence(db, binding)
    db.add(
        AuditEvent(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type="authorization.binding_revoked",
            entity_type="authorization_binding",
            entity_id=str(binding.id),
            details={
                "revoker_type": GrantorType.TENANT_USER.value,
                "revoker_id": str(actor_user_id),
                "grantee_user_id": str(binding.principal_user_id),
                "role_bundle": binding.role_bundle,
                "scope": _scope_dict(binding),
                "occurred_at": moment.isoformat(),
                "reason": revoke_reason,
                "authority_sentence": sentence,
            },
        )
    )
    authorization.invalidate_user_authorization(
        db,
        organization_id=organization_id,
        user_id=principal.id,
        reason=f"role binding revoked: {revoke_reason}",
        commit=False,
        locked_user=principal,
    )
    db.flush()
    if commit:
        db.commit()
        db.refresh(binding)
    return binding


def approve_sso_access_request_with_grant(  # noqa: PLR0913
    db: Session,
    *,
    organization_id: str,
    user_id: UUID,
    role_bundle: RoleBundle,
    scope: authorization.BindingScope,
    actor_user_id: UUID,
    reason: str,
    expected_authority_sentence: str,
) -> GrantResult:
    """Activate a verified JIT identity only as one complete grant is created."""

    user = authentication.get_sso_access_request(
        db, organization_id=organization_id, user_id=user_id, lock=True
    )
    user.is_active = True
    # Binding authority is the only new authority.  The scalar remains a
    # compatibility-only read role until endpoint rollout (#144 and later).
    user.role = "viewer"
    result = create_scoped_grant(
        db,
        organization_id=organization_id,
        principal_user_id=user.id,
        role_bundle=role_bundle,
        scope=scope,
        actor_user_id=actor_user_id,
        reason=reason,
        expected_authority_sentence=expected_authority_sentence,
        commit=False,
    )
    db.commit()
    db.refresh(result.binding)
    authentication.provision_signer_identity(db, user)
    db.commit()
    return result
