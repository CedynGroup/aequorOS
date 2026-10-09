from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FigureRefusalRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    reason_code: str
    rule_citation: str
    row_ref: tuple[int, ...]
    reason: str | None = None
