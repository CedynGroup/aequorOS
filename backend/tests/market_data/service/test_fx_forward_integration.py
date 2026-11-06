"""FC-6b FX-forward construction service: CIP composition, grid, published legs.

Covers the two leg-resolution paths the service supports:

* ``quotes`` — bootstrap each currency's discount curve from pillar quotes and
  compose by covered interest parity (the exact path);
* ``published`` — reconstruct a discount curve from a published desk curve's stored
  forward grid (read layer only) and compose the same way.

Plus tenor-grid resolution and the error contract. The CIP maths itself is pinned
in ``tests/market_data/domain/curves/test_fx_forward.py``; here the focus is orchestration.

This module's other-suite cases live in ``test_fx_forward.py``.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.market_data.domain.curves.calendars import GHANA
from app.market_data.domain.curves.multicurve import SolvedCurve, forward_grid
from app.market_data.service.market_desk import fx_forward as fx
from app.models import DeskDetermination
from tests.market_data.service.test_fx_forward import AS_OF, DATES


def _publish_curve(db: Session, code: str, zero: float) -> tuple[date, ...]:
    """Insert a published desk determination whose forward grid encodes a flat curve.

    Returns the grid boundary END dates (rows[1:]) so a test can align FX dates.
    """
    nodes = (date(2026, 11, 9), date(2027, 8, 9), date(2029, 8, 7), date(2036, 8, 7))
    curve = SolvedCurve(AS_OF, nodes, tuple(zero for _ in nodes))
    grid = forward_grid(curve, as_of=AS_OF, curve_frequency_months=3, calendar=GHANA, periods=6)
    rows = [
        {
            "start": row.start.isoformat(),
            "end": row.end.isoformat(),
            "discount_factor": format(row.discount_factor, ".12f"),
            "forward_yield": format(row.yield_, ".12f"),
        }
        for row in grid.rows
    ]
    determination = DeskDetermination(
        cob_date=AS_OF,
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        input_snapshot=[],
        input_digest="0" * 64,
        derived_values={
            "curves": {code: {"curve_type": "forward", "points": []}},
            "forward_grids": {code: {"rows": rows}},
            "curves_qa_passed": True,
        },
        qa_results={},
        status="published",
        prepared_by="analyst@aequoros.com",
        reviewed_by="lead@aequoros.com",
    )
    db.add(determination)
    db.flush()
    return tuple(date.fromisoformat(row["end"]) for row in rows[1:])


def test_published_legs_compose_by_cip(db_session: Session) -> None:
    """Two published curve codes read back and composed into an FX-forward grid."""
    _publish_curve(db_session, "AEQ.USD.OIS", 0.05)
    quote_ends = _publish_curve(db_session, "AEQ.GHS.OIS", 0.22)
    db_session.commit()

    spot = 12.5
    fx_dates = quote_ends[:4]
    result = fx.construct_fx_forward(
        db_session,
        as_of=AS_OF,
        base_ccy="USD",
        quote_ccy="GHS",
        spot=spot,
        dates=fx_dates,
        base_leg=fx.FxLegSpec(source="published", curve_code="AEQ.USD.OIS"),
        quote_leg=fx.FxLegSpec(source="published", curve_code="AEQ.GHS.OIS"),
    )
    assert len(result.rows) == len(fx_dates)
    assert result.base_source == "AEQ.USD.OIS"
    assert result.quote_source == "AEQ.GHS.OIS"
    # Quote currency out-yields base => base at a forward premium in quote terms.
    assert all(row.forward_rate > spot for row in result.rows)
    assert all(0.0 < row.df_base <= 1.0 for row in result.rows)
    assert len(result.input_digest) == 64


def test_missing_published_curve_is_rejected(db_session: Session) -> None:
    _publish_curve(db_session, "AEQ.USD.OIS", 0.05)
    db_session.commit()
    with pytest.raises(fx.FxForwardServiceError):
        fx.construct_fx_forward(
            db_session,
            as_of=AS_OF,
            base_ccy="USD",
            quote_ccy="GHS",
            spot=12.5,
            dates=DATES,
            base_leg=fx.FxLegSpec(source="published", curve_code="AEQ.USD.OIS"),
            quote_leg=fx.FxLegSpec(source="published", curve_code="AEQ.GHS.NOPE"),
        )
