from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    request_id: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorBody


class FigureRefusalRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    reason_code: str
    rule_citation: str
    row_ref: tuple[int, ...]
    reason: str | None = None
