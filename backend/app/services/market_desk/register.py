"""The methodology register (spec §5): versioned parameters, Track-2 governance.

The register is the artifact a bank's due-diligence team or a regulator
inspects: for each methodology code, every version with its full parameter
set, rationale, proposer, approver, and effective date. The weekly
determination READS from the register (never hard-codes assumptions); a
methodology change WRITES a new version here under Track-2 control.

Immutability is enforced in this service layer: an approved version is never
edited — ``update_draft`` refuses non-draft rows and any parameter change
mints version+1 via ``propose_version``. (A Postgres UPDATE-blocking trigger
for approved rows was judged overkill this phase; the service is the single
write path and the operator API is its only caller.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import func, select

from app.db.base import utc_now
from app.models import DeskMethodology

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

DEFAULT_METHODOLOGY_CODE = "AEQ-GHS-CURVES"

# The bootstrap actor for the seeded v1 row: a system identity, so any human
# operator can be its Track-2 approver (approver != proposer).
_BOOTSTRAP_PROPOSER = "desk-bootstrap@aequoros.system"

# ---------------------------------------------------------------------------
# Default methodology v1 — every versioned parameter named in spec §5 steps
# 1-8, flat-keyed by the spec's own parameter names. Calibrated 2026-08-09 on
# the harvested Ghana fixtures (tests/fixtures/market_desk/series/) by the
# Track-2 evidence artifact ``scripts/desk_calibration_ghana.py``; the NSS
# bounds and the liquidity-premium curve remain documented placeholders
# (spec §14). The calculation pipeline that consumes this set lives in
# ``app/services/market_desk/calculation.py``.
# ---------------------------------------------------------------------------
DEFAULT_METHODOLOGY_PARAMETERS_V1: dict[str, Any] = {
    "parameter_status": (
        "v1_calibrated_2026-08-09 (scripts/desk_calibration_ghana.py); "
        "nss_bounds and liquidity_premium_bps_by_tenor remain placeholders"
    ),
    # Step 1 — raw observation capture and cleaning.
    "min_trade_count": 3,
    "max_staleness_days": 5,
    "outlier_zscore_bound": 4.0,
    # Step 2 — observation selection and weighting.
    "weighting_scheme": "volume_weighted_mean",
    "aggregation_window_days": 5,
    # Step 3 — yield/price normalization (GFIM conventions: actual/364).
    "target_day_count": "ACT_364",
    "target_compounding": "annual",
    # Step 4 — zero-curve construction (Lartey & Li: PCH/monotone-convex for
    # Ghana; NSS parametric fallback on thin days). The fallback threshold is
    # 3 because the standing weekly tender supplies exactly three bill points
    # (91/182/364-day): the bootstrap is node-exact there, and NSS (six
    # parameters) is only ever the better curve when even the standing bill
    # set is incomplete.
    "interpolation_method": "monotone_convex",
    "nss_fallback_min_liquid_points": 3,
    "nss_bounds": {
        "beta0": [0.0, 0.60],
        "beta1": [-0.30, 0.30],
        "beta2": [-0.30, 0.30],
        "beta3": [-0.30, 0.30],
        "tau1": [0.05, 5.0],
        "tau2": [0.5, 10.0],
    },
    # Step 5 — long-end extrapolation.
    "extrapolation_rule": "flat_forward",
    "extrapolation_anchor_tenor_y": 15,
    # Step 6 — forward-curve QA gate. ``oscillation_tolerance`` carries the
    # total-variation-ratio semantics of ``qa_forwards`` (1.0 = monotone path,
    # ~2.0 = one clean hump, unbounded as the path rings; the gate passes at
    # or below the tolerance) — the earlier 0.0025 placeholder predated the
    # QA implementation and was on the wrong scale.
    "oscillation_tolerance": 3.0,
    "enforce_positive_forwards": True,
    "forward_qa_grid": {"start_y": 0.1, "stop_y": 10.0, "step_y": 0.1},
    # Step 7 — synthetic OIS / discounting curve (AGD). Announced 2026 MPC
    # decision dates from the captured BoG pages (tests/fixtures/market_desk/
    # raw/bog_wdt_table62_mpc_rate_page.json): 28 Jan / 18 Mar / 20 May /
    # 22 Jul. The September and November dates were not yet on the captured
    # pages and are documented placeholders on the observed ~8-week grid —
    # replace on BoG's announcement (a Track-2 change).
    "mpc_meeting_dates": [
        {"date": "2026-01-28", "status": "announced"},
        {"date": "2026-03-18", "status": "announced"},
        {"date": "2026-05-20", "status": "announced"},
        {"date": "2026-07-22", "status": "announced"},
        {"date": "2026-09-23", "status": "projected_placeholder"},
        {"date": "2026-11-25", "status": "projected_placeholder"},
    ],
    # AGD short end (calculation.py step 7): the step-curve level is
    # MPR + rolling mean of (interbank overnight - MPR) over the last
    # ``overnight_spread_window_bdays`` business days — every input directly
    # observable, NO cointegration in the level. Calibration evidence
    # (2026-08-09): the interbank rate sits 477 bps BELOW the 15.00% MPR —
    # outside the ±100 bps corridor on 100% of 2026 trading days — but the
    # spread is extremely stable (std 73 bps over 2026, ~0 bps over the last
    # 20 business days), so a short rolling mean is both honest and tight.
    # Stage 4 readiness: synthetic_agd (default AGD) or ois_bootstrap when
    # true overnight-indexed instruments exist (GHS.OIS.*). Insufficient OIS
    # instruments always fall back to synthetic_agd with a disclosed flag.
    "discounting_mode": "synthetic_agd",
    # Stage 3 credit curve (AEQ.GHS.CORP): minimum liquid corporate GFIM yields.
    "credit_curve_min_points": 2,
    "credit_curve_min_trades": 1,
    "overnight_spread_window_bdays": 20,
    # Static reference snapshot of the spread at calibration (2026-08-07,
    # interbank 10.23 vs MPR 15.00). The LIVE anchor is the rolling window
    # above; this figure is retained as documentation of the regime the
    # window parameters were calibrated in.
    "overnight_spread_bps": -477,
    "expected_policy_moves_bps": None,
    # AGD longer end: sovereign zero + governed discounting-basis curve.
    # v1 default rule (disclosed assumption): the short-tenor basis is the
    # OBSERVED gap (step-curve-implied short zero - sovereign short zero) at
    # the shortest liquid tenor, tapering linearly to zero by
    # ``basis_taper_tenor_y``. ``explicit_bps_by_tenor_y`` (Track-2 governed)
    # overrides the observed rule when set.
    "discount_basis": {
        "mode": "observed_short_anchor_linear_taper",
        "basis_taper_tenor_y": 5.0,
        "explicit_bps_by_tenor_y": None,
    },
    "agd_node_grid_months": [1, 3, 6, 12, 24, 36, 60, 84, 120],
    "fwd_node_grid_months": [3, 6, 12, 24, 36, 60, 84, 120],
    # Cointegration is a weekly DIAGNOSTIC only in v1 — it never sets the AGD
    # level. Local calibration (scripts/desk_calibration_ghana.py, 2026-08-09,
    # 91-day T-bill yield on the daily interbank rate, auction-date aligned):
    # full-sample beta 0.9458 (inside the ZAR 0.93-0.97 literature range) but
    # the no-cointegration null is NOT rejected at the 5% governance bar in
    # any regime window (best ADF -3.08 in the 2025+ window vs -3.04 at 10%
    # asymptotic / -3.34 at 5%; residual std ~3.9 pp). Promotion to
    # level-setter is a documented future Track-2 event; ``status`` refers to
    # that promotion. The ZAR-borrowed alpha/beta record is retained for
    # reference.
    "cointegration": {
        "role": "diagnostic",
        "alpha": 0.002,
        "beta": 0.95,
        "source": "ZAR-borrowed (Jakarasi 2015 / Van Heeswijk 2017)",
        "status": "awaiting_local_calibration",
        "decision_significance": "5%",
        "diagnostic_window_bdays": 250,
        "adf_maxlag": 8,
        "significance_disclosed": True,
        "local_evidence": {
            "calibrated_on": "2026-08-09",
            "script": "scripts/desk_calibration_ghana.py",
            "alignment": "auction_date",
            "beta_full_sample": 0.9458,
            "alpha_full_sample": -0.4418,
            "n_full_sample": 366,
            "best_window": "2025+",
            "best_window_adf": -3.077,
            "adf_critical_10pct": -3.04,
            "adf_critical_5pct": -3.34,
            "best_window_residual_std_pp": 3.89,
            "verdict": "cointegration_rejected_at_5pct_in_every_regime_window",
        },
    },
    # Step 8 — derived values and research assumptions.
    "grr_formula": {
        "type": "three_input",
        "components": ["MPR", "GHS.INTERBANK.ON", "GHS.TBILL.91.YIELD"],
        "weights": [1 / 3, 1 / 3, 1 / 3],
        "note": "confirm with BoG — Track-2 change on confirmation",
    },
    # GRR pass-through cross-check gate: |published - reconstructed| above
    # this many percentage points raises a steward QA flag — never a silent
    # block (spec §4 validation is "lightweight but real").
    "grr_check_tolerance_pp": 1.0,
    "liquidity_premium_bps_by_tenor": {
        "12": 0,
        "24": 25,
        "36": 40,
        "60": 60,
        "84": 75,
        "120": 90,
    },
    # Per-series treatments (calculation.py step 1): the pipeline REFUSES any
    # snapshot series without a declared treatment. Keys are exact series
    # codes or a '.*'-suffixed prefix pattern. ``treatment`` is one of
    # pass_through | windowed | derived | bond_quote.
    "series_methodologies": {
        "GHS.MPR": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 70,
            "validation": "changes_only_on_mpc_meeting_dates",
        },
        "GHS.GRR": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 45,
            "cross_check": "grr_three_input_reconstruction",
        },
        "GHS.INTERBANK.ON": {
            "treatment": "windowed",
            "unit": "pct",
            "max_staleness_days": 5,
            "window_param": "overnight_spread_window_bdays",
        },
        "GHS.INTERBANK.MONTHLY_AVG": {
            "treatment": "windowed",
            "unit": "pct",
            "role": "grr_check_input_only",
        },
        "GHS.TBILL.91.DISCOUNT": {
            "treatment": "derived",
            "unit": "pct",
            "max_staleness_days": 10,
            "tenor_days": 91,
            "emits": "GHS.TBILL.91.YIELD",
            "convention": "ACT/364 discount->yield",
        },
        "GHS.TBILL.182.DISCOUNT": {
            "treatment": "derived",
            "unit": "pct",
            "max_staleness_days": 10,
            "tenor_days": 182,
            "emits": "GHS.TBILL.182.YIELD",
            "convention": "ACT/364 discount->yield",
        },
        "GHS.TBILL.364.DISCOUNT": {
            "treatment": "derived",
            "unit": "pct",
            "max_staleness_days": 10,
            "tenor_days": 364,
            "emits": "GHS.TBILL.364.YIELD",
            "convention": "ACT/364 discount->yield",
        },
        "GHS.TBILL.91.YIELD": {
            "treatment": "derived",
            "unit": "pct",
            "derived_from": "GHS.TBILL.91.DISCOUNT",
            "observed_role": "cross_check",
        },
        "GHS.TBILL.182.YIELD": {
            "treatment": "derived",
            "unit": "pct",
            "derived_from": "GHS.TBILL.182.DISCOUNT",
            "observed_role": "cross_check",
        },
        "GHS.TBILL.364.YIELD": {
            "treatment": "derived",
            "unit": "pct",
            "derived_from": "GHS.TBILL.364.DISCOUNT",
            "observed_role": "cross_check",
        },
        "GHS.USDGHS.MID": {
            "treatment": "pass_through",
            "unit": "rate",
            "max_staleness_days": 3,
            "note": "alias dual-written from GHS.FX.USDGHS.MID (BoG table 31/40)",
        },
        "GHS.FX.USDGHS.MID": {
            "treatment": "pass_through",
            "unit": "rate",
            "max_staleness_days": 3,
            "note": "BoG interbank USD/GHS mid (table 31/40)",
        },
        "GHS.FX.*": {
            "treatment": "pass_through",
            "unit": "rate",
            "max_staleness_days": 3,
        },
        "GHS.FX.USDGHS.REF": {
            "treatment": "pass_through",
            "unit": "rate",
            "max_staleness_days": 3,
            "note": "BoG weighted-median reference rate (BOG/FMD/2024/65) — passes through",
        },
        "GHS.TBILL.91.INTEREST": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 14,
            "note": "BoG published true interest leg (ACT/364); dual-written as .YIELD",
        },
        "GHS.TBILL.182.INTEREST": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 14,
        },
        "GHS.TBILL.364.INTEREST": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 14,
        },
        "GHS.BOGBILL.*": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 14,
        },
        "GHS.APR.*": {
            "treatment": "pass_through",
            "unit": "pct",
            "max_staleness_days": 62,
            "role": "lending_indicator_input",
        },
        # GFIM secondary legs (yield/price/volume/trades) share a prefix pattern;
        # Stage 3 credit selection further filters to corporate YIELD + maturity.
        "GHS.GFIM.*": {
            "treatment": "gfim_quote",
            "unit": "pct",
            "max_staleness_days": 5,
            "role": "credit_curve_input",
        },
        "GHS.OIS.*": {
            "treatment": "ois_instrument",
            "unit": "pct",
            "max_staleness_days": 5,
            "role": "true_ois_bootstrap_input",
        },
        "GHS.GOG.BOND.*": {
            "treatment": "bond_quote",
            "unit": "index",
            "max_staleness_days": 10,
            "coupon_frequency": 2,
            "price_type": "clean",
            "day_count": "ACT/365F",
        },
        "GHS.LENDING.INDICATOR": {
            "treatment": "derived",
            "unit": "pct",
            "derived_from": "GHS.APR.*",
            "method": "median_with_trimmed_mean",
            "trim_fraction": 0.20,
        },
        "GHS.BASE.GRR_CONSISTENT": {
            "treatment": "derived",
            "unit": "pct",
            "derived_from": "GHS.GRR",
            "method": "grr_pass_through_with_decomposition",
        },
    },
}


def ensure_default_methodology(db: Session) -> DeskMethodology:
    """Get-or-create the AEQ-GHS-CURVES v1 DRAFT (idempotent, service-seeded).

    Deliberately NOT a data migration: the row is desk master data, approval
    happens through the console/API under Track-2 control, and a deployment
    that never approved it has — correctly — no active methodology.
    """
    existing = db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == DEFAULT_METHODOLOGY_CODE)
        .order_by(DeskMethodology.version)
        .limit(1)
    )
    if existing is not None:
        return existing
    row = DeskMethodology(
        methodology_code=DEFAULT_METHODOLOGY_CODE,
        version=1,
        status="draft",
        parameters=DEFAULT_METHODOLOGY_PARAMETERS_V1,
        change_rationale=(
            "Initial AEQ-GHS-CURVES methodology, calibrated 2026-08-09 on the "
            "harvested Ghana fixtures (scripts/desk_calibration_ghana.py): "
            "monotone-convex sovereign zero curve with NSS fallback; AGD short "
            "end anchored at MPR + 20-business-day rolling mean of the "
            "interbank-to-MPR spread (directly observable — no cointegration "
            "in the level); AGD long end as sovereign zero + a disclosed "
            "short-anchor basis tapering to zero by 5y; Engle-Granger "
            "cointegration retained as a weekly diagnostic only (rejected at "
            "5% in every regime window; beta full-sample 0.9458); three-input "
            "GRR-consistent base with a 1.0pp reconstruction cross-check; "
            "declared per-series treatments. NSS bounds and the liquidity "
            "premium curve remain placeholders (spec §14)."
        ),
        proposed_by=_BOOTSTRAP_PROPOSER,
    )
    db.add(row)
    db.flush()
    return row


def create_methodology(
    db: Session,
    *,
    methodology_code: str,
    parameters: dict[str, Any],
    change_rationale: str,
    proposed_by: str,
) -> DeskMethodology:
    """Register a NEW methodology code at version 1 (draft)."""
    exists = db.scalar(
        select(func.count())
        .select_from(DeskMethodology)
        .where(DeskMethodology.methodology_code == methodology_code)
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Methodology {methodology_code!r} already exists; propose a new version.",
        )
    row = DeskMethodology(
        methodology_code=methodology_code,
        version=1,
        status="draft",
        parameters=parameters,
        change_rationale=change_rationale,
        proposed_by=proposed_by,
    )
    db.add(row)
    db.flush()
    return row


def list_methodologies(
    db: Session, *, methodology_code: str | None = None
) -> list[DeskMethodology]:
    query = select(DeskMethodology).order_by(
        DeskMethodology.methodology_code, DeskMethodology.version
    )
    if methodology_code is not None:
        query = query.where(DeskMethodology.methodology_code == methodology_code)
    return list(db.scalars(query))


def get_version(db: Session, methodology_code: str, version: int) -> DeskMethodology:
    row = db.scalar(
        select(DeskMethodology).where(
            DeskMethodology.methodology_code == methodology_code,
            DeskMethodology.version == version,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Methodology {methodology_code!r} version {version} does not exist.",
        )
    return row


def propose_version(
    db: Session,
    *,
    methodology_code: str,
    parameters: dict[str, Any],
    change_rationale: str,
    proposed_by: str,
) -> DeskMethodology:
    """Track 2: draft version+1 with a documented rationale.

    One open draft per code at a time — a second concurrent draft would make
    "which parameter set is under review" ambiguous at approval.
    """
    latest = db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == methodology_code)
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Methodology {methodology_code!r} does not exist; create it first.",
        )
    if latest.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Methodology {methodology_code!r} v{latest.version} is still a draft; "
                "approve or supersede it before proposing another version."
            ),
        )
    if not change_rationale.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A methodology change requires a non-empty rationale (Track 2).",
        )
    row = DeskMethodology(
        methodology_code=methodology_code,
        version=latest.version + 1,
        status="draft",
        parameters=parameters,
        change_rationale=change_rationale,
        proposed_by=proposed_by,
    )
    db.add(row)
    db.flush()
    return row


def update_draft(
    db: Session,
    *,
    methodology_code: str,
    version: int,
    parameters: dict[str, Any],
    change_rationale: str,
) -> DeskMethodology:
    """Amend a DRAFT in place. Approved/retired versions are immutable —
    history is never silently altered (spec §5 Track 2)."""
    row = get_version(db, methodology_code, version)
    if row.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Methodology {methodology_code!r} v{version} is {row.status} and "
                "immutable; propose a new version instead."
            ),
        )
    row.parameters = parameters
    row.change_rationale = change_rationale
    db.flush()
    return row


def approve_version(
    db: Session,
    *,
    methodology_code: str,
    version: int,
    approved_by: str,
    effective_from: date,
) -> DeskMethodology:
    """Track-2 approval: dual control, effective-dated, append-only."""
    row = get_version(db, methodology_code, version)
    if row.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Methodology {methodology_code!r} v{version} is already {row.status}.",
        )
    if approved_by.strip().lower() == row.proposed_by.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A methodology version cannot be approved by its proposer "
                "(dual control, spec §5 Track 2)."
            ),
        )
    row.status = "approved"
    row.approved_by = approved_by
    row.approved_at = utc_now()
    row.effective_from = effective_from
    db.flush()
    return row


def get_active(
    db: Session, methodology_code: str, cob_date: date
) -> DeskMethodology | None:
    """The version the weekly run applies: latest APPROVED version whose
    ``effective_from`` is on or before the COB date."""
    return db.scalar(
        select(DeskMethodology)
        .where(
            DeskMethodology.methodology_code == methodology_code,
            DeskMethodology.status == "approved",
            DeskMethodology.effective_from <= cob_date,
        )
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )
