"""Whole-institution authority for regulatory returns.

Ordinary returns require Regulatory Reporting/restricted authority. ICAAP keeps
its Capital/confidential visibility. Every family exports under Regulatory
Reporting/restricted authority. Visibility is checked first, with 404 hiding
packages a principal cannot read; every action then requires its own permission.
Scalar roles never authorize a return, and narrowed grants never serve snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import (
    AuthorizationDecision,
    ConditionCheck,
    ConditionKind,
    DataScope,
    ExaminerDocumentAuthority,
    InstitutionScope,
    Module,
    Permission,
    ResourceLocator,
    Sensitivity,
)
from app.core.return_family_access import GATED, RETURN_GATE, FamilyGate, gate_for
from app.identity.public import AuthorizationBinding, Bank
from app.reporting.models.packages import RETURN_FAMILIES, RegulatoryPackage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.api.deps import TenantContext  # noqa: PLC0415


_DETAIL = "This return requires an active scoped binding for the institution."

#: The authority to TRANSMIT a return to the regulator — one gate for every
#: return family.
#:
#: Filing is a Regulatory Reporting act, not a Capital one, so an ICAAP and a
#: BSD3 are transmitted under the same authority even though they are READ under
#: different ones. Two transmission authorities would be a seam, and this
#: codebase loses gates at seams (D-069). The exact institution is still
#: required: covering one bank never covers its sibling.
TRANSMISSION_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)

# Taking a return away is a Regulatory Reporting act for every family, exactly as transmission is.
EXPORT_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)

#: Formal certification is a REG/restricted Signer act for every return family.
#: ICAAP keeps CAPITAL/confidential SOURCE READ, but that visibility never
#: supplies signing authority and must not change the signing grant's module.
SIGNING_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)

_TRANSMISSION_DETAIL = (
    "Transmitting a return to the regulator requires Validator authority: an "
    "active Regulatory Reporting grant covering restricted data for this "
    "institution, carrying 'submit'. Approval authority is not transmission "
    "authority."
)

#: The authority to take a REVIEW-CHAIN decision on a return — the Approver's
#: stage, and any other stage short of transmission.
#:
#: The same subject as :data:`TRANSMISSION_GATE` and deliberately so: approving
#: a return and filing it are two VERBS on one object, and giving them different
#: modules or different classifications would mean an officer's grant said one
#: thing about the return's data when they approve it and another when they
#: file it. Only the permission differs.
CHAIN_DECISION_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _forbidden(detail: str = _DETAIL) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


#: The maker-checker refusal when the failed condition carries no reason of its
#: own. One sentence whatever the act, because four eyes on a regulatory return
#: is one rule: the officer who generated it cannot also decide on it.
MAKER_CHECKER_DETAIL: Final[str] = (
    "Maker-checker: the officer who generated this return cannot also decide on "
    "it; a different officer must review, certify or file it."
)


def _denial_detail(decision: AuthorizationDecision | None, generic: str) -> str:
    """The refusal a denied caller should read, chosen by WHY the gate shut.

    A maker/checker condition denial is not a missing grant: the caller's
    binding is complete and the condition wrote its own reason — who may take
    this act. Returning the generic binding sentence would send that officer to
    re-issue a grant they already hold (#129), so the condition's reason is the
    detail and this constant stands in only when a condition names none. Every
    other denial keeps the surface's generic detail unchanged.
    """
    if decision is None or decision.reason != "condition_denied:maker_checker":
        return generic
    failed = next((check for check in decision.condition_trace if not check.passed), None)
    if failed is not None and failed.reason:
        return failed.reason
    return MAKER_CHECKER_DETAIL


def _institution_class(db: Session, bank: Bank) -> str:
    from app.policy.public import institution_types  # noqa: PLC0415

    return institution_types.institution_class(db, bank)


def _evaluate_decision(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    gate: FamilyGate,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
    prefetched_bindings: tuple[bool, list[AuthorizationBinding]] | None = None,
    structural: bool = False,
    exact_institution: bool = False,
) -> AuthorizationDecision | None:
    """One complete active binding for this exact institution, or None.

    Any evaluator failure denies (the enforcement rule everywhere else in the
    codebase): a binding engine that cannot answer has not said yes, so the
    failure is ``None`` and every caller treats ``None`` as a denial.
    """
    from app.identity import public as authorization_service  # noqa: PLC0415

    if ctx.actor_user_id is None:
        return None
    principal = authorization_service.principal_locator(ctx)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        gate.module,
        gate.sensitivity,
    )
    try:
        if prefetched_bindings is None:
            if structural:
                decision = authorization_service.evaluate_structural_permission(
                    db, principal, permission, resource
                )
            else:
                decision = authorization_service.evaluate_permission(
                    db, principal, permission, resource, conditions=conditions
                )
            whole_institution = False
            if decision.allowed:
                whole_institution = authorization_service.effective_data_scope(
                    db,
                    organization_id=ctx.organization_id,
                    binding_ids=decision.matching_binding_ids,
                ).whole_institution
        else:
            active, bindings = prefetched_bindings
            decision = authorization_service.evaluate_prefetched_permission(
                principal,
                permission,
                resource,
                bindings,
                principal_active=active,
                conditions=conditions,
            )
            whole_institution = any(
                binding.id in decision.matching_binding_ids
                and binding.data_scope_kind == DataScope.ALL.value
                and authorization_service.binding_is_effective(binding)
                for binding in bindings
            )
        if decision.allowed:
            decision = authorization_service.institution_grain_decision(
                decision, whole_institution=whole_institution
            )
        if decision.allowed and exact_institution:
            matched_grants = authorization_service.load_effective_grants(
                db,
                organization_id=ctx.organization_id,
                binding_ids=decision.matching_binding_ids,
            )
            # One complete active binding must match every dimension; partial
            # rows never compose. The exact-bank signing binding itself must
            # carry the whole-institution data scope, so an organization-scope
            # grant can never lend its breadth to a separate branch-restricted
            # exact-bank grant (mirrors the stress require_signer check).
            if not any(
                grant.institution_scope is InstitutionScope.INSTITUTION
                and grant.institution_id == bank.id
                and grant.data_scope is DataScope.ALL
                for grant in matched_grants.values()
            ):
                decision = replace(
                    decision, allowed=False, reason="signing_requires_exact_institution_binding"
                )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal, permission, resource, surface=surface, error=exc
        )
        return None
    authorization_service.record_binding_decision(
        decision, surface=surface, severity="info" if decision.allowed else "warning"
    )
    return decision


def _evaluate(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    gate: FamilyGate,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
    prefetched_bindings: tuple[bool, list[AuthorizationBinding]] | None = None,
    structural: bool = False,
    exact_institution: bool = False,
) -> bool:
    """The boolean half of :func:`_evaluate_decision`."""
    decision = _evaluate_decision(
        db,
        ctx,
        bank,
        gate,
        permission,
        surface=surface,
        conditions=conditions,
        prefetched_bindings=prefetched_bindings,
        structural=structural,
        exact_institution=exact_institution,
    )
    return decision is not None and decision.allowed


def can_view(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    family: str | None,
    *,
    prefetched_bindings: tuple[bool, list[AuthorizationBinding]] | None = None,
) -> bool:
    """Whole-institution VIEW on this family's exact module and sensitivity.

    An authorized impersonated examiner can read sealed packages; ICAAP
    remains available only to the bank regime.
    """
    gate = gate_for(family)
    if family in GATED and _institution_class(db, bank) != "bank":
        # A gated family is a bank regime. An SDI tenant does not merely lack
        # permission: the surface does not exist for that licence class (C-27).
        return False
    if ctx.impersonation_context is not None:
        from app.icaap.service import guards  # noqa: PLC0415 - avoid an import cycle

        try:
            guards.require_examiner(ctx)
        except HTTPException:
            return False
        return True
    return _evaluate(
        db,
        ctx,
        bank,
        gate,
        Permission.VIEW,
        surface=f"{family}_package_view",
        prefetched_bindings=prefetched_bindings,
    )


def require_generation_authority(db: Session, ctx: TenantContext, bank: Bank, family: str) -> None:
    """Ordinary generation needs RUN; frozen ICAAP keeps its workspace rules.

    The workspace's freeze gate owns EDIT and reviewed-round conditions. Its
    mint peer additionally preserves whole-institution Capital visibility.
    """
    permission = Permission.VIEW if family in GATED else Permission.RUN
    if (
        ctx.impersonation_context is not None
        or not can_view(db, ctx, bank, family)
        or not _evaluate(
            db,
            ctx,
            bank,
            gate_for(family),
            permission,
            surface=f"{family}_package_generate",
        )
    ):
        raise _forbidden(
            "Generating a return requires whole-institution authority for its family, "
            f"carrying '{permission.value}'."
        )


def prefetch_view_authority(
    db: Session, ctx: TenantContext, bank: Bank
) -> tuple[bool, list[AuthorizationBinding]] | None:
    """Load policy rows once for a calendar/list over a tenant-resolved bank."""
    from app.identity import public as authorization_service  # noqa: PLC0415

    if bank.organization_id != ctx.organization_id or ctx.actor_user_id is None:
        return False, []
    if ctx.impersonation_context is not None:
        return None
    principal = authorization_service.principal_locator(ctx)
    try:
        return authorization_service.prefetch_principal_bindings(db, principal)
    except Exception as exc:  # noqa: BLE001 - a failed policy load hides every family
        authorization_service.record_binding_evaluation_failure(
            principal,
            Permission.VIEW,
            ResourceLocator(
                ctx.organization_id,
                InstitutionScope.INSTITUTION,
                bank.id,
                RETURN_GATE.module,
                RETURN_GATE.sensitivity,
            ),
            surface="package_family_list",
            error=exc,
        )
        return False, []


def hidden_families(db: Session, ctx: TenantContext, bank: Bank) -> frozenset[str]:
    """Which return families this principal may NOT see, for list filtering.

    Computed once per request and applied as a NOT-IN over the package query,
    so a list endpoint never has to reason per row.
    """
    if bank.organization_id != ctx.organization_id:
        return frozenset(RETURN_FAMILIES)
    prefetched_bindings = prefetch_view_authority(db, ctx, bank)
    visible: dict[str | None, bool] = {}
    hidden: set[str] = set()
    for family in RETURN_FAMILIES:
        # Ordinary families share one Regulatory Reporting policy sentence.
        # Evaluate each distinct family policy once, independently of row count.
        policy_family = family if family in GATED else None
        if policy_family not in visible:
            visible[policy_family] = can_view(
                db, ctx, bank, policy_family, prefetched_bindings=prefetched_bindings
            )
        if not visible[policy_family]:
            hidden.add(family)
    return frozenset(hidden)


def require_view(db: Session, ctx: TenantContext, bank: Bank, package: RegulatoryPackage) -> None:
    """404 when this principal may not know the package exists."""
    if not can_view(db, ctx, bank, package.return_family):
        raise _not_found()


def sealed_document_decision(
    db: Session, ctx: TenantContext, bank: Bank, family: str | None
) -> AuthorizationDecision | ExaminerDocumentAuthority:
    """A fresh whole-institution VIEW decision for reading this family's sealed bytes.

    The storage kernel re-checks the exact module and sensitivity of the decision
    it is handed, so the returned decision is the authority itself rather than a
    boolean it could not inspect. A refusal hides the document (404) exactly as
    the surface-level read does.
    """
    from app.identity import public as authorization_service  # noqa: PLC0415

    gate = gate_for(family)
    decision = authorization_service.document_view_decision(
        db,
        ctx,
        bank_id=bank.id,
        module=gate.module,
        sensitivity=gate.sensitivity,
        surface=f"{family or 'return'}_sealed_document_view",
    )
    if not decision.allowed:
        raise _not_found()
    return decision


def require_permission(  # noqa: PLR0913 - the complete policy tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> None:
    """VIEW first (404 hides), then the action permission (403 explains).

    The order is the policy, and it is the same one the ICAAP workspace uses:
    a caller who cannot see the institution's ICAAP at all is told nothing; a
    caller who can see it but may not act is told exactly that.
    """
    gate = gate_for(package.return_family)
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None:
        # Reached only if a read-only impersonation somehow arrived at an action
        # gate. The dependency layer already refuses; this is the last line.
        raise _forbidden()
    decision = _evaluate_decision(
        db,
        ctx,
        bank,
        gate,
        permission,
        surface=surface,
        conditions=conditions,
    )
    if decision is None or not decision.allowed:
        raise _forbidden(_denial_detail(decision, _DETAIL))


def require_export_authority(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    *,
    surface: str = "package_export",
) -> None:
    """Taking the return away requires ``EXPORT``, never merely ``VIEW``.

    Reading a filed return and receiving its file are separate acts. A bank
    grants a supervisor the first without the second, and the artifact, the
    archived revision and the downtime email bundle are all copies of the same
    restricted document. Visibility is still decided first: the caller who
    cannot see the return is told nothing (404), while one who can see it and
    may not take it receives a forbidden response (403).
    """
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None or not _evaluate(
        db, ctx, bank, EXPORT_GATE, Permission.EXPORT, surface=surface
    ):
        raise _forbidden()


def require_transmission_authority(  # noqa: PLR0913 - the complete policy tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> None:
    """The gate in front of the regulator, for EVERY family.

    Before 2026-09-20 this act shared ``Permission.APPROVE`` with the approval
    decision, so whoever approved a return could also file it — and on an
    ungated family the scalar ``approver`` role alone was enough. Both are gone:
    transmission now requires one complete active binding carrying
    :data:`~app.core.authorization.Permission.SUBMIT` over
    :data:`TRANSMISSION_GATE` for this exact institution, and no scalar role
    satisfies it.

    Visibility is decided first for every family: a package the caller cannot
    see returns 404, so the refusal cannot disclose its existence. Once it is
    visible, missing transmission authority returns 403 with the required grant.
    """
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None:
        # An examiner reads; an examiner never files. The dependency layer
        # already refuses every impersonated mutation; this is the last line.
        raise _forbidden(_TRANSMISSION_DETAIL)
    decision = _evaluate_decision(
        db,
        ctx,
        bank,
        TRANSMISSION_GATE,
        permission,
        surface=surface,
        conditions=conditions,
    )
    if decision is None or not decision.allowed:
        raise _forbidden(_denial_detail(decision, _TRANSMISSION_DETAIL))


#: What a near-miss binding got wrong, in the order the operator should hear it.
#: Institution first, because covering the wrong bank is a different mistake
#: from covering the wrong data; then module; then classification.
_NEAR_MISS_DIMENSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("institution_matches", "institution"),
    ("module_matches", "module"),
    ("sensitivity_matches", "sensitivity"),
)


@dataclass(frozen=True)
class ChainDecisionVerdict:
    """Whether a binding authorised a chain decision, and if not, why not.

    The ``near_miss`` half exists because of a real debugging round: an officer
    held a complete, active Approver binding for the right institution that
    named ``confidential`` where a Regulatory Reporting act needs ``restricted``.
    The evaluator knew — its trace said ``sensitivity_mismatch`` — and the
    operator was shown "This action requires the 'analyst' role or higher",
    a sentence about a scalar role they will never hold. The information existed
    and never reached the person who could act on it.
    """

    allowed: bool
    #: ``(role_bundle, dimension)`` of the closest binding that missed, or None
    #: when the caller holds no binding for this surface at all.
    near_miss: tuple[str, str] | None = None


def _near_miss(decision: object) -> tuple[str, str] | None:
    """The closest binding that failed on exactly one SCOPE dimension.

    Only a binding that is active and carries the permission counts: a revoked
    row, or one whose bundle simply does not do this job, is not a near miss and
    saying so would send the operator to fix the wrong thing. Ties break on the
    dimension order above rather than on row order, so the message is stable.
    """
    traces = getattr(decision, "binding_trace", ())
    best: tuple[int, str, str] | None = None
    for trace in traces:
        if trace.matched or not trace.active or not trace.permission_matches:
            continue
        if not trace.organization_matches:
            continue
        missed = [
            label for attribute, label in _NEAR_MISS_DIMENSIONS if not getattr(trace, attribute)
        ]
        if len(missed) != 1:
            continue
        rank = next(
            index
            for index, (_attribute, label) in enumerate(_NEAR_MISS_DIMENSIONS)
            if label == missed[0]
        )
        candidate = (rank, str(trace.role_bundle), missed[0])
        if best is None or candidate[0] < best[0]:
            best = candidate
    return None if best is None else (best[1], best[2])


def chain_decision_verdict(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> ChainDecisionVerdict:
    """Does a stored binding authorise this chain decision? Never raises.

    Returns a verdict so the caller can explain a visible return's scope miss
    without disclosing a return the principal cannot read.

    An impersonated principal is always no: an examiner reads and never decides.
    A return family the caller cannot see is always a generic no with NO near
    miss — a better error message must never become the disclosure that an
    ICAAP package exists for a date.
    """
    from app.identity import public as authorization_service  # noqa: PLC0415

    if ctx.impersonation_context is not None:
        return ChainDecisionVerdict(allowed=False)
    if not can_view(db, ctx, bank, package.return_family):
        return ChainDecisionVerdict(allowed=False)
    if ctx.actor_user_id is None:
        return ChainDecisionVerdict(allowed=False)
    principal = authorization_service.principal_locator(ctx)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        CHAIN_DECISION_GATE.module,
        CHAIN_DECISION_GATE.sensitivity,
    )
    try:
        decision = authorization_service.evaluate_permission(
            db, principal, permission, resource, conditions=conditions
        )
        if decision.allowed:
            data_scope = authorization_service.effective_data_scope(
                db,
                organization_id=ctx.organization_id,
                binding_ids=decision.matching_binding_ids,
            )
            decision = authorization_service.institution_grain_decision(
                decision, whole_institution=data_scope.whole_institution
            )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal, permission, resource, surface=surface, error=exc
        )
        return ChainDecisionVerdict(allowed=False)
    authorization_service.record_binding_decision(
        decision, surface=surface, severity="info" if decision.allowed else "warning"
    )
    if decision.allowed:
        return ChainDecisionVerdict(allowed=True)
    return ChainDecisionVerdict(allowed=False, near_miss=_near_miss(decision))


def near_miss_detail(bank: Bank, near_miss: tuple[str, str]) -> str:
    """The refusal an operator can act on, naming the dimension that missed.

    It says what the grant must cover rather than what it currently covers: the
    holder can read their own grant in Settings, and what they cannot work out
    is which of the four dimensions this act needs.
    """
    bundle, dimension = near_miss
    role = bundle.replace("_", " ").title()
    required = {
        "institution": bank.name,
        "module": "Regulatory Reporting",
        "sensitivity": f"{CHAIN_DECISION_GATE.sensitivity.value.title()} data",
    }[dimension]
    return (
        f"Your {role} grant is active and carries the right authority, but its "
        f"{dimension} scope does not reach this return: deciding on a return "
        f"needs a grant covering {required}. Ask an Org Owner to re-issue it "
        "from Settings \u2192 Members."
    )


def chain_decision_allowed(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> bool:
    """The boolean half of :func:`chain_decision_verdict`."""
    return chain_decision_verdict(
        db, ctx, bank, package, permission, surface=surface, conditions=conditions
    ).allowed


#: Formal signing requires the fixed REG/restricted SIGN_OFF binding for every
#: slot. Source READ remains an independent family-specific gate. Checkers
#: additionally need APPROVE; signer-only is not checker approval (D8).
CERTIFY_PERMISSIONS: Final[dict[str, Permission]] = dict.fromkeys(
    ("preparer", "approver", "board", "witness"), Permission.SIGN_OFF
)

CHECKER_ROLES: Final[frozenset[str]] = frozenset({"approver", "board"})


def certification_conditions(
    ctx: TenantContext, package: RegulatoryPackage, permission: Permission
) -> tuple[ConditionCheck, ...]:
    """An approval signer or nominee must be distinct from the return's maker.

    The ceremony additionally enforces signing-slot separation, titles and
    prior signatures under its lock; this is the binding evaluator's veto.
    """
    if permission is not Permission.APPROVE:
        return ()
    return (
        ConditionCheck(
            ConditionKind.MAKER_CHECKER,
            ctx.actor_user_id is not None and ctx.actor_user_id != package.generated_by,
            "The return's preparer cannot provide its checker signature.",
        ),
    )


def require_certification_sign_off(  # noqa: PLR0913 - complete ceremony evidence tuple
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    role: str,
    *,
    step_up_established: bool = False,
    maker_checker_established: bool = False,
) -> None:
    """Require source READ and the slot's SIGN_OFF binding; raise if absent.

    Preflight (both evidence flags left false) deliberately evaluates the
    stored bindings structurally, so the denial lands before any ceremony side
    effect. Only the locked signing ceremony may set both execution-evidence
    flags after its workflow checks and one-shot step-up consumption.
    """
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None:
        raise _forbidden()
    execution = step_up_established or maker_checker_established
    sign_off_conditions = (
        (
            ConditionCheck(
                ConditionKind.MAKER_CHECKER,
                maker_checker_established,
                "The signing ceremony must establish maker/checker separation.",
            ),
            ConditionCheck(
                ConditionKind.STEP_UP,
                step_up_established,
                "The signing ceremony must establish a genuine recent step-up.",
            ),
        )
        if execution
        else ()
    )
    decision = _evaluate_decision(
        db,
        ctx,
        bank,
        SIGNING_GATE,
        CERTIFY_PERMISSIONS.get(role, Permission.SIGN_OFF),
        surface=f"{package.return_family}_package_certify",
        conditions=sign_off_conditions,
        structural=not execution,
        exact_institution=True,
    )
    if decision is None or not decision.allowed:
        raise _forbidden(_denial_detail(decision, _DETAIL))


def require_checker_approve(  # noqa: PLR0913 - complete ceremony evidence tuple
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    role: str,
    *,
    step_up_established: bool = False,
    maker_checker_established: bool = False,
) -> None:
    """A checker slot also needs its separate APPROVE duty (D8).

    Evaluated STRUCTURALLY when the ceremony has not established its runtime
    evidence — the preflight that keeps a routed checker whose APPROVE was
    revoked from ever minting a step-up token or writing its success audit.
    The locked ceremony re-evaluates the same duty with the established
    maker/checker evidence, so a revocation between preflight and execution
    still denies.
    """
    if role not in CHECKER_ROLES:
        return
    execution = step_up_established or maker_checker_established
    decision = _evaluate_decision(
        db,
        ctx,
        bank,
        SIGNING_GATE,
        Permission.APPROVE,
        surface=f"{package.return_family}_package_certify_approval",
        conditions=(
            (
                ConditionCheck(
                    ConditionKind.MAKER_CHECKER,
                    maker_checker_established,
                    "The locked checker guard must precede approval authority.",
                ),
            )
            if execution
            else ()
        ),
        structural=not execution,
        exact_institution=True,
    )
    if decision is None or not decision.allowed:
        raise _forbidden(_denial_detail(decision, _DETAIL))


def require_certify_authority(  # noqa: PLR0913 - complete ceremony evidence tuple
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    role: str,
    *,
    step_up_established: bool = False,
    maker_checker_established: bool = False,
) -> bool:
    """Require source READ, SIGN_OFF, and any distinct checker APPROVE duty.

    The whole gate in one call: :func:`require_certification_sign_off` then
    :func:`require_checker_approve`. The signing surface's own preflight splits
    the pair around the named-route check, so an unrouted caller still hears
    the routing conflict; the combined form is the execution path's gate, where
    the route was already established.
    """
    require_certification_sign_off(
        db,
        ctx,
        bank,
        package,
        role,
        step_up_established=step_up_established,
        maker_checker_established=maker_checker_established,
    )
    require_checker_approve(
        db,
        ctx,
        bank,
        package,
        role,
        step_up_established=step_up_established,
        maker_checker_established=maker_checker_established,
    )
    return True


def nominee_may_sign(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    nominee: TenantContext,
    role: str,
) -> bool:
    """Evaluate structural nominee authority without inventing step-up proof."""
    if not can_view(db, ctx, bank, package.return_family):
        return False
    # The SIGNER bundle carries no VIEW, so the nominee's source READ is an
    # independent duty the ceremony will demand (require_certification_sign_off
    # opens with require_view). Refuse the route here — before any recipient
    # row is persisted — for a nominee who could never pass it, evaluating the
    # nominee in their own session rather than through the nominator's
    # impersonation. can_view also owns the gated families' Capital/confidential
    # source gate for ICAAP.
    if not can_view(
        db,
        replace(nominee, impersonation_context=None),
        bank,
        package.return_family,
    ):
        return False
    if not _evaluate(
        db,
        nominee,
        bank,
        SIGNING_GATE,
        CERTIFY_PERMISSIONS.get(role, Permission.SIGN_OFF),
        surface=f"{package.return_family}_package_nominee",
        structural=True,
        exact_institution=True,
    ):
        return False
    return role not in CHECKER_ROLES or _evaluate(
        db,
        nominee,
        bank,
        SIGNING_GATE,
        Permission.APPROVE,
        surface=f"{package.return_family}_package_nominee_approval",
        conditions=certification_conditions(nominee, package, Permission.APPROVE),
        exact_institution=True,
    )


__all__ = [
    "CERTIFY_PERMISSIONS",
    "GATED",
    "TRANSMISSION_GATE",
    "FamilyGate",
    "can_view",
    "gate_for",
    "hidden_families",
    "nominee_may_sign",
    "require_certification_sign_off",
    "require_certify_authority",
    "require_checker_approve",
    "require_export_authority",
    "require_generation_authority",
    "require_permission",
    "require_transmission_authority",
    "require_view",
]
