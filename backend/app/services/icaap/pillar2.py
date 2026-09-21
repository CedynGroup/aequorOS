"""The Pillar 2 register: quantifying the risks Pillar 1 does not cover.

One live figure per framework component, each carrying a canonical amount in
the reporting currency and the basis it was converted from (D-009). Every save
and every computation writes an UNALTERABLE revision, and an approval pins a
revision number — so "which figure did the Board approve, and what was it
computed from" is answerable from the database years later.

Four states are deliberately distinguished, because collapsing them is how a
report comes to overstate what it knows:

* ``computed`` — the method ran on complete inputs;
* ``interim_non_sf`` — it ran, by a method the platform itself labels interim
  (D-013), so nobody reads it as the standardised framework;
* ``incomplete`` / ``not_computable`` — inputs are missing. This is a STATE
  with a plain reason, not an error: an ICAAP in progress is allowed to be
  unfinished, and readiness is what stops a freeze.
* a missing governed PARAMETER is none of those. It is a typed 409 naming the
  console row, because there is no honest figure to show and no default to
  fall back on (D-024 §4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.authorization import ConditionCheck, ConditionKind
from app.db.base import utc_now
from app.domain.credit import granularity as granularity_credit
from app.domain.icaap.blocks import BLOCK_CATALOGUE
from app.domain.icaap.frameworks.schema import Framework, Pillar2Component
from app.domain.icaap.methods import PILLAR2_METHODS
from app.domain.icaap.pillar2 import (
    DEFERRED_METHODS,
    METHOD_REGISTRY,
)
from app.domain.icaap.pillar2 import (
    concentration as concentration_domain,
)
from app.domain.icaap.pillar2 import fx as fx_domain
from app.domain.icaap.pillar2 import granularity_method as granularity_domain
from app.domain.icaap.pillar2 import irrbb as irrbb_domain
from app.domain.icaap.pillar2 import irrbb_sf_method as irrbb_sf_domain
from app.domain.icaap.pillar2 import judgemental as judgemental_domain
from app.domain.icaap.pillar2 import liquidity as liquidity_domain
from app.domain.icaap.pillar2 import operational as operational_domain
from app.domain.icaap.pillar2 import register as register_domain
from app.domain.icaap.pillar2 import sovereign as sovereign_domain
from app.domain.icaap.pillar2.types import MethodResult, MethodStatus, MissingParameter
from app.domain.icaap.units import HUNDRED, Basis, UnitConversionError, from_amount
from app.domain.irr import standardised_params as sf_params
from app.models import Bank
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.models.icaap_risk_capital import (
    ICAAP_DIVERSIFICATION_COMPONENT,
    BankSupervisoryAddon,
    IcaapPillar2Item,
    IcaapPillar2ItemRevision,
)
from app.schemas.icaap_risk_capital import (
    IcaapConsistencyRead,
    IcaapParameterUseRead,
    IcaapPillar2Approve,
    IcaapPillar2ComponentSlotRead,
    IcaapPillar2Compute,
    IcaapPillar2FindingRead,
    IcaapPillar2ItemCreate,
    IcaapPillar2ItemRead,
    IcaapPillar2ItemUpdate,
    IcaapPillar2RegisterRead,
    IcaapPillar2RevisionListRead,
    IcaapPillar2RevisionRead,
    IcaapRetire,
    IcaapTable5RowTotalRead,
    IcaapTable5TotalsRead,
)
from app.services import jurisdictions
from app.services.audit import record_event
from app.services.icaap import digests, guards, params, pillar2_inputs
from app.services.icaap.pillar2_inputs import granularity as granularity_inputs

#: Codes the register itself reads, beyond the ones each method needs.
REGISTER_CODES: tuple[str, ...] = (
    "icaap_diversification_benefit_allowed",
    "icaap_pillar2_source_tolerance_pct",
)

CONCENTRATION_CODES: tuple[str, ...] = (
    "ccr_metric_set",
    "ccr_name_cr_n",
    "ccr_min_dimension_coverage_pct",
    "ccr_name_bands_hhi",
    "ccr_name_bands_gini",
    "ccr_name_bands_crn",
    "ccr_sector_bands_hhi",
)
HEURISTIC_CODES: tuple[str, ...] = ("ccr_name_hhi_coeff", "ccr_sector_hhi_coeff")
#: The FULL granularity adjustment: six rows the formula applies plus the
#: three the exposure book is resolved with. The book codes are listed here
#: so the item's provenance carries them too — the proxy PD table is
#: REPRESENTATIVE, and a reader has to be told when a figure rested on it.
GRANULARITY_CODES: tuple[str, ...] = (
    *(use.code for use in granularity_domain.PARAMETER_USES),
    *granularity_inputs.BOOK_PARAMETER_CODES,
)
IRRBB_CODES: tuple[str, ...] = (
    "irrbb_outlier_threshold_pct_tier1",
    "icaap_irrbb_interim_scenarios",
)
#: The standardised framework's own governed inputs, plus the outlier
#: threshold the register states the measure against. The framework's rows are
#: listed even though the RUN consumed them rather than this method: the
#: register has to name a representative calibration the figure rested on, and
#: a change to one of those rows has to make the item stale.
IRRBB_SF_CODES: tuple[str, ...] = (
    irrbb_sf_domain.PARAM_OUTLIER_THRESHOLD,
    *sf_params.REQUIRED_CODES,
)
FX_CODES: tuple[str, ...] = ("fx_p2_shock_pct",)
OPERATIONAL_CODES: tuple[str, ...] = ("op_p2_scenario_severity_pct_gross_income",)
SOVEREIGN_CODES: tuple[str, ...] = ("sov_p2_haircut_pct",)

_APPROVABLE_STATUSES = frozenset({MethodStatus.COMPUTED.value, MethodStatus.INTERIM_NON_SF.value})
#: How a machine reason packs its parts: ``code:count:ref:ref…``. The same
#: separator the granularity book builder writes with.
_GA_FIELD = ":"

_COMPUTE_COPY = {
    "not_computable": (
        "This figure cannot be worked out yet: {detail}. Link the missing figures to "
        "this ICAAP, then compute it again."
    ),
    "incomplete": (
        "This figure is incomplete: {detail}. It would understate the requirement, so "
        "it is not offered for approval."
    ),
}


@dataclass(frozen=True)
class ComponentSlot:
    """A framework component and the live item quantifying it, if any."""

    component: Pillar2Component
    category_key: str
    risk_key: str
    item: IcaapPillar2Item | None


def _items(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, *, include_retired: bool = False
) -> list[IcaapPillar2Item]:
    statement = select(IcaapPillar2Item).where(
        IcaapPillar2Item.organization_id == access.ctx.organization_id,
        IcaapPillar2Item.cycle_id == cycle.id,
    )
    if not include_retired:
        statement = statement.where(IcaapPillar2Item.retired_at.is_(None))
    return list(db.scalars(statement.order_by(IcaapPillar2Item.item_key.asc())))


def component_slots(
    framework: Framework, items: Sequence[IcaapPillar2Item]
) -> tuple[ComponentSlot, ...]:
    by_component = {item.component_key: item for item in items}
    slots: list[ComponentSlot] = []
    for category in framework.risk_categories:
        for component in category.components:
            slots.append(
                ComponentSlot(
                    component=component,
                    category_key=category.key,
                    risk_key=category.key,
                    item=by_component.get(component.key),
                )
            )
    return tuple(slots)


def _component_or_422(framework: Framework, component_key: str) -> ComponentSlot:
    for slot in component_slots(framework, ()):
        if slot.component.key == component_key:
            return slot
    raise guards.unprocessable(
        "unknown_risk_key",
        "That risk component is not in this ICAAP's framework.",
        component_key=component_key,
    )


def register_items(
    items: Sequence[IcaapPillar2Item],
) -> tuple[register_domain.RegisterItem, ...]:
    """The live items as the pure register sees them."""
    return tuple(
        register_domain.RegisterItem(
            item_key=item.item_key,
            component_key=item.component_key,
            table5_row=item.table5_row,
            baseline=item.baseline_amount,
            stressed=item.stressed_amount,
            source=item.source,
            method=item.method,
            status=item.method_status,
            approved=item.approved_revision_no == item.current_revision_no
            and item.approved_revision_no is not None,
        )
        for item in items
    )


def _snapshot(item: IcaapPillar2Item) -> dict[str, Any]:
    """The value-based content of one revision. No ids, no timestamps."""
    return {
        "item_key": item.item_key,
        "component_key": item.component_key,
        "risk_key": item.risk_key,
        "category_key": item.category_key,
        "table5_row": item.table5_row,
        "method": item.method,
        "method_version": item.method_version,
        "source": item.source,
        "input_mode": item.input_mode,
        "method_status": item.method_status,
        "status_detail": item.status_detail,
        "basis": item.basis,
        "basis_value": _text(item.basis_value),
        "baseline_amount": _text(item.baseline_amount),
        "stressed_amount": _text(item.stressed_amount),
        "baseline_derivation": item.baseline_derivation,
        "stressed_derivation": item.stressed_derivation,
        "currency": item.currency,
        "scenario_definition": item.scenario_definition,
        "rationale": item.rationale,
        "zero_amount_justification": item.zero_amount_justification,
        "evidence_attachment_ids": sorted(str(value) for value in item.evidence_attachment_ids),
        "evidence_reference": item.evidence_reference,
    }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _write_revision(  # noqa: PLR0913 - a revision is its six named parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    item: IcaapPillar2Item,
    *,
    change_kind: str,
    computation: dict[str, Any] | None = None,
    note: str | None = None,
) -> IcaapPillar2ItemRevision:
    snapshot = _snapshot(item)
    item.current_revision_no += 1
    revision = IcaapPillar2ItemRevision(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        item_id=item.id,
        revision_no=item.current_revision_no,
        change_kind=change_kind,
        round=cycle.round,
        snapshot=snapshot,
        computation=computation,
        inputs_digest=item.inputs_digest,
        snapshot_sha256=digests.register_digest(snapshot),
        note=note,
        created_by=guards.actor_id(access),
    )
    db.add(revision)
    db.flush()
    return revision


# --- staleness -------------------------------------------------------------


def item_staleness(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    item: IcaapPillar2Item,
    *,
    resolved: params.P2Parameters | None = None,
) -> tuple[str, ...]:
    """Why this computed figure no longer matches what it was computed from.

    Recomputed on read rather than stored, for the same reason block staleness
    is: a new capital run or a console change must be visible immediately, not
    after somebody re-opens the item.
    """
    if item.method_status not in _APPROVABLE_STATUSES or item.inputs_digest is None:
        return ()
    revision = db.scalar(
        select(IcaapPillar2ItemRevision)
        .where(
            IcaapPillar2ItemRevision.organization_id == access.ctx.organization_id,
            IcaapPillar2ItemRevision.item_id == item.id,
            IcaapPillar2ItemRevision.revision_no == item.current_revision_no,
        )
        .limit(1)
    )
    computation = (revision.computation if revision is not None else None) or {}
    reasons: list[str] = []
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415 - mutual read

    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    for binding_ref in computation.get("input_bindings", []):
        if not isinstance(binding_ref, Mapping):
            continue
        block_type = str(binding_ref.get("block_type"))
        current = bound.get(block_type)
        if current is None:
            reasons.append(f"binding_missing:{block_type}")
        elif current.payload_sha256 != binding_ref.get("payload_sha256"):
            reasons.append(f"binding_superseded:{block_type}")
    recorded_ids = computation.get("parameter_row_ids")
    if isinstance(recorded_ids, Mapping) and recorded_ids:
        current_params = resolved or params.resolve_p2(
            db, access.bank, as_of=cycle.as_of_date, codes=sorted(recorded_ids)
        )
        now = current_params.row_ids(sorted(recorded_ids))
        for code, parameter_id in recorded_ids.items():
            if now.get(str(code)) != parameter_id:
                reasons.append(f"parameter_changed:{code}")
    probe = computation.get("canonical_probe_key")
    if probe is not None and item.method in {
        "benchmark_mapped",
        "hhi_proportional_heuristic",
        "granularity_adjustment",
    }:
        _name, _sector, current_probe = pillar2_inputs._concentration_vectors(  # noqa: SLF001
            db, access, cycle
        )
        if current_probe != probe:
            reasons.append("canonical_book_changed")
    return tuple(reasons)


# --- reads -----------------------------------------------------------------


def _item_read(
    item: IcaapPillar2Item,
    *,
    stale_reasons: Sequence[str],
    computation: dict[str, Any] | None,
    editable: bool,
) -> IcaapPillar2ItemRead:
    approval_current = (
        item.approved_revision_no is not None
        and item.approved_revision_no == item.current_revision_no
    )
    parameter_rows = (computation or {}).get("parameters_used") or []
    return IcaapPillar2ItemRead(
        id=item.id,
        item_key=item.item_key,
        risk_key=item.risk_key,
        category_key=item.category_key,
        component_key=item.component_key,
        table5_row=item.table5_row,
        method=item.method,
        method_label=PILLAR2_METHODS[item.method].title
        if item.method in PILLAR2_METHODS
        else item.method,
        method_version=item.method_version,
        source=item.source,  # pyright: ignore[reportArgumentType]
        input_mode=item.input_mode,  # pyright: ignore[reportArgumentType]
        method_status=item.method_status,  # pyright: ignore[reportArgumentType]
        status_detail=item.status_detail,
        basis=item.basis,  # pyright: ignore[reportArgumentType]
        basis_value=item.basis_value,
        baseline_amount=item.baseline_amount,
        stressed_amount=item.stressed_amount,
        baseline_derivation=item.baseline_derivation,
        stressed_derivation=item.stressed_derivation,
        currency=item.currency,
        inputs_digest=item.inputs_digest,
        scenario_definition=item.scenario_definition,
        rationale=item.rationale,
        zero_amount_justification=item.zero_amount_justification,
        evidence_attachment_ids=pillar2_inputs.evidence_ids(item),
        evidence_reference=item.evidence_reference,
        current_revision_no=item.current_revision_no,
        approved_revision_no=item.approved_revision_no,
        approved_by=item.approved_by,
        approved_at=item.approved_at,
        approval_note=item.approval_note,
        approval_current=approval_current,
        stale=bool(stale_reasons),
        stale_reasons=list(stale_reasons),
        computation=computation,
        parameters=[
            IcaapParameterUseRead(**row) for row in parameter_rows if isinstance(row, dict)
        ],
        representative_parameters=[
            str(row.get("param_code"))
            for row in parameter_rows
            if isinstance(row, dict) and row.get("representative")
        ],
        pending_parameters=[
            str(row.get("param_code"))
            for row in parameter_rows
            if isinstance(row, dict) and row.get("confirmation_status") == params.PENDING
        ],
        editable=editable,
        approvable=(
            editable
            and not stale_reasons
            and item.method_status in _APPROVABLE_STATUSES
            and not approval_current
        ),
        updated_at=item.updated_at,
    )


def _latest_computation(
    db: Session, access: IcaapAccess, item: IcaapPillar2Item
) -> dict[str, Any] | None:
    revision = db.scalar(
        select(IcaapPillar2ItemRevision)
        .where(
            IcaapPillar2ItemRevision.organization_id == access.ctx.organization_id,
            IcaapPillar2ItemRevision.item_id == item.id,
            IcaapPillar2ItemRevision.computation.is_not(None),
        )
        .order_by(IcaapPillar2ItemRevision.revision_no.desc())
        .limit(1)
    )
    return None if revision is None else revision.computation


def _totals(framework: Framework, items: Sequence[IcaapPillar2Item]):
    return register_domain.row_totals(
        register_items(items), row_keys=[row.key for row in framework.table5_rows]
    )


#: The code a register finding carries when it states a framework-declared
#: method mandate rather than reporting a defect.
#:
#: WHY IT RIDES THE FINDINGS LIST. The Pillar 2 card has to be able to say
#: whether the standardised framework is already required for the cycle's
#: as-of date. That answer is a GOVERNED console row resolved against the
#: framework's own ``method_mandates`` declaration (A6) — not something a
#: screen may compute from a date it was handed, which is the client-mirrors-
#: the-rule mistake this workspace avoids everywhere else. ``params`` carries
#: the server's own sentence, so the card prints it rather than composing one.
MANDATE_FINDING = "pillar2_method_mandate"


def _mandate_findings(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> list[IcaapPillar2FindingRead]:
    """Every method mandate this framework declares, resolved for this cycle.

    A DISPATCH-plane read: it decides what to show and seals no run, so the
    commencement row must not enter the session's consumption ledger (D-078).
    ``sf_state`` is the one seam that resolves it with ``record=False``, and
    calling it here rather than re-resolving the code is what keeps the card,
    the readiness checklist and the freeze preflight from disagreeing.

    A framework that declares no mandate produces no finding — Nigeria's and
    Kenya's declare none, and a silent card is correct there.
    """
    from app.services.icaap import sf_state  # noqa: PLC0415 - avoid an import cycle
    from app.services.regulatory_irr_sf import SfMandate  # noqa: PLC0415

    decisions = sf_state.method_mandates(
        db, access.bank, framework, as_of=cycle.as_of_date, today=date.today()
    )
    findings: list[IcaapPillar2FindingRead] = []
    for decision in decisions:
        statement = SfMandate(
            mandatory=decision.mandatory,
            mandatory_from=decision.mandatory_from,
            as_of=cycle.as_of_date,
            confirmation_status=decision.confirmation_status,
            source_citation="",
        ).statement
        findings.append(
            IcaapPillar2FindingRead(
                code=MANDATE_FINDING,
                ref=decision.component_keys[0] if decision.component_keys else decision.method,
                params={
                    "method": decision.method,
                    "replaces": decision.replaces or "",
                    "component_keys": ",".join(decision.component_keys),
                    "mandatory": "true" if decision.mandatory else "false",
                    "mandatory_from": (
                        ""
                        if decision.mandatory_from is None
                        else decision.mandatory_from.isoformat()
                    ),
                    "as_of": cycle.as_of_date.isoformat(),
                    "confirmation_status": decision.confirmation_status,
                    "param_code": decision.param_code,
                    "statement": statement,
                },
            )
        )
    return findings


def get_register(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapPillar2RegisterRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    items = _items(db, access, cycle)
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=REGISTER_CODES)
    editable = cycle.status in guards.EDITABLE_STATUSES and not access.examiner
    labels = {row.key: row.label for row in framework.table5_rows}
    totals = _totals(framework, items)
    findings = register_domain.like_for_like_findings(register_items(items))
    reads = [
        _item_read(
            item,
            stale_reasons=item_staleness(db, access, cycle, item),
            computation=_latest_computation(db, access, item),
            editable=editable,
        )
        for item in items
    ]
    return IcaapPillar2RegisterRead(
        cycle_id=cycle.id,
        currency=jurisdictions.base_currency(access.bank),
        items=reads,
        components=[
            IcaapPillar2ComponentSlotRead(
                component_key=slot.component.key,
                category_key=slot.category_key,
                risk_key=slot.risk_key,
                table5_row=slot.component.table5_row,
                p29_class=slot.component.p29_class,
                allowed_methods=[
                    method
                    for method in slot.component.allowed_methods
                    if method not in DEFERRED_METHODS
                ],
                deferred_methods=[
                    method
                    for method in slot.component.allowed_methods
                    if method in DEFERRED_METHODS
                ],
                item_id=None if slot.item is None else slot.item.id,
            )
            for slot in component_slots(framework, items)
        ],
        table5_totals=IcaapTable5TotalsRead(
            rows=[
                IcaapTable5RowTotalRead(
                    row=row.row,
                    label=labels.get(row.row, row.row),
                    baseline=row.baseline,
                    stressed=row.stressed,
                    item_keys=list(row.item_keys),
                    sources=list(row.sources),
                    partial=row.partial,
                )
                for row in totals.rows
            ],
            total_baseline=totals.total_baseline,
            total_stressed=totals.total_stressed,
            partial_rows=list(totals.partial_rows),
        ),
        consistency=_consistency(db, access, cycle, framework, items, resolved),
        diversification_allowed=resolved.optional_flag("icaap_diversification_benefit_allowed"),
        findings=[
            IcaapPillar2FindingRead(code=finding.code, ref=finding.ref, params=dict(finding.params))
            for finding in findings
        ]
        + _mandate_findings(db, access, cycle, framework),
        parameters=[IcaapParameterUseRead(**use) for use in resolved.uses(REGISTER_CODES)],
    )


def _consistency(  # noqa: PLR0913 - the control compares five named sources
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    framework: Framework,
    items: Sequence[IcaapPillar2Item],
    resolved: params.P2Parameters,
) -> list[IcaapConsistencyRead]:
    """The internal control (M1): does the ICAAP agree with its own sources?

    Deliberately separate from the ¶49(l) reconciliation. This compares the
    ICAAP register with the approved capital plan, the supervisory add-ons and
    the stress overlay, and the domain refuses outright if a baseline map is
    handed where a stressed one belongs.
    """
    from app.domain.icaap import reconciliation as recon_domain  # noqa: PLC0415

    rows = [row.key for row in framework.table5_rows]
    totals = _totals(framework, items)
    icaap_baseline = recon_domain.BaselineByRow({row.row: row.baseline for row in totals.rows})
    icaap_stressed = recon_domain.StressedByRow({row.row: row.stressed for row in totals.rows})
    plan_baseline = recon_domain.BaselineByRow(_plan_by_row(db, access, cycle, rows))
    supervisory = recon_domain.BaselineByRow(_supervisory_by_row(db, access, cycle, rows))
    overlay = recon_domain.StressedByRow(_overlay_by_row(db, access, cycle, rows))
    try:
        comparisons = recon_domain.pillar2_source_consistency(
            icaap_baseline=icaap_baseline,
            plan_baseline=plan_baseline,
            supervisory_baseline=supervisory,
            icaap_stressed=icaap_stressed,
            overlay_stressed=overlay,
            tolerance_pct=resolved.optional_decimal("icaap_pillar2_source_tolerance_pct"),
        )
    except TypeError as exc:
        message = str(exc)
        if message.startswith("basis_mismatch:"):
            raise params.basis_mismatch(message.split(":", maxsplit=1)[1]) from exc
        raise
    explanations = _control_explanations(db, access, cycle)
    out: list[IcaapConsistencyRead] = []
    for comparison in comparisons:
        stored = explanations.get(comparison.comparison_key)
        values_digest = digests.register_digest(
            {
                "icaap": _text(comparison.icaap_value),
                "other": _text(comparison.other_value),
            }
        )
        out.append(
            IcaapConsistencyRead(
                comparison_key=comparison.comparison_key,
                basis=comparison.basis,
                comparator=comparison.comparator,
                row=comparison.row,
                icaap=comparison.icaap_value,
                other=comparison.other_value,
                relative_diff_pct=comparison.relative_difference_pct,
                status=comparison.status,  # pyright: ignore[reportArgumentType]
                explanation=None if stored is None else stored.explanation,
                explanation_current=(stored is not None and stored.values_digest == values_digest),
            )
        )
    return out


def _control_explanations(db: Session, access: IcaapAccess, cycle: IcaapCycle):
    from app.models.icaap_risk_capital import IcaapControlExplanation  # noqa: PLC0415

    return {
        row.comparison_key: row
        for row in db.scalars(
            select(IcaapControlExplanation).where(
                IcaapControlExplanation.organization_id == access.ctx.organization_id,
                IcaapControlExplanation.cycle_id == cycle.id,
            )
        )
    }


def _plan_by_row(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, rows: Sequence[str]
) -> dict[str, Decimal | None]:
    """The approved capital plan's add-ons, converted to amounts by Table 5 row."""
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415

    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    plan = bound.get("capital_plan")
    if plan is None:
        return dict.fromkeys(rows)
    pillar1 = bound.get(pillar2_inputs.BLOCK_PILLAR1)
    total_rwa = _decimal(blocks_service.fact_value(pillar1, "total_rwa"))
    if total_rwa is None:
        return dict.fromkeys(rows)
    by_row: dict[str, Decimal | None] = dict.fromkeys(rows)
    for table in (plan.payload or {}).get("tables", []):
        if not isinstance(table, dict) or table.get("key") != "pillar2_addons":
            continue
        for entry in table.get("rows", []):
            cells = entry.get("cells", {}) if isinstance(entry, dict) else {}
            row_key = _row_for_label(str(cells.get("risk_type") or ""), rows)
            raw_pct = cells.get("add_on_pct_rwa")
            pct = _decimal(None if raw_pct is None else str(raw_pct))
            if row_key is None or pct is None:
                continue
            amount = pct * total_rwa / HUNDRED
            by_row[row_key] = (by_row.get(row_key) or Decimal(0)) + amount
    return by_row


def _row_for_label(label: str, rows: Sequence[str]) -> str | None:
    key = label.strip().casefold().replace(" ", "_")
    return key if key in set(rows) else None


def _supervisory_by_row(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, rows: Sequence[str]
) -> dict[str, Decimal | None]:
    from app.services.icaap import supervisory_addons  # noqa: PLC0415 - mutual read

    return supervisory_addons.amounts_by_table5_row(db, access, cycle, rows)


def _overlay_by_row(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, rows: Sequence[str]
) -> dict[str, Decimal | None]:
    """The attested stress run's own Pillar 2 grid, year one, as amounts."""
    from app.domain.icaap.pillar2 import table5 as table5_domain  # noqa: PLC0415
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415

    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    appendix = bound.get(pillar2_inputs.BLOCK_APPENDIX)
    by_row: dict[str, Decimal | None] = dict.fromkeys(rows)
    if appendix is None:
        return by_row
    raw = (appendix.payload or {}).get("raw", {}).get("raw_appendix_ii")
    if not isinstance(raw, Mapping):
        return by_row
    table5 = raw.get("table5_rwa")
    if not isinstance(table5, Mapping):
        return by_row
    for entry in table5.get("rows", []):
        if not isinstance(entry, Mapping) or entry.get("label") != "stress_y1":
            continue
        pillar2 = entry.get("pillar2")
        if not isinstance(pillar2, Mapping):
            continue
        for row in rows:
            field = table5_domain.APPENDIX_FIELD_BY_ROW.get(row, row)
            value = _decimal(None if pillar2.get(field) is None else str(pillar2[field]))
            by_row[row] = None if value is None else value * pillar2_inputs.FROM_REPORTING_SCALE
    return by_row


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except Exception:  # noqa: BLE001 - a malformed stored figure is simply absent
        return None


# --- mutations -------------------------------------------------------------


def _item_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, item_id: UUID
) -> IcaapPillar2Item:
    item = db.scalar(
        select(IcaapPillar2Item).where(
            IcaapPillar2Item.id == item_id,
            IcaapPillar2Item.organization_id == access.ctx.organization_id,
            IcaapPillar2Item.cycle_id == cycle.id,
        )
    )
    if item is None:
        guards.not_found()
    return item


def _check_method(slot: ComponentSlot, method: str, input_mode: str, cycle: IcaapCycle) -> None:
    if method not in slot.component.allowed_methods:
        raise guards.unprocessable(
            "method_not_allowed",
            "That method is not one the framework allows for this risk component.",
            component_key=slot.component.key,
            method=method,
        )
    if method in DEFERRED_METHODS:
        raise guards.unprocessable(
            "method_not_available",
            "That method is not built yet. Choose one of the methods offered.",
            method=method,
        )
    spec = METHOD_REGISTRY.get(method)
    if spec is None:  # pragma: no cover - the registry mirrors the vocabulary
        raise guards.unprocessable("method_not_available", "Unknown method.", method=method)
    if input_mode not in spec.supported_input_modes:
        raise guards.conflict(
            "method_basis_unsupported",
            "This method cannot read bound engine figures in that mode.",
            method=method,
            input_mode=input_mode,
        )
    if cycle.basis == "consolidated" and input_mode == "bound_blocks":
        raise guards.conflict(
            "method_basis_unsupported",
            "The engines compute solo figures, so a consolidated ICAAP supplies this "
            "risk's inputs by hand with evidence.",
            method=method,
            basis=cycle.basis,
        )


def _check_evidence(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, attachment_ids: Sequence[UUID]
) -> None:
    if not attachment_ids:
        return
    found = {
        row.id
        for row in db.scalars(
            select(IcaapAttachment).where(
                IcaapAttachment.organization_id == access.ctx.organization_id,
                IcaapAttachment.cycle_id == cycle.id,
                IcaapAttachment.id.in_(list(attachment_ids)),
            )
        )
    }
    missing = [str(value) for value in attachment_ids if value not in found]
    if missing:
        raise guards.unprocessable(
            "evidence_required",
            "One of those evidence files is not part of this ICAAP.",
            attachment_ids=missing,
        )


def create_item(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapPillar2ItemCreate
) -> IcaapPillar2ItemRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    slot = _component_or_422(framework, payload.component_key)
    _check_method(slot, payload.method, payload.input_mode, cycle)
    _check_evidence(db, access, cycle, payload.evidence_attachment_ids)
    item = IcaapPillar2Item(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        item_key=slot.component.key,
        risk_key=slot.risk_key,
        category_key=slot.category_key,
        component_key=slot.component.key,
        table5_row=slot.component.table5_row,
        method=payload.method,
        source=payload.source,
        input_mode=payload.input_mode,
        method_status=MethodStatus.NOT_COMPUTABLE.value
        if payload.method == "not_capitalised"
        else "not_computed",
        currency=jurisdictions.base_currency(access.bank),
        scenario_definition=payload.scenario_definition,
        rationale=payload.rationale,
        evidence_attachment_ids=[str(value) for value in payload.evidence_attachment_ids],
        evidence_reference=payload.evidence_reference,
        current_revision_no=0,
        created_by=guards.actor_id(access),
        updated_by=guards.actor_id(access),
    )
    if payload.method == "not_capitalised":
        item.method_status = MethodStatus.NOT_CAPITALISED.value
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "item_exists", "This risk component already carries a Pillar 2 figure."
        ) from exc
    _write_revision(db, access, cycle, item, change_kind="created", note=payload.reason)
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.item_created",
        entity_type="icaap_pillar2_item",
        entity_id=item.id,
        details={
            "cycle_id": str(cycle.id),
            "component_key": item.component_key,
            "method": item.method,
            "source": item.source,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    return _item_read(item, stale_reasons=(), computation=None, editable=True)


def update_item(  # noqa: PLR0913 - the addressed item is five parts plus the payload
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2ItemUpdate,
) -> IcaapPillar2ItemRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    item = _item_or_404(db, access, cycle, item_id)
    _require_revision(item, payload.base_revision_no)
    if item.retired_at is not None:
        raise guards.conflict("item_retired", "This Pillar 2 figure has been retired.")
    slot = _component_or_422(framework, item.component_key)
    method = payload.method or item.method
    input_mode = payload.input_mode or item.input_mode
    _check_method(slot, method, input_mode, cycle)
    if payload.evidence_attachment_ids is not None:
        _check_evidence(db, access, cycle, payload.evidence_attachment_ids)
        item.evidence_attachment_ids = [str(value) for value in payload.evidence_attachment_ids]
    item.method = method
    item.input_mode = input_mode
    if payload.source is not None:
        item.source = payload.source
    if payload.rationale is not None:
        item.rationale = payload.rationale
    if payload.zero_amount_justification is not None:
        item.zero_amount_justification = payload.zero_amount_justification
    if payload.evidence_reference is not None:
        item.evidence_reference = payload.evidence_reference
    if payload.scenario_definition is not None:
        item.scenario_definition = payload.scenario_definition
    if method == "judgemental":
        _apply_judgemental(db, access, cycle, item, payload)
    # Changing anything a method reads invalidates the computed figure: a
    # stale amount beside a new method would read as if it had been checked.
    elif payload.method is not None and payload.method != item.method:
        item.method_status = "not_computed"
    item.updated_by = guards.actor_id(access)
    _write_revision(db, access, cycle, item, change_kind="edited", note=payload.reason)
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.item_updated",
        entity_type="icaap_pillar2_item",
        entity_id=item.id,
        details={
            "cycle_id": str(cycle.id),
            "component_key": item.component_key,
            "method": item.method,
            "revision_no": item.current_revision_no,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    return _item_read(
        item,
        stale_reasons=item_staleness(db, access, cycle, item),
        computation=_latest_computation(db, access, item),
        editable=True,
    )


def _require_revision(item: IcaapPillar2Item, base_revision_no: int) -> None:
    if base_revision_no != item.current_revision_no:
        raise guards.conflict(
            "revision_changed",
            "Somebody else changed this figure while you were editing it.",
            current_revision_no=item.current_revision_no,
        )


def _apply_judgemental(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    item: IcaapPillar2Item,
    payload: IcaapPillar2ItemUpdate,
) -> None:
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=REGISTER_CODES)
    material = _risk_is_material(db, access, cycle, item.risk_key)
    result = judgemental_domain.validate_judgemental(
        component=item.component_key,
        baseline=payload.baseline_amount,
        stressed=payload.stressed_amount,
        stressed_same_as_baseline=payload.stressed_same_as_baseline,
        rationale=payload.rationale or item.rationale,
        evidence_count=len(item.evidence_attachment_ids or []),
        zero_amount_justification=payload.zero_amount_justification
        or item.zero_amount_justification,
        risk_is_material=material,
        diversification_allowed=resolved.optional_flag("icaap_diversification_benefit_allowed"),
    )
    if result.status is MethodStatus.INCOMPLETE:
        code = _judgemental_code(result.reasons)
        raise guards.unprocessable(
            code,
            _JUDGEMENTAL_COPY.get(code, "This judgement is not complete."),
            reasons=list(result.reasons),
        )
    _store_result(item, result, basis_override=payload.basis)


_JUDGEMENTAL_COPY = {
    "rationale_required": "A Board judgement needs the reasoning written down.",
    "evidence_required": "A Board judgement needs its evidence attached.",
    "zero_amount_justification_required": (
        "A material risk quantified at zero needs that explained."
    ),
    "diversification_not_allowed": (
        "This institution has no approved diversification benefit, so a negative "
        "Pillar 2 figure cannot be recorded."
    ),
}


def _judgemental_code(reasons: Sequence[str]) -> str:
    for reason in reasons:
        head = reason.split(":", maxsplit=1)[0]
        if head in _JUDGEMENTAL_COPY:
            return head
    return "rationale_required"


def _risk_is_material(db: Session, access: IcaapAccess, cycle: IcaapCycle, risk_key: str) -> bool:
    from app.models.icaap_risk_capital import IcaapRiskAssessment  # noqa: PLC0415

    row = db.scalar(
        select(IcaapRiskAssessment).where(
            IcaapRiskAssessment.organization_id == access.ctx.organization_id,
            IcaapRiskAssessment.cycle_id == cycle.id,
            IcaapRiskAssessment.risk_key == risk_key,
        )
    )
    return row is not None and row.verdict == "material"


def _store_result(
    item: IcaapPillar2Item, result: MethodResult, *, basis_override: str | None = None
) -> None:
    item.method_version = result.method_version
    item.method_status = result.status.value
    item.basis = (
        basis_override
        if basis_override is not None
        else (None if result.basis is None else result.basis.value)
    )
    item.basis_value = result.basis_value
    item.baseline_amount = result.baseline_amount
    item.stressed_amount = result.stressed_amount
    item.baseline_derivation = result.baseline_derivation
    item.stressed_derivation = result.stressed_derivation
    if result.scenario_definition is not None:
        item.scenario_definition = dict(result.scenario_definition)
    item.status_detail = _status_detail(result)


# ---------------------------------------------------------------------------
# States whose whole sentence is written here
# ---------------------------------------------------------------------------
#
# ``_COMPUTE_COPY`` ends every reason with "Link the missing figures to this
# ICAAP, then compute it again." For the granularity adjustment that is wrong
# advice: nothing is unlinked. The method either declines to engage on the book
# it was given, or refuses the whole measurement because the book could not be
# assembled completely — and in both cases what a preparer has to do next is
# different. So these reasons carry their own sentence, and the template is not
# wrapped around them.


def _ga_sample(tail: str) -> tuple[str, str]:
    """``"<count>:<ref>:<ref>…"`` as (count, a readable sample)."""
    count, _, sample = tail.partition(_GA_FIELD)
    references = [entry for entry in sample.split(_GA_FIELD) if entry]
    return count or "several", ", ".join(references)


def _ga_book_statement(singular: str, plural: str, fix: str, tail: str) -> str:
    """One refused book, said in a sentence that reads correctly for one row.

    The singular and plural forms are both written out rather than a count
    stitched onto a fixed clause: "1 exposures carry no probability of default"
    is the kind of sentence that tells a preparer the screen was assembled
    rather than written.
    """
    count, sample = _ga_sample(tail)
    subject = (
        f"one exposure {singular}" if count == "1" else f"{count} exposures {plural}"
    )
    lead = (
        f"The granularity adjustment was refused for this book: {subject}. "
        "A granularity adjustment computed over only the names that could be priced is a "
        "smaller figure that looks exactly like a correct one, so no figure is reported "
        f"rather than an understated one. {fix}"
    )
    return lead if not sample else f"{lead} First references: {sample}."


#: Why the obligor book could not be assembled completely, and what to do —
#: one entry per refusal the book builder raises (D-070 rule 1: a partial book
#: refuses the WHOLE measurement).
_GA_BOOK_REFUSALS: Mapping[str, tuple[str, str, str]] = {
    granularity_inputs.REFUSAL_PD_UNRESOLVED: (
        "carries no probability of default the platform can resolve",
        "carry no probability of default the platform can resolve",
        "Supply a rating or a probability of default for them, or record this "
        "component by another method.",
    ),
    granularity_inputs.REFUSAL_SEGMENT_UNMAPPED: (
        "belongs to a counterparty type the governed segment map does not cover",
        "belong to counterparty types the governed segment map does not cover",
        "Extend the counterparty segment map in the console, or record this "
        "component by another method.",
    ),
    granularity_inputs.REFUSAL_MATURITY_UNRESOLVED: (
        "states no remaining maturity, which this calibration requires",
        "state no remaining maturity, which this calibration requires",
        "Supply their maturities, or record this component by another method.",
    ),
}


def _granularity_statement(
    reason: str, detail: Mapping[str, str | None]
) -> str | None:
    """The granularity adjustment's own states, said in full or not at all."""
    head, _, tail = reason.partition(_GA_FIELD)
    if head == granularity_credit.REASON_BELOW_MIN_EFFECTIVE_NAMES:
        # GAP-4 item 4. The design's §4.5 aggregation rule — take the higher of
        # this adjustment and a benchmark charge — is NOT built, and no
        # calibration for a benchmark was invented to build it. The product
        # therefore has to say what actually happens to a bank under the floor,
        # including the part that reads badly: the floor switches the method off
        # for the banks whose books are most concentrated.
        floor = detail.get("min_effective_names")
        names = detail.get("n_eff")
        measured = (
            f"This book has {names} effective names against a floor of {floor}. "
            if names is not None and floor is not None
            else ""
        )
        return (
            "The granularity adjustment does not engage on this book. "
            + measured
            + "The floor is the point below which a first-order correction stops being "
            "reliable; it is a REPRESENTATIVE AequorOS calibration, pending confirmation "
            "with the supervisor, not a published supervisory value, and it is editable "
            "in the console. Nothing is substituted in its place — no benchmark charge "
            "is applied as an arithmetic floor under this method — so this component "
            "stays unquantified until an officer chooses another method for it and says "
            "why. Read the gate carefully before treating it as comfort: a book "
            "concentrated enough to fall under the floor carries MORE name "
            "concentration risk, not less."
        )
    if head == granularity_credit.REASON_NO_EXPOSURES:
        return (
            "The granularity adjustment measured nothing: after the exclusions this "
            "method discloses, no obligor exposure was left in the book. Check the "
            "credit exposures for this reporting date before relying on the absence."
        )
    if head == granularity_inputs.REFUSAL_BOOK_NOT_SUPPLIED:
        return (
            "The granularity adjustment needs the institution's own obligor book, which "
            "cannot be entered by hand. Switch this item to compute from linked figures, "
            "or record the component by a method that accepts a manual amount."
        )
    if head == granularity_credit.REASON_K_STAR_NOT_POSITIVE:
        return (
            "The granularity adjustment is undefined for this book: its "
            "exposure-weighted capital charge is not positive, so the correction has "
            "nothing to scale. Check the risk weights behind the credit exposures."
        )
    book = _GA_BOOK_REFUSALS.get(head)
    return None if book is None else _ga_book_statement(*book, tail)


def _status_detail(result: MethodResult) -> str | None:
    """Plain language for a state, naming blocks by the title a preparer sees."""
    if result.status in (MethodStatus.COMPUTED, MethodStatus.INTERIM_NON_SF):
        return None
    if not result.reasons:
        return None
    template = _COMPUTE_COPY.get(result.status.value)
    written: list[str] = []
    phrases: list[str] = []
    for reason in result.reasons:
        statement = _granularity_statement(reason, result.detail)
        if statement is None:
            phrases.append(_reason_phrase(reason))
        else:
            written.append(statement)
    parts: list[str] = []
    if phrases:
        joined = ", ".join(phrases)
        parts.append(joined if template is None else template.format(detail=joined))
    parts.extend(written)
    return " ".join(parts) or None


def _reason_phrase(reason: str) -> str:
    head, _, tail = reason.partition(":")
    if head == "binding_missing" and tail:
        spec = BLOCK_CATALOGUE.get(tail)
        title = spec.title if spec is not None else tail.replace("_", " ")
        return f"{title} is not linked to this ICAAP"
    if head == irrbb_sf_domain.REASON_REFUSED and tail:
        # The engine's ONE refusal name (D-061) reaches the register unchanged;
        # only the SENTENCE is rendered here, and an unmapped code still says
        # something truthful rather than printing the raw token.
        from app.services.icaap.resolvers import irrbb_sf as sf_resolver  # noqa: PLC0415

        copy = sf_resolver.REFUSAL_COPY.get(tail)
        return (
            f"the standardised framework refused this measurement because {copy}"
            if copy is not None
            else "the standardised framework refused this measurement"
        )
    if head == irrbb_sf_domain.REASON_MEASURE_ABSENT:
        return "the standardised framework run reports no economic value risk measure"
    if head == irrbb_sf_domain.REASON_SOURCE_ABSENT:
        return "no standardised framework result is linked to this ICAAP"
    if not tail:
        return head.replace("_", " ")
    return f"{head.replace('_', ' ')}: {tail}"


def compute_item(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2Compute,
) -> IcaapPillar2ItemRead:
    """Run the method on this cycle's current inputs and write a revision."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    item = _item_or_404(db, access, cycle, item_id)
    _require_revision(item, payload.base_revision_no)
    if item.retired_at is not None:
        raise guards.conflict("item_retired", "This Pillar 2 figure has been retired.")
    slot = _component_or_422(framework, item.component_key)
    _check_method(slot, item.method, item.input_mode, cycle)
    if item.method == "judgemental":
        raise guards.conflict(
            "method_basis_unsupported",
            "A Board judgement is entered, not computed.",
            method=item.method,
        )

    codes = _codes_for(item.method)
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=codes)
    try:
        inputs = pillar2_inputs.build(db, access, cycle, item, manual_inputs=payload.manual_inputs)
    except MissingParameter as exc:
        # Assembling a method's inputs can itself need a governed row — the
        # granularity book resolves the counterparty-segment map before there is
        # anything to compute. The operator is told which console row to fill.
        raise params.from_missing(exc) from exc
    if inputs.missing_blocks:
        result = MethodResult(
            method=item.method,
            method_version=item.method_version or "",
            status=MethodStatus.NOT_COMPUTABLE,
            reasons=tuple(f"binding_missing:{name}" for name in inputs.missing_blocks),
        )
    else:
        try:
            result = _dispatch(item, inputs, resolved)
        except MissingParameter as exc:
            raise params.from_missing(exc) from exc
        except UnitConversionError as exc:
            raise guards.conflict(
                "denominator_missing",
                "The figures this conversion needs are not available for this ICAAP.",
                basis=exc.basis.value,
                denominator=exc.denominator,
            ) from exc
    _store_result(item, result)
    computation = {
        "detail": dict(result.detail),
        "reasons": list(result.reasons),
        "input_bindings": inputs.bindings,
        "canonical_probe_key": inputs.canonical_probe_key,
        "manual_inputs": inputs.manual,
        "parameters_used": resolved.provenance_for(result.parameters_used),
        "parameter_row_ids": resolved.row_ids([use.code for use in result.parameters_used]),
    }
    item.inputs_digest = digests.inputs_digest(
        {
            "inputs": inputs.digest_body(),
            "parameters": computation["parameters_used"],
            "method": item.method,
        }
    )
    item.updated_by = guards.actor_id(access)
    _write_revision(
        db,
        access,
        cycle,
        item,
        change_kind="computed",
        computation=computation,
        note=payload.reason,
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.item_computed",
        entity_type="icaap_pillar2_item",
        entity_id=item.id,
        details={
            "cycle_id": str(cycle.id),
            "component_key": item.component_key,
            "method": item.method,
            "method_status": item.method_status,
            "baseline_amount": _text(item.baseline_amount),
            "stressed_amount": _text(item.stressed_amount),
            "inputs_digest": item.inputs_digest,
            "revision_no": item.current_revision_no,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    return _item_read(
        item,
        stale_reasons=item_staleness(db, access, cycle, item),
        computation=computation,
        editable=True,
    )


def _codes_for(method: str) -> tuple[str, ...]:  # noqa: PLR0911 - one per method
    if method == "benchmark_mapped":
        return CONCENTRATION_CODES
    if method == "hhi_proportional_heuristic":
        return HEURISTIC_CODES
    if method == "granularity_adjustment":
        return GRANULARITY_CODES
    if method == "irrbb_interim_delta_eve":
        return IRRBB_CODES
    if method == irrbb_sf_domain.METHOD:
        return IRRBB_SF_CODES
    if method == "fx_nop_addon":
        return FX_CODES
    if method == "operational_scenario_net_p1":
        return OPERATIONAL_CODES
    if method == "sovereign_stress_addon":
        return SOVEREIGN_CODES
    return ()


def _dispatch(  # noqa: PLR0911, PLR0912 - one branch per implemented method
    item: IcaapPillar2Item,
    inputs: pillar2_inputs.MethodInputs,
    resolved: params.P2Parameters,
) -> MethodResult:
    method = item.method
    if method == "benchmark_mapped":
        return concentration_domain.benchmark_mapped(
            name_vector=_require_vector(inputs.name_vector, "single_name"),
            sector_vector=_require_vector(inputs.sector_vector, "sector"),
            metric_set=_metric_set(resolved),
            cr_n=resolved.optional_integer("ccr_name_cr_n"),
            tables=_band_tables(resolved),
            min_coverage_pct=resolved.optional_decimal("ccr_min_dimension_coverage_pct"),
            baseline=inputs.baseline,
            stressed=inputs.stressed,
        )
    if method == "hhi_proportional_heuristic":
        return concentration_domain.hhi_proportional_heuristic(
            name_vector=_require_vector(inputs.name_vector, "single_name"),
            sector_vector=_require_vector(inputs.sector_vector, "sector"),
            name_coeff=resolved.optional_decimal("ccr_name_hhi_coeff"),
            sector_coeff=resolved.optional_decimal("ccr_sector_hhi_coeff"),
            baseline=inputs.baseline,
            stressed=inputs.stressed,
        )
    if method == "granularity_adjustment":
        book = inputs.ga_book or granularity_inputs.EMPTY_BOOK
        book_reasons = (
            book.refusals
            if inputs.ga_book is not None
            else (granularity_inputs.REFUSAL_BOOK_NOT_SUPPLIED,)
        )
        return granularity_domain.granularity_adjustment_method(
            exposures=book.exposures,
            params=granularity_domain.parse_params(
                confidence_q=resolved.optional_decimal(granularity_domain.PARAM_CONFIDENCE_Q),
                delta=resolved.optional_decimal(granularity_domain.PARAM_DELTA),
                lgd_variance_gamma=resolved.optional_decimal(
                    granularity_domain.PARAM_LGD_VARIANCE_GAMMA
                ),
                min_effective_names=resolved.optional_decimal(
                    granularity_domain.PARAM_MIN_EFFECTIVE_NAMES
                ),
                asset_correlation=resolved.optional_body(
                    granularity_domain.PARAM_ASSET_CORRELATION
                ),
                maturity_adjustment=resolved.optional_body(
                    granularity_domain.PARAM_MATURITY_ADJUSTMENT
                ),
            ),
            excluded=book.excluded,
            book_reasons=book_reasons,
            book_parameters=book.parameters_used,
        )
    if method == "irrbb_interim_delta_eve":
        scenarios = resolved.optional_body("icaap_irrbb_interim_scenarios") or {}
        return irrbb_domain.interim_delta_eve(
            deltas=inputs.irrbb_deltas,
            scenario_codes=_string_list(scenarios.get("codes")),
            required_codes=_string_list(scenarios.get("required")),
            tier1=inputs.tier1,
            outlier_threshold_pct_tier1=resolved.optional_decimal(
                "irrbb_outlier_threshold_pct_tier1"
            ),
        )
    if method == irrbb_sf_domain.METHOD:
        return irrbb_sf_domain.standardised_framework(
            figures=inputs.sf_figures,
            refusal=inputs.sf_refusal,
            outlier_threshold_pct_tier1=resolved.optional_decimal(
                irrbb_sf_domain.PARAM_OUTLIER_THRESHOLD
            ),
        )
    if method == "fx_nop_addon":
        shocks = resolved.optional_body("fx_p2_shock_pct")
        return fx_domain.fx_nop_addon(
            positions=inputs.fx_positions,
            shocks=None if shocks is None else fx_domain.FxShockSet.from_payload(shocks),
            market_rwa=inputs.market_rwa,
            car_min_pct=inputs.baseline.car_min_pct,
        )
    if method == "operational_scenario_net_p1":
        return operational_domain.operational_scenario_net_p1(
            gross_income=inputs.gross_income,
            severities_pct_gross_income=_severities(resolved),
            operational_rwa=inputs.operational_rwa,
            car_min_pct=inputs.baseline.car_min_pct,
            bank_scenarios=inputs.operational_scenarios,
            stressed_operational_rwa=inputs.stressed_operational_rwa,
            stressed_car_min_pct=(None if inputs.stressed is None else inputs.stressed.car_min_pct),
        )
    if method == "sovereign_stress_addon":
        grid_body = resolved.optional_body("sov_p2_haircut_pct")
        row = resolved.optional("sov_p2_haircut_pct")
        return sovereign_domain.sovereign_stress_addon(
            holdings=inputs.sovereign_holdings,
            grid=None if grid_body is None else sovereign_domain.parse_haircut_grid(grid_body),
            car_min_pct=inputs.baseline.car_min_pct,
            grid_param_id=None if row is None else row.parameter_id,
        )
    if method == "not_capitalised":
        return liquidity_domain.not_capitalised(ilaap_facts=inputs.ilaap_facts)
    raise guards.unprocessable(  # pragma: no cover - _check_method covers this
        "method_not_available", "That method is not built yet.", method=method
    )


def _require_vector(
    vector: concentration_domain.DimensionVector | None, dimension: str
) -> concentration_domain.DimensionVector:
    if vector is not None:
        return vector
    return concentration_domain.DimensionVector(dimension=dimension, stated=())


def _metric_set(resolved: params.P2Parameters) -> Mapping[str, Sequence[str]] | None:
    body = resolved.optional_body("ccr_metric_set")
    if body is None:
        return None
    metrics = body.get("metrics")
    source = metrics if isinstance(metrics, Mapping) else body
    return {
        str(key): _string_list(value) or ()
        for key, value in source.items()
        if key != "schema" and isinstance(value, list)
    }


def _string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(str(entry) for entry in value)


def _band_tables(resolved: params.P2Parameters):
    tables = {}
    for code, key in (
        ("ccr_name_bands_hhi", ("single_name", "hhi")),
        ("ccr_name_bands_gini", ("single_name", "gini")),
        ("ccr_name_bands_crn", ("single_name", "crn")),
        ("ccr_sector_bands_hhi", ("sector", "hhi")),
    ):
        if resolved.has(code):
            tables[key] = resolved.band_table(code)
    return tables


def _severities(resolved: params.P2Parameters) -> Mapping[str, Decimal] | None:
    body = resolved.optional_body("op_p2_scenario_severity_pct_gross_income")
    if body is None:
        return None
    scenarios = body.get("scenarios")
    source = scenarios if isinstance(scenarios, Mapping) else body
    out: dict[str, Decimal] = {}
    for key, value in source.items():
        if key in {"schema", "basis"}:
            continue
        try:
            out[str(key)] = Decimal(str(value))
        except Exception:  # noqa: BLE001 - shape validation ran at resolution
            continue
    return out


def approval_conditions(
    db: Session, ctx: TenantContext, bank: Bank, cycle_id: UUID, item_id: UUID
) -> tuple[ConditionCheck, ...]:
    """Maker-checker for a Pillar 2 figure: the author cannot approve it.

    "Author" means the author of the revision being approved, not merely of
    the item — a reviewer who corrected a typo three revisions ago is still an
    eligible approver of what somebody else then computed.
    """
    revision = db.scalar(
        select(IcaapPillar2ItemRevision)
        .join(
            IcaapPillar2Item,
            IcaapPillar2Item.id == IcaapPillar2ItemRevision.item_id,
        )
        .where(
            IcaapPillar2ItemRevision.organization_id == ctx.organization_id,
            IcaapPillar2ItemRevision.item_id == item_id,
            IcaapPillar2ItemRevision.cycle_id == cycle_id,
            IcaapPillar2Item.bank_id == bank.id,
            IcaapPillar2ItemRevision.revision_no == IcaapPillar2Item.current_revision_no,
        )
        .limit(1)
    )
    distinct = (
        revision is None or ctx.actor_user_id is None or revision.created_by != ctx.actor_user_id
    )
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "Pillar 2 approver is distinct from the author of this revision"
                if distinct
                else "the author of a Pillar 2 figure cannot approve it"
            ),
        ),
    )


def approve_item(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2Approve,
) -> IcaapPillar2ItemRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    item = _item_or_404(db, access, cycle, item_id)
    if payload.revision_no != item.current_revision_no:
        raise guards.conflict(
            "revision_changed",
            "This figure changed after you opened it. Review the current version.",
            current_revision_no=item.current_revision_no,
        )
    if item.retired_at is not None:
        raise guards.conflict("item_retired", "This Pillar 2 figure has been retired.")
    reasons: list[str] = []
    if item.approved_revision_no == item.current_revision_no:
        # Re-approving the same revision would silently replace the recorded
        # approver of a figure nobody had changed.
        reasons.append("already_approved")
    if item.method_status not in _APPROVABLE_STATUSES and item.method != "judgemental":
        reasons.append(f"status:{item.method_status}")
    if item.method == "judgemental" and item.baseline_amount is None:
        reasons.append("judgement_incomplete")
    stale = item_staleness(db, access, cycle, item)
    reasons.extend(stale)
    if reasons:
        raise guards.conflict(
            "item_not_approvable",
            "This figure is not ready to be approved.",
            reasons=reasons,
        )
    revision = db.scalar(
        select(IcaapPillar2ItemRevision).where(
            IcaapPillar2ItemRevision.organization_id == access.ctx.organization_id,
            IcaapPillar2ItemRevision.item_id == item.id,
            IcaapPillar2ItemRevision.revision_no == item.current_revision_no,
        )
    )
    actor = guards.actor_id(access)
    if revision is not None and revision.created_by == actor:
        raise guards.conflict(
            "self_approval",
            "The person who produced this figure cannot approve it (maker-checker).",
        )
    item.approved_revision_no = item.current_revision_no
    item.approved_by = actor
    item.approved_at = utc_now()
    item.approval_note = payload.note
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.item_approved",
        entity_type="icaap_pillar2_item",
        entity_id=item.id,
        details={
            "cycle_id": str(cycle.id),
            "component_key": item.component_key,
            "revision_no": item.current_revision_no,
            "note": payload.note,
        },
    )
    db.commit()
    db.refresh(item)
    return _item_read(
        item,
        stale_reasons=(),
        computation=_latest_computation(db, access, item),
        editable=True,
    )


def retire_item(
    db: Session, access: IcaapAccess, cycle_id: UUID, item_id: UUID, payload: IcaapRetire
) -> IcaapPillar2ItemRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    item = _item_or_404(db, access, cycle, item_id)
    item.retired_at = utc_now()
    item.retired_by = guards.actor_id(access)
    item.retire_reason = payload.reason
    item.updated_by = guards.actor_id(access)
    _write_revision(db, access, cycle, item, change_kind="retired", note=payload.reason)
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.item_retired",
        entity_type="icaap_pillar2_item",
        entity_id=item.id,
        details={
            "cycle_id": str(cycle.id),
            "component_key": item.component_key,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    return _item_read(item, stale_reasons=(), computation=None, editable=True)


def list_revisions(
    db: Session, access: IcaapAccess, cycle_id: UUID, item_id: UUID
) -> IcaapPillar2RevisionListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    item = _item_or_404(db, access, cycle, item_id)
    rows = db.scalars(
        select(IcaapPillar2ItemRevision)
        .where(
            IcaapPillar2ItemRevision.organization_id == access.ctx.organization_id,
            IcaapPillar2ItemRevision.item_id == item.id,
        )
        .order_by(IcaapPillar2ItemRevision.revision_no.desc())
    ).all()
    return IcaapPillar2RevisionListRead(
        item_id=item.id,
        revisions=[
            IcaapPillar2RevisionRead(
                id=row.id,
                revision_no=row.revision_no,
                change_kind=row.change_kind,
                round=row.round,
                snapshot=row.snapshot,
                computation=row.computation,
                inputs_digest=row.inputs_digest,
                snapshot_sha256=row.snapshot_sha256,
                note=row.note,
                created_by=row.created_by,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


# --- capital-plan proposal -------------------------------------------------


def propose_capital_plan_update(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    payload: Any,
) -> Any:
    """Carry the approved Pillar 2 figures into a capital-plan DRAFT.

    It goes through ``capital_plan.put_capital_plan``, so the existing
    maker-checker applies unchanged: the proposer becomes the plan's preparer
    and somebody else must approve it. Bypassing that to write an approved
    plan directly is the one thing this must never do.
    """
    from app.schemas.capital_plan import CapitalPlanContent, CapitalPlanPut  # noqa: PLC0415
    from app.services import capital_plan as capital_plan_service  # noqa: PLC0415
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415

    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    items = [item for item in _items(db, access, cycle) if item.table5_row is not None]
    not_ready = [
        item.item_key
        for item in items
        if item.approved_revision_no != item.current_revision_no
        or item.baseline_amount is None
        or item.baseline_amount <= Decimal(0)
        or item_staleness(db, access, cycle, item)
    ]
    carried = [item for item in items if item.item_key not in set(not_ready)]
    if not carried:
        raise guards.conflict(
            "proposal_items_not_ready",
            "No approved, current Pillar 2 figure is ready to carry into the plan.",
            items=not_ready,
        )
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    pillar1 = bound.get(pillar2_inputs.BLOCK_PILLAR1) or bound.get(pillar2_inputs.BLOCK_CAPITAL)
    total_rwa = _decimal(blocks_service.fact_value(pillar1, "total_rwa"))
    if total_rwa is None or total_rwa <= Decimal(0):
        raise guards.conflict(
            "proposal_items_not_ready",
            "The plan's add-ons are expressed against total risk-weighted assets, and "
            "this ICAAP has no current capital position linked.",
            items=not_ready,
        )

    current = capital_plan_service.get_capital_plan(db, access.ctx, access.bank.id)
    draft = current.current if current.current is not None else None
    if (
        draft is not None
        and draft.status == "draft"
        and not payload.replace_existing_draft
        and draft.prepared_by is not None
        and draft.prepared_by != access.ctx.actor_user_id
    ):
        raise guards.conflict(
            "capital_plan_draft_exists",
            "Somebody else has a capital-plan draft in progress. Ask them to finish it, "
            "or choose to replace it.",
            prepared_by=str(draft.prepared_by),
        )
    base = draft if (draft is not None and draft.status == "draft") else current.approved
    content = (
        CapitalPlanContent.model_validate(base.content)
        if base is not None
        else CapitalPlanContent()
    )
    labels = {row.key: row.label for row in framework.table5_rows}
    addons = []
    excluded = [
        item.item_key for item in items if item.component_key == ICAAP_DIVERSIFICATION_COMPONENT
    ]
    for item in carried:
        if item.component_key == ICAAP_DIVERSIFICATION_COMPONENT:
            continue
        amount = item.baseline_amount or Decimal(0)
        pct = from_amount(
            amount,
            Basis.PCT_TOTAL_RWA,
            _denominators_for(total_rwa),
        )
        label = labels.get(item.table5_row or "", item.component_key)
        method_label = (
            PILLAR2_METHODS[item.method].title if item.method in PILLAR2_METHODS else item.method
        )
        rationale = (
            f"ICAAP FY{cycle.fiscal_year} ({cycle.basis}) — {method_label}; "
            f"{amount} {item.currency}; cycle {cycle.id}, "
            f"item revision {item.current_revision_no}"
        )
        addons.append(
            {
                "risk_type": label,
                "add_on_pct_rwa": pct,
                "rationale": rationale,
                "item_key": item.item_key,
                "amount": amount,
                "item_revision_no": item.current_revision_no,
            }
        )
    content = content.model_copy(
        update={
            "pillar2_addons": [
                {
                    "risk_type": entry["risk_type"],
                    "add_on_pct_rwa": entry["add_on_pct_rwa"],
                    "rationale": entry["rationale"],
                }
                for entry in addons
            ]
        }
    )
    plan = capital_plan_service.put_capital_plan(
        db,
        access.ctx,
        access.bank.id,
        CapitalPlanPut.model_validate(
            {"content": content.model_dump(mode="json"), "reason": payload.reason}
        ),
        commit=False,
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.pillar2.capital_plan_proposed",
        entity_type="capital_plan",
        entity_id=plan.id,
        details={
            "cycle_id": str(cycle.id),
            "plan_id": str(plan.id),
            "version": plan.version,
            "item_revisions": {entry["item_key"]: entry["item_revision_no"] for entry in addons},
            "conversions": {entry["item_key"]: str(entry["add_on_pct_rwa"]) for entry in addons},
            "reason": payload.reason,
        },
    )
    db.commit()
    from app.schemas.icaap_risk_capital import (  # noqa: PLC0415 - response shape only
        IcaapCapitalPlanAddonRead,
        IcaapCapitalPlanProposalRead,
    )

    notes = [
        "A different person must approve this draft in Basel > Capital planning.",
    ]
    if excluded:
        notes.append(
            "A diversification benefit is not carried into a capital plan: plan "
            "add-ons are never negative."
        )
    return IcaapCapitalPlanProposalRead(
        plan_id=plan.id,
        version=plan.version,
        status=plan.status,
        total_rwa=total_rwa,
        currency=jurisdictions.base_currency(access.bank),
        addons=[
            IcaapCapitalPlanAddonRead(
                risk_type=entry["risk_type"],
                add_on_pct_rwa=entry["add_on_pct_rwa"],
                amount=entry["amount"],
                item_key=entry["item_key"],
                item_revision_no=entry["item_revision_no"],
                rationale=entry["rationale"],
            )
            for entry in addons
        ],
        excluded_item_keys=excluded,
        notes=notes,
    )


def _denominators_for(total_rwa: Decimal):
    from app.domain.icaap.units import Denominators  # noqa: PLC0415

    return Denominators(total_rwa=total_rwa)


def active_supervisory_addons(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> list[BankSupervisoryAddon]:
    from app.services.icaap import supervisory_addons  # noqa: PLC0415

    return supervisory_addons.active_for(db, access, cycle)


__all__ = [
    "CONCENTRATION_CODES",
    "FX_CODES",
    "GRANULARITY_CODES",
    "HEURISTIC_CODES",
    "IRRBB_CODES",
    "OPERATIONAL_CODES",
    "REGISTER_CODES",
    "SOVEREIGN_CODES",
    "ComponentSlot",
    "active_supervisory_addons",
    "approval_conditions",
    "build_table5",
    "approve_item",
    "component_slots",
    "compute_item",
    "create_item",
    "get_register",
    "get_table5",
    "item_staleness",
    "list_revisions",
    "propose_capital_plan_update",
    "register_items",
    "retire_item",
    "update_item",
]


# --- Table 5 (B3) ----------------------------------------------------------


def _appendix_columns(binding: Any) -> tuple[list[Any], dict[str, Any] | None]:
    """The attested run's Table 5 columns, in the order BoG prints them.

    Only the Pillar 1 half is taken. The run's own Pillar 2 grid is deliberately
    left behind: mixing it with the ICAAP register would put two different
    measurements of one risk in one row.
    """
    from app.domain.icaap.pillar2 import table5 as table5_domain  # noqa: PLC0415

    if binding is None:
        return [], None
    raw = (binding.payload or {}).get("raw", {}).get("raw_appendix_ii")
    if not isinstance(raw, Mapping):
        return [], None
    table5 = raw.get("table5_rwa")
    if not isinstance(table5, Mapping):
        return [], None
    columns: list[Any] = []
    for entry in table5.get("rows", []):
        if not isinstance(entry, Mapping):
            continue
        label = str(entry.get("label") or "")
        if not label:
            continue
        basis = "stressed" if label.startswith("stress") else "baseline"
        columns.append(
            table5_domain.AppendixColumn(
                key=label,
                label=label.replace("_", " ").capitalize(),
                basis=basis,
                pillar1={
                    field: _decimal(None if entry.get(field) is None else str(entry[field]))
                    for _key, _label, field in table5_domain.PILLAR1_ROWS
                },
            )
        )
    reference = {
        "block_id": str(binding.block_id),
        "seq": binding.seq,
        "payload_sha256": binding.payload_sha256,
    }
    return columns, reference


def build_table5(db: Session, access: IcaapAccess, cycle: IcaapCycle, *, strict: bool = False):
    """The BoG grid for this cycle, or an ``Unavailable`` saying what is missing."""
    from app.domain.icaap.pillar2 import table5 as table5_domain  # noqa: PLC0415
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415
    from app.services.icaap.resolvers import Unavailable  # noqa: PLC0415

    framework = guards.require_framework(cycle)
    items = _items(db, access, cycle)
    totals = _totals(framework, items)
    labels = {row.key: row.label for row in framework.table5_rows}
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    columns, reference = _appendix_columns(bound.get(pillar2_inputs.BLOCK_APPENDIX))
    if not columns:
        raise Unavailable(
            "Table 5 needs the Board-attested stress run's own Pillar 1 figures. Link "
            "the Appendix II block to this ICAAP, then try again."
        )
    try:
        grid = table5_domain.compose(columns, totals, labels, strict=strict)
    except table5_domain.Table5Error as exc:
        raise guards.conflict(
            exc.code,
            "One Table 5 row has a figure on the current basis but not on the stress "
            "basis, so the grid would show a stress requirement covering fewer risks "
            "than the current one.",
            **{key: str(value) for key, value in exc.context.items()},
        ) from exc
    return grid, reference


def get_table5(db: Session, access: IcaapAccess, cycle_id: UUID) -> Any:
    """The Table 5 read: the grid when it can be composed, the reason when not."""
    from app.schemas.icaap_risk_capital import (  # noqa: PLC0415 - response shape only
        IcaapTable5CellRead,
        IcaapTable5ColumnRead,
        IcaapTable5GridRowRead,
        IcaapTable5Read,
    )
    from app.services.icaap.resolvers import Unavailable  # noqa: PLC0415

    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    items = _items(db, access, cycle)
    findings = register_domain.like_for_like_findings(register_items(items))
    finding_reads = [
        IcaapPillar2FindingRead(code=finding.code, ref=finding.ref, params=dict(finding.params))
        for finding in findings
    ]
    currency = jurisdictions.base_currency(access.bank)
    try:
        grid, _reference = build_table5(db, access, cycle, strict=False)
    except Unavailable as exc:
        totals = _totals(framework, items)
        return IcaapTable5Read(
            cycle_id=cycle.id,
            unit="thousands",
            currency=currency,
            columns=[],
            rows=[],
            partial_rows=list(totals.partial_rows),
            notes=[],
            findings=finding_reads,
            available=False,
            unavailable_reason=exc.reason,
        )
    return IcaapTable5Read(
        cycle_id=cycle.id,
        unit="thousands",
        currency=currency,
        columns=[
            IcaapTable5ColumnRead(key=column.key, label=column.label, basis=column.basis)
            for column in grid.columns
        ],
        rows=[
            IcaapTable5GridRowRead(
                key=row.key,
                label=row.label,
                group=row.group,
                partial=row.partial,
                cells=[
                    IcaapTable5CellRead(column=column.key, value=row.cells.get(column.key))
                    for column in grid.columns
                ],
            )
            for row in grid.rows
        ],
        partial_rows=list(grid.partial_rows),
        register_total_baseline=grid.register_total_baseline,
        register_total_stressed=grid.register_total_stressed,
        notes=[
            "Pillar 1 lines are the Board-attested stress run's own figures.",
            "Pillar 2 lines are this ICAAP's register: current and base from the "
            "baseline figures, stress from the stressed ones.",
            "An empty cell is not modelled, not zero.",
            f"Amounts in thousands of {currency}.",
        ],
        findings=finding_reads,
        available=True,
        unavailable_reason=None,
    )
