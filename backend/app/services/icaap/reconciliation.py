"""¶49(l): lining the internal capital requirement up against the regulatory one.

Split in two, deliberately (D-015, audit M1).

**Requirement** answers "risk by risk, what do we hold against what are we
required to hold". Pillar 1 lines are RWA times the governed minimum; Pillar 2
lines are the ICAAP register's baseline amounts against the supervisor's own
add-ons; buffers come from the control plane. It is a SNAPSHOT: computing
replaces every line, and an explanation written against earlier figures is
carried forward and marked as written against figures that have since moved.

**Resources** answers "what capital do we count, and what does the rulebook
recognise". A component the rules do not recognise, or that differs from its
regulatory amount, needs an explanation — the database enforces that, not just
this service (REG-ICAAP-027).

The third thing the workspace checks is not part of ¶49(l) at all: the internal
SOURCE-CONSISTENCY control asks whether the ICAAP, the capital plan, the
supervisory letters and the stress overlay say the same thing about the same
risk. It lives beside the reconciliation and is computed live.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import reconciliation as domain
from app.domain.icaap.frameworks.schema import Framework
from app.domain.icaap.pillar2.types import MissingParameter
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.models.icaap_risk_capital import (
    IcaapControlExplanation,
    IcaapPillar2Item,
    IcaapRequirementReconciliationLine,
    IcaapResourcesReconciliationLine,
)
from app.schemas.icaap_risk_capital import (
    IcaapCapitalRequirementRead,
    IcaapControlExplanationRead,
    IcaapExplanation,
    IcaapParameterUseRead,
    IcaapReason,
    IcaapReconciliationRead,
    IcaapRequirementLineRead,
    IcaapRequirementTotalsRead,
    IcaapResourcesLineCreate,
    IcaapResourcesLineRead,
    IcaapResourcesLineUpdate,
    IcaapResourcesRead,
    IcaapResourcesTotalsRead,
)
from app.services import jurisdictions
from app.services.audit import record_event
from app.services.icaap import blocks as blocks_service
from app.services.icaap import digests, guards, params, pillar2, pillar2_inputs, supervisory_addons

#: The governed policy the requirement reconciliation applies.
POLICY_CODES: tuple[str, ...] = (
    "car_min",
    "icaap_car_min_includes_ccb1",
    "ccb1_pct",
    "ccyb_pct",
    "dsib_buffer_pct",
)
#: The governed recognition ceilings the resources reconciliation applies.
CAP_CODES: tuple[str, ...] = ("at1_cap_pct_rwa", "tier2_cap_pct_rwa")

CONTROL_SOURCE_CONSISTENCY = "pillar2_source_consistency"

_PILLAR1_LINES: tuple[tuple[str, str, str], ...] = (
    ("pillar1_credit", "Credit risk", "credit_rwa"),
    ("pillar1_market", "Market risk", "market_rwa"),
    ("pillar1_operational", "Operational risk", "operational_rwa"),
)


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except Exception:  # noqa: BLE001 - a malformed stored figure is simply absent
        return None


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _requirement_rows(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> list[IcaapRequirementReconciliationLine]:
    return list(
        db.scalars(
            select(IcaapRequirementReconciliationLine)
            .where(
                IcaapRequirementReconciliationLine.organization_id == access.ctx.organization_id,
                IcaapRequirementReconciliationLine.cycle_id == cycle.id,
            )
            .order_by(IcaapRequirementReconciliationLine.position.asc())
        )
    )


def _resources_rows(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> list[IcaapResourcesReconciliationLine]:
    return list(
        db.scalars(
            select(IcaapResourcesReconciliationLine)
            .where(
                IcaapResourcesReconciliationLine.organization_id == access.ctx.organization_id,
                IcaapResourcesReconciliationLine.cycle_id == cycle.id,
            )
            .order_by(IcaapResourcesReconciliationLine.position.asc())
        )
    )


def _values_digest(line: IcaapRequirementReconciliationLine) -> str:
    return digests.register_digest(
        {
            "internal": _text(line.internal_amount),
            "regulatory": _text(line.regulatory_amount),
            "supervisory": _text(line.supervisory_amount),
        }
    )


def _build_inputs(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    framework: Framework,
) -> tuple[list[domain.RiskInput], Decimal, dict[str, Decimal | None], Decimal]:
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    pillar1_binding = bound.get(pillar2_inputs.BLOCK_PILLAR1) or bound.get(
        pillar2_inputs.BLOCK_CAPITAL
    )
    total_rwa = _decimal(blocks_service.fact_value(pillar1_binding, "total_rwa"))
    if total_rwa is None:
        raise guards.conflict(
            "denominator_missing",
            "This ICAAP has no linked Pillar 1 risk-weighted assets, so the capital "
            "requirement cannot be reconciled.",
            basis="total_rwa",
        )
    rows = [row.key for row in framework.table5_rows]
    labels = {row.key: row.label for row in framework.table5_rows}
    items = list(
        db.scalars(
            select(IcaapPillar2Item).where(
                IcaapPillar2Item.organization_id == access.ctx.organization_id,
                IcaapPillar2Item.cycle_id == cycle.id,
                IcaapPillar2Item.retired_at.is_(None),
            )
        )
    )
    totals = pillar2.register_items(items)
    from app.domain.icaap.pillar2 import register as register_domain  # noqa: PLC0415

    register_totals = register_domain.row_totals(totals, row_keys=rows)
    supervisory = supervisory_addons.amounts_by_table5_row(db, access, cycle, rows)
    inputs: list[domain.RiskInput] = []
    for key, label, fact_key in _PILLAR1_LINES:
        inputs.append(
            domain.RiskInput(
                line_key=key,
                label=label,
                group="pillar1",
                rwa=_decimal(blocks_service.fact_value(pillar1_binding, fact_key)),
            )
        )
    for row in register_totals.rows:
        inputs.append(
            domain.RiskInput(
                line_key=f"pillar2_{row.row}",
                label=labels.get(row.row, row.row),
                group="pillar2",
                table5_row=row.row,
                pillar2_baseline=row.baseline,
                supervisory=supervisory.get(row.row) or Decimal(0),
            )
        )
    unattributed = supervisory_addons.unattributed_amount(db, access, cycle, rows)
    return inputs, total_rwa, supervisory, unattributed


def compute_requirement(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapReason
) -> IcaapReconciliationRead:
    """Recompute every requirement line, carrying forward the explanations."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=POLICY_CODES)
    inputs, total_rwa, _supervisory, unattributed = _build_inputs(db, access, cycle, framework)
    policy = domain.RegulatoryPolicy(
        car_min_pct=_car_min(db, access, cycle),
        car_min_includes_ccb1=resolved.optional_flag("icaap_car_min_includes_ccb1"),
        ccb1_pct=resolved.optional_decimal("ccb1_pct"),
        ccyb_pct=resolved.optional_decimal("ccyb_pct"),
        dsib_pct=resolved.optional_decimal("dsib_buffer_pct"),
    )
    try:
        result = domain.requirement_reconciliation(
            inputs,
            total_rwa=total_rwa,
            policy=policy,
            supervisory_unattributed=unattributed,
        )
    except MissingParameter as exc:
        raise params.from_missing(exc) from exc

    previous = {row.line_key: row for row in _requirement_rows(db, access, cycle)}
    for row in previous.values():
        db.delete(row)
    db.flush()
    now = utc_now()
    actor = guards.actor_id(access)
    computed_digest = digests.register_digest(
        {
            "lines": [
                {
                    "line_key": line.line_key,
                    "internal": _text(line.internal),
                    "regulatory": _text(line.regulatory),
                }
                for line in result.lines
            ],
            "total_internal": _text(result.total_internal_capital_requirement),
            "total_regulatory": _text(result.total_regulatory_requirement),
        }
    )
    computation = {
        "parameters": resolved.uses(POLICY_CODES),
        "total_rwa": _text(total_rwa),
        "supervisory_unattributed": _text(unattributed),
    }
    for position, line in enumerate(result.lines, start=1):
        carried = previous.get(line.line_key)
        row = IcaapRequirementReconciliationLine(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            line_key=line.line_key,
            line_group=line.group,
            position=position,
            label=line.label,
            table5_row=line.table5_row,
            pillar1_amount=line.internal if line.group == "pillar1" else None,
            pillar2_amount=line.internal if line.group == "pillar2" else None,
            internal_amount=line.internal,
            regulatory_amount=line.regulatory,
            supervisory_amount=line.regulatory if line.group == "pillar2" else None,
            difference=line.difference,
            explanation_required=line.explanation_required,
            explanation=None if carried is None else carried.explanation,
            explanation_by=None if carried is None else carried.explanation_by,
            explanation_at=None if carried is None else carried.explanation_at,
            explanation_values_digest=(
                None if carried is None else carried.explanation_values_digest
            ),
            computed_digest=computed_digest,
            computation=computation,
            computed_by=actor,
            computed_at=now,
        )
        db.add(row)
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.requirement_computed",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={
            "cycle_id": str(cycle.id),
            "lines": len(result.lines),
            "total_internal": _text(result.total_internal_capital_requirement),
            "total_regulatory": _text(result.total_regulatory_requirement),
            "computed_digest": computed_digest,
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def _car_min(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> Decimal | None:
    from app.services.icaap import floors  # noqa: PLC0415 - avoid an import cycle

    return floors.car_min_pct(db, access, as_of=cycle.as_of_date).value_pct


def _requirement_read(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> IcaapCapitalRequirementRead:
    rows = _requirement_rows(db, access, cycle)
    if not rows:
        return IcaapCapitalRequirementRead(
            lines=[],
            totals=IcaapRequirementTotalsRead(),
            stale=True,
        )
    stale = False
    try:
        inputs, total_rwa, _supervisory, unattributed = _build_inputs(db, access, cycle, framework)
        resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=POLICY_CODES)
        recomputed = domain.requirement_reconciliation(
            inputs,
            total_rwa=total_rwa,
            policy=domain.RegulatoryPolicy(
                car_min_pct=_car_min(db, access, cycle),
                car_min_includes_ccb1=resolved.optional_flag("icaap_car_min_includes_ccb1"),
                ccb1_pct=resolved.optional_decimal("ccb1_pct"),
                ccyb_pct=resolved.optional_decimal("ccyb_pct"),
                dsib_pct=resolved.optional_decimal("dsib_buffer_pct"),
            ),
            supervisory_unattributed=unattributed,
        )
        current_digest = digests.register_digest(
            {
                "lines": [
                    {
                        "line_key": line.line_key,
                        "internal": _text(line.internal),
                        "regulatory": _text(line.regulatory),
                    }
                    for line in recomputed.lines
                ],
                "total_internal": _text(recomputed.total_internal_capital_requirement),
                "total_regulatory": _text(recomputed.total_regulatory_requirement),
            }
        )
        stale = current_digest != rows[0].computed_digest
    except Exception:  # noqa: BLE001 - a read must not fail because inputs moved
        stale = True
    internal_total = _sum(row.internal_amount for row in rows)
    regulatory_total = _sum(row.regulatory_amount for row in rows)
    return IcaapCapitalRequirementRead(
        lines=[
            IcaapRequirementLineRead(
                line_key=row.line_key,
                line_group=row.line_group,
                position=row.position,
                label=row.label,
                table5_row=row.table5_row,
                pillar1_amount=row.pillar1_amount,
                pillar2_amount=row.pillar2_amount,
                internal_amount=row.internal_amount,
                regulatory_amount=row.regulatory_amount,
                supervisory_amount=row.supervisory_amount,
                difference=row.difference,
                explanation_required=row.explanation_required,
                explanation=row.explanation,
                explanation_current=(
                    row.explanation is None or row.explanation_values_digest == _values_digest(row)
                ),
                explanation_by=row.explanation_by,
                explanation_at=row.explanation_at,
            )
            for row in rows
        ],
        totals=IcaapRequirementTotalsRead(
            total_internal_requirement=internal_total,
            total_regulatory_requirement=regulatory_total,
            difference=(
                None
                if internal_total is None or regulatory_total is None
                else internal_total - regulatory_total
            ),
            explanation_required=any(
                row.explanation_required and not row.explanation for row in rows
            ),
        ),
        computed_at=rows[0].computed_at,
        computed_by=rows[0].computed_by,
        computed_digest=rows[0].computed_digest,
        stale=stale,
        computation=rows[0].computation,
    )


def _sum(values) -> Decimal | None:
    stated = [value for value in values if value is not None]
    if not stated:
        return None
    total = Decimal(0)
    for value in stated:
        total += value
    return total


def _resources_read(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, ticr: Decimal | None
) -> IcaapResourcesRead:
    rows = _resources_rows(db, access, cycle)
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=CAP_CODES)
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    capital = bound.get(pillar2_inputs.BLOCK_CAPITAL)
    pillar1 = bound.get(pillar2_inputs.BLOCK_PILLAR1) or capital
    total_rwa = _decimal(blocks_service.fact_value(pillar1, "total_rwa"))
    regulatory_total = _decimal(blocks_service.fact_value(capital, "total_capital"))
    caps = [IcaapParameterUseRead(**use) for use in resolved.uses(CAP_CODES)]
    if not rows or total_rwa is None:
        return IcaapResourcesRead(
            lines=[
                IcaapResourcesLineRead(
                    id=row.id,
                    line_key=row.line_key,
                    position=row.position,
                    label=row.label,
                    tier=row.tier,  # pyright: ignore[reportArgumentType]
                    origin=row.origin,  # pyright: ignore[reportArgumentType]
                    regulatory_component_key=row.regulatory_component_key,
                    regulatory_amount=row.regulatory_amount,
                    internal_amount=row.internal_amount,
                    regulatory_eligible=row.regulatory_eligible,
                    explanation=row.explanation,
                    evidence_attachment_id=row.evidence_attachment_id,
                    source_binding_ref=row.source_binding_ref,
                    row_rev=row.row_rev,
                )
                for row in rows
            ],
            totals=IcaapResourcesTotalsRead(),
            caps=caps,
        )
    try:
        result = domain.resources_reconciliation(
            [
                domain.Component(
                    line_key=row.line_key,
                    label=row.label,
                    tier=row.tier,
                    regulatory_amount=row.regulatory_amount,
                    internal_amount=row.internal_amount,
                    regulatory_eligible=row.regulatory_eligible,
                )
                for row in rows
            ],
            total_rwa=total_rwa,
            caps=domain.RecognitionCaps(
                at1_cap_pct_rwa=resolved.optional_decimal("at1_cap_pct_rwa"),
                tier2_cap_pct_rwa=resolved.optional_decimal("tier2_cap_pct_rwa"),
            ),
            regulatory_total_capital=regulatory_total,
            ticr=ticr or Decimal(0),
        )
    except MissingParameter as exc:
        raise params.from_missing(exc) from exc
    by_key = {line.line_key: line for line in result.lines}
    return IcaapResourcesRead(
        lines=[
            IcaapResourcesLineRead(
                id=row.id,
                line_key=row.line_key,
                position=row.position,
                label=row.label,
                tier=row.tier,  # pyright: ignore[reportArgumentType]
                origin=row.origin,  # pyright: ignore[reportArgumentType]
                regulatory_component_key=row.regulatory_component_key,
                regulatory_amount=row.regulatory_amount,
                internal_amount=row.internal_amount,
                recognised_amount=(
                    None if row.line_key not in by_key else by_key[row.line_key].recognised_amount
                ),
                above_cap_amount=(
                    None if row.line_key not in by_key else by_key[row.line_key].above_cap_amount
                ),
                regulatory_eligible=row.regulatory_eligible,
                explanation=row.explanation,
                evidence_attachment_id=row.evidence_attachment_id,
                source_binding_ref=row.source_binding_ref,
                explanation_required=(
                    row.line_key in by_key and by_key[row.line_key].explanation_required
                ),
                row_rev=row.row_rev,
            )
            for row in rows
        ],
        totals=IcaapResourcesTotalsRead(
            available_internal_capital=result.available_internal_capital,
            recognised_regulatory_capital=result.recognised_regulatory_capital,
            regulatory_total_capital=result.regulatory_total_capital,
            matches_regulatory_total=result.matches_regulatory_total,
            internal_capital_surplus=result.internal_capital_surplus,
            internal_capital_coverage_pct=result.coverage_pct,
        ),
        caps=caps,
    )


def get_reconciliation(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    requirement = _requirement_read(db, access, cycle, framework)
    resources = _resources_read(db, access, cycle, requirement.totals.total_internal_requirement)
    register = pillar2.get_register(db, access, cycle_id)
    explanations = list(
        db.scalars(
            select(IcaapControlExplanation).where(
                IcaapControlExplanation.organization_id == access.ctx.organization_id,
                IcaapControlExplanation.cycle_id == cycle.id,
            )
        )
    )
    resolved = params.resolve_p2(
        db, access.bank, as_of=cycle.as_of_date, codes=(*POLICY_CODES, *CAP_CODES)
    )
    return IcaapReconciliationRead(
        cycle_id=cycle.id,
        currency=jurisdictions.base_currency(access.bank),
        requirement=requirement,
        resources=resources,
        controls=register.consistency,
        control_explanations=[
            IcaapControlExplanationRead(
                control_code=row.control_code,
                comparison_key=row.comparison_key,
                explanation=row.explanation,
                explained_by=row.explained_by,
                explained_at=row.explained_at,
                current=any(
                    entry.comparison_key == row.comparison_key and entry.explanation_current
                    for entry in register.consistency
                ),
            )
            for row in explanations
        ],
        parameters=[
            IcaapParameterUseRead(**use) for use in resolved.uses((*POLICY_CODES, *CAP_CODES))
        ],
    )


def explain_requirement_line(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    line_key: str,
    payload: IcaapExplanation,
) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = db.scalar(
        select(IcaapRequirementReconciliationLine).where(
            IcaapRequirementReconciliationLine.organization_id == access.ctx.organization_id,
            IcaapRequirementReconciliationLine.cycle_id == cycle.id,
            IcaapRequirementReconciliationLine.line_key == line_key,
        )
    )
    if row is None:
        guards.not_found()
    row.explanation = payload.explanation
    row.explanation_by = guards.actor_id(access)
    row.explanation_at = utc_now()
    row.explanation_values_digest = _values_digest(row)
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.line_explained",
        entity_type="icaap_requirement_reconciliation_line",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "line_key": line_key, "reason": payload.reason},
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def _check_evidence(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, attachment_id: UUID | None
) -> None:
    if attachment_id is None:
        return
    found = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.id == attachment_id,
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
    )
    if found is None:
        raise guards.unprocessable(
            "evidence_required", "That evidence file is not part of this ICAAP."
        )


def create_resources_line(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapResourcesLineCreate
) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    _check_evidence(db, access, cycle, payload.evidence_attachment_id)
    _require_explanation(
        payload.regulatory_eligible,
        payload.regulatory_amount,
        payload.internal_amount,
        payload.explanation,
    )
    existing = _resources_rows(db, access, cycle)
    position = payload.position or (len(existing) + 1)
    row = IcaapResourcesReconciliationLine(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        line_key=payload.line_key,
        position=position,
        label=payload.label,
        tier=payload.tier,
        origin="manual",
        regulatory_component_key=payload.regulatory_component_key,
        regulatory_amount=payload.regulatory_amount,
        internal_amount=payload.internal_amount,
        regulatory_eligible=payload.regulatory_eligible,
        explanation=payload.explanation,
        evidence_attachment_id=payload.evidence_attachment_id,
        row_rev=0,
        created_by=guards.actor_id(access),
        updated_by=guards.actor_id(access),
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.resources_line_saved",
        entity_type="icaap_resources_reconciliation_line",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "line_key": row.line_key, "reason": payload.reason},
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def _require_explanation(
    eligible: bool,
    regulatory: Decimal | None,
    internal: Decimal | None,
    explanation: str | None,
) -> None:
    """The DB CHECK, restated where a person can be told what to do about it."""
    if eligible and regulatory is not None and internal == regulatory:
        return
    if explanation:
        return
    raise guards.unprocessable(
        "explanation_required",
        "A component the rules do not recognise, or that differs from its regulatory "
        "amount, needs a short explanation.",
    )


def update_resources_line(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    line_id: UUID,
    payload: IcaapResourcesLineUpdate,
) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _resources_line_or_404(db, access, cycle, line_id)
    if payload.base_rev != row.row_rev:
        raise guards.conflict(
            "row_rev_conflict",
            "Somebody else changed this line while you were editing it.",
            current_rev=row.row_rev,
        )
    _check_evidence(db, access, cycle, payload.evidence_attachment_id)
    for field in (
        "label",
        "tier",
        "internal_amount",
        "regulatory_amount",
        "regulatory_eligible",
        "explanation",
        "evidence_attachment_id",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    _require_explanation(
        row.regulatory_eligible, row.regulatory_amount, row.internal_amount, row.explanation
    )
    row.row_rev += 1
    row.updated_by = guards.actor_id(access)
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.resources_line_saved",
        entity_type="icaap_resources_reconciliation_line",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "line_key": row.line_key, "reason": payload.reason},
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def _resources_line_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, line_id: UUID
) -> IcaapResourcesReconciliationLine:
    row = db.scalar(
        select(IcaapResourcesReconciliationLine).where(
            IcaapResourcesReconciliationLine.id == line_id,
            IcaapResourcesReconciliationLine.organization_id == access.ctx.organization_id,
            IcaapResourcesReconciliationLine.cycle_id == cycle.id,
        )
    )
    if row is None:
        guards.not_found()
    return row


def delete_resources_line(
    db: Session, access: IcaapAccess, cycle_id: UUID, line_id: UUID
) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _resources_line_or_404(db, access, cycle, line_id)
    line_key = row.line_key
    db.delete(row)
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.resources_line_deleted",
        entity_type="icaap_resources_reconciliation_line",
        entity_id=line_id,
        details={"cycle_id": str(cycle.id), "line_key": line_key},
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def load_regulatory_components(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapReason
) -> IcaapReconciliationRead:
    """Seed the resources table from the bound capital position's components.

    Loaded lines are marked ``regulatory_component`` and carry the binding they
    came from, so "did the bank type this or did the engine produce it" stays
    answerable. Existing manual lines are left alone.
    """
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    capital = bound.get(pillar2_inputs.BLOCK_CAPITAL)
    if capital is None:
        raise guards.conflict(
            "denominator_missing",
            "Link this ICAAP's capital position before loading its components.",
            basis="capital_position",
        )
    existing = {row.line_key for row in _resources_rows(db, access, cycle)}
    position = len(existing)
    loaded = 0
    for table in (capital.payload or {}).get("tables", []):
        if not isinstance(table, dict) or table.get("key") != "capital_components":
            continue
        for entry in table.get("rows", []):
            cells = entry.get("cells", {}) if isinstance(entry, dict) else {}
            label = str(cells.get("label") or "").strip()
            amount = _decimal(None if cells.get("amount") is None else str(cells["amount"]))
            if not label or amount is None:
                continue
            line_key = _component_key(label)
            if line_key in existing:
                continue
            position += 1
            loaded += 1
            db.add(
                IcaapResourcesReconciliationLine(
                    organization_id=cycle.organization_id,
                    bank_id=cycle.bank_id,
                    cycle_id=cycle.id,
                    line_key=line_key,
                    position=position,
                    label=label,
                    tier="cet1",
                    origin="regulatory_component",
                    regulatory_component_key=line_key,
                    regulatory_amount=amount,
                    internal_amount=amount,
                    regulatory_eligible=True,
                    source_binding_ref={
                        "block_id": str(capital.block_id),
                        "seq": capital.seq,
                        "payload_sha256": capital.payload_sha256,
                    },
                    row_rev=0,
                    created_by=guards.actor_id(access),
                    updated_by=guards.actor_id(access),
                )
            )
            existing.add(line_key)
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.regulatory_components_loaded",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={"cycle_id": str(cycle.id), "loaded": loaded, "reason": payload.reason},
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def _component_key(label: str) -> str:
    cleaned = [char if char.isalnum() else "_" for char in label.casefold()]
    collapsed = "".join(cleaned).strip("_")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    limit = int(
        getattr(IcaapResourcesReconciliationLine.__table__.c.line_key.type, "length", 0) or 0
    )
    return collapsed[:limit] or "component"


def explain_control_difference(  # noqa: PLR0913 - the addressed pair is six parts
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    control_code: str,
    comparison_key: str,
    payload: IcaapExplanation,
) -> IcaapReconciliationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    if control_code != CONTROL_SOURCE_CONSISTENCY:
        raise guards.unprocessable(
            "explanation_required",
            "That is not a control this ICAAP runs.",
            control_code=control_code,
        )
    register = pillar2.get_register(db, access, cycle_id)
    comparison = next(
        (entry for entry in register.consistency if entry.comparison_key == comparison_key),
        None,
    )
    if comparison is None:
        guards.not_found()
    values_digest = digests.register_digest(
        {"icaap": _text(comparison.icaap), "other": _text(comparison.other)}
    )
    row = db.scalar(
        select(IcaapControlExplanation).where(
            IcaapControlExplanation.organization_id == access.ctx.organization_id,
            IcaapControlExplanation.cycle_id == cycle.id,
            IcaapControlExplanation.control_code == control_code,
            IcaapControlExplanation.comparison_key == comparison_key,
        )
    )
    now = utc_now()
    if row is None:
        row = IcaapControlExplanation(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            control_code=control_code,
            comparison_key=comparison_key,
            values_digest=values_digest,
            explanation=payload.explanation,
            explained_by=guards.actor_id(access),
            explained_at=now,
        )
        db.add(row)
    else:
        row.values_digest = values_digest
        row.explanation = payload.explanation
        row.explained_by = guards.actor_id(access)
        row.explained_at = now
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.reconciliation.control_explained",
        entity_type="icaap_control_explanation",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "control_code": control_code,
            "comparison_key": comparison_key,
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_reconciliation(db, access, cycle_id)


def reconciliation_payload(read: IcaapReconciliationRead) -> dict[str, Any]:
    """The value-based body the reconciliation block digests."""
    return {
        "requirement": [
            {
                "line_key": line.line_key,
                "label": line.label,
                "internal": _text(line.internal_amount),
                "regulatory": _text(line.regulatory_amount),
                "difference": _text(line.difference),
                "explanation_required": line.explanation_required,
            }
            for line in read.requirement.lines
        ],
        "resources": [
            {
                "line_key": line.line_key,
                "label": line.label,
                "tier": line.tier,
                "internal": _text(line.internal_amount),
                "regulatory": _text(line.regulatory_amount),
                "recognised": _text(line.recognised_amount),
                "eligible": line.regulatory_eligible,
            }
            for line in read.resources.lines
        ],
        "controls": [
            {
                "comparison_key": control.comparison_key,
                "status": control.status,
                "relative_diff_pct": _text(control.relative_diff_pct),
            }
            for control in read.controls
        ],
    }


def requirement_lines(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> Sequence[IcaapRequirementReconciliationLine]:
    return _requirement_rows(db, access, cycle)


__all__ = [
    "CAP_CODES",
    "CONTROL_SOURCE_CONSISTENCY",
    "POLICY_CODES",
    "compute_requirement",
    "create_resources_line",
    "delete_resources_line",
    "explain_control_difference",
    "explain_requirement_line",
    "get_reconciliation",
    "load_regulatory_components",
    "reconciliation_payload",
    "requirement_lines",
    "update_resources_line",
]
