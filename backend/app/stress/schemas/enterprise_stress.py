"""Enterprise-wide stress test contracts (docs/stress.md §3.3–3.4, Phase 2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanAssumptionsIn(BaseModel):
    """The bank's base-case business plan (the base-leg forecast assumptions).

    Every field is optional; an unset field falls back to a macro-derived value
    where the scenario supports one, otherwise to a documented PLATFORM DEFAULT
    (``app/services/enterprise_stress.py``). Which of the three supplied each
    number is reported on the run as ``plan_provenance`` — a Board-attested ICAAP
    must be able to show which assumptions were the institution's own
    (enterprise audit P0-13).

    All values are Decimal percentages (pp for the securities shift). **Every
    field is bounded.** Before 2026-08-21 all ten were unbounded ``Decimal |
    None``, so ``tax_rate_pct = -9999`` was accepted and projected. The bounds
    below are structural domain limits — a payout ratio is a share of profit, a
    tax rate is a share of income — not regulatory numbers, so they belong in the
    contract rather than the control plane.
    """

    model_config = ConfigDict(extra="forbid")

    #: Nominal annual growth. Below -100% a book would have negative volume.
    loan_growth_pct: Decimal | None = Field(default=None, ge=-100, le=200)
    deposit_growth_pct: Decimal | None = Field(default=None, ge=-100, le=200)
    #: Net interest margin on earning assets. Negative is possible (an inverted
    #: book funding above its asset yield) but not unbounded.
    nim_pct: Decimal | None = Field(default=None, ge=-50, le=100)
    #: Operating cost as a share of total income. Above 100% is a loss-making
    #: bank, which is a legitimate plan; 500% is a typo.
    cost_to_income_pct: Decimal | None = Field(default=None, ge=0, le=500)
    #: Credit losses as a share of gross loans. A share cannot be negative.
    credit_loss_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    #: Cedi depreciation applied once in year 1. -100% would zero the currency.
    fx_depreciation_pct: Decimal | None = Field(default=None, ge=-100, le=1000)
    #: Dividends as a share of net income — a share, so 0..100.
    dividend_payout_pct: Decimal | None = Field(default=None, ge=0, le=100)
    #: Fee income as a share of average assets — a share, so non-negative.
    fee_income_pct_assets: Decimal | None = Field(default=None, ge=0, le=100)
    #: Effective tax rate — a share of pre-tax profit, so 0..100.
    tax_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    #: Securities growth relative to deposit growth, in percentage points.
    securities_shift_pp: Decimal | None = Field(default=None, ge=-100, le=100)


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
    #: The CAR the Appendix II "capital required" and Pillar-1-requirement lines
    #: are measured against. NULL (the default) ⇒ the institution's OWN governed
    #: minimum CAR resolves from the regulatory-parameter control plane.
    #:
    #: This defaulted to the literal ``13`` until 2026-08-22 (audit D-15), which
    #: made one run carry two capital floors: the governed, effective-dated
    #: ``car_min`` the engines check the ratios against, and this request default
    #: the capital-requirement lines were computed from. They are the same
    #: regulatory quantity, and a literal cannot track it — BoG has moved the
    #: minimum (CRD ¶71 10% + the ¶75 conservation buffer) more than once, and an
    #: SDI's Act 930 s.29 floor is 10%, not 13%.
    #:
    #: Supplying a value models an INTERNAL target above the regulatory floor; a
    #: value below the governed floor is refused rather than filed.
    car_target_pct: Decimal | None = Field(default=None, gt=0, le=100)
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
    #: False when ANY base-case plan assumption fell back to a platform default
    #: rather than being supplied by the institution (enterprise audit P0-13).
    #: A Board-attested ICAAP resting on platform constants must say so on its
    #: face; ``EnterpriseStressRead.plan_provenance`` names the exact fields.
    plan_fully_supplied_by_institution: bool = False
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
    #: Per-field origin of every base-case plan assumption: ``bank_plan`` (the
    #: institution supplied it), ``macro_scenario`` (derived from the approved
    #: scenario's own base path) or ``platform_default`` (a documented platform
    #: constant). The audit record for P0-13 — no assumption is invented
    #: invisibly.
    plan_provenance: dict[str, Any] = Field(default_factory=dict)
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
