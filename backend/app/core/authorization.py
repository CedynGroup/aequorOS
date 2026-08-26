"""Pure authorization vocabulary and deny-by-default policy evaluation.

This module is deliberately free of SQLAlchemy and FastAPI.  It is the one
canonical vocabulary for the first scoped-policy rollout and can therefore be
used by services, tests, workers, and future audit writers without importing an
HTTP boundary.  Route names and dashboard navigation never imply permission.
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
    """The small, explicit action vocabulary understood by policy v1."""

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
    """Binding module scope; ``ALL`` is an explicit broad grant."""

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
    """Binding sensitivity scope; ``ALL`` is an explicit broad grant."""

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


class RoleBundle(StrEnum):
    """Static bundles only; persisted custom permission catalogs are out of scope."""

    VIEWER = "viewer"
    AUDITOR = "auditor"
    ANALYST = "analyst"
    APPROVER = "approver"
    ACCOUNT_ADMIN = "account_admin"
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
        RoleBundle.APPROVER: frozenset(
            {Permission.VIEW, Permission.REVIEW, Permission.APPROVE}
        ),
        # Account administration is intentionally outside operational bundles.
        RoleBundle.ACCOUNT_ADMIN: frozenset({Permission.ADMINISTER}),
        # Machine principals do not inherit a human Analyst preset or seat.
        RoleBundle.INTEGRATION_WRITER: frozenset({Permission.INGEST}),
    }
)


class ConditionKind(StrEnum):
    """Reserved non-bypassable runtime condition hooks."""

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
    """Canonical attributes a policy decision may match in rollout v1."""

    organization_id: str
    institution_id: str | None
    module: Module
    sensitivity: Sensitivity


@dataclass(frozen=True)
class BindingGrant:
    """Persistence-neutral form of one indivisible scoped binding."""

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
    """Result supplied by a workflow-specific condition hook.

    A false result is a global veto.  It cannot be overcome by another binding,
    which is what makes demo-mode, maker/checker, step-up, and limit checks
    non-bypassable without putting workflow state inside RBAC rows.
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
        """Return a JSON-ready explanation for future immutable audit events."""

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
    return resource.institution_id is not None and binding.institution_id == resource.institution_id


def evaluate_permission(  # noqa: PLR0913 - the complete decision tuple is explicit
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    bindings: Sequence[BindingGrant],
    *,
    conditions: Sequence[ConditionCheck] = (),
    now: datetime | None = None,
) -> AuthorizationDecision:
    """OR bindings only after every dimension inside each one matches.

    No matching active binding means deny.  There are intentionally no deny
    grants in v1; workflow conditions are the only global veto and are supplied
    as typed, explainable results by their owning services.
    """

    moment = _aware(now or datetime.now(UTC))
    traces: list[BindingTrace] = []
    matching_ids: list[UUID] = []

    tenant_matches = principal.organization_id == resource.organization_id
    for binding in bindings:
        active, lifecycle_reason = _active(binding, moment)
        permission_matches = permission in ROLE_PERMISSIONS[binding.role_bundle]
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
            reason = "permission_not_in_bundle"
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
