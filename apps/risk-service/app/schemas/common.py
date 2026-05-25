from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    request_id: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorBody
