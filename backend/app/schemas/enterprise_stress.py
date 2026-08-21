"""Enterprise-wide stress test contracts (docs/stress.md §3.3–3.4, Phase 2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanAssumptionsIn(BaseModel):
    """The bank's base-case business plan (the base-leg forecast assumptions).

    Every field is optional; unset fields fall back to the service defaults
    (documented in ``app/services/enterprise_stress.py``). All values are Decimal
    percentages (pp for the securities shift).
    """

    model_config = ConfigDict(extra="forbid")

    loan_growth_pct: Decimal | None = None
    deposit_growth_pct: Decimal | None = None
    nim_pct: Decimal | None = None
    cost_to_income_pct: Decimal | None = None
    credit_loss_rate_pct: Decimal | None = None
    fx_depreciation_pct: Decimal | None = None
    dividend_payout_pct: Decimal | None = None
    fee_income_pct_assets: Decimal | None = None
    tax_rate_pct: Decimal | None = None
    securities_shift_pp: Decimal | None = None


class EnterpriseStressRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    reporting_period_id: UUID
    plan: PlanAssumptionsIn | None = None
    #: An APPROVED governed management-actions plan to model with/without (¶67(f),
    #: ¶78–81). NULL ⇒ the run reports the pre-management-action projection only,
    #: and Appendix II Table 1's management-action blocks stay empty.
    management_action_plan_id: UUID | None = None
    horizon_years: int = Field(default=3, ge=3, le=10)
    paid_up_min: Decimal | None = Field(default=None, ge=0)
    car_target_pct: Decimal = Field(default=Decimal("13"), gt=0, le=100)
    include_irr: bool = True
    include_fx: bool = True
    reason: str = Field(min_length=1, max_length=1000)


class EnterpriseStressSummary(BaseModel):
    scenario_code: str
    stressed_car_end_pct: Decimal
    baseline_car_end_pct: Decimal
    car_erosion_pp: Decimal
    # LCR/NSFR + the solvency–liquidity coupling are Basel measures — present for a
    # bank, NULL for an SDI (docs/sdi.md §4.6; QA audit 2026-08-20 P0-1). The UI must
    # treat null as "not assessed under the SDI regime", never as a passing result.
    stressed_lcr_pct: Decimal | None = None
    baseline_lcr_pct: Decimal | None = None
    both_breached: bool | None = None
    stress_stays_above_all_minima: bool
    first_breach_year: int | None
    binding_minima: list[str]
    capital_gap: Decimal
    # Phase 3 management-actions overlay (present only when a plan was modelled):
    # the directive's "results with and without management actions" headline.
    management_action_plan_code: str | None = None
    with_actions_stays_above_all_minima: bool | None = None
    with_actions_first_breach_year: int | None = None
    residual_capital_required_after_actions: Decimal | None = None


class EnterpriseStressRead(BaseModel):
    run_id: UUID
    bank_id: str
    reporting_period_id: UUID
    scenario_id: UUID
    scenario_code: str
    input_hash: str
    engine_version: str
    summary: EnterpriseStressSummary
    outcome: dict[str, Any]
    projection: dict[str, Any]
    appendix_ii: dict[str, Any]
    created_at: datetime


class EnterpriseStressRunSummary(BaseModel):
    """A lightweight history row for the run registry (docs/stress.md §4.5).

    Built from immutable-run fields + persisted metrics only — no re-computation
    and no per-run scenario fetch — so the workbench can list the full run history
    and re-open any run by ``run_id`` (via GET ``…/enterprise-stress/runs/{run_id}``).
    """

    run_id: UUID
    reporting_period_id: UUID
    scenario_code: str
    status: str
    input_hash: str
    engine_version: str
    stressed_car_end_pct: Decimal
    baseline_car_end_pct: Decimal
    car_erosion_pp: Decimal
    stress_stays_above_all_minima: bool
    first_breach_year: int | None
    capital_gap: Decimal
    management_action_plan_code: str | None = None
    with_actions_stays_above_all_minima: bool | None = None
    created_at: datetime
