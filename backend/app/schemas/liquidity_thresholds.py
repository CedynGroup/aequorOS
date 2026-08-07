"""Board threshold register contracts (LMTD 2026 ¶11(b)–(e))."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

ThresholdSource = Literal["board_register", "regulatory_default"]
InstitutionClass = Literal["bank", "sdi"]


class LiquidityThresholdRead(BaseModel):
    """One resolved threshold: the Board's value, or the directive's floor."""

    threshold_code: str
    institution_class: InstitutionClass
    threshold_pct: Decimal
    source: ThresholdSource
    effective_from: date | None = None
    effective_to: date | None = None
    approved_by: str | None = None
    approval_timestamp: datetime | None = None
    notes: str | None = None


class LiquidityThresholdHistoryRead(BaseModel):
    """One recorded generation — the Board-approval evidence trail."""

    threshold_code: str
    institution_class: InstitutionClass
    threshold_pct: Decimal
    effective_from: date
    effective_to: date | None
    approved_by: str
    approval_timestamp: datetime
    notes: str | None


class LiquidityThresholdRegisterRead(BaseModel):
    bank_id: str
    jurisdiction_code: str
    as_of_date: date
    thresholds: list[LiquidityThresholdRead]
    history: list[LiquidityThresholdHistoryRead]


class LiquidityThresholdUpdate(BaseModel):
    """A Board-approved threshold generation.

    ``approved_by`` names the approving authority (Board minute reference or
    officer); ``reason`` is the audit narrative. Codes not present keep their
    current resolution.
    """

    institution_class: InstitutionClass = "bank"
    effective_from: date
    approved_by: str = Field(min_length=1, max_length=120)
    thresholds: dict[str, Decimal] = Field(min_length=1)
    reason: str = Field(min_length=1)
    notes: str | None = None
