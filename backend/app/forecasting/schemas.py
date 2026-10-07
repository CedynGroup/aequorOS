"""API contract for the governed forecast assumption register."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type ForecastAssumptionVersionStatus = Literal["draft", "submitted", "approved", "rejected"]
type ForecastAssumptionVersionOrigin = Literal["tenant", "starting_position", "register"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastPresetAssumptionsWrite(ClosedModel):
    """One scenario's projection drivers, as proposed (percentages)."""

    loan_growth_pct: Decimal = Field(title="Preset Loan Growth Pct")
    deposit_growth_pct: Decimal = Field(title="Preset Deposit Growth Pct")
    nim_pct: Decimal = Field(title="Preset NIM Pct")
    cost_to_income_pct: Decimal = Field(title="Preset Cost To Income Pct")
    credit_loss_rate_pct: Decimal = Field(title="Preset Credit Loss Rate Pct")
    fx_depreciation_pct: Decimal = Field(title="Preset FX Depreciation Pct")
    dividend_payout_pct: Decimal = Field(title="Preset Dividend Payout Pct")


class ForecastPresetSetWrite(ClosedModel):
    """A complete proposal: every preset scenario with every driver."""

    base: ForecastPresetAssumptionsWrite
    adverse: ForecastPresetAssumptionsWrite
    severely_adverse: ForecastPresetAssumptionsWrite


class ForecastPresetAssumptionsRead(ClosedModel):
    loan_growth_pct: Decimal
    deposit_growth_pct: Decimal
    nim_pct: Decimal
    cost_to_income_pct: Decimal
    credit_loss_rate_pct: Decimal
    fx_depreciation_pct: Decimal
    dividend_payout_pct: Decimal


class ForecastPresetSetRead(ClosedModel):
    base: ForecastPresetAssumptionsRead
    adverse: ForecastPresetAssumptionsRead
    severely_adverse: ForecastPresetAssumptionsRead


class ForecastAssumptionVersionCreate(ClosedModel):
    effective_from: date
    presets: ForecastPresetSetWrite
    change_note: str = Field(min_length=1, max_length=2000)


class ForecastAssumptionVersionUpdate(ClosedModel):
    """Revise a draft; unset fields keep their drafted value."""

    effective_from: date | None = Field(default=None, title="Assumption Effective From Update")
    presets: ForecastPresetSetWrite | None = Field(default=None, title="Assumption Presets Update")
    change_note: str | None = Field(
        default=None, min_length=1, max_length=2000, title="Assumption Change Note Update"
    )


class ForecastAssumptionDecision(ClosedModel):
    """The checker's decision note: optional on approval, required on rejection."""

    note: str | None = Field(
        default=None, min_length=1, max_length=2000, title="Assumption Decision Note"
    )


class ForecastAssumptionProvenanceRead(ClosedModel):
    """Which approved version supplied a projection's assumptions, and on whose authority."""

    version_id: UUID
    version_number: int
    origin: ForecastAssumptionVersionOrigin
    effective_from: date
    approved_by: UUID | None = Field(title="Assumption Approver Id")
    approved_by_name: str | None = Field(title="Assumption Approver Name")
    approved_at: datetime


class ForecastAssumptionVersionRead(ClosedModel):
    id: UUID
    bank_id: str
    version_number: int
    status: ForecastAssumptionVersionStatus
    origin: ForecastAssumptionVersionOrigin
    effective_from: date
    presets: ForecastPresetSetRead
    change_note: str
    created_by: UUID | None = Field(title="Assumption Author Id")
    created_by_name: str | None = Field(title="Assumption Author Name")
    created_at: datetime
    submitted_by: UUID | None = Field(title="Assumption Submitter Id")
    submitted_by_name: str | None = Field(title="Assumption Submitter Name")
    submitted_at: datetime | None = Field(title="Assumption Submitted At")
    reviewed_by: UUID | None = Field(title="Assumption Reviewer Id")
    reviewed_by_name: str | None = Field(title="Assumption Reviewer Name")
    reviewed_at: datetime | None = Field(title="Assumption Reviewed At")
    review_note: str | None = Field(title="Assumption Review Note")
    updated_at: datetime


class ForecastAssumptionRegisterRead(ClosedModel):
    """A bank's versions, newest first, with the ones that matter named.

    ``as_of`` is the bank's latest book date; ``effective_version_id`` is the
    approved version a run on that book resolves (``None``: forecasting is not
    computable). ``open_version_id`` is the draft or submission in flight.
    """

    bank_id: str
    as_of: date | None = Field(title="Assumption Register As Of")
    effective_version_id: UUID | None = Field(title="Effective Assumption Version Id")
    open_version_id: UUID | None = Field(title="Open Assumption Version Id")
    versions: list[ForecastAssumptionVersionRead]
