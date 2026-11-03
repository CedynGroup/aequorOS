"""Scoped AUDIT authority for the examiner evidence surfaces (issue 180).

Audit evidence is the one bundle that aggregates every module: run snapshots,
filed packages, ICAAP cycles, the contingency-funding plan, the register
catalogue and audit volume. It therefore takes TWO independent decisions, never
one composed from parts of each other:

* an ENTRY gate — one complete active human binding for this institution at
  ``audit`` / ``restricted`` carrying ``view``, whole-book coverage required.
  A bank's internal auditor is not an entitled reader of the bank's evidence
  until somebody says so.
* a SOURCE gate — for every embedded module, the same ``view`` the module's own
  routes take (Liquidity, Capital, Regulatory Reporting, Credit …). Seeing the
  bundle must not reveal a module the reader cannot open.

Operator inspection is a separate lifecycle and is deliberately not one of
those decisions: an act-as-examiner staff session reads the bundle as a
supervisor, gains nothing from a tenant AUDIT grant, and requires none. It is
the same exclusion ``identity.service.authorization.project_examiner_authority``
states for the staff seat — the projection lists every institution module
EXCEPT ``audit``.

There is no scalar-role fallback and no compatibility grant: ``examiner`` as a
token role is not audit authority, and this module never reads ``ctx.roles``.
"""

from __future__ import annotations

from typing import Final

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import Module, Permission, Sensitivity
from app.core.observability import authorization_denied
from app.core.tenancy import TenantContext
from app.identity import public as authorization_service
from app.identity.public import Bank

#: Evidence is the bank's internal record: restricted, never aggregated.
AUDIT_SENSITIVITY: Final = Sensitivity.RESTRICTED

#: One sentence for every refusal, so a denied reader cannot probe which half
#: of the sentence (Audit, or a source module) it is missing.
EVIDENCE_DENIAL_DETAIL: Final = (
    "Examiner evidence requires an active scoped binding for this institution."
)

#: What the evidence bundle embeds, and the authority each source takes. The
#: register endpoints the bundle cites are listed with their source too, so a
#: disclosed catalogue cannot outrun the reader's module authority.
SOURCE_REGISTERS: Final[tuple[tuple[str, Module, Sensitivity], ...]] = (
    ("liquidity-thresholds", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
    ("liquidity-haircuts", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
    ("liquidity/ewis", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
    ("ecl-assumptions", Module.CREDIT, Sensitivity.CONFIDENTIAL),
    ("crm-haircuts", Module.CREDIT, Sensitivity.CONFIDENTIAL),
    ("capital-plan", Module.CAPITAL, Sensitivity.CONFIDENTIAL),
)


def staff_inspection(ctx: TenantContext) -> bool:
    """Whether this is a verified, read-only act-as-examiner staff session.

    A session without the operator provenance or with a failing request-wide
    condition (demo mode, for one) is not a verified inspection and is refused
    by the entry gate like any other principal.
    """
    if ctx.impersonation_context is None or ctx.actor_operator is None:
        return False
    return all(check.passed for check in authorization_service.request_wide_condition_checks())


def _require_audit_view(db: Session, ctx: TenantContext, bank: Bank, *, surface: str) -> None:
    """One complete AUDIT/restricted ``view`` binding for this institution."""

    if ctx.actor_user_id is None or ctx.authorization_version is None:
        authorization_denied(
            reason="human_scoped_binding_required",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            module=Module.AUDIT.value,
            sensitivity=AUDIT_SENSITIVITY.value,
            permission=Permission.VIEW.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EVIDENCE_DENIAL_DETAIL)
    # ``require_whole_institution`` stays at its default (True): a branch- or
    # region-scoped Audit grant cannot produce an institution's evidence bundle,
    # because the bundle is the institution's book, not a slice of it.
    authorization_service.require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.AUDIT,
        sensitivity=AUDIT_SENSITIVITY,
        surface=surface,
        denial_detail=EVIDENCE_DENIAL_DETAIL,
    )


def require_evidence_view(db: Session, ctx: TenantContext, bank: Bank) -> None:
    """The bundle's ENTRY gate: verified staff inspection, or exact AUDIT view."""

    if staff_inspection(ctx):
        return
    _require_audit_view(db, ctx, bank, surface="examiner_evidence_view")


class EvidenceAuthority:
    """Per-request source-module authority, decided once per policy sentence.

    One instance serves one examiner request: the bundle asks about up to a
    dozen modules and several of them share one authority (the four
    Forecasting run modules, for one), so caching here keeps the request to one
    decision per distinct ``(module, sensitivity)`` pair instead of one per
    embedded row.

    A staff inspection session admits every source: that seat reads the whole
    institution by construction. Tenant principals get the exact evaluator
    answer, and an evaluator failure denies — a decision engine that cannot
    answer has not said yes.
    """

    def __init__(self, db: Session, ctx: TenantContext, bank: Bank) -> None:
        self._db = db
        self._ctx = ctx
        self._bank = bank
        self._staff = staff_inspection(ctx)
        self._decisions: dict[tuple[Module, Sensitivity], bool] = {}

    def allows(
        self,
        module: Module,
        sensitivity: Sensitivity,
        *,
        surface: str,
    ) -> bool:
        """Whether this reader may see one source of the bundle."""

        if self._staff:
            return True
        key = (module, sensitivity)
        cached = self._decisions.get(key)
        if cached is not None:
            return cached
        decision = authorization_service.evaluate_bank_permission(
            self._db,
            self._ctx,
            self._bank,
            permission=Permission.VIEW,
            module=module,
            sensitivity=sensitivity,
            surface=surface,
        )
        allowed = decision is not None and decision.allowed
        self._decisions[key] = allowed
        return allowed

    def run_module_visible(self, run_module: str) -> bool:
        """Whether a stored run module's own authority is held.

        The run-module → module map is the canonical one the run registry
        already uses (``liquidity.service.regulatory_liquidity``); an unknown or
        future run module has no established authority and is refused, never
        guessed.
        """

        from app.liquidity.public import run_authorization_module  # noqa: PLC0415 - leaf contract

        module = run_authorization_module(run_module)
        if module is None:
            return False
        return self.allows(
            module,
            Sensitivity.CONFIDENTIAL,
            surface=f"examiner_run_source_{run_module}",
        )

    def run_modules_visible(self) -> frozenset[str]:
        """Every stored run module this reader may see, from the canonical map."""

        from app.liquidity.public import run_authorization_modules  # noqa: PLC0415 - leaf contract

        return frozenset(
            name for name in run_authorization_modules() if self.run_module_visible(name)
        )

    def cfp_visible(self) -> bool:
        return self.allows(
            Module.LIQUIDITY,
            Sensitivity.CONFIDENTIAL,
            surface="examiner_contingency_funding_plan",
        )

    def register_visible(self, register: str) -> bool:
        """Whether one cited register endpoint is served under held authority."""

        for name, module, sensitivity in SOURCE_REGISTERS:
            if name == register:
                return self.allows(module, sensitivity, surface=f"examiner_register_{register}")
        return False

    def withheld_run_modules(self) -> list[str]:
        """Run modules withheld from this reader, for an explicit disclosure."""

        from app.liquidity.public import run_authorization_modules  # noqa: PLC0415 - leaf contract

        return sorted(
            name for name in run_authorization_modules() if not self.run_module_visible(name)
        )
