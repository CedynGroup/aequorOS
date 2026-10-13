"""Low-level authorization binding evaluation and persistence operations.

The Org Owner administration API wraps these primitives with delegation,
separation-of-duties, audit, and presentation policy. Creating a binding checks
tenant ownership and updates the user's authorization version in the same
transaction, which also revokes all their refresh tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    DATA_SCOPE_VALUE_MAX_LENGTH,
    AuthorizationDecision,
    BindingGrant,
    BindingStatus,
    ConditionCheck,
    ConditionKind,
    DataScope,
    GrantorType,
    GrantReasonCategory,
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
    normalise_data_scope_values,
    principal_bundle_compatible,
)
from app.core.authorization import (
    evaluate_permission as evaluate_grants,
)
from app.core.config import get_settings
from app.core.observability import authorization_binding_decision
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.user import User
from app.identity.schemas.authorization import (
    DataScopeRead,
    EffectiveAuthorityRead,
    EffectiveCapabilityRead,
    InstitutionCapabilitiesRead,
)
from app.identity.service import authentication
from app.models.audit_event import AuditEvent
from app.models.operator import OperatorUser


class AuthorizationInvariantError(ValueError):
    """A requested binding would break the authorization rules."""


@dataclass(frozen=True)
class GrantorRef:
    kind: GrantorType
    identifier: str


@dataclass(frozen=True)
class BindingScope:
    institution_scope: InstitutionScope
    institution_id: str | None
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope
    #: WHICH SLICE of the institution's book.  Defaulted at the END of the
    #: dataclass so every existing positional construction keeps meaning the
    #: whole institution, which is what those grants have always meant.
    data_scope: DataScope = DataScope.ALL
    data_scope_values: tuple[str, ...] = ()


def binding_scope_details(binding: AuthorizationBinding) -> dict[str, object]:
    return {
        "institution_scope": binding.institution_scope,
        "institution_id": binding.institution_id,
        "module_scope": binding.module_scope,
        "sensitivity_scope": binding.sensitivity_scope,
        "data_scope_kind": binding.data_scope_kind,
        "data_scope_values": list(binding.data_scope_values or ()),
    }


# ---------------------------------------------------------------------------
# effective data scope: the one authority on WHICH SLICE a principal reads
# ---------------------------------------------------------------------------

#: ``mixed`` and ``none`` are deliberately NOT storable (the column vocabulary
#: is :class:`~app.core.authorization.DataScope`).  They only ever describe a
#: derived union.
DataScopeKind = Literal["all", "branch", "region", "mixed", "none"]


@dataclass(frozen=True, slots=True)
class EffectiveDataScope:
    """The union of one principal's matched bindings' data scopes, as DECLARED.

    Resolving a declared scope to rows is the BI layer's job, not this one's: a
    region becomes branch codes through ``bi_dim_branch.region``, and the
    account plane must not read ``bi_*`` tables.  So this says what the bindings
    SAY, and the BI compiler turns it into an unremovable filter.
    """

    kind: DataScopeKind = "all"
    branches: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    @property
    def whole_institution(self) -> bool:
        return self.kind == "all"

    @property
    def serves_nothing(self) -> bool:
        return self.kind == "none"


ALL_INSTITUTION_DATA: Final[EffectiveDataScope] = EffectiveDataScope(kind="all")
#: Nothing authorized the read.  Explicit rather than ``all`` so a reader cannot
#: reach the whole institution by accident: ``whole_institution`` is False, so a
#: forgetful reader falls into the scoped path where an empty value set yields
#: no rows.
NO_INSTITUTION_DATA: Final[EffectiveDataScope] = EffectiveDataScope(kind="none")


def reduce_data_scope(grants: Sequence[BindingGrant]) -> EffectiveDataScope:
    """Reduce already-loaded, already-matched bindings to one effective scope.

    The reduction rules, in order:

    1. **No grants at all → nothing.**  Returning the whole institution here
       would be the fail-open this phase exists to close.
    2. **Any grant of kind ``all`` → the whole institution.**  Bindings OR, so
       the WIDEST wins; narrowing them would revoke authority the Org Owner
       granted.
    3. Otherwise union the ``branch`` rows' values and the ``region`` rows',
       and name the union ``branch`` / ``region`` / ``mixed`` accordingly.
    """

    if not grants:
        return NO_INSTITUTION_DATA
    if any(grant.data_scope is DataScope.ALL for grant in grants):
        return ALL_INSTITUTION_DATA
    branches: set[str] = set()
    regions: set[str] = set()
    for grant in grants:
        target = branches if grant.data_scope is DataScope.BRANCH else regions
        target.update(grant.data_scope_values)
    if branches and regions:
        kind: DataScopeKind = "mixed"
    elif regions:
        kind = "region"
    else:
        kind = "branch"
    return EffectiveDataScope(
        kind=kind,
        branches=tuple(sorted(branches)),
        regions=tuple(sorted(regions)),
    )


def load_effective_grants(
    db: Session,
    *,
    organization_id: str,
    binding_ids: Sequence[UUID],
) -> dict[UUID, BindingGrant]:
    """The EFFECTIVE grants among the named ids, keyed by id, in one query.

    Exists so a caller that must reduce SEVERAL groups of ids — one per resource
    it evaluated — pays one query rather than one per group, and still reduces
    through the same pure :func:`reduce_data_scope` rather than reading the
    columns a second time. ``authorize_query`` is that caller: unioning its
    groups before reducing was audit finding A10-01.

    The id list is a SELECTOR, never authority. Rows are filtered to the
    organization, to ``status = 'active'`` and to the validity window, so an id
    belonging to another tenant, or one since revoked or expired, is simply
    absent from the result.
    """

    if not binding_ids:
        return {}
    rows = db.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.id.in_(set(binding_ids)),
            AuthorizationBinding.status == BindingStatus.ACTIVE.value,
        )
    )
    return {row.id: _binding_grant(row) for row in rows if binding_is_effective(row)}


def effective_data_scope(
    db: Session,
    *,
    organization_id: str,
    binding_ids: Sequence[UUID],
) -> EffectiveDataScope:
    """The declared slice the named bindings admit, re-read from the database.

    For ONE resource's matched bindings. Reducing ids matched against DIFFERENT
    resources through this function is the A10-01 defect: rule 2 of
    :func:`reduce_data_scope` ("any ``all`` wins") is sound only within a single
    resource's matches, because across resources an ``all`` binding that
    authorized one of them would discard the narrowing that applies to another.
    A caller with several groups uses :func:`load_effective_grants` and reduces
    each group, then combines the results with a narrowest-wins rule.

    The caller's id list is a SELECTOR, never authority. The rows are re-read
    and filtered to the organization, to ``status = 'active'`` and to the
    validity window, so a binding id belonging to another tenant — or one that
    has since been revoked or expired — contributes nothing, and a list whose
    every id falls away is indistinguishable from an empty list: both serve
    nothing.
    """

    grants = load_effective_grants(db, organization_id=organization_id, binding_ids=binding_ids)
    return reduce_data_scope(list(grants.values()))


def data_scope_read(scope: EffectiveDataScope) -> DataScopeRead:
    return DataScopeRead(
        kind=scope.kind,
        branches=list(scope.branches),
        regions=list(scope.regions),
    )


def binding_is_effective(
    binding: AuthorizationBinding,
    *,
    now: datetime | None = None,
) -> bool:
    moment = now or utc_now()

    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return (
        binding.status == BindingStatus.ACTIVE.value
        and binding.revoked_at is None
        and aware(binding.valid_from) <= aware(moment)
        and (binding.valid_until is None or aware(binding.valid_until) > aware(moment))
    )


def institution_grain_decision(
    decision: AuthorizationDecision, *, whole_institution: bool
) -> AuthorizationDecision:
    """Refuse institution figures unless the matched grants cover the whole book.

    Preserve the binding trace so telemetry distinguishes a scope refusal from
    a missing grant. Row-filtering surfaces must instead apply their effective
    data scope to every row and count.
    """
    if decision.allowed and not whole_institution:
        return replace(
            decision, allowed=False, reason="institution_grain_requires_whole_institution"
        )
    return decision


def record_binding_grant_audit(
    db: Session,
    *,
    binding: AuthorizationBinding,
    grantor: GrantorRef,
    authority_sentence: str,
) -> None:
    actor_user_id = UUID(grantor.identifier) if grantor.kind is GrantorType.TENANT_USER else None
    db.add(
        AuditEvent(
            organization_id=binding.organization_id,
            actor_user_id=actor_user_id,
            event_type="authorization.binding_granted",
            entity_type="authorization_binding",
            entity_id=str(binding.id),
            details={
                "grantor_type": grantor.kind.value,
                "grantor_id": grantor.identifier,
                "grantee_user_id": str(binding.principal_user_id),
                "role_bundle": binding.role_bundle,
                "scope": binding_scope_details(binding),
                "occurred_at": binding.granted_at.isoformat(),
                "reason": binding.grant_reason,
                "reason_category": binding.grant_reason_category,
                "reference": binding.grant_reference,
                "authority_sentence": authority_sentence,
            },
        )
    )


def record_binding_revoke_audit(
    db: Session,
    *,
    binding: AuthorizationBinding,
    actor_user_id: UUID | None,
    authority_sentence: str,
) -> None:
    db.add(
        AuditEvent(
            organization_id=binding.organization_id,
            actor_user_id=actor_user_id,
            event_type="authorization.binding_revoked",
            entity_type="authorization_binding",
            entity_id=str(binding.id),
            details={
                "revoker_type": binding.revoked_by_type,
                "revoker_id": binding.revoked_by_id,
                "grantee_user_id": str(binding.principal_user_id),
                "role_bundle": binding.role_bundle,
                "scope": binding_scope_details(binding),
                "occurred_at": (
                    binding.revoked_at.isoformat() if binding.revoked_at is not None else None
                ),
                "reason": binding.revoked_reason,
                "authority_sentence": authority_sentence,
            },
        )
    )


_ORGANIZATION_MODULES = (Module.ACCOUNT, Module.AUDIT)
_INSTITUTION_MODULES = tuple(module for module in Module if module not in _ORGANIZATION_MODULES)
_REQUIRED_RUNTIME_CONDITIONS: dict[Permission, tuple[ConditionKind, ...]] = {
    Permission.APPROVE: (ConditionKind.MAKER_CHECKER,),
    Permission.SIGN_OFF: (ConditionKind.MAKER_CHECKER, ConditionKind.STEP_UP),
    # Filing to the regulator is four-eyes by construction: the caller must
    # supply the maker/checker verdict or the evaluation denies. A future route
    # that grants SUBMIT without establishing who prepared the return is refused
    # rather than allowed — the seam this whole permission exists to defend
    # (docs/filing_workflow_redesign.md §3.3).
    Permission.SUBMIT: (ConditionKind.MAKER_CHECKER,),
}


class TenantPrincipalContext(Protocol):
    @property
    def organization_id(self) -> str: ...

    @property
    def actor_user_id(self) -> UUID | None: ...

    @property
    def authorization_version(self) -> int | None: ...


def principal_locator(ctx: TenantPrincipalContext) -> PrincipalLocator:
    """Build the evaluator principal represented by an authenticated tenant context."""

    organization_id = ctx.organization_id
    principal_id = ctx.actor_user_id
    if principal_id is None:
        raise AuthorizationInvariantError("authenticated tenant identity has no principal")
    authorization_version = ctx.authorization_version
    principal_type = (
        PrincipalType.HUMAN if authorization_version is not None else PrincipalType.MACHINE
    )
    return PrincipalLocator(organization_id, principal_id, principal_type)


def runtime_condition_checks(
    permission: Permission,
    _resource: ResourceLocator,
    workflow_conditions: Sequence[ConditionCheck] = (),
) -> tuple[ConditionCheck, ...]:
    supplied = tuple(workflow_conditions)
    supplied_kinds = {condition.kind for condition in supplied}
    missing = tuple(
        ConditionCheck(
            kind=kind,
            passed=False,
            reason=f"required runtime context is not established: {kind.value}",
        )
        for kind in _REQUIRED_RUNTIME_CONDITIONS.get(permission, ())
        if kind not in supplied_kinds
    )
    return (*supplied, *missing)


def request_wide_condition_checks() -> tuple[ConditionCheck, ...]:
    demo_mode = get_settings().app.demo_mode
    return (
        ConditionCheck(
            kind=ConditionKind.DEMO_MODE,
            passed=not demo_mode,
            reason=(
                "demo mode is disabled" if not demo_mode else "demo mode blocks effective authority"
            ),
        ),
    )


def _effective_capabilities(  # noqa: PLR0913 - projection requires the complete tuple
    principal: PrincipalLocator,
    resource_scope: InstitutionScope,
    institution_id: str | None,
    modules: Sequence[Module],
    bindings: Sequence[BindingGrant],
    request_conditions: Sequence[ConditionCheck],
) -> list[EffectiveCapabilityRead]:
    capabilities: list[EffectiveCapabilityRead] = []
    by_id = {grant.binding_id: grant for grant in bindings}
    for module in modules:
        for sensitivity in Sensitivity:
            resource = ResourceLocator(
                principal.organization_id,
                resource_scope,
                institution_id,
                module,
                sensitivity,
            )
            for permission in Permission:
                decision = evaluate_grants(
                    principal,
                    permission,
                    resource,
                    bindings,
                    conditions=request_conditions,
                )
                if decision.allowed:
                    # Reduced from the bindings that matched THIS capability's
                    # own resource, so the scope cannot overstate: a principal
                    # holding Credit by branch and Liquidity institution-wide
                    # gets the true answer on each row rather than one answer
                    # standing for both.  No extra query — the grants are the
                    # ones already loaded for the evaluation.
                    capabilities.append(
                        EffectiveCapabilityRead(
                            module=module,
                            sensitivity=sensitivity,
                            permission=permission,
                            requires_contextual_authorization=bool(
                                _REQUIRED_RUNTIME_CONDITIONS.get(permission)
                            ),
                            data_scope=data_scope_read(
                                reduce_data_scope(
                                    [
                                        by_id[binding_id]
                                        for binding_id in decision.matching_binding_ids
                                        if binding_id in by_id
                                    ]
                                )
                            ),
                        )
                    )
    return capabilities


def project_effective_authority(
    db: Session,
    ctx: TenantPrincipalContext,
    institutions: Sequence[Bank],
    *,
    failure_surface: str,
) -> EffectiveAuthorityRead:
    """Project exact evaluator-derived capabilities without alternate authority sources."""

    principal = principal_locator(ctx)
    try:
        request_conditions = request_wide_condition_checks()
        principal_active, bindings = _load_principal_grants(db, principal)
        user = db.scalar(
            select(User).where(
                User.id == principal.principal_id,
                User.organization_id == principal.organization_id,
            )
        )
        if user is None:
            raise AuthorizationInvariantError("authenticated principal is not available")
        effective_bindings: Sequence[BindingGrant] = bindings if principal_active else ()
        organization_capabilities = _effective_capabilities(
            principal,
            InstitutionScope.ORGANIZATION,
            None,
            _ORGANIZATION_MODULES,
            effective_bindings,
            request_conditions,
        )
        institution_capabilities: list[InstitutionCapabilitiesRead] = []
        for institution in institutions:
            if institution.organization_id != principal.organization_id:
                continue
            capabilities = _effective_capabilities(
                principal,
                InstitutionScope.INSTITUTION,
                institution.id,
                _INSTITUTION_MODULES,
                effective_bindings,
                request_conditions,
            )
            if capabilities:
                institution_capabilities.append(
                    InstitutionCapabilitiesRead(
                        institution_id=institution.id,
                        capabilities=capabilities,
                    )
                )
        return EffectiveAuthorityRead(
            authv=user.authorization_version,
            organization_capabilities=organization_capabilities,
            institution_capabilities=institution_capabilities,
        )
    except Exception as exc:
        record_binding_evaluation_failure(
            principal,
            Permission.VIEW,
            ResourceLocator(
                principal.organization_id,
                InstitutionScope.ORGANIZATION,
                None,
                Module.ACCOUNT,
                Sensitivity.CONFIDENTIAL,
            ),
            surface=failure_surface,
            error=exc,
        )
        raise


def project_examiner_authority(
    ctx: TenantPrincipalContext,
    institutions: Sequence[Bank],
) -> EffectiveAuthorityRead:
    if (
        getattr(ctx, "impersonation_context", None) is None
        or getattr(ctx, "actor_operator", None) is None
    ):
        raise AuthorizationInvariantError("verified examiner context is required")
    if not all(condition.passed for condition in request_wide_condition_checks()):
        return EffectiveAuthorityRead(
            authv=0,
            organization_capabilities=[],
            institution_capabilities=[],
        )
    capabilities = [
        EffectiveCapabilityRead(
            module=module,
            sensitivity=sensitivity,
            permission=Permission.VIEW,
            requires_contextual_authorization=False,
            # An examiner reads the whole book by construction — the position is
            # a read-everything supervisory seat with no binding behind it, so
            # there is no declared slice to narrow to and none may be invented.
            data_scope=data_scope_read(ALL_INSTITUTION_DATA),
        )
        for module in _INSTITUTION_MODULES
        for sensitivity in Sensitivity
    ]
    return EffectiveAuthorityRead(
        authv=0,
        organization_capabilities=[],
        institution_capabilities=[
            InstitutionCapabilitiesRead(
                institution_id=institution.id,
                capabilities=capabilities,
            )
            for institution in institutions
            if institution.organization_id == ctx.organization_id
        ],
    )


def _principal_type(user: User) -> PrincipalType:
    return PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN


def _binding_grant(binding: AuthorizationBinding) -> BindingGrant:
    return BindingGrant(
        binding_id=binding.id,
        organization_id=binding.organization_id,
        principal_id=binding.principal_user_id,
        principal_type=PrincipalType(binding.principal_type),
        role_bundle=RoleBundle(binding.role_bundle),
        institution_scope=InstitutionScope(binding.institution_scope),
        institution_id=binding.institution_id,
        module_scope=ModuleScope(binding.module_scope),
        sensitivity_scope=SensitivityScope(binding.sensitivity_scope),
        status=BindingStatus(binding.status),
        valid_from=binding.valid_from,
        valid_until=binding.valid_until,
        revoked_at=binding.revoked_at,
        data_scope=DataScope(binding.data_scope_kind),
        data_scope_values=tuple(binding.data_scope_values or ()),
    )


def data_scope_column_values(scope: BindingScope) -> list[str] | None:
    """The ``data_scope_values`` column for ``scope``, or refuse the shape.

    ``all`` is the only kind whose list is NULL, and a narrow kind must name at
    least one value: the database CHECK says the same thing, but a caller
    deserves a sentence rather than an IntegrityError, and the service must not
    depend on the CHECK to be the only guard.
    """

    values = normalise_data_scope_values(scope.data_scope_values)
    if scope.data_scope is DataScope.ALL:
        if values:
            raise AuthorizationInvariantError(
                "a whole-institution data scope must not name branches or regions"
            )
        return None
    if not values:
        raise AuthorizationInvariantError(
            "a branch or region data scope must name at least one branch or region"
        )
    overlong = sorted(value for value in values if len(value) > DATA_SCOPE_VALUE_MAX_LENGTH)
    if overlong:
        raise AuthorizationInvariantError(
            "a branch or region longer than "
            f"{DATA_SCOPE_VALUE_MAX_LENGTH} characters cannot match any branch"
        )
    return list(values)


def _validate_scope(db: Session, organization_id: str, scope: BindingScope) -> None:
    if scope.data_scope is not DataScope.ALL and scope.module_scope is not ModuleScope.CREDIT:
        raise AuthorizationInvariantError(
            "Branch and region narrowing is supported only for Credit"
        )
    # A NARROW data scope requires exact institution coverage, and this is where
    # that is enforced rather than only in the request schema (audit A10-05). A
    # branch code belongs to one institution's core banking system, so two sibling
    # banks of one organization can share a code that means two different books;
    # an organization-wide binding naming ``B1`` therefore does not describe a
    # slice anybody can resolve. The Pydantic layer already refuses it with a
    # sentence, but a schema only guards the one route that uses it — any other
    # service caller could write the shape the composer calls impossible.
    #
    # The DATABASE check deliberately still permits it, so that an
    # organization-wide REGION grant (regions being declared per bank in the same
    # register, and plausibly shared) can be designed later without a migration.
    # Until that is designed, the service refuses it.
    if scope.data_scope is not DataScope.ALL and (
        scope.institution_scope is InstitutionScope.ORGANIZATION
    ):
        raise AuthorizationInvariantError(
            "a branch or region data scope requires exact institution coverage"
        )
    if scope.institution_scope is InstitutionScope.ORGANIZATION:
        if scope.institution_id is not None:
            raise AuthorizationInvariantError(
                "organization-wide scope must not carry an institution id"
            )
        return
    if scope.institution_id is None:
        raise AuthorizationInvariantError("institution-specific scope requires an institution id")
    institution = db.scalar(
        select(Bank.id).where(
            Bank.id == scope.institution_id,
            Bank.organization_id == organization_id,
        )
    )
    if institution is None:
        raise AuthorizationInvariantError(
            "institution does not belong to the binding's organization"
        )


def _validate_grantor(db: Session, organization_id: str, grantor: GrantorRef) -> None:
    identifier = grantor.identifier.strip()
    if not identifier:
        raise AuthorizationInvariantError("grantor identifier must be non-empty")
    if grantor.kind is GrantorType.SYSTEM:
        return
    try:
        grantor_id = UUID(identifier)
    except ValueError as exc:
        raise AuthorizationInvariantError("user/operator grantor id must be a UUID") from exc
    if grantor.kind is GrantorType.TENANT_USER:
        user = db.scalar(
            select(User.id).where(
                User.id == grantor_id,
                User.organization_id == organization_id,
                User.is_active.is_(True),
            )
        )
        if user is None:
            raise AuthorizationInvariantError(
                "tenant-user grantor is not active in the binding's organization"
            )
        return
    operator = db.scalar(
        select(OperatorUser.id).where(
            OperatorUser.id == grantor_id,
            OperatorUser.is_active.is_(True),
        )
    )
    if operator is None:
        raise AuthorizationInvariantError("operator grantor is not active")


def invalidate_user_authorization(  # noqa: PLR0913 - lock optimization is explicit
    db: Session,
    *,
    organization_id: str,
    user_id: UUID,
    reason: str,
    refresh_reason: str = "authorization_changed",
    commit: bool = True,
    locked_user: User | None = None,
) -> int:
    """Update the user's authorization version and revoke all their refresh tokens.

    Any change to a user's role, scope, status, or security settings must call
    this in the same transaction. The user-row lock is shared with refresh
    token rotation, so a concurrent token refresh cannot skip the revocation.
    If the caller already holds a FOR UPDATE lock on the user (e.g. from
    ``create_role_binding``), pass it via ``locked_user`` to avoid a second
    query.
    """

    if not reason.strip():
        raise AuthorizationInvariantError("authorization changes require a reason")
    if locked_user is not None:
        user = locked_user
    else:
        user = db.scalar(
            select(User)
            .where(User.id == user_id, User.organization_id == organization_id)
            .with_for_update(key_share=True)
        )
    if user is None:
        raise AuthorizationInvariantError("principal is not a member of the organization")
    user.authorization_version += 1
    authentication.revoke_user_refresh_tokens(
        db,
        user.id,
        reason=refresh_reason,
        commit=False,
    )
    db.flush()
    if commit:
        db.commit()
    return user.authorization_version


def create_role_binding(  # noqa: PLR0913 - every binding dimension is explicit
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    principal_type: PrincipalType,
    role_bundle: RoleBundle,
    scope: BindingScope,
    grantor: GrantorRef,
    reason: str,
    reason_category: GrantReasonCategory = GrantReasonCategory.OTHER,
    reference: str | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    commit: bool = True,
) -> AuthorizationBinding:
    """Create a single binding and invalidate the user's existing sessions.

    This remains a low-level service function, not a tenant admin API. Tenant
    callers must use ``grant_administration.create_scoped_grant``, which adds
    delegation, separation-of-duties, audit, and public-bundle policy.
    """

    grant_reason = reason.strip()
    if not grant_reason:
        raise AuthorizationInvariantError("a role binding requires a grant reason")
    principal = db.scalar(
        select(User)
        .where(
            User.id == principal_user_id,
            User.organization_id == organization_id,
        )
        .with_for_update(key_share=True)
    )
    if principal is None:
        raise AuthorizationInvariantError("principal is not a member of the organization")
    actual_type = _principal_type(principal)
    if principal_type is not actual_type:
        raise AuthorizationInvariantError("principal type does not match the identity record")
    if not principal_bundle_compatible(principal_type, role_bundle):
        if principal_type is PrincipalType.MACHINE:
            raise AuthorizationInvariantError(
                "machine principals require a machine permission bundle"
            )
        raise AuthorizationInvariantError("human principals cannot receive a machine bundle")
    if role_bundle is RoleBundle.MEMBER and (
        principal_type is not PrincipalType.HUMAN
        or grantor.kind is not GrantorType.SYSTEM
        or scope.institution_scope is not InstitutionScope.ORGANIZATION
        or scope.institution_id is not None
        or scope.module_scope is not ModuleScope.ACCOUNT
        or scope.sensitivity_scope is not SensitivityScope.RESTRICTED
        or scope.data_scope is not DataScope.ALL
        or valid_until is not None
    ):
        raise AuthorizationInvariantError(
            "baseline membership requires a permanent system-granted "
            "organization-wide Account/restricted human binding"
        )
    data_scope_values = data_scope_column_values(scope)
    _validate_scope(db, organization_id, scope)
    _validate_grantor(db, organization_id, grantor)

    starts_at = valid_from or utc_now()
    if valid_until is not None and valid_until <= starts_at:
        raise AuthorizationInvariantError("binding validity must end after it starts")
    binding = AuthorizationBinding(
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        principal_type=principal_type.value,
        role_bundle=role_bundle.value,
        institution_scope=scope.institution_scope.value,
        institution_id=scope.institution_id,
        module_scope=scope.module_scope.value,
        sensitivity_scope=scope.sensitivity_scope.value,
        data_scope_kind=scope.data_scope.value,
        data_scope_values=data_scope_values,
        granted_by_type=grantor.kind.value,
        granted_by_id=grantor.identifier.strip(),
        grant_reason_category=reason_category.value,
        grant_reason=grant_reason,
        grant_reference=reference.strip() if reference else None,
        granted_at=utc_now(),
        status=BindingStatus.ACTIVE.value,
        valid_from=starts_at,
        valid_until=valid_until,
    )
    db.add(binding)
    db.flush()
    invalidate_user_authorization(
        db,
        organization_id=organization_id,
        user_id=principal_user_id,
        reason=f"role binding granted: {grant_reason}",
        commit=False,
        locked_user=principal,
    )
    if commit:
        db.commit()
        db.refresh(binding)
    else:
        db.flush()
    return binding


def _deny_with_trace(  # noqa: PLR0913 - the complete decision tuple is explicit
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    conditions: tuple[ConditionCheck, ...],
    now: datetime | None,
    reason: str | None = None,
) -> AuthorizationDecision:
    """Evaluate with no bindings and optionally override the denial reason."""
    decision = evaluate_grants(principal, permission, resource, (), conditions=conditions, now=now)
    if reason is not None:
        return replace(decision, allowed=False, reason=reason)
    return decision


def _load_principal_grants(
    db: Session,
    principal: PrincipalLocator,
) -> tuple[bool, list[BindingGrant]]:
    user = db.scalar(
        select(User).where(
            User.id == principal.principal_id,
            User.organization_id == principal.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None or _principal_type(user) is not principal.principal_type:
        return False, []
    bindings = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == principal.organization_id,
                AuthorizationBinding.principal_user_id == principal.principal_id,
                AuthorizationBinding.principal_type == principal.principal_type.value,
            )
        )
    )
    return True, [_binding_grant(binding) for binding in bindings]


def prefetch_principal_bindings(
    db: Session, principal: PrincipalLocator
) -> tuple[bool, list[AuthorizationBinding]]:
    """Load one principal and its grants once for several resource decisions.

    Consumers must resolve each resource in the principal's tenant before using
    evaluate_prefetched_permission, and apply their own data-grain requirement.
    """
    rows = cast(
        Sequence[tuple[User, AuthorizationBinding | None]],
        db.execute(
            select(User, AuthorizationBinding)
            .select_from(User)
            .outerjoin(
                AuthorizationBinding,
                (AuthorizationBinding.organization_id == principal.organization_id)
                & (AuthorizationBinding.principal_user_id == principal.principal_id)
                & (AuthorizationBinding.principal_type == principal.principal_type.value),
            )
            .where(
                User.id == principal.principal_id,
                User.organization_id == principal.organization_id,
                User.is_active.is_(True),
            )
        )
        .tuples()
        .all(),
    )
    if not rows or _principal_type(rows[0][0]) is not principal.principal_type:
        return False, []
    return True, [binding for _, binding in rows if binding is not None]


def evaluate_permission(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    *,
    conditions: tuple[ConditionCheck, ...] = (),
    now: datetime | None = None,
) -> AuthorizationDecision:
    """Check permissions using only stored bindings, returning a trace for audit."""

    conditions = (
        *request_wide_condition_checks(),
        *runtime_condition_checks(permission, resource, conditions),
    )
    principal_active, bindings = _load_principal_grants(db, principal)
    if not principal_active:
        return _deny_with_trace(
            principal, permission, resource, conditions, now, "principal_not_active"
        )
    if resource.organization_id != principal.organization_id:
        return _deny_with_trace(principal, permission, resource, conditions, now)
    if resource.institution_scope is InstitutionScope.INSTITUTION:
        institution = db.scalar(
            select(Bank.id).where(
                Bank.id == resource.institution_id,
                Bank.organization_id == resource.organization_id,
            )
        )
        if institution is None:
            return _deny_with_trace(
                principal,
                permission,
                resource,
                conditions,
                now,
                "resource_institution_not_in_tenant",
            )
    return evaluate_grants(
        principal,
        permission,
        resource,
        bindings,
        conditions=conditions,
        now=now,
    )


def evaluate_prefetched_permission(  # noqa: PLR0913 - complete authorization sentence
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    bindings: Sequence[AuthorizationBinding],
    *,
    principal_active: bool,
    conditions: tuple[ConditionCheck, ...] = (),
    now: datetime | None = None,
) -> AuthorizationDecision:
    """Evaluate rows loaded with the target resource in one database query."""

    conditions = (
        *request_wide_condition_checks(),
        *runtime_condition_checks(permission, resource, conditions),
    )
    if not principal_active:
        return _deny_with_trace(
            principal, permission, resource, conditions, now, "principal_not_active"
        )
    if resource.organization_id != principal.organization_id:
        return _deny_with_trace(principal, permission, resource, conditions, now)
    return evaluate_grants(
        principal,
        permission,
        resource,
        [_binding_grant(binding) for binding in bindings],
        conditions=conditions,
        now=now,
    )


def evaluate_liquidity_monitoring_views(
    db: Session,
    *,
    organization_id: str,
    principal_id: UUID,
    institutions: Sequence[Bank],
    failure_surface: str | None = None,
) -> dict[str, AuthorizationDecision]:
    """Evaluate Liquidity Monitoring for tenant-resolved institutions in one load."""

    from app.core.authorization import Module, Sensitivity  # noqa: PLC0415

    principal = PrincipalLocator(organization_id, principal_id, PrincipalType.HUMAN)
    permission = Permission.VIEW
    conditions = request_wide_condition_checks()
    resources = {
        institution.id: ResourceLocator(
            organization_id,
            InstitutionScope.INSTITUTION,
            institution.id,
            Module.LIQUIDITY,
            Sensitivity.CONFIDENTIAL,
        )
        for institution in institutions
    }
    try:
        principal_active, bindings = _load_principal_grants(db, principal)
    except Exception as exc:  # noqa: BLE001 - callers deny closed after telemetry
        if failure_surface is not None:
            for resource in resources.values():
                record_binding_evaluation_failure(
                    principal,
                    permission,
                    resource,
                    surface=failure_surface,
                    error=exc,
                )
        raise
    decisions: dict[str, AuthorizationDecision] = {}
    for institution in institutions:
        resource = resources[institution.id]
        if not principal_active:
            decision = _deny_with_trace(
                principal,
                permission,
                resource,
                conditions,
                None,
                "principal_not_active",
            )
        elif institution.organization_id != organization_id:
            decision = _deny_with_trace(
                principal,
                permission,
                resource,
                conditions,
                None,
                "resource_institution_not_in_tenant",
            )
        else:
            decision = evaluate_grants(
                principal,
                permission,
                resource,
                bindings,
                conditions=conditions,
            )
        decisions[institution.id] = decision
    return decisions


def evaluate_liquidity_monitoring_view(
    db: Session,
    *,
    organization_id: str,
    principal_id: UUID,
    institution: Bank,
    surface: str | None = None,
) -> AuthorizationDecision:
    """Evaluate and optionally record one Liquidity Monitoring decision."""

    decision = evaluate_liquidity_monitoring_views(
        db,
        organization_id=organization_id,
        principal_id=principal_id,
        institutions=(institution,),
        failure_surface=surface,
    )[institution.id]
    if surface is not None:
        record_binding_decision(
            decision,
            surface=surface,
            severity="info" if decision.allowed else "warning",
        )
    return decision


def record_binding_decision(
    decision: AuthorizationDecision,
    *,
    surface: str,
    severity: str = "info",
) -> None:
    """Emit the evaluator's complete enforcing outcome for one product surface."""

    authorization_binding_decision(
        allowed=decision.allowed,
        reason=decision.reason,
        severity=severity,
        surface=surface,
        **_decision_target_fields(
            decision.principal,
            decision.permission,
            decision.resource,
        ),
        matching_binding_ids=",".join(str(value) for value in decision.matching_binding_ids),
        binding_trace=",".join(
            f"{trace.binding_id}:{trace.reason}" for trace in decision.binding_trace
        ),
        condition_trace=",".join(
            f"{check.kind.value}:{check.reason}:{check.passed}"
            for check in decision.condition_trace
        ),
    )


def _decision_target_fields(
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
) -> dict[str, str | None]:
    return {
        "organization_id": resource.organization_id,
        "principal_id": str(principal.principal_id),
        "principal_type": principal.principal_type.value,
        "permission": permission.value,
        "institution_scope": resource.institution_scope.value,
        "institution_id": resource.institution_id,
        "module": resource.module.value,
        "sensitivity": resource.sensitivity.value,
    }


def record_binding_evaluation_failure(
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    *,
    surface: str,
    error: Exception,
) -> None:
    """Record an evaluator failure that the enforcing surface denied closed."""

    authorization_binding_decision(
        allowed=False,
        reason="binding_evaluation_failed",
        severity="error",
        surface=surface,
        **_decision_target_fields(principal, permission, resource),
        error_type=type(error).__name__,
    )
