"""Pure quantitative curve library (spec: AequorOS Market Data & Curve Platform §5-§7).

Numerical heart of the market-data platform: day-count/quote conventions,
zero-curve interpolation (including Hagan-West monotone convex), NSS parametric
fitting, sovereign bootstrap from Ghana's bill/bond reality, forward derivation
with the pre-publish QA gate, the MPR-anchored meeting-date step curve, and the
Engle-Granger machinery behind the synthetic discounting level.

Unlike the accounting-style domain engines (IRR, FTP), this package is
float64/numpy end to end: every routine is iterative numerical analysis
(Newton, least squares, spline construction) where ``Decimal`` buys nothing and
costs interoperability with numpy/scipy. The ``Decimal`` boundary sits at the
calling service, which quantizes *published* node values; inside this package
determinism comes from fixed algorithms, fixed iteration rules and zero
randomness (no random seeds anywhere in library code).

The forward-curve construction layer (spec ``forward_curve_construction.md``,
FC-0/1/2) adds the calendar/schedule engine, the rate-helper / instrument
catalog, and the simultaneous multi-curve solve with the tenor-adjusted
forward-grid output. The key public names are re-exported here for convenience;
individual modules remain the canonical import path.
"""

from app.domain.curves.calendars import (
    GHANA,
    KENYA,
    NIGERIA,
    SOUTH_AFRICA,
    USA,
    BusinessDayConvention,
    Calendar,
    add_calendar_months,
    build_calendar,
    calendar_from_holidays,
    ghana_holidays,
    kenya_holidays,
    next_imm_date,
    nigeria_holidays,
    south_africa_holidays,
    tenor_to_months,
    third_wednesday,
    us_federal_holidays,
)
from app.domain.curves.fx_forward import (
    FxForwardCurve,
    FxForwardError,
    FxForwardPoint,
    build_fx_forward_curve,
)
from app.domain.curves.instruments import (
    DepositHelper,
    DiscountCurve,
    FraHelper,
    FuturesHelper,
    InstrumentSet,
    OisHelper,
    RateHelper,
    SwapHelper,
    convexity_adjustment,
)
from app.domain.curves.multicurve import (
    CurveSet,
    ForwardGrid,
    ForwardGridRow,
    MarketQuote,
    SolvedCurve,
    build_curve_set,
    convert_basis,
    forward_grid,
    forward_grid_for_frequency,
)

__all__ = [
    "GHANA",
    "KENYA",
    "NIGERIA",
    "SOUTH_AFRICA",
    "USA",
    "BusinessDayConvention",
    "Calendar",
    "CurveSet",
    "DepositHelper",
    "DiscountCurve",
    "ForwardGrid",
    "ForwardGridRow",
    "FraHelper",
    "FuturesHelper",
    "FxForwardCurve",
    "FxForwardError",
    "FxForwardPoint",
    "InstrumentSet",
    "MarketQuote",
    "OisHelper",
    "RateHelper",
    "SolvedCurve",
    "SwapHelper",
    "add_calendar_months",
    "build_calendar",
    "build_curve_set",
    "build_fx_forward_curve",
    "calendar_from_holidays",
    "convert_basis",
    "convexity_adjustment",
    "forward_grid",
    "forward_grid_for_frequency",
    "ghana_holidays",
    "kenya_holidays",
    "next_imm_date",
    "nigeria_holidays",
    "south_africa_holidays",
    "tenor_to_months",
    "third_wednesday",
    "us_federal_holidays",
]
