"""Typed pass-through contracts for the cashflow-ml proxy.

The ML service serializes with camelCase JSON; these models keep that wire
shape (via ``to_camel`` aliases) so the SPA receives exactly the contract the
forecasting service defines, while the OpenAPI schema stays fully typed.
"""

from __future__ import annotations

from datetime import date
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

type CashflowForecastMode = Literal["lstm", "static"]


class CashflowHorizon(IntEnum):
    """Supported forecast horizons in days (mirrors the ML service contract)."""

    DAYS_30 = 30
    DAYS_60 = 60
    DAYS_90 = 90


class CamelClosedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )


class CashflowForecastAccuracyRead(CamelClosedModel):
    lstm_mape: float
    static_mape: float
    improvement_pct: float


class CashflowForecastPointRead(CamelClosedModel):
    day: int
    date: date
    net_flow: float
    lower: float
    upper: float


class CashflowForecastRead(CamelClosedModel):
    mode: CashflowForecastMode
    horizon: int
    as_of_date: date
    model_version: str
    accuracy: CashflowForecastAccuracyRead
    points: list[CashflowForecastPointRead]


class CashflowHistoryPointRead(CamelClosedModel):
    date: date
    net_flow: float


class CashflowHistoryRead(CamelClosedModel):
    points: list[CashflowHistoryPointRead]
