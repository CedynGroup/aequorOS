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
# 1-8, flat-keyed by the spec's own parameter names. VALUES ARE PLACEHOLDERS
# (spec §14): thresholds, bounds, and β estimates must be calibrated against
# real Ghanaian data before production use; the structure — WHICH parameters
# are governed and HOW — is the deliverable here.
# ---------------------------------------------------------------------------
DEFAULT_METHODOLOGY_PARAMETERS_V1: dict[str, Any] = {
    "parameter_status": "placeholder_pending_calibration",
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
    # Ghana; NSS parametric fallback on thin days).
    "interpolation_method": "monotone_convex",
    "nss_fallback_min_liquid_points": 6,
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
    # Step 6 — forward-curve QA gate.
    "oscillation_tolerance": 0.0025,
    "enforce_positive_forwards": True,
    # Step 7 — synthetic OIS / discounting curve. Announced 2026 MPC decision
    # dates from the captured BoG pages (tests/fixtures/market_desk/raw/
    # bog_wdt_table62_mpc_rate_page.json): 28 Jan / 18 Mar / 20 May / 22 Jul.
    # The September and November dates were not yet on the captured pages and
    # are documented placeholders on the observed ~8-week grid — replace on
    # BoG's announcement (a Track-2 change).
    "mpc_meeting_dates": [
        {"date": "2026-01-28", "status": "announced"},
        {"date": "2026-03-18", "status": "announced"},
        {"date": "2026-05-20", "status": "announced"},
        {"date": "2026-07-22", "status": "announced"},
        {"date": "2026-09-23", "status": "projected_placeholder"},
        {"date": "2026-11-25", "status": "projected_placeholder"},
    ],
    # Interbank overnight vs MPR spread inside the ±100 bps corridor; observed
    # deeply negative in excess-liquidity conditions (spec §6.2 ~-170 bps late
    # 2025) — modeled dynamically, this is the calibration anchor only.
    "overnight_spread_bps": -170,
    "cointegration": {
        "alpha": 0.002,
        "beta": 0.95,
        "source": "ZAR-borrowed (Jakarasi 2015 / Van Heeswijk 2017)",
        "status": "awaiting_local_calibration",
    },
    # Step 8 — derived values and research assumptions.
    "grr_formula": {
        "type": "three_input",
        "components": ["MPR", "GHS.INTERBANK.ON", "GHS.TBILL.91.YIELD"],
        "weights": [1 / 3, 1 / 3, 1 / 3],
        "note": "confirm with BoG — Track-2 change on confirmation",
    },
    "liquidity_premium_bps_by_tenor": {
        "12": 0,
        "24": 25,
        "36": 40,
        "60": 60,
        "84": 75,
        "120": 90,
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
            "Initial AEQ-GHS-CURVES methodology: three-input GRR-consistent base "
            "(MPR + interbank overnight + 91-day T-bill), monotone-convex zero "
            "curve with NSS fallback, MPR-anchored meeting-date discounting step "
            "function with ZAR-borrowed cointegration level. All parameter values "
            "are placeholders pending calibration on Ghanaian data (spec §14)."
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
