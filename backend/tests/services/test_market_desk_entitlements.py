"""Desk dataset entitlement tiers (spec §10)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Organization
from app.services.market_desk import entitlements


def _org(db: Session, name: str) -> Organization:
    row = Organization(name=name)
    db.add(row)
    db.flush()
    return row


def test_default_tier_is_standard_when_no_rows(db_session: Session) -> None:
    org = _org(db_session, "Entitlement Default")
    db_session.commit()
    granted = entitlements.active_datasets(db_session, org.id, as_of=date(2026, 8, 1))
    assert "DESK_RATES" in granted
    assert "DESK_CURVES_SOVEREIGN" in granted
    assert "DESK_CURVES_CREDIT" not in granted  # premium only


def test_grant_premium_unlocks_credit(db_session: Session) -> None:
    org = _org(db_session, "Entitlement Premium")
    entitlements.grant_tier(
        db_session,
        organization_id=org.id,
        tier="premium",
        effective_from=date(2026, 1, 1),
        granted_by="ops@aequoros.com",
    )
    db_session.commit()
    granted = entitlements.active_datasets(db_session, org.id, as_of=date(2026, 8, 1))
    assert "DESK_CURVES_CREDIT" in granted
    assert entitlements.curve_allowed("AEQ.GHS.CORP", granted)
    assert entitlements.curve_allowed("AEQ.GHS.SOV.ZERO", granted)


def test_core_tier_blocks_sovereign_curves(db_session: Session) -> None:
    org = _org(db_session, "Entitlement Core")
    entitlements.grant_tier(
        db_session,
        organization_id=org.id,
        tier="core",
        effective_from=date(2026, 1, 1),
        granted_by="ops@aequoros.com",
    )
    db_session.commit()
    granted = entitlements.active_datasets(db_session, org.id)
    assert entitlements.curve_allowed("AEQ.GHS.SOV.ZERO", granted) is False
    assert entitlements.index_allowed("GHS.MPR", granted) is True
    assert entitlements.index_allowed("GHS.APR.GCB", granted) is False


def test_revoke_ends_grant(db_session: Session) -> None:
    org = _org(db_session, "Entitlement Revoke")
    rows = entitlements.grant_tier(
        db_session,
        organization_id=org.id,
        tier="standard",
        effective_from=date(2026, 1, 1),
        granted_by="ops@aequoros.com",
    )
    target = next(r for r in rows if r.dataset_code == "DESK_CURVES_DISCOUNT")
    entitlements.revoke(db_session, target.id, revoked_by="ops@aequoros.com")
    db_session.commit()
    granted = entitlements.active_datasets(db_session, org.id)
    assert "DESK_CURVES_DISCOUNT" not in granted
