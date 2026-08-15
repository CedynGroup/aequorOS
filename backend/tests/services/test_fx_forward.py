"""FC-6b FX-forward construction service: CIP composition, grid, published legs.

Covers the two leg-resolution paths the service supports:

* ``quotes`` — bootstrap each currency's discount curve from pillar quotes and
  compose by covered interest parity (the exact path);
* ``published`` — reconstruct a discount curve from a published desk curve's stored
  forward grid (read layer only) and compose the same way.

Plus tenor-grid resolution and the error contract. The CIP maths itself is pinned
in ``tests/domain/curves/test_fx_forward.py``; here the focus is orchestration.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain.curves.calendars import GHANA
from app.domain.curves.multicurve import SolvedCurve, forward_grid
from app.models import DeskDetermination
from app.services.market_desk import fx_forward as fx

AS_OF = date(2026, 8, 7)
DATES = (date(2027, 2, 8), date(2027, 8, 9), date(2028, 8, 7))


def _leg_quotes(*, high: bool) -> fx.FxLegSpec:
    """A self-discounting leg: short deposits + OIS. ``high`` scales the whole curve."""
    bump = 0.15 if high else 0.0
    return fx.FxLegSpec(
        source="quotes",
        calendar_name="GHANA",
        quotes=(
            fx.FxLegQuoteSpec("deposit", "3M", 0.05 + bump),
            fx.FxLegQuoteSpec("deposit", "6M", 0.051 + bump),
            fx.FxLegQuoteSpec("ois", "1Y", 0.052 + bump),
            fx.FxLegQuoteSpec("ois", "2Y", 0.053 + bump),
            fx.FxLegQuoteSpec("ois", "3Y", 0.054 + bump),
        ),
    )


# ---------------------------------------------------------------------------
# Quotes path
# ---------------------------------------------------------------------------


def test_quotes_path_identity_when_legs_match() -> None:
    """Two identical legs => the forward equals spot at every date (CIP identity)."""
    spot = 12.5
    result = fx.construct_fx_forward(
        None,
        as_of=AS_OF,
        base_ccy="USD",
        quote_ccy="GHS",
        spot=spot,
        dates=DATES,
        base_leg=_leg_quotes(high=False),
        quote_leg=_leg_quotes(high=False),
    )
    assert len(result.rows) == len(DATES)
    assert result.pair == "USDGHS"
    assert result.base_source == "quotes"
    assert result.basis_calibrated is False
    for row in result.rows:
        assert row.forward_rate == pytest.approx(spot, rel=1e-9)
        assert row.forward_points == pytest.approx(0.0, abs=1e-6)


def test_quotes_path_higher_quote_yield_gives_forward_premium() -> None:
    """Quote currency out-yields base => base at a forward premium (F > S), growing."""
    spot = 12.5
    result = fx.construct_fx_forward(
        None,
        as_of=AS_OF,
        base_ccy="USD",
        quote_ccy="GHS",
        spot=spot,
        dates=DATES,
        base_leg=_leg_quotes(high=False),
        quote_leg=_leg_quotes(high=True),
    )
    assert all(row.forward_rate > spot for row in result.rows)
    points = [row.forward_points for row in result.rows]
    assert all(later > earlier for earlier, later in zip(points, points[1:], strict=False))
    assert len(result.input_digest) == 64


def test_quotes_path_digest_is_reproducible() -> None:
    kwargs = {
        "as_of": AS_OF,
        "base_ccy": "USD",
        "quote_ccy": "GHS",
        "spot": 12.5,
        "dates": DATES,
        "base_leg": _leg_quotes(high=False),
        "quote_leg": _leg_quotes(high=True),
    }
    a = fx.construct_fx_forward(None, **kwargs)  # type: ignore[arg-type]
    b = fx.construct_fx_forward(None, **kwargs)  # type: ignore[arg-type]
    assert a.input_digest == b.input_digest
    c = fx.construct_fx_forward(None, **{**kwargs, "basis_bps": 40.0})  # type: ignore[arg-type]
    assert c.input_digest != a.input_digest


# ---------------------------------------------------------------------------
# Tenor-grid resolution
# ---------------------------------------------------------------------------


def test_resolve_grid_dates_is_ascending_and_calendar_adjusted() -> None:
    dates = fx.resolve_grid_dates(
        AS_OF, ["3M", "6M", "1Y", "2Y"], calendar_name="GHANA", spot_lag_days=2
    )
    assert len(dates) == 4
    assert all(later > earlier for earlier, later in zip(dates, dates[1:], strict=False))
    # Every resolved date is a Ghana business day (Modified-Following adjusted).
    assert all(GHANA.is_business_day(day) for day in dates)


def test_resolve_grid_dates_rejects_unknown_calendar() -> None:
    with pytest.raises(fx.FxForwardServiceError):
        fx.resolve_grid_dates(AS_OF, ["3M"], calendar_name="ATLANTIS")


# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------


def test_rejects_same_currency() -> None:
    with pytest.raises(fx.FxForwardServiceError):
        fx.construct_fx_forward(
            None,
            as_of=AS_OF,
            base_ccy="USD",
            quote_ccy="USD",
            spot=1.0,
            dates=DATES,
            base_leg=_leg_quotes(high=False),
            quote_leg=_leg_quotes(high=False),
        )


def test_published_leg_without_db_is_rejected() -> None:
    with pytest.raises(fx.FxForwardServiceError):
        fx.construct_fx_forward(
            None,
            as_of=AS_OF,
            base_ccy="USD",
            quote_ccy="GHS",
            spot=12.5,
            dates=DATES,
            base_leg=fx.FxLegSpec(source="published", curve_code="AEQ.USD.OIS"),
            quote_leg=_leg_quotes(high=True),
        )


# ---------------------------------------------------------------------------
# Published path (read layer only)
# ---------------------------------------------------------------------------


def _publish_curve(db: Session, code: str, zero: float) -> tuple[date, ...]:
    """Insert a published desk determination whose forward grid encodes a flat curve.

    Returns the grid boundary END dates (rows[1:]) so a test can align FX dates.
    """
    nodes = (date(2026, 11, 9), date(2027, 8, 9), date(2029, 8, 7), date(2036, 8, 7))
    curve = SolvedCurve(AS_OF, nodes, tuple(zero for _ in nodes))
    grid = forward_grid(
        curve, as_of=AS_OF, curve_frequency_months=3, calendar=GHANA, periods=6
    )
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
