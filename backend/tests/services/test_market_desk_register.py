"""The methodology register: Track-2 governance semantics (spec §5).

Version immutability after approval, dual control (approver != proposer),
one open draft per code, and effective-dated resolution of the active
version.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.market_desk import register

PROPOSER = "analyst@aequoros.com"
CHECKER = "lead@aequoros.com"

# Every versioned parameter spec §5 steps 1-8 names must exist in v1.
GOVERNED_PARAMETER_KEYS = {
    "min_trade_count",
    "max_staleness_days",
    "outlier_zscore_bound",
    "weighting_scheme",
    "aggregation_window_days",
    "target_day_count",
    "target_compounding",
    "interpolation_method",
    "nss_fallback_min_liquid_points",
    "nss_bounds",
    "extrapolation_rule",
    "extrapolation_anchor_tenor_y",
    "oscillation_tolerance",
    "enforce_positive_forwards",
    "mpc_meeting_dates",
    "overnight_spread_bps",
    "cointegration",
    "grr_formula",
    "liquidity_premium_bps_by_tenor",
}


def _approve_v1(db: Session, *, effective_from: date = date(2026, 1, 1)) -> None:
    register.ensure_default_methodology(db)
    register.approve_version(
        db,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=CHECKER,
        effective_from=effective_from,
    )


def test_ensure_default_methodology_seeds_v1_draft_with_governed_parameters(
    db_session: Session,
) -> None:
    row = register.ensure_default_methodology(db_session)
    assert row.methodology_code == register.DEFAULT_METHODOLOGY_CODE
    assert row.version == 1
    assert row.status == "draft"
    assert set(row.parameters) >= GOVERNED_PARAMETER_KEYS
    # The GRR methodology v1 is the three-input formula, encoded as data.
    grr = row.parameters["grr_formula"]
    assert grr["type"] == "three_input"
    assert grr["components"] == ["MPR", "GHS.INTERBANK.ON", "GHS.TBILL.91.YIELD"]
    assert len(grr["weights"]) == 3
    # Six MPC dates on the 2026 grid (announced + documented placeholders).
    assert len(row.parameters["mpc_meeting_dates"]) == 6
    # Cointegration is explicitly flagged as borrowed until locally calibrated.
    assert row.parameters["cointegration"]["status"] == "awaiting_local_calibration"

    # Idempotent: a second ensure returns the same row, no duplicate.
    again = register.ensure_default_methodology(db_session)
    assert again.id == row.id
    assert len(register.list_methodologies(db_session)) == 1


def test_create_methodology_refuses_duplicate_code(db_session: Session) -> None:
    register.ensure_default_methodology(db_session)
    with pytest.raises(HTTPException) as excinfo:
        register.create_methodology(
            db_session,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            parameters={"x": 1},
            change_rationale="dup",
            proposed_by=PROPOSER,
        )
    assert excinfo.value.status_code == 409


def test_approval_requires_a_distinct_approver(db_session: Session) -> None:
    row = register.ensure_default_methodology(db_session)
    with pytest.raises(HTTPException) as excinfo:
        register.approve_version(
            db_session,
            methodology_code=row.methodology_code,
            version=1,
            approved_by=row.proposed_by,  # maker approving own proposal
            effective_from=date(2026, 1, 1),
        )
    assert excinfo.value.status_code == 422
    assert register.get_version(db_session, row.methodology_code, 1).status == "draft"


def test_approval_stamps_dual_control_metadata(db_session: Session) -> None:
    _approve_v1(db_session)
    row = register.get_version(db_session, register.DEFAULT_METHODOLOGY_CODE, 1)
    assert row.status == "approved"
    assert row.approved_by == CHECKER
    assert row.approved_at is not None
    assert row.effective_from == date(2026, 1, 1)


def test_approved_version_is_immutable(db_session: Session) -> None:
    _approve_v1(db_session)
    with pytest.raises(HTTPException) as excinfo:
        register.update_draft(
            db_session,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            version=1,
            parameters={"min_trade_count": 99},
            change_rationale="sneaky in-place edit",
        )
    assert excinfo.value.status_code == 409
    # Re-approval is equally refused: history is never silently rewritten.
    with pytest.raises(HTTPException) as excinfo:
        register.approve_version(
            db_session,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            version=1,
            approved_by="someone-else@aequoros.com",
            effective_from=date(2026, 2, 1),
        )
    assert excinfo.value.status_code == 409
    row = register.get_version(db_session, register.DEFAULT_METHODOLOGY_CODE, 1)
    assert row.parameters["min_trade_count"] != 99
    assert row.effective_from == date(2026, 1, 1)


def test_draft_is_amendable_until_approval(db_session: Session) -> None:
    register.ensure_default_methodology(db_session)
    row = register.update_draft(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        parameters={**register.DEFAULT_METHODOLOGY_PARAMETERS_V1, "min_trade_count": 5},
        change_rationale="tightened liquidity gate before first approval",
    )
    assert row.parameters["min_trade_count"] == 5
    assert row.status == "draft"


def test_propose_version_mints_next_version_with_one_open_draft(db_session: Session) -> None:
    _approve_v1(db_session)
    v2 = register.propose_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        parameters={**register.DEFAULT_METHODOLOGY_PARAMETERS_V1, "outlier_zscore_bound": 3.5},
        change_rationale="re-estimated outlier bound on H1 data",
        proposed_by=PROPOSER,
    )
    assert v2.version == 2
    assert v2.status == "draft"
    # A second concurrent draft is refused.
    with pytest.raises(HTTPException) as excinfo:
        register.propose_version(
            db_session,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            parameters={},
            change_rationale="another",
            proposed_by=PROPOSER,
        )
    assert excinfo.value.status_code == 409


def test_propose_version_requires_rationale_and_existing_code(db_session: Session) -> None:
    _approve_v1(db_session)
    with pytest.raises(HTTPException) as excinfo:
        register.propose_version(
            db_session,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            parameters={},
            change_rationale="   ",
            proposed_by=PROPOSER,
        )
    assert excinfo.value.status_code == 422
    with pytest.raises(HTTPException) as excinfo:
        register.propose_version(
            db_session,
            methodology_code="AEQ-DOES-NOT-EXIST",
            parameters={},
            change_rationale="x",
            proposed_by=PROPOSER,
        )
    assert excinfo.value.status_code == 404


def test_get_active_resolves_by_effective_date(db_session: Session) -> None:
    _approve_v1(db_session, effective_from=date(2026, 1, 1))
    register.propose_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        parameters={**register.DEFAULT_METHODOLOGY_PARAMETERS_V1, "min_trade_count": 4},
        change_rationale="Q3 recalibration",
        proposed_by=PROPOSER,
    )
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=2,
        approved_by=CHECKER,
        effective_from=date(2026, 9, 1),
    )

    code = register.DEFAULT_METHODOLOGY_CODE
    active_before = register.get_active(db_session, code, date(2026, 8, 14))
    assert active_before is not None and active_before.version == 1
    active_on = register.get_active(db_session, code, date(2026, 9, 1))
    assert active_on is not None and active_on.version == 2
    # Before any version is effective: nothing is active.
    assert register.get_active(db_session, code, date(2025, 12, 31)) is None
    # Unknown code: nothing is active.
    assert register.get_active(db_session, "AEQ-UNKNOWN", date(2026, 8, 14)) is None
