from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import AppEnv


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: AppEnv
    status: Literal["ok"]


class ReadinessResponse(HealthResponse):
    database: dict[str, str]
