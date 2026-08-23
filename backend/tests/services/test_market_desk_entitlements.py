"""Desk dataset entitlement tiers (spec §10)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import DeskCurveDefinition, Organization
from app.services.market_desk import entitlements


def _org(db: Session, name: str) -> Organization:
    row = Organization(name=name)
    db.add(row)
    db.flush()
    return row


def _approved_curve_definition(
    db: Session, *, curve_code: str, tier: str
) -> DeskCurveDefinition:
    row = DeskCurveDefinition(
        curve_code=curve_code,
        version=1,
        status="approved",
        currency="GHS",
        calendar_name="GHANA",
        curve_kind="forward",
        instrument_set_ref="GHS.SOV",
        interpolation_method="monotone_convex",
        output_daycount="ACT_360",
        payment_interval_months=3,
        curve_frequency="3M",
        entitlement_tier=tier,
        change_rationale="test",
        proposed_by="analyst@aequoros.com",
        approved_by="lead@aequoros.com",
        effective_from=date(2026, 1, 1),
    )
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


# ---------------------------------------------------------------------------
# FC-6d: definition entitlement tier gating
# ---------------------------------------------------------------------------


def test_org_tier_resolves_the_highest_fully_held_tier() -> None:
    assert entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["core"])) == "core"
    assert (
        entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["standard"])) == "standard"
    )
    assert (
        entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["premium"])) == "premium"
    )


def test_curve_visible_gates_on_definition_tier(db_session: Session) -> None:
    _approved_curve_definition(db_session, curve_code="AEQ.GHS.SOV.FWD", tier="premium")
    _approved_curve_definition(db_session, curve_code="AEQ.GHS.SOV.ZERO", tier="standard")
    db_session.flush()
    standard = set(entitlements.ENTITLEMENT_TIERS["standard"])
    premium = set(entitlements.ENTITLEMENT_TIERS["premium"])

    # The name-family gate alone would admit both to a standard org...
    assert entitlements.curve_allowed("AEQ.GHS.SOV.FWD", standard) is True
    # ...but the premium tier on the FWD definition hides it from a standard org.
    assert entitlements.curve_visible(db_session, "AEQ.GHS.SOV.FWD", standard) is False
    assert entitlements.curve_visible(db_session, "AEQ.GHS.SOV.ZERO", standard) is True
    # A premium org sees both.
    assert entitlements.curve_visible(db_session, "AEQ.GHS.SOV.FWD", premium) is True
    assert entitlements.curve_visible(db_session, "AEQ.GHS.SOV.ZERO", premium) is True


def test_ungoverned_aeq_curve_keeps_name_gate_only(db_session: Session) -> None:
    # No definition governs AEQ.GHS.OIS → tier gate is a no-op; the name-family
    # gate (OIS needs the discount dataset) still applies.
    assert entitlements.curve_definition_tier(db_session, "AEQ.GHS.OIS") is None
    premium = set(entitlements.ENTITLEMENT_TIERS["premium"])
    core = set(entitlements.ENTITLEMENT_TIERS["core"])
    assert entitlements.curve_visible(db_session, "AEQ.GHS.OIS", premium) is True
    assert entitlements.curve_visible(db_session, "AEQ.GHS.OIS", core) is False


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
    entitlements.revoke(
        db_session, target.id, organization_id=org.id, revoked_by="ops@aequoros.com"
    )
    db_session.commit()
    granted = entitlements.active_datasets(db_session, org.id)
    assert "DESK_CURVES_DISCOUNT" not in granted


def test_revoke_refuses_another_tenants_entitlement(db_session: Session) -> None:
    """The org is part of the LOOKUP key, not a check applied afterwards.

    ``revoke`` used to resolve the row with a bare ``db.get`` by id, so any
    operator could end any tenant's grant by naming its uuid (audit finding
    D-26). A foreign row is a 404 — indistinguishable from one that does not
    exist, so the id space stays unprobeable.
    """
    owner = _org(db_session, "Entitlement Owner")
    bystander = _org(db_session, "Entitlement Bystander")
    rows = entitlements.grant_tier(
        db_session,
        organization_id=owner.id,
        tier="standard",
        effective_from=date(2026, 1, 1),
        granted_by="ops@aequoros.com",
    )
    target = next(r for r in rows if r.dataset_code == "DESK_CURVES_DISCOUNT")
    with pytest.raises(HTTPException) as excinfo:
        entitlements.revoke(
            db_session,
            target.id,
            organization_id=bystander.id,
            revoked_by="ops@aequoros.com",
        )
    assert excinfo.value.status_code == 404
    db_session.rollback()
    assert "DESK_CURVES_DISCOUNT" in entitlements.active_datasets(db_session, owner.id)
