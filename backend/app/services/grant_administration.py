"""Tenant-facing policy and lifecycle for indivisible scoped grants."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    GrantReasonCategory,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
    normalise_data_scope_values,
)
from app.db.base import utc_now
from app.models import AuthorizationBinding, Bank, Organization, User
from app.schemas.authorization import DATA_SCOPE_LABELS
from app.services import authentication, authorization, membership


class GrantAdministrationError(ValueError):
    """A requested tenant grant mutation cannot be applied."""


class SodOutcome(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class SodFinding:
    """One rule that fired, in plain words, and the existing grants it fired on."""

    code: str
    message: str
    conflicting_binding_ids: tuple[UUID, ...] = ()


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
    RoleBundle.MEMBER: "Member",
    RoleBundle.VIEWER: "Viewer",
    RoleBundle.AUDITOR: "Auditor",
    RoleBundle.ANALYST: "Analyst",
    RoleBundle.APPROVER: "Approver",
    RoleBundle.VALIDATOR: "Validator",
    RoleBundle.ACCOUNT_ADMIN: "Organization Administrator",
    RoleBundle.ORG_OWNER: "Organization Owner",
    RoleBundle.INTEGRATION_WRITER: "Integration Writer",
    RoleBundle.BI_READER: "Analytics Feed Reader",
}

_MODULE_LABELS = {
    ModuleScope.ALL: "all modules",
    ModuleScope.LIQUIDITY: "Liquidity Monitoring",
    ModuleScope.CAPITAL: "Basel Capital",
    ModuleScope.CREDIT: "Credit",
    ModuleScope.IRRBB: "IRRBB",
    ModuleScope.FX: "Foreign Exchange",
    ModuleScope.FTP: "Funds Transfer Pricing",
    ModuleScope.FORECASTING: "Forecasting",
    ModuleScope.BEHAVIORAL: "Behavioral Models",
    ModuleScope.DATA: "Data Engine",
    ModuleScope.REGULATORY: "Regulatory Reporting",
    ModuleScope.RISK: "Risk & Limits",
    ModuleScope.MARKETS: "Markets",
    ModuleScope.INSTITUTION: "Institution Profile",
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

_OPERATIONAL_WRITE_BUNDLES = frozenset(
    {RoleBundle.ANALYST, RoleBundle.APPROVER, RoleBundle.VALIDATOR}
)
_ACCOUNT_ADMIN_BUNDLES = frozenset({RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER})


def binding_is_effective(binding: AuthorizationBinding, *, now: datetime | None = None) -> bool:
    return authorization.binding_is_effective(binding, now=now)


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
    return (
        institution_overlaps
        and module_overlaps
        and sensitivity_overlaps
        and _data_scopes_overlap(left, right)
    )


def _data_scopes_overlap(left: AuthorizationBinding, right: authorization.BindingScope) -> bool:
    """Whether two data scopes can touch the same rows.

    Deliberately conservative: it returns True unless the two are PROVABLY
    disjoint, so the maker/checker warning is raised whenever it might apply.
    Two narrow scopes of DIFFERENT kinds count as overlapping, because deciding
    whether branch ``ACC-001`` sits in region ``Ashanti`` needs the branch
    register — which is the BI plane's dimension, not the account plane's — and
    guessing "no" would silently drop a real separation-of-duties finding.
    """

    if left.data_scope_kind == DataScope.ALL.value or right.data_scope is DataScope.ALL:
        return True
    if left.data_scope_kind != right.data_scope.value:
        return True
    return bool(set(left.data_scope_values or ()) & set(right.data_scope_values))


#: Findings that refuse the grant outright. Everything else is a warning that
#: the Owner may proceed past, because a non-bypassable runtime condition
#: already catches it per object.
_BLOCKING_SOD_CODES = frozenset(
    {
        "c9_account_administration_operational_conflict",
        "approval_and_transmission_separation_required",
    }
)


def check_sod_policy(
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    role_bundle: RoleBundle,
    scope: authorization.BindingScope,
) -> SodDecision:
    """Return the server-authoritative assignment-time SoD decision.

    C9 is a hard block: a delegated account administrator cannot also receive an
    operational maker or checker bundle, and an operational maker/checker
    cannot be turned into an account administrator.  An overlapping
    Analyst/Approver pair is allowed because the engine deliberately unions
    bindings, but it is warned: maker-checker remains a non-bypassable
    per-object condition at action time.

    Approver alongside Validator is the second hard block. There is no runtime
    condition yet that stops one identity approving a return and then filing it,
    so the separation is enforced where it currently can be — at assignment.
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
    conflict = _ConflictWording(db, organization_id, principal_user_id, role_bundle)

    held_account_admin = existing_bundles & _ACCOUNT_ADMIN_BUNDLES
    if role_bundle in _OPERATIONAL_WRITE_BUNDLES and held_account_admin:
        # The Owner may accept this exception for themselves; a delegated
        # account administrator may not (founder decision, 2026-09-20).
        #
        # C9 exists because whoever decides who may file must not also file.
        # That reasoning is unchanged and the finding is still raised — what
        # changed is who may proceed over it. An Org Owner is the named,
        # accountable principal for the tenant, and in a small institution is
        # often genuinely the same person as the treasurer; a delegated
        # ACCOUNT_ADMIN is not accountable in that way and cannot
        # self-authorise an exception, so that case stays a hard block.
        #
        # This is a DOCUMENTED exception, not a removal: the finding is
        # returned, the grant still demands a reason, and the mutation audits
        # the complete sentence, scope, reason and actors — so an examiner
        # reads an accepted, justified risk rather than an absent control.
        # Restore it to a block, for the Owner too, once the stage engine's
        # per-object condition can catch this at action time
        # (docs/filing_workflow_redesign.md §3.3 layer 3).
        owner_only = held_account_admin == {RoleBundle.ORG_OWNER}
        held = [row for row in active if RoleBundle(row.role_bundle) in _ACCOUNT_ADMIN_BUNDLES]
        if owner_only:
            findings.append(
                _sod_finding(
                    "c9_owner_operational_exception",
                    held,
                    f"{conflict.name} owns the organization. "
                    f"Making {conflict.name} {conflict.requested} means the owner also "
                    "does operational work. This is allowed for the owner and recorded "
                    "as an accepted exception.",
                )
            )
        else:
            findings.append(
                _sod_finding(
                    "c9_account_administration_operational_conflict",
                    held,
                    f"{conflict.name} already has {conflict.held(held)}. "
                    f"Making {conflict.name} {conflict.requested} would let one person "
                    "both decide who has access and do the work that access protects. "
                    f"{conflict.remedy(held)}",
                )
            )
    if role_bundle is RoleBundle.ACCOUNT_ADMIN and existing_bundles & _OPERATIONAL_WRITE_BUNDLES:
        held = [row for row in active if RoleBundle(row.role_bundle) in _OPERATIONAL_WRITE_BUNDLES]
        findings.append(
            _sod_finding(
                "c9_account_administration_operational_conflict",
                held,
                f"{conflict.name} already has {conflict.held(held)}. "
                f"Making {conflict.name} {conflict.requested} would let one person both "
                "do operational work and decide who has access to it. "
                f"{conflict.remedy(held)}",
            )
        )

    # Approving a return and transmitting it to the regulator must not land on
    # one identity. Unlike the Analyst/Approver pair below this is NOT scope
    # sensitive: transmission authority is a single Regulatory Reporting grant
    # that files every family, so an approval grant on any module overlaps it.
    # It is also a BLOCK rather than a warn, because the per-object condition
    # that would catch it at action time does not exist yet — the stage engine
    # owns it (docs/filing_workflow_redesign.md §3.3 layer 3). Relax this to a
    # warn only when that condition is live, never to make an assignment pass.
    filing_counterpart = (
        RoleBundle.VALIDATOR
        if role_bundle is RoleBundle.APPROVER
        else RoleBundle.APPROVER
        if role_bundle is RoleBundle.VALIDATOR
        else None
    )
    if filing_counterpart is not None and filing_counterpart in existing_bundles:
        held = [row for row in active if RoleBundle(row.role_bundle) is filing_counterpart]
        findings.append(
            _sod_finding(
                "approval_and_transmission_separation_required",
                held,
                f"{conflict.name} already has {conflict.held(held)}. "
                f"Making {conflict.name} {conflict.requested} would let one person both "
                f"approve a return and file it with the regulator. {conflict.remedy(held)}",
            )
        )

    counterpart = (
        RoleBundle.APPROVER
        if role_bundle is RoleBundle.ANALYST
        else RoleBundle.ANALYST
        if role_bundle is RoleBundle.APPROVER
        else None
    )
    overlapping = [
        row
        for row in active
        if counterpart is not None
        and RoleBundle(row.role_bundle) is counterpart
        and _scope_overlaps(row, scope)
    ]
    if overlapping:
        findings.append(
            _sod_finding(
                "maker_checker_runtime_condition_required",
                overlapping,
                f"{conflict.name} already has {conflict.held(overlapping)}. "
                f"Making {conflict.name} {conflict.requested} lets one person both prepare "
                "and check work here. Nobody can approve work they prepared, so each item "
                "still needs a second person.",
            )
        )

    blocked = any(finding.code in _BLOCKING_SOD_CODES for finding in findings)
    if blocked:
        return SodDecision(SodOutcome.BLOCK, tuple(findings))
    if findings:
        return SodDecision(SodOutcome.WARN, tuple(findings))
    return SodDecision(SodOutcome.ALLOW)


def _sod_finding(code: str, held: Sequence[AuthorizationBinding], message: str) -> SodFinding:
    return SodFinding(code, message, tuple(row.id for row in held))


class _ConflictWording:
    """Plain-language parts of a finding: who, what they already hold, what is asked.

    A finding names the person and the exact grants it fired on, so the screen
    never has to guess which grant conflicts or what to remove.
    """

    def __init__(
        self,
        db: Session,
        organization_id: str,
        principal_user_id: UUID,
        role_bundle: RoleBundle,
    ) -> None:
        self._db = db
        self._organization_id = organization_id
        principal = db.get(User, principal_user_id)
        self.name = (principal.display_name or principal.email) if principal else "This person"
        self.requested = _with_article(_ROLE_LABELS[role_bundle])

    def held(self, rows: Sequence[AuthorizationBinding]) -> str:
        grants = [
            f"the {_ROLE_LABELS[RoleBundle(row.role_bundle)]} grant "
            f"({_MODULE_LABELS[ModuleScope(row.module_scope)]}, {self._institution(row)})"
            for row in rows
        ]
        return _joined(grants)

    def remedy(self, rows: Sequence[AuthorizationBinding]) -> str:
        roles = sorted({_ROLE_LABELS[RoleBundle(row.role_bundle)] for row in rows})
        plural = "grants" if len(roles) > 1 else "grant"
        return f"Remove the {_joined(roles)} {plural} first, or choose someone else."

    def _institution(self, row: AuthorizationBinding) -> str:
        if row.institution_id is None:
            return "every institution"
        bank = self._db.scalar(
            select(Bank).where(
                Bank.id == row.institution_id,
                Bank.organization_id == self._organization_id,
            )
        )
        return bank.name if bank is not None else row.institution_id


def _with_article(label: str) -> str:
    return f"{'an' if label[0].lower() in 'aeiou' else 'a'} {label}"


def _joined(values: Sequence[str]) -> str:
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} and {values[-1]}"


def data_scope_label(data_scope: DataScope, data_scope_values: Sequence[str]) -> str:
    """Ready-to-display copy for one binding's stored data scope."""

    label = DATA_SCOPE_LABELS[data_scope]
    if data_scope is DataScope.ALL:
        return label
    return f"{label}: {', '.join(data_scope_values)}"


def _data_scope_clause(data_scope: DataScope, data_scope_values: Sequence[str]) -> str:
    """The sentence's data-scope clause; EMPTY for the whole institution.

    Whole-institution is what every grant written before data scopes meant, so
    its sentence stays byte-identical: a stored sentence, an audit record and a
    review confirmation from before this phase all still match.
    """

    if data_scope is DataScope.ALL or not data_scope_values:
        return ""
    named = _joined(list(data_scope_values))
    if data_scope is DataScope.BRANCH:
        noun = "branch" if len(data_scope_values) == 1 else "branches"
        return f", limited to {noun} {named}"
    noun = "region" if len(data_scope_values) == 1 else "regions"
    return f", limited to the {named} {noun}"


def compose_authority_sentence(  # noqa: PLR0913 - the complete sentence is explicit
    *,
    principal_name: str,
    role_bundle: RoleBundle,
    institution_name: str,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    data_scope: DataScope = DataScope.ALL,
    data_scope_values: Sequence[str] = (),
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
        f"{sensitivity_phrase}{_data_scope_clause(data_scope, data_scope_values)}."
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
        data_scope=DataScope(binding.data_scope_kind),
        data_scope_values=tuple(binding.data_scope_values or ()),
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
        data_scope=scope.data_scope,
        data_scope_values=normalise_data_scope_values(scope.data_scope_values),
    )


#: Never grantable from the Members composer. The two machine bundles are here
#: because a human may not hold one at all: the database CHECK refuses the row,
#: and this refuses the request with a sentence first.
_NON_GRANTABLE_BUNDLES = frozenset(
    {
        RoleBundle.MEMBER,
        RoleBundle.ORG_OWNER,
        RoleBundle.INTEGRATION_WRITER,
        RoleBundle.BI_READER,
    }
)


def validate_public_grant(role_bundle: RoleBundle, scope: authorization.BindingScope) -> None:
    if scope.data_scope is not DataScope.ALL and scope.module_scope is not ModuleScope.CREDIT:
        raise GrantAdministrationError("Branch and region narrowing is supported only for Credit")
    if role_bundle in _NON_GRANTABLE_BUNDLES:
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
    reason_category: GrantReasonCategory = GrantReasonCategory.OTHER,
    reference: str | None = None,
    valid_until: datetime | None = None,
    commit: bool = True,
    reuse_existing: bool = False,
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
    # Every dimension, the data scope included: two grants that differ only by
    # which branches they name are different authority and must both exist,
    # while a re-submission of the same set — in any order or spelling — is the
    # duplicate this refuses, because the values are normalised before they get
    # here.
    requested_values = normalise_data_scope_values(scope.data_scope_values)
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
            and binding.data_scope_kind == scope.data_scope.value
            and tuple(binding.data_scope_values or ()) == requested_values
        ),
        None,
    )
    if duplicate is not None:
        if not reuse_existing:
            raise DuplicateScopedGrant(duplicate.id, sentence)
        return GrantResult(
            duplicate,
            check_sod_policy(
                db,
                organization_id=organization_id,
                principal_user_id=principal_user_id,
                role_bundle=role_bundle,
                scope=scope,
            ),
            sentence,
        )
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
            reason_category=reason_category,
            reference=reference,
            valid_from=utc_now(),
            valid_until=valid_until,
            commit=False,
        )
    except authorization.AuthorizationInvariantError as exc:
        raise GrantAdministrationError(str(exc)) from exc
    authorization.record_binding_grant_audit(
        db,
        binding=binding,
        grantor=actor,
        authority_sentence=sentence,
    )
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
    if binding.role_bundle == RoleBundle.MEMBER.value:
        raise GrantAdministrationError(
            "baseline membership ends only when the member is deactivated"
        )
    if binding.role_bundle in {
        RoleBundle.INTEGRATION_WRITER.value,
        RoleBundle.BI_READER.value,
    }:
        # A machine binding's lifecycle belongs to its key: revocation
        # deactivates the key, the binding and the service identity together, so
        # cutting only the binding here would leave a live credential behind.
        raise GrantAdministrationError("machine authority must be revoked with its integration key")
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
    authorization.record_binding_revoke_audit(
        db,
        binding=binding,
        actor_user_id=actor_user_id,
        authority_sentence=sentence,
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
    reason_category: GrantReasonCategory = GrantReasonCategory.OTHER,
    reference: str | None = None,
    valid_until: datetime | None = None,
) -> GrantResult:
    """Activate a verified JIT identity only as one complete grant is created."""

    user = authentication.get_sso_access_request(
        db, organization_id=organization_id, user_id=user_id, lock=True
    )
    user.is_active = True
    # Binding authority is the only new authority.  The scalar remains a
    # compatibility-only read role until endpoint rollout (#144 and later).
    user.role = "viewer"
    membership.ensure_baseline_membership(
        db,
        user=user,
        granted_by_id=f"sso_access_approval:{actor_user_id}",
        commit=False,
    )
    result = create_scoped_grant(
        db,
        organization_id=organization_id,
        principal_user_id=user.id,
        role_bundle=role_bundle,
        scope=scope,
        actor_user_id=actor_user_id,
        reason=reason,
        reason_category=reason_category,
        reference=reference,
        valid_until=valid_until,
        expected_authority_sentence=expected_authority_sentence,
        commit=False,
    )
    db.commit()
    db.refresh(result.binding)
    authentication.provision_signer_identity(db, user)
    db.commit()
    return result
