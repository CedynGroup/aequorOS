"""Schemas for manual market data uploads (market_data_adapter.md §8.3)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketDataUploadRead(ClosedModel):
    """Summary of one manual market data upload.

    Wraps the ingestion batch the pull produced: its terminal status, the
    scopes persisted, canonical record count, and the operator-facing
    warnings (row-level problems included) and errors.
    """

    batch_id: UUID
    bank_id: UUID
    status: str
    as_of_date: date
    scopes: list[str]
    canonical_records_produced: int
    quota_consumed: int
    warnings: list[str]
    errors: list[str]
