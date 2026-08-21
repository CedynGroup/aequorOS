"""SDI module-readiness / data-quality diagnostics (docs/sdi.md §11, Phase I).

Given a bank + as_of, reports whether each SDI-relevant module has enough ingested
canonical data to compute (READY), what is degraded (PARTIAL), or what is missing
(BLOCKED) — so an onboarding institution sees exactly what to feed to light up
each module. Read-only; absent data is never reported as compliant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.services import sdi_capital_checks

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
_EXPOSURE_TYPES = frozenset({"LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING"})

READY = "ready"
PARTIAL = "partial"
BLOCKED = "blocked"


@dataclass
class ModuleReadiness:
    """One module's readiness plus the specific data reasons behind it."""

    module: str
    status: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class _BookSummary:
    total: int
    by_type: dict[str, int]
    missing_balance_ghs: int
    missing_maturity: int
    deposits: int
    deposits_missing_account_type: int
    deposits_missing_counterparty: int
    loans: int
    loans_missing_dpd_and_stage: int


def _summarize(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> _BookSummary:
    rows = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()
    by_type: dict[str, int] = {}
    counters = {
        "missing_balance_ghs": 0,
        "missing_maturity": 0,
        "deposits": 0,
        "deposits_missing_account_type": 0,
        "deposits_missing_counterparty": 0,
        "loans": 0,
        "loans_missing_dpd_and_stage": 0,
    }
    for snap, pos in rows:
        by_type[pos.position_type] = by_type.get(pos.position_type, 0) + 1
        attrs = snap.attributes or {}
        if attrs.get("balance_ghs") is None:
            counters["missing_balance_ghs"] += 1
        if snap.contractual_maturity is None:
            counters["missing_maturity"] += 1
        if pos.position_type == "DEPOSIT":
            counters["deposits"] += 1
            if not snap.deposit_account_type:
                counters["deposits_missing_account_type"] += 1
            if snap.counterparty_id is None:
                counters["deposits_missing_counterparty"] += 1
        if pos.position_type == "LOAN":
            counters["loans"] += 1
            if attrs.get("days_past_due") is None and snap.ifrs9_stage is None:
                counters["loans_missing_dpd_and_stage"] += 1
    return _BookSummary(total=len(rows), by_type=by_type, **counters)


def assess_sdi_readiness(  # noqa: PLR0912, PLR0915 - one linear per-module readiness pass
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[ModuleReadiness]:
    """Per-module readiness for the SDI-relevant modules at ``as_of``."""
    s = _summarize(db, ctx, bank, as_of)
    has_capital_structure = bool(sdi_capital_checks.capital_components(db, ctx, bank, as_of))
    modules: list[ModuleReadiness] = []

    liq = ModuleReadiness("liquidity_table1", READY)
    if s.total == 0:
        liq.status = BLOCKED
        liq.reasons.append("No canonical position snapshots ingested for the period end.")
    elif s.deposits == 0:
        liq.status = BLOCKED
        liq.reasons.append(
            "No DEPOSIT positions — the Table-1 deposit denominators cannot be computed."
        )
    else:
        if s.missing_balance_ghs:
            liq.reasons.append(
                f"{s.missing_balance_ghs} position(s) without a balance_ghs contribute zero."
            )
        if s.deposits_missing_account_type:
            liq.status = PARTIAL
            liq.reasons.append(
                f"{s.deposits_missing_account_type} deposit(s) without deposit_account_type "
                "(the volatile / short-term split is degraded)."
            )
    modules.append(liq)

    ladder = ModuleReadiness("maturity_ladder", READY)
    if s.total == 0:
        ladder.status = BLOCKED
        ladder.reasons.append("No positions ingested.")
    elif s.missing_maturity:
        ladder.status = PARTIAL
        ladder.reasons.append(
            f"{s.missing_maturity} position(s) without contractual_maturity fall into the "
            "residual (undated) ladder bucket."
        )
    modules.append(ladder)

    conc = ModuleReadiness("funding_concentration", READY)
    if s.deposits == 0:
        conc.status = BLOCKED
        conc.reasons.append("No deposits to assess for concentration.")
    elif s.deposits_missing_counterparty:
        conc.status = PARTIAL
        conc.reasons.append(
            f"{s.deposits_missing_counterparty} deposit(s) without a counterparty are excluded "
            "from the Top-20/100 depositor analysis."
        )
    modules.append(conc)

    cap = ModuleReadiness("capital", READY)
    if not has_capital_structure:
        cap.status = BLOCKED
        cap.reasons.append(
            "The capital_structure reference dataset has not been ingested — capital "
            "adequacy and the paid-up-capital check cannot compute."
        )
    modules.append(cap)

    exp = ModuleReadiness("exposures", READY)
    if not (_EXPOSURE_TYPES & set(s.by_type)):
        exp.status = BLOCKED
        exp.reasons.append(
            "No LOAN / INTERBANK_PLACEMENT / SECURITY_HOLDING positions to assess against "
            "the single-obligor and large-exposure limits."
        )
    if not has_capital_structure:
        exp.status = BLOCKED
        exp.reasons.append("Net Own Funds requires the capital_structure dataset (a capital run).")
    modules.append(exp)

    prov = ModuleReadiness("provisioning", READY)
    if s.loans == 0:
        prov.status = BLOCKED
        prov.reasons.append("No LOAN positions to classify.")
    elif s.loans_missing_dpd_and_stage:
        prov.status = PARTIAL
        prov.reasons.append(
            f"{s.loans_missing_dpd_and_stage} loan(s) have neither days_past_due nor an "
            "ifrs9_stage and land in the unclassified bucket (never silently 'current')."
        )
    modules.append(prov)

    return modules
