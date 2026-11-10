"""IFRS 9 ECL assumptions + CRM supervisory-haircut registers (items 8/9)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EclAssumptionRead(ClosedModel):
    segment: str
    stage: int
    pd_pct: Decimal
    lgd_pct: Decimal
    effective_from: date
    effective_to: date | None = None
    approved_by: str
    approval_timestamp: datetime
    notes: str | None = None


class EclAssumptionEntry(ClosedModel):
    segment: str = Field(min_length=1, max_length=60)
    stage: int = Field(ge=1, le=3)
    pd_pct: Decimal = Field(ge=0, le=100)
    lgd_pct: Decimal = Field(ge=0, le=100)


class EclAssumptionUpdate(ClosedModel):
    assumptions: list[EclAssumptionEntry]
    effective_from: date
    approved_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class EclAssumptionRegisterRead(ClosedModel):
    bank_id: str
    jurisdiction_code: str
    as_of_date: date
    assumptions: list[EclAssumptionRead]
    history: list[EclAssumptionRead]


class EclAssumptionWritten(ClosedModel):
    """Only an assumption generation created by this request, never a resolved row."""

    id: UUID
    segment: str
    stage: int
    pd_pct: Decimal
    lgd_pct: Decimal


class EclAssumptionWriteAcknowledgement(ClosedModel):
    """Write evidence only. Read the register/history through its authorized GET."""

    bank_id: str
    effective_from: date
    approved_by: str
    approval_timestamp: datetime
    notes: str | None
    written: list[EclAssumptionWritten]


class CrmHaircutRead(ClosedModel):
    collateral_class: str
    haircut_pct: Decimal
    effective_from: date | None = None
    effective_to: date | None = None
    approved_by: str | None = None
    approval_timestamp: datetime | None = None
    notes: str | None = None
    #: True for the Basel II ¶151 code default (no register row yet).
    is_default: bool = False


class CrmHaircutUpdate(ClosedModel):
    haircuts: dict[str, Decimal]
    effective_from: date
    approved_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class CrmHaircutRegisterRead(ClosedModel):
    bank_id: str
    jurisdiction_code: str
    as_of_date: date
    haircuts: list[CrmHaircutRead]
    history: list[CrmHaircutRead]


class CrmHaircutWritten(ClosedModel):
    """Only a normalized haircut generation created by this request."""

    id: UUID
    collateral_class: str
    haircut_pct: Decimal


class CrmHaircutWriteAcknowledgement(ClosedModel):
    """Write evidence only, without the merged defaults or prior generations."""

    bank_id: str
    effective_from: date
    approved_by: str
    approval_timestamp: datetime
    notes: str | None
    written: list[CrmHaircutWritten]
