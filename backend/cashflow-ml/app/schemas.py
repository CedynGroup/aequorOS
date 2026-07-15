"""API response models.

``/forecast`` and ``/history`` serialize with camelCase aliases per the SPA
contract; ``/health`` and ``/train`` return snake_case metric payloads.
"""

from __future__ import annotations

import datetime
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Horizon(IntEnum):
    """Supported forecast horizons in days."""

    DAYS_30 = 30
    DAYS_60 = 60
    DAYS_90 = 90


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_trained: bool
    model_version: str | None


class TrainResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    lstm_mape: float
    lstm_rmse: float
    static_mape: float
    static_rmse: float
    improvement_pct: float
    trained_at: str
    model_version: str


class ForecastAccuracy(CamelModel):
    lstm_mape: float
    static_mape: float
    improvement_pct: float


class ForecastPoint(CamelModel):
    day: int
    date: datetime.date
    net_flow: float
    lower: float
    upper: float


class ForecastResponse(CamelModel):
    mode: Literal["lstm", "static"]
    horizon: int
    as_of_date: datetime.date
    model_version: str
    accuracy: ForecastAccuracy
    points: list[ForecastPoint]


class HistoryPoint(CamelModel):
    date: datetime.date
    net_flow: float


class HistoryResponse(CamelModel):
    points: list[HistoryPoint]
