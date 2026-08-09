"""Reverse stress testing contracts (Phase 2 item 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ReverseStressRunCreate(BaseModel):
    reporting_period_id: UUID


class ReverseStressRead(BaseModel):
    """The persisted frontier: how far each axis can scale before breach."""

    run_id: UUID
    bank_id: str
    reporting_period_id: UUID
    input_hash: str
    engine_version: str
    liquidity_axis: dict[str, Any]
    capital_axis: dict[str, Any]
    narrative: str
    created_at: datetime
