"""The risk appetite statement: what the Board will tolerate, and where it stands now.

¶76 asks for appetite expressed in quantitative AND qualitative terms. Each
quantitative metric carries three levels in a fixed order — appetite, then
tolerance, then capacity — and a direction that says which way "worse" runs.
For a floor (a capital ratio) the numbers fall; for a ceiling (an NPL ratio)
they rise. Getting that backwards is the mistake this module exists to stop, so
the ordering is checked by the database, by the domain, and again at read time.

The regulatory reference is resolved at the cycle's as-of date from the governed
control plane, never from a literal. Two consequences follow, and both are
deliberate:

* a capacity WEAKER than the regulatory minimum is refused — a bank cannot set
  its own last line below the regulator's;
* when no floor is governed for a metric (the Ghana LCR directive is not
  public), the read says "not assessed against a regulatory floor" rather than
  inventing one (D-036). That is a readiness finding, not a silent pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import appetite as domain
from app.domain.policy import Direction
from app.models import ParamCapitalThreshold
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.models.icaap_risk_capital import IcaapAppetiteMetric
from app.schemas.icaap_risk_capital import (
    IcaapAppetiteEvaluationRead,
    IcaapAppetiteMetricCreate,
    IcaapAppetiteMetricDefRead,
    IcaapAppetiteMetricRead,
    IcaapAppetiteMetricUpdate,
    IcaapAppetiteRead,
    IcaapAppetiteSummaryRead,
    IcaapParameterUseRead,
    IcaapRegulatoryReferenceRead,
    IcaapRetire,
)
from app.services.audit import record_event
from app.services.icaap import blocks as blocks_service
from app.services.icaap import guards, params
from app.services.params import get_active_params

#: Governed codes an appetite metric may reference. A bank cannot bind its
#: appetite to an arbitrary control-plane row.
REFERENCE_CODES: frozenset[str] = domain.ALLOWED_REFERENCE_CODES


@dataclass(frozen=True)
class MetricView:
    """One metric with everything needed to evaluate and to explain it."""

    row: IcaapAppetiteMetric
    definition: domain.MetricDef | None
    current_value: Decimal | None
    reference: domain.RegulatoryReference | None
    reference_row: params.ParameterRow | None
    board_register_value: Decimal | None
    evaluation: domain.AppetiteEvaluation | None
    violations: tuple[str, ...]


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _rows(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, *, include_retired: bool = False
) -> list[IcaapAppetiteMetric]:
    statement = select(IcaapAppetiteMetric).where(
        IcaapAppetiteMetric.organization_id == access.ctx.organization_id,
        IcaapAppetiteMetric.cycle_id == cycle.id,
    )
    if not include_retired:
        statement = statement.where(IcaapAppetiteMetric.retired_at.is_(None))
    return list(db.scalars(statement.order_by(IcaapAppetiteMetric.metric_key.asc())))


def _board_register(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, Decimal]:
    """The institution's own capital minima, shown beside the regulatory ones.

    A board value stricter than the regulator's is a legitimate choice and is
    surfaced as information, never as a violation.
    """
    return {
        row.threshold_code: Decimal(str(row.value_pct))
        for row in get_active_params(
            db,
            access.ctx.organization_id,
            access.bank.jurisdiction_code,
            ParamCapitalThreshold,
            cycle.as_of_date,
        )
    }


def _reference(
    row: IcaapAppetiteMetric, resolved: params.P2Parameters
) -> tuple[domain.RegulatoryReference | None, params.ParameterRow | None]:
    code = row.regulatory_param_code
    if code is None or row.direction is None:
        return None, None
    parameter = resolved.optional(code)
    if parameter is None or parameter.value is None:
        return None, None
    return (
        domain.RegulatoryReference(
            param_code=code,
            value=parameter.value,
            direction=Direction(row.direction),
            confirmation_status=parameter.confirmation_status,
            representative=parameter.representative,
        ),
        parameter,
    )


def _current_value(row: IcaapAppetiteMetric, facts: Mapping[str, Any]) -> Decimal | None:
    if row.value_source == "manual":
        return row.manual_value
    if row.value_source == "block_fact" and row.source_block_type and row.source_fact_key:
        binding = facts.get(row.source_block_type)
        return _decimal(blocks_service.fact_value(binding, row.source_fact_key))
    return None


def _view(
    row: IcaapAppetiteMetric,
    resolved: params.P2Parameters,
    register: Mapping[str, Decimal],
    facts: Mapping[str, Any],
) -> MetricView:
    definition = domain.APPETITE_METRIC_CATALOGUE.get(row.metric_key)
    reference, reference_row = _reference(row, resolved)
    board_value = (
        register.get(row.board_register_code) if row.board_register_code is not None else None
    )
    if row.measure_kind == "qualitative":
        return MetricView(
            row=row,
            definition=definition,
            current_value=None,
            reference=reference,
            reference_row=reference_row,
            board_register_value=board_value,
            evaluation=None,
            violations=(),
        )
    thresholds = domain.Thresholds(
        appetite=row.appetite_value or Decimal(0),
        tolerance=row.tolerance_value or Decimal(0),
        capacity=row.capacity_value or Decimal(0),
    )
    direction = Direction(row.direction or Direction.FLOOR.value)
    value = _current_value(row, facts)
    evaluation = domain.evaluate(direction, thresholds, value, reference, row.prior_value)
    violations = domain.validate_ordering(direction, thresholds, reference)
    return MetricView(
        row=row,
        definition=definition,
        current_value=value,
        reference=reference,
        reference_row=reference_row,
        board_register_value=board_value,
        evaluation=evaluation,
        violations=tuple(violation.value for violation in violations),
    )


def metric_views(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, *, include_retired: bool = False
) -> tuple[MetricView, ...]:
    """Every live metric with its evaluation, re-resolved at read time.

    Re-evaluating on every read is the point: a governed value staff lowered
    yesterday must change today's verdict without anybody re-saving the row.
    """
    resolved = params.resolve_p2(
        db, access.bank, as_of=cycle.as_of_date, codes=sorted(REFERENCE_CODES)
    )
    register = _board_register(db, access, cycle)
    facts = blocks_service.current_bindings_by_type(db, access, cycle)
    return tuple(
        _view(row, resolved, register, facts)
        for row in _rows(db, access, cycle, include_retired=include_retired)
    )


def _metric_read(view: MetricView) -> IcaapAppetiteMetricRead:
    row = view.row
    evaluation = view.evaluation
    board_stricter = False
    if view.board_register_value is not None and row.capacity_value is not None:
        board_stricter = not domain.at_least_as_strict(
            Direction(row.direction or Direction.FLOOR.value),
            row.capacity_value,
            view.board_register_value,
        )
    return IcaapAppetiteMetricRead(
        id=row.id,
        metric_key=row.metric_key,
        risk_key=row.risk_key,
        label=row.label,
        is_custom=row.is_custom,
        measure_kind=row.measure_kind,  # pyright: ignore[reportArgumentType]
        unit=row.unit,  # pyright: ignore[reportArgumentType]
        direction=row.direction,  # pyright: ignore[reportArgumentType]
        appetite_value=row.appetite_value,
        tolerance_value=row.tolerance_value,
        capacity_value=row.capacity_value,
        regulatory_param_code=row.regulatory_param_code,
        board_register_code=row.board_register_code,
        value_source=row.value_source,  # pyright: ignore[reportArgumentType]
        source_block_type=row.source_block_type,
        source_fact_key=row.source_fact_key,
        manual_value=row.manual_value,
        manual_evidence_attachment_id=row.manual_evidence_attachment_id,
        prior_value=row.prior_value,
        prior_value_label=row.prior_value_label,
        qualitative_statement=row.qualitative_statement,
        board_approval_reference=row.board_approval_reference,
        board_approved_on=row.board_approved_on,
        row_rev=row.row_rev,
        evaluation=(
            None
            if evaluation is None
            else IcaapAppetiteEvaluationRead(
                status=evaluation.status.value,
                rag=evaluation.rag,
                current_value=view.current_value,
                utilisation_pct=evaluation.utilisation_pct,
                headroom_to_appetite=evaluation.headroom_to_appetite,
                headroom_to_tolerance=evaluation.headroom_to_tolerance,
                headroom_to_capacity=evaluation.headroom_to_capacity,
                headroom_to_regulatory=evaluation.headroom_to_regulatory,
                trend=evaluation.trend,
                regulatory_reference_absent=evaluation.regulatory_reference_absent,
            )
        ),
        regulatory_reference=(
            None
            if view.reference is None or view.reference_row is None
            else IcaapRegulatoryReferenceRead(
                param_code=view.reference.param_code,
                value=view.reference.value,
                direction=view.reference.direction.value,  # pyright: ignore[reportArgumentType]
                confirmation_status=view.reference.confirmation_status,
                representative=view.reference.representative,
                source_citation=view.reference_row.source_citation,
                effective_from=view.reference_row.effective_from,
            )
        ),
        reference_missing=(row.regulatory_param_code is not None and view.reference is None),
        board_register_value=view.board_register_value,
        board_register_stricter=board_stricter,
        violations=list(view.violations),
        updated_at=row.updated_at,
    )


def get_appetite(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapAppetiteRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    views = metric_views(db, access, cycle)
    resolved = params.resolve_p2(
        db, access.bank, as_of=cycle.as_of_date, codes=sorted(REFERENCE_CODES)
    )
    reads = [_metric_read(view) for view in views]
    breaches = [
        read for read in reads if read.evaluation is not None and read.evaluation.rag == "red"
    ]
    amber = [
        read for read in reads if read.evaluation is not None and read.evaluation.rag == "amber"
    ]
    return IcaapAppetiteRead(
        cycle_id=cycle.id,
        catalogue=[
            IcaapAppetiteMetricDefRead(
                key=definition.key,
                label=definition.label,
                unit=definition.unit,
                direction=definition.direction.value,  # pyright: ignore[reportArgumentType]
                default_risk_key=definition.default_risk_key,
                source_block_type=definition.source_block_type,
                source_fact_key=definition.source_fact_key,
                regulatory_param_code=definition.regulatory_param_code,
                board_register_code=definition.board_register_code,
            )
            for definition in domain.APPETITE_METRIC_CATALOGUE.values()
        ],
        metrics=reads,
        summary=IcaapAppetiteSummaryRead(
            metric_count=len(reads),
            breach_count=len(breaches),
            amber_count=len(amber),
            qualitative_count=len([read for read in reads if read.measure_kind == "qualitative"]),
        ),
        parameters=[IcaapParameterUseRead(**use) for use in resolved.uses(REFERENCE_CODES)],
    )


def _validate_quantitative(  # noqa: PLR0913 - every level and its reference is checked
    *,
    direction: str | None,
    appetite_value: Decimal | None,
    tolerance_value: Decimal | None,
    capacity_value: Decimal | None,
    unit: str | None,
    reference: domain.RegulatoryReference | None,
) -> None:
    if direction is None or unit is None:
        raise guards.unprocessable(
            "appetite_ordering_invalid",
            "A measured appetite metric needs a unit and a direction.",
            violations=["incomplete"],
        )
    if appetite_value is None or tolerance_value is None or capacity_value is None:
        raise guards.unprocessable(
            "appetite_ordering_invalid",
            "A measured appetite metric needs an appetite, a tolerance and a capacity.",
            violations=["incomplete"],
        )
    violations = domain.validate_ordering(
        Direction(direction),
        domain.Thresholds(
            appetite=appetite_value, tolerance=tolerance_value, capacity=capacity_value
        ),
        reference,
    )
    if violations:
        raise guards.unprocessable(
            "appetite_ordering_invalid",
            "These levels are not in a workable order for this metric.",
            violations=[violation.value for violation in violations],
            reference=None if reference is None else reference.param_code,
        )


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


def create_metric(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapAppetiteMetricCreate
) -> IcaapAppetiteMetricRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    definition = domain.APPETITE_METRIC_CATALOGUE.get(payload.metric_key)
    is_custom = definition is None
    unit = payload.unit or (None if definition is None else _unit_of(definition))
    direction = payload.direction or (None if definition is None else definition.direction.value)
    resolved = params.resolve_p2(
        db, access.bank, as_of=cycle.as_of_date, codes=sorted(REFERENCE_CODES)
    )
    reference_code = None if definition is None else definition.regulatory_param_code
    reference = _reference_for(reference_code, direction, resolved)
    _check_evidence(db, access, cycle, payload.manual_evidence_attachment_id)
    if payload.measure_kind == "quantitative":
        _validate_quantitative(
            direction=direction,
            appetite_value=payload.appetite_value,
            tolerance_value=payload.tolerance_value,
            capacity_value=payload.capacity_value,
            unit=unit,
            reference=reference,
        )
        if payload.value_source is None:
            raise guards.unprocessable(
                "appetite_ordering_invalid",
                "Say where the current figure comes from.",
                violations=["value_source_missing"],
            )
        if payload.value_source == "manual" and payload.manual_value is None:
            raise guards.unprocessable(
                "appetite_ordering_invalid",
                "A manual metric needs a figure.",
                violations=["manual_value_missing"],
            )
    row = IcaapAppetiteMetric(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        metric_key=payload.metric_key,
        risk_key=payload.risk_key or (None if definition is None else definition.default_risk_key),
        label=payload.label,
        is_custom=is_custom,
        measure_kind=payload.measure_kind,
        unit=unit if payload.measure_kind == "quantitative" else None,
        direction=direction if payload.measure_kind == "quantitative" else None,
        appetite_value=(payload.appetite_value if payload.measure_kind == "quantitative" else None),
        tolerance_value=(
            payload.tolerance_value if payload.measure_kind == "quantitative" else None
        ),
        capacity_value=(payload.capacity_value if payload.measure_kind == "quantitative" else None),
        regulatory_param_code=reference_code,
        board_register_code=None if definition is None else definition.board_register_code,
        value_source=payload.value_source if payload.measure_kind == "quantitative" else None,
        source_block_type=None if definition is None else definition.source_block_type,
        source_fact_key=None if definition is None else definition.source_fact_key,
        manual_value=payload.manual_value,
        manual_evidence_attachment_id=payload.manual_evidence_attachment_id,
        prior_value=payload.prior_value,
        prior_value_label=payload.prior_value_label,
        qualitative_statement=payload.qualitative_statement,
        board_approval_reference=payload.board_approval_reference,
        board_approved_on=payload.board_approved_on,
        row_rev=0,
        created_by=guards.actor_id(access),
        updated_by=guards.actor_id(access),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "metric_exists", "That appetite metric is already in this ICAAP."
        ) from exc
    record_event(
        db,
        access.ctx,
        event_type="icaap.appetite.metric_created",
        entity_type="icaap_appetite_metric",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "metric_key": row.metric_key,
            "measure_kind": row.measure_kind,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read_one(db, access, cycle, row)


def _unit_of(definition: domain.MetricDef) -> str:
    return "percent" if definition.unit == domain.UNIT_PCT else "ratio"


def _reference_for(
    code: str | None, direction: str | None, resolved: params.P2Parameters
) -> domain.RegulatoryReference | None:
    if code is None or direction is None:
        return None
    parameter = resolved.optional(code)
    if parameter is None or parameter.value is None:
        return None
    return domain.RegulatoryReference(
        param_code=code,
        value=parameter.value,
        direction=Direction(direction),
        confirmation_status=parameter.confirmation_status,
        representative=parameter.representative,
    )


def _read_one(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, row: IcaapAppetiteMetric
) -> IcaapAppetiteMetricRead:
    resolved = params.resolve_p2(
        db, access.bank, as_of=cycle.as_of_date, codes=sorted(REFERENCE_CODES)
    )
    register = _board_register(db, access, cycle)
    facts = blocks_service.current_bindings_by_type(db, access, cycle)
    return _metric_read(_view(row, resolved, register, facts))


def _metric_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, metric_id: UUID
) -> IcaapAppetiteMetric:
    row = db.scalar(
        select(IcaapAppetiteMetric).where(
            IcaapAppetiteMetric.id == metric_id,
            IcaapAppetiteMetric.organization_id == access.ctx.organization_id,
            IcaapAppetiteMetric.cycle_id == cycle.id,
        )
    )
    if row is None:
        guards.not_found()
    return row


def update_metric(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    metric_id: UUID,
    payload: IcaapAppetiteMetricUpdate,
) -> IcaapAppetiteMetricRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _metric_or_404(db, access, cycle, metric_id)
    if row.retired_at is not None:
        raise guards.conflict("metric_retired", "This appetite metric has been retired.")
    if payload.base_rev != row.row_rev:
        raise guards.conflict(
            "row_rev_conflict",
            "Somebody else changed this metric while you were editing it.",
            current_rev=row.row_rev,
        )
    _check_evidence(db, access, cycle, payload.manual_evidence_attachment_id)
    for field in (
        "label",
        "qualitative_statement",
        "risk_key",
        "unit",
        "direction",
        "appetite_value",
        "tolerance_value",
        "capacity_value",
        "value_source",
        "manual_value",
        "manual_evidence_attachment_id",
        "prior_value",
        "prior_value_label",
        "board_approval_reference",
        "board_approved_on",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if row.measure_kind == "quantitative":
        resolved = params.resolve_p2(
            db, access.bank, as_of=cycle.as_of_date, codes=sorted(REFERENCE_CODES)
        )
        _validate_quantitative(
            direction=row.direction,
            appetite_value=row.appetite_value,
            tolerance_value=row.tolerance_value,
            capacity_value=row.capacity_value,
            unit=row.unit,
            reference=_reference_for(row.regulatory_param_code, row.direction, resolved),
        )
    row.row_rev += 1
    row.updated_by = guards.actor_id(access)
    record_event(
        db,
        access.ctx,
        event_type="icaap.appetite.metric_updated",
        entity_type="icaap_appetite_metric",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "metric_key": row.metric_key,
            "row_rev": row.row_rev,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read_one(db, access, cycle, row)


def retire_metric(
    db: Session, access: IcaapAccess, cycle_id: UUID, metric_id: UUID, payload: IcaapRetire
) -> IcaapAppetiteMetricRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _metric_or_404(db, access, cycle, metric_id)
    row.retired_at = utc_now()
    row.retired_by = guards.actor_id(access)
    row.retire_reason = payload.reason
    row.row_rev += 1
    row.updated_by = guards.actor_id(access)
    record_event(
        db,
        access.ctx,
        event_type="icaap.appetite.metric_retired",
        entity_type="icaap_appetite_metric",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "metric_key": row.metric_key,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read_one(db, access, cycle, row)


def appetite_payload(views: Sequence[MetricView]) -> dict[str, Any]:
    """The appetite table as a value-based body — what the block digests."""
    return {
        "metrics": [
            {
                "metric_key": view.row.metric_key,
                "risk_key": view.row.risk_key,
                "label": view.row.label,
                "measure_kind": view.row.measure_kind,
                "direction": view.row.direction,
                "appetite": _text(view.row.appetite_value),
                "tolerance": _text(view.row.tolerance_value),
                "capacity": _text(view.row.capacity_value),
                "current": _text(view.current_value),
                "status": None if view.evaluation is None else view.evaluation.status.value,
                "rag": None if view.evaluation is None else view.evaluation.rag,
                "reference": (
                    None
                    if view.reference is None
                    else {
                        "param_code": view.reference.param_code,
                        "value": _text(view.reference.value),
                        "confirmation_status": view.reference.confirmation_status,
                        "representative": view.reference.representative,
                    }
                ),
            }
            for view in views
        ]
    }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "REFERENCE_CODES",
    "MetricView",
    "appetite_payload",
    "create_metric",
    "get_appetite",
    "metric_views",
    "retire_metric",
    "update_metric",
]
