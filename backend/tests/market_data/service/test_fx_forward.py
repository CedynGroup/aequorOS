"""FC-6b FX-forward construction service: CIP composition, grid, published legs.

Covers the two leg-resolution paths the service supports:

* ``quotes`` — bootstrap each currency's discount curve from pillar quotes and
  compose by covered interest parity (the exact path);
* ``published`` — reconstruct a discount curve from a published desk curve's stored
  forward grid (read layer only) and compose the same way.

Plus tenor-grid resolution and the error contract. The CIP maths itself is pinned
in ``tests/market_data/domain/curves/test_fx_forward.py``; here the focus is orchestration.

This module's other-suite cases live in ``test_fx_forward_integration.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.market_data.domain.curves.calendars import GHANA
from app.market_data.service.market_desk import fx_forward as fx

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
