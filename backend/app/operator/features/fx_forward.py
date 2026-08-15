"""/operator/v1/fx-forward — the FX-forward (outright) curve console API (STAFF, FC-6b).

Desk-side construction/preview of an outright FX-forward curve by covered interest
parity from two single-currency rate curves plus an FX spot. Read/preview only: it
writes no curve/determination state (publication + governance are out of this
surface's scope), only the staff audit row lands. Consistent with the sibling
curve-construction ``/curves/construct`` preview.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.schemas.fx_forward import (
    FxForwardConstructRequest,
    FxForwardConstructResponse,
    FxForwardRowRead,
    FxLeg,
)
from app.services.market_desk import fx_forward

router = APIRouter(prefix="/fx-forward", tags=["operator-fx-forward"])


def _leg_spec(leg: FxLeg) -> fx_forward.FxLegSpec:
    return fx_forward.FxLegSpec(
        source=leg.source,
        calendar_name=leg.calendar_name,
        spot_lag_days=leg.spot_lag_days,
        interpolation=leg.interpolation,
        extrapolation=leg.extrapolation,
        float_frequency_months=leg.float_frequency_months,
        quotes=tuple(
            fx_forward.FxLegQuoteSpec(
                instrument=quote.instrument,
                tenor=quote.tenor,
                quote=quote.quote,
                forward_start=quote.forward_start,
            )
            for quote in leg.quotes
        ),
        curve_code=leg.curve_code,
    )


@router.post("/construct", response_model=FxForwardConstructResponse)
def construct_fx_forward(
    payload: FxForwardConstructRequest, db: OperatorDb, operator: Operator
) -> FxForwardConstructResponse:
    """Run FX-forward construction — a preview that writes no curve state (only the
    staff audit row lands)."""
    try:
        if payload.tenor_grid:
            dates = fx_forward.resolve_grid_dates(
                payload.as_of,
                payload.tenor_grid,
                calendar_name=payload.grid_calendar or "",
                spot_lag_days=payload.grid_spot_lag_days,
            )
        else:
            dates = tuple(payload.dates)
        result = fx_forward.construct_fx_forward(
            db,
            as_of=payload.as_of,
            base_ccy=payload.base_ccy,
            quote_ccy=payload.quote_ccy,
            spot=payload.spot,
            dates=dates,
            base_leg=_leg_spec(payload.base_leg),
            quote_leg=_leg_spec(payload.quote_leg),
            basis_bps=payload.basis_bps,
            day_count=payload.day_count,
        )
    except fx_forward.FxForwardServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    record_operator_action(
        db,
        operator,
        action="desk.fx_forward.construct",
        detail={
            "pair": result.pair,
            "as_of": result.as_of.isoformat(),
            "base_source": result.base_source,
            "quote_source": result.quote_source,
            "basis_bps": result.basis_bps,
            "input_digest": result.input_digest,
        },
    )
    db.commit()
    return FxForwardConstructResponse(
        base_ccy=result.base_ccy,
        quote_ccy=result.quote_ccy,
        pair=result.pair,
        spot=result.spot,
        as_of=result.as_of,
        basis_bps=result.basis_bps,
        basis_calibrated=result.basis_calibrated,
        day_count=result.day_count.value,
        base_source=result.base_source,
        quote_source=result.quote_source,
        input_digest=result.input_digest,
        rows=[
            FxForwardRowRead(
                date=row.date,
                year_fraction=row.year_fraction,
                df_base=row.df_base,
                df_quote=row.df_quote,
                forward_rate=row.forward_rate,
                forward_points=row.forward_points,
            )
            for row in result.rows
        ],
    )
