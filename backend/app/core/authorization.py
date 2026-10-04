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
    # Credit is its own module (2026-09-22): the credit engine, blotter and
    # marts were reachable under the ``risk`` label with no server-side gate.
    # Mirror migration `202609220067` widens the DB CHECK to match this enum.
    CREDIT = "credit"
    IRRBB = "irrbb"
    FX = "fx"
    FTP = "ftp"
    FORECASTING = "fcst"
    BEHAVIORAL = "beh"
    DATA = "data"
    REGULATORY = "reg"
    RISK = "risk"
    MARKETS = "markets"
    INSTITUTION = "institution"
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
    CREDIT = Module.CREDIT
    IRRBB = Module.IRRBB
    FX = Module.FX
    FTP = Module.FTP
    FORECASTING = Module.FORECASTING
    BEHAVIORAL = Module.BEHAVIORAL
    DATA = Module.DATA
    REGULATORY = Module.REGULATORY
    RISK = Module.RISK
    MARKETS = Module.MARKETS
    INSTITUTION = Module.INSTITUTION
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


class DataScope(StrEnum):
    """Which slice of an institution's book ONE binding row admits.

    This is the stored column vocabulary of ``data_scope_kind`` and nothing
    else. The DERIVED union of several bindings' scopes is
    ``services.authorization.EffectiveDataScope``, whose ``kind`` additionally
    admits ``mixed`` and ``none`` — neither of which any row may carry, because
    a row states one declared slice and only a reduction can be mixed or empty.
    """

    ALL = "all"
    BRANCH = "branch"
    REGION = "region"


#: A branch code or region name longer than this can never match the branch
#: dimension the BI compiler resolves the scope against (``bi_dim_branch``
#: stores both ``branch_code`` and ``region`` as ``VARCHAR(120)``), so a longer
#: value is refused at the boundary rather than stored as a grant that silently
#: matches nothing. Restated here rather than imported: the account plane must
#: not depend on the BI plane, and ``tests/core/test_data_scopes.py`` pins the
#: two numbers together so the restatement cannot drift.
DATA_SCOPE_VALUE_MAX_LENGTH: Final = 120


def normalise_data_scope_values(values: Sequence[str]) -> tuple[str, ...]:
    """Trim, drop blanks, de-duplicate and order the declared scope values.

    One authority for the stored shape, so two spellings of the same grant —
    ``["ACC-001", " TEM-002 "]`` and ``["TEM-002", "ACC-001", "ACC-001"]`` —
    produce one row, one authority sentence and one duplicate-grant refusal.
    Case is PRESERVED: a branch code is the core banking system's own token and
    folding it would make two genuinely different branches collide.
    """

    return tuple(sorted({value.strip() for value in values if value.strip()}))


class PrincipalType(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class BindingStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class GrantorType(StrEnum):
    SYSTEM = "system"
    TENANT_USER = "tenant_user"
    OPERATOR = "operator"


class GrantReasonCategory(StrEnum):
    NEW_JOINER = "new_joiner"
    ROLE_CHANGE = "role_change"
    PROJECT_ENGAGEMENT = "project_engagement"
    TEMPORARY_COVER = "temporary_cover"
    REGULATOR_AUDIT_REQUEST = "regulator_audit_request"
    INCIDENT_BREAK_GLASS = "incident_break_glass"
    OTHER = "other"


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

    MEMBER = "member"
    VIEWER = "viewer"
    AUDITOR = "auditor"
    ANALYST = "analyst"
    APPROVER = "approver"
    #: The officer who transmits a return to the regulator. Named for the third
    #: filing role the bank actually has (docs/filing_workflow_redesign.md), and
    #: it deliberately does NOT carry ``Permission.VALIDATE`` — that verb is the
    #: machine rules check the Preparer re-runs, not this person's act.
    VALIDATOR = "validator"
    ACCOUNT_ADMIN = "account_admin"
    ORG_OWNER = "org_owner"
    INTEGRATION_WRITER = "integration_writer"
    #: The credential a report server PULLS the curated analytics feed with
    #: (docs/bi.md §Phase 4). Deliberately disjoint from INTEGRATION_WRITER:
    #: giving the push bundle a read permission would widen every push key in
    #: the estate, and giving this bundle INGEST would hand every reporting
    #: gateway the authority to write canonical facts.
    BI_READER = "bi_reader"


#: Bundles a MACHINE principal may hold; equivalently, the bundles a human may
#: not. A TUPLE, not a set, because ``models.AuthorizationBinding`` renders it
#: into ``ck_authorization_bindings_principal_bundle`` and that text must match
#: migration ``202609270074`` exactly.
MACHINE_ROLE_BUNDLES: Final[tuple[RoleBundle, ...]] = (
    RoleBundle.INTEGRATION_WRITER,
    RoleBundle.BI_READER,
)


ROLE_PERMISSIONS: Final[Mapping[RoleBundle, frozenset[Permission]]] = MappingProxyType(
    {
        # Active tenant membership is explicit, but it is not product-data
        # authority. Shell/profile access is authenticated self-service and the
        # evaluator must never turn this row into an institution or module grant.
        RoleBundle.MEMBER: frozenset(),
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
        # Transmission to the regulator is its OWN authority. The bundle carries
        # VIEW so the holder can open the return they are being asked to file,
        # and SUBMIT so they can file it — and nothing else.
        #
        # APPROVE is deliberately absent, and adding it later would re-open the
        # hole this bundle exists to close: before 2026-09-20 the approve and
        # submit route dependencies required the same permission, so whoever
        # approved a return could also transmit it to the regulator alone. One
        # bundle that both approves and files reinstates that under a new name.
        # A bank whose Validator must also record the approval decision needs
        # two explicit bindings, which the grant surface blocks under SoD until
        # the stage engine's per-object condition lands
        # (docs/filing_workflow_redesign.md §3.3).
        RoleBundle.VALIDATOR: frozenset({Permission.VIEW, Permission.SUBMIT}),
        # Account administration is intentionally outside operational bundles.
        RoleBundle.ACCOUNT_ADMIN: frozenset({Permission.ADMINISTER}),
        # Ownership is a distinct authority even though its first bounded
        # permission vocabulary is intentionally no broader than account
        # administration. Grant/transfer policy belongs to its later API, which
        # can distinguish this binding without treating account admins as owners.
        RoleBundle.ORG_OWNER: frozenset({Permission.ADMINISTER}),
        # Machine principals do not inherit a human Analyst preset or seat.
        RoleBundle.INTEGRATION_WRITER: frozenset({Permission.INGEST}),
        # Read, and only read. The feed serves curated datasets; a pull may not
        # create, edit, run, export a signed artifact or configure anything.
        RoleBundle.BI_READER: frozenset({Permission.VIEW}),
    }
)


def principal_bundle_compatible(principal_type: PrincipalType, role_bundle: RoleBundle) -> bool:
    """Whether the bundle belongs to that KIND of principal.

    A set on both sides (2026-09-27). The invariant is unchanged and stays
    complete — a machine principal holds a machine bundle and a human holds a
    human one, with no row able to straddle — but it no longer reads as "the
    machine bundle", a shape that could not hold a second one.
    """

    return (principal_type is PrincipalType.MACHINE) == (role_bundle in MACHINE_ROLE_BUNDLES)


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
    #: The declared slice of the institution's book, carried so that one load of
    #: a principal's bindings can answer both "may they?" and "over what?".  It
    #: deliberately takes no part in :func:`evaluate_permission`: a branch-scoped
    #: binding GRANTS its permission in full and the scope narrows the ROWS,
    #: which the BI compiler applies.  Defaulted so every existing construction
    #: site keeps meaning the whole institution.
    data_scope: DataScope = DataScope.ALL
    data_scope_values: tuple[str, ...] = ()


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
