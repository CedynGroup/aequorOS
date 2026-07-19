"""Typed contracts for the in-process cash-flow forecast endpoints.

These models serialize with camelCase JSON (via ``to_camel`` aliases) — the
wire shape originally defined by the cashflow-ml sidecar and preserved
verbatim when the ML code moved in-process (``app/ml``), so the generated
OpenAPI client needed no regeneration.
"""

from __future__ import annotations

from datetime import date
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

type CashflowForecastMode = Literal["lstm", "static"]
# Whether the serving model was trained on this bank's own ingested cash-flow
# history ("bank_specific") or is the shared generic bootstrap model served while
# the bank lacks enough daily history ("generic"). Surfaced so a generic model is
# never mistaken for a bank-specific one (ai_engine.md §12.1).
type CashflowForecastModelScope = Literal["bank_specific", "generic"]


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
    model_scope: CashflowForecastModelScope
    accuracy: CashflowForecastAccuracyRead
    points: list[CashflowForecastPointRead]


class CashflowHistoryPointRead(CamelClosedModel):
    date: date
    net_flow: float


class CashflowHistoryRead(CamelClosedModel):
    points: list[CashflowHistoryPointRead]
