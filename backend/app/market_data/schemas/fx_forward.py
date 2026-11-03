"""FX-forward (outright) curve construction contracts (operator control plane ONLY).

Serves the STAFF FX-forward preview under ``app.operator`` (FC-6b); nothing here is
mounted on — or importable into — the tenant API surface. Actor identities are
operator emails (workforce IdP), never tenant users. Mirrors the ClosedModel
(``extra="forbid"``) discipline of ``schemas/market_desk_curves.py``.

An FX-forward curve is built by covered interest parity from two single-currency
rate curves plus an FX spot; each leg is either explicit money-market/swap/OIS
pillar quotes (solved on the fly) or an already-published desk curve code (read
back through the determinations read layer). The optional cross-currency
``basis_bps`` is a governed additive spread, ``0`` by default (textbook CIP).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- leg specification ---------------------------------------------------------------


class FxLegQuote(ClosedModel):
    """One rate-curve pillar quote for a leg (rate as a DECIMAL fraction, 0.05 = 5%)."""

    instrument: Literal["deposit", "depo", "money_market", "mm", "swap", "ois", "fra"] = "deposit"
    tenor: str = Field(min_length=1, max_length=20)
    quote: float
    # FC-G3: an explicit forward/IMM start makes the instrument forward-starting;
    # ``None`` keeps the classic spot-start behaviour.
    forward_start: date | None = Field(default=None, title="Quote Forward Start")


class FxLeg(ClosedModel):
    """One currency leg: solve from ``quotes`` or read a ``published`` curve code."""

    source: Literal["quotes", "published"]
    # -- source == "quotes" --
    calendar_name: str | None = Field(default=None, max_length=40, title="Leg Calendar Name")
    spot_lag_days: int = Field(default=2, ge=0)
    interpolation: str = Field(default="log_linear_df", min_length=1, max_length=40)
    extrapolation: str = Field(default="flat_forward", min_length=1, max_length=30)
    float_frequency_months: int = Field(default=6, gt=0)
    quotes: list[FxLegQuote] = Field(default_factory=list)
    # -- source == "published" --
    curve_code: str | None = Field(default=None, max_length=60, title="Leg Curve Code")

    @model_validator(mode="after")
    def _check_source(self) -> FxLeg:
        if self.source == "quotes":
            if not self.quotes:
                raise ValueError("A 'quotes' leg needs at least one pillar quote.")
            if not self.calendar_name:
                raise ValueError("A 'quotes' leg needs a calendar_name.")
        elif not self.curve_code:
            raise ValueError("A 'published' leg needs a curve_code.")
        return self


# -- request -------------------------------------------------------------------------


class FxForwardConstructRequest(ClosedModel):
    """Construct an outright FX-forward curve by covered interest parity (preview).

    ``spot`` is quote-currency units per one base-currency unit (GHS per USD for
    ``USDGHS``). Provide EITHER a ``tenor_grid`` (resolved to dates via
    ``grid_calendar``) OR explicit ``dates`` — exactly one.
    """

    base_ccy: str = Field(min_length=3, max_length=3)
    quote_ccy: str = Field(min_length=3, max_length=3)
    spot: float = Field(gt=0.0)
    as_of: date
    base_leg: FxLeg
    quote_leg: FxLeg
    basis_bps: float = Field(default=0.0, title="Cross-Currency Basis Bps")
    day_count: str = Field(default="ACT_365F", min_length=1, max_length=20)
    tenor_grid: list[str] = Field(default_factory=list)
    dates: list[date] = Field(default_factory=list)
    grid_calendar: str | None = Field(default=None, max_length=40, title="Grid Calendar Name")
    grid_spot_lag_days: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def _check_grid(self) -> FxForwardConstructRequest:
        if bool(self.tenor_grid) == bool(self.dates):
            raise ValueError("Provide exactly one of tenor_grid or dates.")
        if self.tenor_grid and not self.grid_calendar:
            raise ValueError("A tenor_grid needs a grid_calendar to resolve dates.")
        if self.base_ccy.upper() == self.quote_ccy.upper():
            raise ValueError("base_ccy and quote_ccy must differ.")
        return self


# -- response ------------------------------------------------------------------------


class FxForwardRowRead(ClosedModel):
    date: date
    year_fraction: float
    df_base: float
    df_quote: float
    forward_rate: float
    forward_points: float


class FxForwardConstructResponse(ClosedModel):
    """The desk FX-forward preview: outright forwards + implied points + digest."""

    base_ccy: str
    quote_ccy: str
    pair: str
    spot: float
    as_of: date
    basis_bps: float
    basis_calibrated: bool
    day_count: str
    base_source: str
    quote_source: str
    input_digest: str
    rows: list[FxForwardRowRead]
