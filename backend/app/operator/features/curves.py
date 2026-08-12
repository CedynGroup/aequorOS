"""/operator/v1/curves — the forward-curve construction console API (STAFF surface, FC-3).

The desk's governed curve DEFINITIONS (the Eikon "Curve 1/2/3" analogue) plus the
"Run construction" preview and publish-to-golden-copy action. Definitions are
Track-2 governed exactly like the methodology register (dual control, immutable
after approval); construction applies an APPROVED definition to a cob's quotes and
is a pure PREVIEW (writes no curve/determination state); publish fans the
constructed curve out through the existing determination seam.

Every mutation is recorded through ``record_operator_action`` — the desk's audit
trail is part of the control story the spec's governance sections commit to.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, status

from app.models import DeskCurveDefinition
from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.schemas.market_desk import DeskPublicationRead
from app.schemas.market_desk_curves import (
    DeskCurveConstructRequest,
    DeskCurveConstructResponse,
    DeskCurveDefinitionApprove,
    DeskCurveDefinitionCreate,
    DeskCurveDefinitionListRead,
    DeskCurveDefinitionRead,
    DeskCurveDefinitionVersionPropose,
    DeskCurveGridRow,
    DeskCurvePillarView,
    DeskCurvePublishRequest,
    DeskCurveQaView,
)
from app.services.market_desk import curve_construction

router = APIRouter(prefix="/curves", tags=["operator-curves"])


# -- read-model builders ------------------------------------------------------------


def _definition_read(row: DeskCurveDefinition) -> DeskCurveDefinitionRead:
    return DeskCurveDefinitionRead(
        id=row.id,
        curve_code=row.curve_code,
        version=row.version,
        status=row.status,
        currency=row.currency,
        calendar_name=row.calendar_name,
        curve_kind=row.curve_kind,
        projection_index=row.projection_index,
        discount_curve_code=row.discount_curve_code,
        instrument_set_ref=row.instrument_set_ref,
        interpolation_method=row.interpolation_method,
        output_daycount=row.output_daycount,
        payment_frequency=row.payment_frequency,
        payment_interval_months=row.payment_interval_months,
        curve_frequency=row.curve_frequency,
        spot_lag_days=row.spot_lag_days,
        roll_convention=row.roll_convention,
        extrapolation_rule=row.extrapolation_rule,
        params=row.params,
        change_rationale=row.change_rationale,
        proposed_by=row.proposed_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        effective_from=row.effective_from,
        created_at=row.created_at,
    )


def _spec_from_create(payload: DeskCurveDefinitionCreate) -> curve_construction.CurveDefinitionSpec:
    return curve_construction.CurveDefinitionSpec(
        curve_code=payload.curve_code,
        currency=payload.currency,
        calendar_name=payload.calendar_name,
        curve_kind=payload.curve_kind,
        projection_index=payload.projection_index,
        discount_curve_code=payload.discount_curve_code,
        instrument_set_ref=payload.instrument_set_ref,
        interpolation_method=payload.interpolation_method,
        output_daycount=payload.output_daycount,
        payment_frequency=payload.payment_frequency,
        payment_interval_months=payload.payment_interval_months,
        curve_frequency=payload.curve_frequency,
        spot_lag_days=payload.spot_lag_days,
        roll_convention=payload.roll_convention,
        extrapolation_rule=payload.extrapolation_rule,
        params=payload.params,
    )


def _spec_from_propose(
    curve_code: str, payload: DeskCurveDefinitionVersionPropose
) -> curve_construction.CurveDefinitionSpec:
    return curve_construction.CurveDefinitionSpec(
        curve_code=curve_code,
        currency=payload.currency,
        calendar_name=payload.calendar_name,
        curve_kind=payload.curve_kind,
        projection_index=payload.projection_index,
        discount_curve_code=payload.discount_curve_code,
        instrument_set_ref=payload.instrument_set_ref,
        interpolation_method=payload.interpolation_method,
        output_daycount=payload.output_daycount,
        payment_frequency=payload.payment_frequency,
        payment_interval_months=payload.payment_interval_months,
        curve_frequency=payload.curve_frequency,
        spot_lag_days=payload.spot_lag_days,
        roll_convention=payload.roll_convention,
        extrapolation_rule=payload.extrapolation_rule,
        params=payload.params,
    )


def _construct_response(
    result: curve_construction.CurveConstructionResult,
) -> DeskCurveConstructResponse:
    qa = result.qa
    forward = qa.forward_qa
    return DeskCurveConstructResponse(
        curve_code=result.curve_code,
        definition_version=result.definition_version,
        as_of=result.as_of,
        output_basis=result.output_basis.value,
        curve_frequency_months=result.grid.curve_frequency_months,
        input_digest=result.input_digest,
        rows=[
            DeskCurveGridRow(
                start=row.start,
                end=row.end,
                discount_factor=row.discount_factor,
                forward_yield=row.yield_,
            )
            for row in result.grid.rows
        ],
        pillars=[
            DeskCurvePillarView(
                instrument=pillar.instrument,
                tenor=pillar.tenor,
                leg=pillar.leg,
                pillar_date=pillar.pillar_date,
                quote=pillar.quote,
                discount_factor=pillar.discount_factor,
                reprice_residual=pillar.reprice_residual,
            )
            for pillar in result.pillars
        ],
        qa=DeskCurveQaView(
            passed=qa.passed,
            reprice_max_residual=qa.reprice_max_residual,
            reprice_tolerance=qa.reprice_tolerance,
            reprice_pass=qa.reprice_pass,
            pillar_count=qa.pillar_count,
            pillar_min_count=qa.pillar_min_count,
            pillar_coverage_pass=qa.pillar_coverage_pass,
            monotone_df_pass=qa.monotone_df_pass,
            forward_positivity_pass=forward.positivity_pass,
            forward_oscillation_pass=forward.oscillation_pass,
            forward_min=forward.min_forward,
            forward_total_variation_ratio=forward.total_variation_ratio,
        ),
    )


def _active_definition(db: OperatorDb, curve_code: str, as_of: date) -> DeskCurveDefinition:
    definition = curve_construction.get_active_definition(db, curve_code, as_of)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No approved curve definition of {curve_code!r} is effective on "
                f"{as_of.isoformat()}; approve one before constructing."
            ),
        )
    return definition


# -- definitions (Track 2) ----------------------------------------------------------


@router.get("/definitions", response_model=DeskCurveDefinitionListRead)
def list_curve_definitions(
    db: OperatorDb,
    _operator: Operator,
    curve_code: str | None = None,
) -> DeskCurveDefinitionListRead:
    rows = curve_construction.list_definitions(db, curve_code=curve_code)
    return DeskCurveDefinitionListRead(
        definitions=[_definition_read(row) for row in rows], total=len(rows)
    )


@router.post("/definitions", response_model=DeskCurveDefinitionRead, status_code=201)
def create_curve_definition(
    payload: DeskCurveDefinitionCreate, db: OperatorDb, operator: Operator
) -> DeskCurveDefinitionRead:
    try:
        row = curve_construction.create_definition(
            db,
            spec=_spec_from_create(payload),
            change_rationale=payload.change_rationale,
            proposed_by=operator.email,
        )
    except curve_construction.CurveConstructionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.curve.create_definition",
        detail={"curve_code": row.curve_code, "version": row.version},
    )
    db.commit()
    return _definition_read(row)


@router.post(
    "/definitions/{curve_code}/versions",
    response_model=DeskCurveDefinitionRead,
    status_code=201,
)
def propose_curve_definition_version(
    curve_code: str,
    payload: DeskCurveDefinitionVersionPropose,
    db: OperatorDb,
    operator: Operator,
) -> DeskCurveDefinitionRead:
    try:
        row = curve_construction.propose_definition_version(
            db,
            spec=_spec_from_propose(curve_code, payload),
            change_rationale=payload.change_rationale,
            proposed_by=operator.email,
        )
    except curve_construction.CurveConstructionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.curve.propose_version",
        detail={"curve_code": curve_code, "version": row.version},
    )
    db.commit()
    return _definition_read(row)


@router.post(
    "/definitions/{curve_code}/versions/{version}/approve",
    response_model=DeskCurveDefinitionRead,
)
def approve_curve_definition_version(
    curve_code: str,
    version: int,
    payload: DeskCurveDefinitionApprove,
    db: OperatorDb,
    operator: Operator,
) -> DeskCurveDefinitionRead:
    row = curve_construction.approve_definition_version(
        db,
        curve_code=curve_code,
        version=version,
        approved_by=operator.email,
        effective_from=payload.effective_from,
    )
    record_operator_action(
        db,
        operator,
        action="desk.curve.approve_version",
        detail={
            "curve_code": curve_code,
            "version": version,
            "effective_from": payload.effective_from.isoformat(),
        },
    )
    db.commit()
    return _definition_read(row)


# -- construction preview + publish -------------------------------------------------


@router.post("/construct", response_model=DeskCurveConstructResponse)
def construct_curve(
    payload: DeskCurveConstructRequest, db: OperatorDb, operator: Operator
) -> DeskCurveConstructResponse:
    """Run construction against the ACTIVE definition — a preview that writes no
    curve/determination state (only the staff audit row lands)."""
    definition = _active_definition(db, payload.curve_code, payload.as_of)
    calendar = curve_construction.resolve_calendar(definition.calendar_name)
    try:
        result = curve_construction.build_forward_curve(
            definition,
            payload.as_of,
            [quote.model_dump() for quote in payload.quotes],
            calendar=calendar,
        )
    except curve_construction.CurveConstructionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.curve.construct",
        detail={
            "curve_code": result.curve_code,
            "definition_version": result.definition_version,
            "as_of": result.as_of.isoformat(),
            "input_digest": result.input_digest,
            "qa_passed": result.qa.passed,
        },
    )
    db.commit()
    return _construct_response(result)


@router.post("/publish", response_model=DeskPublicationRead)
def publish_curve(
    payload: DeskCurvePublishRequest, db: OperatorDb, operator: Operator
) -> DeskPublicationRead:
    """Construct then publish a forward curve to golden copy through the desk
    determination fan-out (bitemporal + lineage + audit for free)."""
    definition = _active_definition(db, payload.curve_code, payload.as_of)
    calendar = curve_construction.resolve_calendar(definition.calendar_name)
    kwargs = (
        {}
        if payload.methodology_code is None
        else {"methodology_code": payload.methodology_code}
    )
    try:
        row = curve_construction.publish_forward_curve(
            db,
            definition=definition,
            as_of=payload.as_of,
            quotes=[quote.model_dump() for quote in payload.quotes],
            calendar=calendar,
            actor=operator.email,
            **kwargs,
        )
    except curve_construction.CurveConstructionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.curve.publish",
        detail={
            "curve_code": payload.curve_code,
            "as_of": payload.as_of.isoformat(),
            "publication_id": str(row.id),
            "status": row.status,
            "banks": len(row.results),
        },
    )
    db.commit()
    return DeskPublicationRead(
        id=row.id,
        determination_id=row.determination_id,
        published_by=row.published_by,
        published_at=row.published_at,
        status=row.status,
        results=row.results,
    )
