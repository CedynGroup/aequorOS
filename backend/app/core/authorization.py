"""Authorization vocabulary and deny-by-default permission evaluation.

This module has no SQLAlchemy or FastAPI imports on purpose. It defines the
shared vocabulary for permission checks so that services, tests, workers, and
audit code can all use it without depending on a web framework. Route names
and dashboard navigation are never a source of permission.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID


class Permission(StrEnum):
    """The set of actions that permission checks can allow or deny."""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    RUN = "run"
    REVIEW = "review"
    APPROVE = "approve"
    CONFIGURE = "configure"
    EXPORT = "export"
    VALIDATE = "validate"
    SIGN_OFF = "sign_off"
    SUBMIT = "submit"
    ADMINISTER = "administer"
    INGEST = "ingest"


class Module(StrEnum):
    """Concrete modules that a resource locator may carry."""

    LIQUIDITY = "liq"
    CAPITAL = "cap"
    IRRBB = "irrbb"
    FX = "fx"
    FTP = "ftp"
    FORECASTING = "fcst"
    BEHAVIORAL = "beh"
    DATA = "data"
    REGULATORY = "reg"
    RISK = "risk"
    MARKETS = "markets"
    ACCOUNT = "account"
    AUDIT = "audit"


class Sensitivity(StrEnum):
    """Concrete data classifications carried by a resource."""

    PUBLISHED = "published"
    AGGREGATED = "aggregated"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ModuleScope(StrEnum):
    """Which modules a binding covers; ``ALL`` means every module."""

    ALL = "all"
    LIQUIDITY = Module.LIQUIDITY
    CAPITAL = Module.CAPITAL
    IRRBB = Module.IRRBB
    FX = Module.FX
    FTP = Module.FTP
    FORECASTING = Module.FORECASTING
    BEHAVIORAL = Module.BEHAVIORAL
    DATA = Module.DATA
    REGULATORY = Module.REGULATORY
    RISK = Module.RISK
    MARKETS = Module.MARKETS
    ACCOUNT = Module.ACCOUNT
    AUDIT = Module.AUDIT


class SensitivityScope(StrEnum):
    """Which data sensitivity levels a binding covers; ``ALL`` means every level."""

    ALL = "all"
    PUBLISHED = Sensitivity.PUBLISHED
    AGGREGATED = Sensitivity.AGGREGATED
    CONFIDENTIAL = Sensitivity.CONFIDENTIAL
    RESTRICTED = Sensitivity.RESTRICTED


class InstitutionScope(StrEnum):
    """Whether a binding covers the whole organization or one institution."""

    ORGANIZATION = "organization"
    INSTITUTION = "institution"


class PrincipalType(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class BindingStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class OwnerAssignmentStatus(StrEnum):
    ASSIGNED = "assigned"
    DESIGNATION_REQUIRED = "designation_required"


class OwnerAssignmentBasis(StrEnum):
    EXACTLY_ONE_ELIGIBLE_ADMIN = "exactly_one_eligible_active_human_administrator"
    ZERO_ELIGIBLE_ADMINS = "zero_eligible_active_human_administrators"
    MULTIPLE_ELIGIBLE_ADMINS = "multiple_eligible_active_human_administrators"
    EXPLICIT_DESIGNATION = "explicit_designation"


class RoleBundle(StrEnum):
    """Fixed role bundles; custom user-defined roles are not supported yet."""

    VIEWER = "viewer"
    AUDITOR = "auditor"
    ANALYST = "analyst"
    APPROVER = "approver"
    ACCOUNT_ADMIN = "account_admin"
    ORG_OWNER = "org_owner"
    INTEGRATION_WRITER = "integration_writer"


ROLE_PERMISSIONS: Final[Mapping[RoleBundle, frozenset[Permission]]] = MappingProxyType(
    {
        RoleBundle.VIEWER: frozenset({Permission.VIEW}),
        # Sensitivity remains a binding dimension: this does not make raw data
        # visible unless the binding explicitly covers that classification.
        RoleBundle.AUDITOR: frozenset({Permission.VIEW}),
        RoleBundle.ANALYST: frozenset(
            {
                Permission.VIEW,
                Permission.CREATE,
                Permission.EDIT,
                Permission.RUN,
                Permission.VALIDATE,
                Permission.EXPORT,
            }
        ),
        RoleBundle.APPROVER: frozenset({Permission.VIEW, Permission.REVIEW, Permission.APPROVE}),
        # Account administration is intentionally outside operational bundles.
        RoleBundle.ACCOUNT_ADMIN: frozenset({Permission.ADMINISTER}),
        # Ownership is a distinct authority even though its first bounded
        # permission vocabulary is intentionally no broader than account
        # administration. Grant/transfer policy belongs to its later API, which
        # can distinguish this binding without treating account admins as owners.
        RoleBundle.ORG_OWNER: frozenset({Permission.ADMINISTER}),
        # Machine principals do not inherit a human Analyst preset or seat.
        RoleBundle.INTEGRATION_WRITER: frozenset({Permission.INGEST}),
    }
)


def principal_bundle_compatible(principal_type: PrincipalType, role_bundle: RoleBundle) -> bool:
    return (principal_type is PrincipalType.MACHINE) == (
        role_bundle is RoleBundle.INTEGRATION_WRITER
    )


class ConditionKind(StrEnum):
    """Runtime conditions that can block a request regardless of bindings."""

    DEMO_MODE = "demo_mode"
    MAKER_CHECKER = "maker_checker"
    STEP_UP = "step_up"
    LIMIT = "limit"


@dataclass(frozen=True)
class PrincipalLocator:
    organization_id: str
    principal_id: UUID
    principal_type: PrincipalType


@dataclass(frozen=True)
class ResourceLocator:
    """The attributes of a resource that a permission check matches against."""

    organization_id: str
    institution_scope: InstitutionScope
    institution_id: str | None
    module: Module
    sensitivity: Sensitivity

    def __post_init__(self) -> None:
        """Reject unclear resource targets before checking permissions.

        A missing institution ID is not a scope on its own. Callers must say
        whether they are targeting the whole organization or one specific
        institution, using the same vocabulary as stored bindings.
        """

        if not isinstance(self.institution_scope, InstitutionScope):
            raise ValueError("resource institution scope must be organization or institution")
        if self.institution_scope is InstitutionScope.ORGANIZATION:
            if self.institution_id is not None:
                raise ValueError("organization-scoped resource must not carry an institution id")
            return
        if self.institution_id is None or not self.institution_id.strip():
            raise ValueError("institution-scoped resource requires an explicit institution id")


@dataclass(frozen=True)
class BindingGrant:
    """An in-memory copy of one stored binding, with all its scope fields."""

    binding_id: UUID
    organization_id: str
    principal_id: UUID
    principal_type: PrincipalType
    role_bundle: RoleBundle
    institution_scope: InstitutionScope
    institution_id: str | None
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope
    status: BindingStatus
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class ConditionCheck:
    """The result of a runtime condition check from a workflow service.

    When the check fails, it blocks the request no matter what bindings say.
    This is how demo-mode, maker-checker, step-up, and approval limits stay
    enforced without storing workflow state in the binding rows.
    """

    kind: ConditionKind
    passed: bool
    reason: str


@dataclass(frozen=True)
class BindingTrace:
    binding_id: UUID
    role_bundle: RoleBundle
    active: bool
    permission_matches: bool
    organization_matches: bool
    institution_matches: bool
    module_matches: bool
    sensitivity_matches: bool
    matched: bool
    reason: str


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    permission: Permission
    principal: PrincipalLocator
    resource: ResourceLocator
    matching_binding_ids: tuple[UUID, ...]
    binding_trace: tuple[BindingTrace, ...]
    condition_trace: tuple[ConditionCheck, ...]

    def to_audit_dict(self) -> dict[str, object]:
        """Return a JSON-ready explanation for audit logs."""

        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "permission": self.permission.value,
            "principal": {
                "organization_id": self.principal.organization_id,
                "principal_id": str(self.principal.principal_id),
                "principal_type": self.principal.principal_type.value,
            },
            "resource": {
                "organization_id": self.resource.organization_id,
                "institution_scope": self.resource.institution_scope.value,
                "institution_id": self.resource.institution_id,
                "module": self.resource.module.value,
                "sensitivity": self.resource.sensitivity.value,
            },
            "matching_binding_ids": [str(value) for value in self.matching_binding_ids],
            "bindings": [
                {
                    **asdict(trace),
                    "binding_id": str(trace.binding_id),
                    "role_bundle": trace.role_bundle.value,
                }
                for trace in self.binding_trace
            ],
            "conditions": [
                {"kind": check.kind.value, "passed": check.passed, "reason": check.reason}
                for check in self.condition_trace
            ],
        }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _active(binding: BindingGrant, now: datetime) -> tuple[bool, str]:
    if binding.status is BindingStatus.SUSPENDED:
        return False, "binding_suspended"
    if binding.status is BindingStatus.REVOKED or binding.revoked_at is not None:
        return False, "binding_revoked"
    if _aware(binding.valid_from) > now:
        return False, "binding_not_yet_valid"
    if binding.valid_until is not None and _aware(binding.valid_until) <= now:
        return False, "binding_expired"
    return True, "active"


def _institution_matches(binding: BindingGrant, resource: ResourceLocator) -> bool:
    if binding.institution_scope is InstitutionScope.ORGANIZATION:
        return binding.institution_id is None
    return (
        resource.institution_scope is InstitutionScope.INSTITUTION
        and binding.institution_id == resource.institution_id
    )


def evaluate_permission(  # noqa: PLR0913 - the complete decision tuple is explicit
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    bindings: Sequence[BindingGrant],
    *,
    conditions: Sequence[ConditionCheck] = (),
    now: datetime | None = None,
) -> AuthorizationDecision:
    """Check whether any binding grants the permission, then apply conditions.

    Each binding must match on every field (organization, institution, module,
    sensitivity, status) before it counts. Matching bindings combine with OR.
    If no binding matches, the result is deny. Workflow conditions can block
    an otherwise-allowed request.
    """

    moment = _aware(now or datetime.now(UTC))
    traces: list[BindingTrace] = []
    matching_ids: list[UUID] = []

    tenant_matches = principal.organization_id == resource.organization_id
    for binding in bindings:
        active, lifecycle_reason = _active(binding, moment)
        bundle_compatible = principal_bundle_compatible(binding.principal_type, binding.role_bundle)
        permission_matches = (
            bundle_compatible and permission in ROLE_PERMISSIONS[binding.role_bundle]
        )
        permission_reason = (
            "permission_not_in_bundle" if bundle_compatible else "principal_bundle_incompatible"
        )
        organization_matches = (
            tenant_matches
            and binding.organization_id == principal.organization_id
            and binding.principal_id == principal.principal_id
            and binding.principal_type is principal.principal_type
        )
        institution_matches = _institution_matches(binding, resource)
        module_matches = binding.module_scope in (
            ModuleScope.ALL,
            ModuleScope(resource.module.value),
        )
        sensitivity_matches = binding.sensitivity_scope in (
            SensitivityScope.ALL,
            SensitivityScope(resource.sensitivity.value),
        )
        matched = all(
            (
                active,
                permission_matches,
                organization_matches,
                institution_matches,
                module_matches,
                sensitivity_matches,
            )
        )
        if matched:
            matching_ids.append(binding.binding_id)
            reason = "matched"
        elif not active:
            reason = lifecycle_reason
        elif not permission_matches:
            reason = permission_reason
        elif not organization_matches:
            reason = "principal_or_tenant_mismatch"
        elif not institution_matches:
            reason = "institution_mismatch"
        elif not module_matches:
            reason = "module_mismatch"
        else:
            reason = "sensitivity_mismatch"
        traces.append(
            BindingTrace(
                binding_id=binding.binding_id,
                role_bundle=binding.role_bundle,
                active=active,
                permission_matches=permission_matches,
                organization_matches=organization_matches,
                institution_matches=institution_matches,
                module_matches=module_matches,
                sensitivity_matches=sensitivity_matches,
                matched=matched,
                reason=reason,
            )
        )

    failed_condition = next((check for check in conditions if not check.passed), None)
    allowed = bool(matching_ids) and failed_condition is None
    if not tenant_matches:
        reason = "resource_tenant_mismatch"
    elif not matching_ids:
        reason = "no_active_exact_binding"
    elif failed_condition is not None:
        reason = f"condition_denied:{failed_condition.kind.value}"
    else:
        reason = "allowed"
    return AuthorizationDecision(
        allowed=allowed,
        reason=reason,
        permission=permission,
        principal=principal,
        resource=resource,
        matching_binding_ids=tuple(matching_ids),
        binding_trace=tuple(traces),
        condition_trace=tuple(conditions),
    )
