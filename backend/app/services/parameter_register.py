"""The institution's own board register — seeded at onboarding, per regime.

Two parameter stores exist and they are NOT interchangeable (founder review,
2026-08-23):

* ``regulatory_parameter`` — the GLOBAL control plane managed in the operator
  console, keyed by ``institution_class``. This is what the REGULATOR imposes:
  the s.29 capital floor, the SDI simplified risk weights, the LMTD Table 1
  ratios, the provisioning grid, the DPD boundaries. It is already populated
  (31 approved rows for ``bank``, 32 for ``sdi``) and nothing here writes to it.
* ``param_*`` (this module) — the TENANT's own effective-dated register, the
  institution's board-set policy: its EVE and NII limits, its stress-shock
  calibration, its FTP margin floor, its internal liquidity floors. A regulator
  does not set these; a board does.

What went wrong
---------------
Nothing ever created the second one. ``provision_tenant`` had steps for storage,
KMS, SSO and a first administrator (now an account administrator with an atomic
Org Owner binding), and :func:`_step_readiness` then certified the tenant
"empty-but-wired … goes live on its first ingestion" without ever checking that
the engines had anything to compute with. So a tenant could ingest 490k position
rows, derive a balancing book, and still produce zero successful runs — which is
exactly what happened, and it presented as "we have data but cannot report".

The catalogue is regime-split on purpose
----------------------------------------
A licence class is seeded ONLY the parameters its own entitled modules read
(``module_scope``). An SDI is deliberately NOT given ``lcr_min``/``nsfr_min``:
BoG imposes no LCR or NSFR on any class, an SDI's liquidity is the LMTD Table 1
view, and seeding a Basel floor for it would assert a supervisory requirement
that does not exist — the precise fail-open this codebase refuses everywhere
else. It is likewise not given ``RW0…RW150``: the s.29 ratio reads the
simplified ``risk_weight_<bucket>`` weights from the control plane instead.

Every value here is a STARTING board position, effective-dated and revisable in
the tenant's own settings. None of it is presented as a regulator's number, and
none of it silently overrides the control plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
)

#: Every tenant parameter table the calculation engines read. A tenant with zero
#: rows across all of them cannot produce a single successful run.
PARAMETER_MODELS: tuple[Any, ...] = (
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
)

#: Board register rows are open-ended from the platform epoch: they are the
#: institution's standing policy until it revises them, not a dated regulatory
#: instrument.
REGISTER_EFFECTIVE_FROM = date(2000, 1, 1)


# ---------------------------------------------------------------------------
# Shared: limits every rate-sensitive institution's board sets
# ---------------------------------------------------------------------------

#: IRRBB board limits. ``eve_tier1_limit_pct`` is the BCBS IRRBB standards'
#: outlier threshold (ΔEVE greater than 15% of Tier 1); ``irr_nii_limit_pct`` is
#: a board tolerance with no supervisory equivalent. Both are read by
#: ``regulatory_irr._load_irr_params_or_none`` and the run refuses without them.
_IRRBB_LIMITS: dict[str, str] = {
    "eve_tier1_limit_pct": "15",
    "irr_nii_limit_pct": "10",
}

#: The six BCBS IRRBB interest-rate shock scenarios, plus the BoG GHS
#: calibration from the IRRBB Guideline (exposure draft, Feb 2026) Appendix
#: II–III Tables 5–6 (GHS ±450 bp parallel). Short-rate scenarios decay with
#: tenor; steepener/flattener use the engine's standard short weight.
_IRRBB_SHOCKS: dict[str, dict[str, str]] = {
    "parallel_up_200": {"parallel_bp": "200"},
    "parallel_down_200": {"parallel_bp": "-200"},
    "parallel_up_450": {"parallel_bp": "450"},
    "parallel_down_450": {"parallel_bp": "-450"},
    "short_up_250": {"short_bp": "250", "decay_years": "3"},
    "short_down_250": {"short_bp": "-250", "decay_years": "3"},
    "steepener": {"short_bp": "-65", "long_bp": "90"},
    "flattener": {"short_bp": "80", "long_bp": "-60"},
}


# ---------------------------------------------------------------------------
# Bank (CRD) register
# ---------------------------------------------------------------------------

#: Split into the same three groups the hermetic book writes, each of which
#: carries its own approver there (capital / FX / FTP desks). Public so the test
#: fixture imports these rather than keeping a second copy — the catalogue is
#: defined ONCE, per the convention ``institution_types.seed_rows`` and
#: ``regulatory_parameters.seed_rows`` already follow.
BANK_FX_THRESHOLDS: dict[str, str] = {
    "fx_nop_single_limit_pct": "10",
    "fx_nop_aggregate_limit_pct": "20",
    "fx_var_confidence_pct": "99",
    "hedge_r2_min_pct": "80",
    "hedge_offset_low_pct": "80",
    "hedge_offset_high_pct": "125",
}

BANK_FTP_THRESHOLDS: dict[str, str] = {
    "ftp_target_roe_pct": "15",
    "ftp_min_product_margin_pct": "0",
    "ftp_liquidity_premium_max_bps": "50",
    "ftp_funding_spread_max_bps": "200",
    "nmd_core_min_pct": "60",
    "nmd_core_max_pct": "70",
}

BANK_CAPITAL_THRESHOLDS: dict[str, str] = {
    # Capital — the BoG CRD floors the institution monitors itself against. The
    # binding regulatory values live in the control plane and are time-varying;
    # these are the board's own monitoring levels.
    "car_min": "10",
    "car_early_warning": "10.5",
    "car_critical": "9",
    "cet1_min": "6.5",
    "tier1_min": "8",
    "leverage_min": "3",
    "rwa_multiplier": "1250",
    "tier2_gp_cap_pct_credit_rwa": "1.25",
    "bia_alpha_pct": "15",
    "fx_charge_pct": "8",
    # Liquidity — internal floors. BoG publishes no LCR/NSFR requirement, so
    # these are the institution's own targets, not a supervisory minimum.
    "lcr_min": "100",
    "lcr_amber_floor": "90",
    "nsfr_min": "100",
    "lcr_inflow_cap_pct": "75",
    **_IRRBB_LIMITS,
}

#: What provisioning writes for a bank: the capital register plus the FX and FTP
#: desks' own limits, in one map.
_BANK_THRESHOLDS: dict[str, str] = {
    **BANK_CAPITAL_THRESHOLDS,
    **BANK_FX_THRESHOLDS,
    **BANK_FTP_THRESHOLDS,
}

BANK_RISK_WEIGHTS: dict[str, str] = {
    "RW0": "0",
    "RW20": "20",
    "RW35": "35",
    "RW50": "50",
    "RW75": "75",
    "RW100": "100",
    "RW150": "150",
}

BANK_LCR_OUTFLOWS: dict[str, str] = {
    "retail_deposits_stable": "5",
    "retail_deposits_less_stable": "10",
    "wholesale_operational": "25",
    "wholesale_non_op_sme": "40",
    "wholesale_non_op_corporate": "100",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "0",
    "committed_retail": "10",
    "committed_corporate": "30",
}

BANK_LCR_INFLOWS: dict[str, str] = {
    "retail_loan_repayments": "50",
    "corporate_sme_repayments": "50",
    "interbank_maturing": "100",
}

BANK_NSFR_ASF: dict[str, str] = {
    "capital_total": "100",
    "retail_deposits_stable": "95",
    "retail_deposits_less_stable": "90",
    "wholesale_operational": "50",
    "wholesale_non_op_sme": "90",
    "wholesale_non_op_corporate": "50",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "100",
}

BANK_NSFR_RSF: dict[str, str] = {
    "cash_vault": "0",
    "bog_required_reserves": "0",
    "bog_excess_reserves": "0",
    "securities_bog_bills": "5",
    "securities_gog_bonds": "5",
    "corporate_unrated": "85",
    "sme_retail": "85",
    "retail_other": "85",
    "residential_mortgage": "65",
    "commercial_real_estate": "85",
    "past_due_90": "100",
    "other_assets": "100",
    "off_balance_commitments": "5",
}


# ---------------------------------------------------------------------------
# SDI (Act 930 s.29) register
# ---------------------------------------------------------------------------
#
# Deliberately small. An SDI's entitled official modules are capital and IRRBB
# (``module_scope``): FX, FTP and Basel liquidity are not run for it, and the
# five-year Basel-ratio projection has no registered s.29 method. Its capital
# ratio reads the simplified bucket weights and the s.29 floor from the CONTROL
# PLANE, so it needs no tenant risk-weight or capital-threshold rows for that —
# only the IRRBB board limits below.
#
# What is NOT here, and why:
#   * ``lcr_min`` / ``nsfr_min`` / LCR run-off / NSFR weights — BoG supervises an
#     SDI's liquidity through the LMTD monitoring tools. Seeding a Basel floor
#     would assert a requirement no instrument imposes.
#   * ``RW0…RW150`` — the s.29 ratio uses ``risk_weight_<bucket>`` from the
#     control plane, not the CRD exposure-class ladder.
#   * ``bia_alpha_pct`` — the operational charge, if any, is declared through
#     ``sdi_rwa_composition`` in the control plane, not a tenant row.

_SDI_THRESHOLDS: dict[str, str] = dict(_IRRBB_LIMITS)


@dataclass(frozen=True)
class SeedResult:
    """What :func:`seed_tenant_register` wrote, per table."""

    created: dict[str, int]
    skipped_existing: dict[str, int]

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    def summary(self) -> str:
        if not self.total_created:
            return "board register already present; nothing written"
        parts = ", ".join(f"{table} +{n}" for table, n in sorted(self.created.items()) if n)
        return f"seeded the institution's board register ({parts})"


def _threshold_rows(institution_class: str) -> dict[str, str]:
    return dict(_BANK_THRESHOLDS if institution_class == "bank" else _SDI_THRESHOLDS)


def _base_curve_rows(base_curve: dict[str, str] | None) -> dict[str, str]:
    """The IRRBB base discount curve, keyed by bucket midpoint (``'1.9y'``).

    Supplied by the caller rather than hardcoded: a zero-coupon curve is MARKET
    DATA, not board policy. When the market-data desk has published a curve for
    the institution's currency the caller passes it; otherwise IRRBB stays
    unresolved and refuses, which is correct — an invented curve would price the
    whole banking book.
    """
    return dict(base_curve or {})


def seed_tenant_register(  # noqa: PLR0913 - tenant keys, regime, approver and
    # the optional market curve; every one is required to write a governed row
    db: Session,
    *,
    organization_id: str,
    jurisdiction_code: str,
    institution_class: str,
    approved_by: str,
    approved_at: datetime,
    base_curve: dict[str, str] | None = None,
) -> SeedResult:
    """Create the institution's starting board register. Idempotent.

    Only tables that are EMPTY for this organization are written, so re-running
    after a board has revised its own limits never overwrites them.

    ``approved_by`` is recorded on every row and is NOT decorative: these tables
    are maker-checker governed (``approved_by``/``approval_timestamp`` are NOT
    NULL by schema), so a seeded starting position must name the operator who
    stood it up, exactly as a later board revision names its approver. It is
    never anonymous and never back-dated.
    """
    created: dict[str, int] = {}
    skipped: dict[str, int] = {}

    def existing(model: Any) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.organization_id == organization_id)
            )
            or 0
        )

    common = {
        "organization_id": organization_id,
        "jurisdiction_code": jurisdiction_code,
        "effective_from": REGISTER_EFFECTIVE_FROM,
        "approved_by": approved_by,
        "approval_timestamp": approved_at,
    }

    rows: list[Any] = []
    if (count := existing(ParamCapitalThreshold)) == 0:
        thresholds = _threshold_rows(institution_class)
        rows += [
            ParamCapitalThreshold(threshold_code=code, value_pct=Decimal(value), **common)
            for code, value in thresholds.items()
        ]
        created[ParamCapitalThreshold.__tablename__] = len(thresholds)
    else:
        skipped[ParamCapitalThreshold.__tablename__] = count

    shock_rows: list[Any] = []
    if (count := existing(ParamStressShock)) == 0:
        for scenario, shocks in _IRRBB_SHOCKS.items():
            shock_rows += [
                ParamStressShock(
                    module="irr", scenario_code=scenario, shock_key=key,
                    shock_value=Decimal(value), **common,
                )
                for key, value in shocks.items()
            ]
        shock_rows += [
            ParamStressShock(
                module="irr", scenario_code="base_curve", shock_key=key,
                shock_value=Decimal(value), **common,
            )
            for key, value in _base_curve_rows(base_curve).items()
        ]
        rows += shock_rows
        created[ParamStressShock.__tablename__] = len(shock_rows)
    else:
        skipped[ParamStressShock.__tablename__] = count

    if institution_class == "bank":
        if (count := existing(ParamRiskWeight)) == 0:
            rows += [
                ParamRiskWeight(risk_weight_code=code, weight_pct=Decimal(value), **common)
                for code, value in BANK_RISK_WEIGHTS.items()
            ]
            created[ParamRiskWeight.__tablename__] = len(BANK_RISK_WEIGHTS)
        else:
            skipped[ParamRiskWeight.__tablename__] = count

        if (count := existing(ParamLcrRunoffRate)) == 0:
            flows = [
                ParamLcrRunoffRate(
                    flow_direction=direction, category=category,
                    rate_pct=Decimal(value), **common,
                )
                for direction, table in (
                    ("outflow", BANK_LCR_OUTFLOWS),
                    ("inflow", BANK_LCR_INFLOWS),
                )
                for category, value in table.items()
            ]
            rows += flows
            created[ParamLcrRunoffRate.__tablename__] = len(flows)
        else:
            skipped[ParamLcrRunoffRate.__tablename__] = count

        if (count := existing(ParamNsfrWeight)) == 0:
            weights = [
                ParamNsfrWeight(
                    side=side, category=category, weight_pct=Decimal(value), **common
                )
                for side, table in (("asf", BANK_NSFR_ASF), ("rsf", BANK_NSFR_RSF))
                for category, value in table.items()
            ]
            rows += weights
            created[ParamNsfrWeight.__tablename__] = len(weights)
        else:
            skipped[ParamNsfrWeight.__tablename__] = count

    db.add_all(rows)
    db.flush()
    return SeedResult(created=created, skipped_existing=skipped)


def register_row_count(db: Session, organization_id: str) -> int:
    """Total tenant parameter rows across every table, for the readiness gate."""
    return sum(
        db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.organization_id == organization_id)
        )
        or 0
        for model in PARAMETER_MODELS
    )
