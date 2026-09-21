"""Who may see, and who may act on, a package of a GATED return family.

Every regulatory package route has always been gated by the scalar role ladder:
``Tenant`` reads, ``MutationTenant`` writes, ``ApproverTenant`` approves. That
is right for a BSD return — a liquidity analyst who can see one prudential
return can see them all — and it is wrong for an ICAAP, which contains the
bank's own assessment of its capital adequacy, its Board's challenge and its
recovery triggers (audit C-4 / D-012).

So the ICAAP family is GATED: a package of a gated family is visible only to a
principal holding an exact institution-scoped binding for the family's module
and sensitivity. The refusal for a MISSING VIEW is **404, not 403** — telling a
liquidity viewer that an ICAAP package exists for a date is itself a disclosure.
Once VIEW is held, a missing action permission is an honest 403.

Nothing here changes an ungated family's READS. ``can_view`` answers ``True``
and ``require_permission`` defers to the route's own scalar dependency, so a BSD
package behaves exactly as it did.

TRANSMISSION is the exception, and deliberately so (2026-09-20). Filing a return
to the regulator is gated for every family by :func:`require_transmission_authority`
against :data:`TRANSMISSION_GATE`, because the act is the same act whatever the
return is — see ``docs/filing_submit_authority_rollout.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import (
    ConditionCheck,
    InstitutionScope,
    Module,
    Permission,
    ResourceLocator,
    Sensitivity,
)
from app.models import Bank, RegulatoryPackage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.api.deps import TenantContext


@dataclass(frozen=True)
class FamilyGate:
    """The authority one gated family is read and written under."""

    module: Module
    sensitivity: Sensitivity


#: The gated families. A family absent from this map is ungated — its routes
#: keep the scalar ladder they have always used, byte for byte.
GATED: Final[dict[str, FamilyGate]] = {
    # The ICAAP filing family. Same module/sensitivity pair as the ICAAP
    # workspace (``deps._require_icaap_access``), because they are one body of
    # information: the report is the workspace, frozen.
    "icaap": FamilyGate(module=Module.CAPITAL, sensitivity=Sensitivity.CONFIDENTIAL),
    # NOT "icaap_stress". The Appendix II annex is the same confidential
    # subject, but it is an EXISTING family that analysts can read today, and
    # gating it here would silently take an existing return away from every
    # principal without a capital binding. That is a product decision with a
    # migration, not a P3 implementation detail — P3-DESIGN §4.1 gates "icaap"
    # alone, and the annex is protected in practice by riding inside the
    # parent's filing (D-011).
}

_DETAIL = "This return requires an active scoped binding for the institution."

#: The authority to TRANSMIT a return to the regulator — one gate for every
#: return family, gated or not.
#:
#: Filing is a Regulatory Reporting act, not a Capital one, so an ICAAP and a
#: BSD3 are transmitted under the same authority even though they are READ under
#: different ones. Two transmission authorities would be a seam, and this
#: codebase loses gates at seams (D-069). The exact institution is still
#: required: covering one bank never covers its sibling.
TRANSMISSION_GATE: Final[FamilyGate] = FamilyGate(
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
#:
#: This gate is ADDITIVE to the route's pre-existing scalar dependency, not a
#: replacement for it: a binding-holder who was previously refused now passes,
#: and nobody who could approve yesterday is refused today. Removing the scalar
#: ladder from these routes is a separate enforcement cutover, and this codebase
#: gates one of those with ``scripts/authorization_access_impact.py`` and a
#: rollout contract (``docs/filing_submit_authority_rollout.md`` is the
#: precedent). Until then the honest description is "either authority".
CHAIN_DECISION_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)


def gate_for(family: str | None) -> FamilyGate | None:
    return GATED.get(family or "")


def is_gated(family: str | None) -> bool:
    return (family or "") in GATED


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _forbidden(detail: str = _DETAIL) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _institution_class(db: Session, bank: Bank) -> str:
    from app.services import institution_types  # noqa: PLC0415 - avoid an import cycle

    return institution_types.institution_class(db, bank)


def _evaluate(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    gate: FamilyGate,
    permission: Permission,
    *,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> bool:
    """One complete active binding for this exact institution, or False.

    Any evaluator failure denies (the enforcement rule everywhere else in the
    codebase): a binding engine that cannot answer has not said yes.
    """
    from app.services import authorization as authorization_service  # noqa: PLC0415

    if ctx.actor_user_id is None:
        return False
    principal = authorization_service.principal_locator(ctx)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        gate.module,
        gate.sensitivity,
    )
    try:
        decision = authorization_service.evaluate_permission(
            db, principal, permission, resource, conditions=conditions
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal, permission, resource, surface=surface, error=exc
        )
        return False
    authorization_service.record_binding_decision(
        decision, surface=surface, severity="info" if decision.allowed else "warning"
    )
    return decision.allowed


def can_view(db: Session, ctx: TenantContext, bank: Bank, family: str | None) -> bool:
    """May this principal know that packages of ``family`` exist for ``bank``?

    An ungated family is always visible (the route's scalar dependency has
    already decided). A gated family is visible to an impersonated examiner —
    a package exists only once the cycle is frozen, so everything served is
    approved, sealed material (C-11) — and otherwise to a holder of VIEW on the
    family's exact module/sensitivity for this institution.
    """
    gate = gate_for(family)
    if gate is None:
        return True
    if _institution_class(db, bank) != "bank":
        # A gated family is a bank regime. An SDI tenant does not merely lack
        # permission: the surface does not exist for that licence class (C-27).
        return False
    if ctx.impersonation_context is not None:
        from app.services.icaap import guards  # noqa: PLC0415 - avoid an import cycle

        try:
            guards.require_examiner(ctx)
        except HTTPException:
            return False
        return True
    return _evaluate(db, ctx, bank, gate, Permission.VIEW, surface=f"{family}_package_view")


def hidden_families(db: Session, ctx: TenantContext, bank: Bank) -> frozenset[str]:
    """Which gated families this principal may NOT see, for list filtering.

    Computed once per request and applied as a NOT-IN over the package query,
    so a list endpoint never has to reason per row.
    """
    return frozenset(family for family in GATED if not can_view(db, ctx, bank, family))


def require_view(db: Session, ctx: TenantContext, bank: Bank, package: RegulatoryPackage) -> None:
    """404 when this principal may not know the package exists."""
    if not can_view(db, ctx, bank, package.return_family):
        raise _not_found()


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
    if gate is None:
        return
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None:
        # Reached only if a read-only impersonation somehow arrived at an action
        # gate. The dependency layer already refuses; this is the last line.
        raise _forbidden()
    if not _evaluate(
        db,
        ctx,
        bank,
        gate,
        permission,
        surface=surface,
        conditions=conditions,
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

    Visibility is decided first and unchanged: a gated family the caller cannot
    see is still a 404, because the refusal must not disclose that the package
    exists. Once it is visible, a missing filing authority is an honest 403 that
    names what is missing.
    """
    require_view(db, ctx, bank, package)
    if ctx.impersonation_context is not None:
        # An examiner reads; an examiner never files. The dependency layer
        # already refuses every impersonated mutation; this is the last line.
        raise _forbidden(_TRANSMISSION_DETAIL)
    if not _evaluate(
        db,
        ctx,
        bank,
        TRANSMISSION_GATE,
        permission,
        surface=surface,
        conditions=conditions,
    ):
        raise _forbidden(_TRANSMISSION_DETAIL)


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
            label
            for attribute, label in _NEAR_MISS_DIMENSIONS
            if not getattr(trace, attribute)
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

    Returns a verdict rather than a bare boolean so the caller can fall back to
    the route's own pre-existing dependency AND, when that also refuses, explain
    the real miss instead of the ladder's unrelated one.

    An impersonated principal is always no: an examiner reads and never decides.
    A gated family the caller cannot see is always a generic no with NO near
    miss — a better error message must never become the disclosure that an
    ICAAP package exists for a date.
    """
    from app.services import authorization as authorization_service  # noqa: PLC0415

    if ctx.impersonation_context is not None:
        return ChainDecisionVerdict(allowed=False)
    if is_gated(package.return_family) and not can_view(db, ctx, bank, package.return_family):
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


#: Which permission a signing role's certification is an exercise of, on a
#: GATED family. The preparer's certification is the act of finishing the
#: document (EDIT); a checker's certification IS the approval of the filing
#: (APPROVE) — the attestation spine already treats it that way, writing the
#: approval decision on the final required signature.
CERTIFY_PERMISSIONS: Final[dict[str, Permission]] = {
    "preparer": Permission.EDIT,
    "approver": Permission.APPROVE,
    "board": Permission.APPROVE,
    "witness": Permission.VIEW,
}


def require_certify_authority(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    role: str,
) -> bool:
    """May this principal certify ``package`` in ``role``?

    Returns True when the family is GATED and the scoped decision was made here,
    so the caller knows the scalar ladder has been superseded and must not also
    apply it. Returns False for an ungated family, leaving today's behaviour
    exactly as it was.

    This closes a real gap. Before it, a Board certification on an ICAAP
    package was gated on the scalar ``approver`` role — the fail-open shape the
    authorization foundation exists to remove, since a scalar role must never
    satisfy a scoped surface. A Board member now certifies because they hold an
    exact CAPITAL/CONFIDENTIAL APPROVE binding for that institution, and a
    scalar approver with no binding is refused.
    """
    if gate_for(package.return_family) is None:
        return False
    permission = CERTIFY_PERMISSIONS.get(role, Permission.APPROVE)
    require_permission(
        db,
        ctx,
        bank,
        package,
        permission,
        surface=f"{package.return_family}_package_certify",
    )
    return True


def nominee_may_sign(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    nominee: TenantContext,
    role: str,
) -> bool | None:
    """Whether a NOMINATED officer could certify ``package`` in ``role``.

    ``None`` for an ungated family: the caller keeps its existing scalar check.
    For a gated family the nominee is evaluated on the same binding the
    nominator would be, so routing a return to someone who could never sign it
    is refused at nomination time rather than discovered at the ceremony.
    """
    gate = gate_for(package.return_family)
    if gate is None:
        return None
    permission = CERTIFY_PERMISSIONS.get(role, Permission.APPROVE)
    if not can_view(db, ctx, bank, package.return_family):
        return False
    return _evaluate(
        db,
        nominee,
        bank,
        gate,
        permission,
        surface=f"{package.return_family}_package_nominee",
    )


__all__ = [
    "CERTIFY_PERMISSIONS",
    "GATED",
    "TRANSMISSION_GATE",
    "FamilyGate",
    "can_view",
    "gate_for",
    "hidden_families",
    "is_gated",
    "nominee_may_sign",
    "require_certify_authority",
    "require_permission",
    "require_transmission_authority",
    "require_view",
]
