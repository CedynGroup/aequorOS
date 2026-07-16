from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.live import LiveModuleView
from app.schemas.regulatory_liquidity import IrrScenarioCode, RegulatoryValidationSeverity

type IrrStatus = Literal["green", "amber", "red"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IrrScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class IrrGapBucketRead(ClosedModel):
    bucket: str
    midpoint_years: Decimal
    rsa_ghs: Decimal
    rsl_ghs: Decimal
    gap_ghs: Decimal
    cumulative_gap_ghs: Decimal
    within_12m: bool


class IrrEveScenarioRead(ClosedModel):
    scenario_code: IrrScenarioCode
    eve_ghs: Decimal
    delta_eve_ghs: Decimal
    delta_eve_pct_tier1: Decimal
    breach: bool


class IrrMetricsRead(ClosedModel):
    eve_base_ghs: Decimal
    worst_scenario_code: IrrScenarioCode
    worst_eve_change_ghs: Decimal
    worst_eve_change_pct_tier1: Decimal
    eve_status: IrrStatus
    eve_limit_pct: Decimal
    ear_up_200_ghs: Decimal
    ear_down_200_ghs: Decimal
    nii_base_ghs: Decimal
    asset_duration: Decimal
    liability_duration: Decimal
    duration_gap: Decimal
    cumulative_12m_gap_ghs: Decimal
    tier1_ghs: Decimal


class IrrValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class IrrTrendPointRead(ClosedModel):
    reporting_period_id: UUID
    label: str
    period_end: date
    worst_eve_change_pct_tier1: Decimal
    duration_gap: Decimal
    cumulative_12m_gap_ghs: Decimal
    stored: bool


class IrrDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    stored: bool
    latest_run_id: UUID | None = Field(title="Irr Dashboard Latest Run Id")
    metrics: IrrMetricsRead
    gap_table: list[IrrGapBucketRead]
    eve_scenarios: list[IrrEveScenarioRead]
    trend: list[IrrTrendPointRead]
    validations: list[IrrValidationRead]
    live: LiveModuleView | None = None
