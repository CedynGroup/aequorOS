"""Per-bank overlay composition: arithmetic, windows, versioning, isolation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Bank, MarketDataOverlay
from app.services.market_data_overlays import (
    active_curve_overlays,
    compose_curve,
    is_active,
)
from tests.api.helpers import ORG_1, ORG_2

AS_OF = date(2026, 7, 15)
CURVE = "AEQ.GHS.SOV.ZERO"

BASE_POINTS: tuple[tuple[int, Decimal], ...] = (
    (3, Decimal("0.25")),
    (12, Decimal("0.20")),
    (60, Decimal("0.18")),
)


def _bank(db_session: Session, org_id: str = ORG_1, name: str = "Overlay Test Bank") -> Bank:
    bank = Bank(
        organization_id=org_id,
        name=name,
        short_name=name[:4].upper(),
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    return bank


def _overlay(  # noqa: PLR0913 - fixture knob per data-model axis
    db_session: Session,
    bank: Bank,
    *,
    value: str,
    component_tag: str = "liquidity_premium",
    adjustment_type: str = "additive_bps",
    tenor_months: int | None = None,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
    curve_name: str = CURVE,
) -> MarketDataOverlay:
    overlay = MarketDataOverlay(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        base_ref_kind="curve",
        base_curve_name=curve_name,
        tenor_months=tenor_months,
        adjustment_type=adjustment_type,
        value=Decimal(value),
        component_tag=component_tag,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    db_session.add(overlay)
    db_session.flush()
    return overlay


def test_additive_bps_composition_is_transparent_arithmetic(db_session: Session) -> None:
    """base 20.00% + 25 bps TLP + 25 bps liquidity = 20.50% (spec §11b)."""
    bank = _bank(db_session)
    _overlay(db_session, bank, value="25", component_tag="term_liquidity_premium")
    _overlay(db_session, bank, value="25", component_tag="liquidity_premium")

    overlays = active_curve_overlays(db_session, ORG_1, bank.id, AS_OF, curve_name=CURVE)
    composed = compose_curve(BASE_POINTS, overlays)

    assert composed is not None
    assert len(composed.components) == 2
    assert composed.adjusted_points == (
        (3, Decimal("0.2550")),
        (12, Decimal("0.2050")),
        (60, Decimal("0.1850")),
    )


def test_tenor_specific_flat_fixed_and_multiplicative(db_session: Session) -> None:
    bank = _bank(db_session)
    # Flat +50 bps funding spread across every tenor.
    _overlay(db_session, bank, value="50", component_tag="funding_spread")
    # 12M-only +30 bps credit spread.
    _overlay(db_session, bank, value="30", component_tag="credit_spread", tenor_months=12)
    # Flat multiplicative 1.10 factor, applied to the base BEFORE spreads.
    _overlay(
        db_session, bank, value="1.10", adjustment_type="multiplicative", component_tag="other"
    )
    # 60M-only fixed +0.005 (rate decimal fraction).
    _overlay(
        db_session,
        bank,
        value="0.005",
        adjustment_type="fixed",
        component_tag="other",
        tenor_months=60,
    )

    overlays = active_curve_overlays(db_session, ORG_1, bank.id, AS_OF, curve_name=CURVE)
    composed = compose_curve(BASE_POINTS, overlays)

    assert composed is not None
    # 3M:  0.25 * 1.10 + 0.0050                     = 0.2800
    # 12M: 0.20 * 1.10 + 0.0050 + 0.0030            = 0.2280
    # 60M: 0.18 * 1.10 + 0.0050 + 0.005 (fixed)     = 0.2080
    by_tenor = dict(composed.adjusted_points)
    assert by_tenor[3] == Decimal("0.2800")
    assert by_tenor[12] == Decimal("0.2280")
    assert by_tenor[60] == Decimal("0.2080")


def test_effective_date_windows_gate_composition(db_session: Session) -> None:
    bank = _bank(db_session)
    _overlay(db_session, bank, value="25", effective_from=AS_OF)  # starts today: active
    not_yet = _overlay(db_session, bank, value="100", effective_from=date(2026, 8, 1))
    ended = _overlay(
        db_session,
        bank,
        value="100",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 6, 30),
    )
    ends_today = _overlay(
        db_session, bank, value="10", effective_from=date(2026, 1, 1), effective_to=AS_OF
    )

    overlays = active_curve_overlays(db_session, ORG_1, bank.id, AS_OF, curve_name=CURVE)

    assert {Decimal(o.value) for o in overlays} == {Decimal("25"), Decimal("10")}
    assert not is_active(not_yet, AS_OF)
    assert not is_active(ended, AS_OF)
    assert is_active(ends_today, AS_OF)  # effective_to is inclusive
    composed = compose_curve(BASE_POINTS, overlays)
    assert composed is not None
    assert dict(composed.adjusted_points)[12] == Decimal("0.2035")  # 20% + 35 bps


def test_versioned_edits_exclude_superseded_rows(db_session: Session) -> None:
    bank = _bank(db_session)
    old = _overlay(db_session, bank, value="25")
    new = _overlay(db_session, bank, value="40")
    old.superseded_by = new.id
    db_session.flush()

    overlays = active_curve_overlays(db_session, ORG_1, bank.id, AS_OF, curve_name=CURVE)

    assert [o.id for o in overlays] == [new.id]
    composed = compose_curve(BASE_POINTS, overlays)
    assert composed is not None
    assert dict(composed.adjusted_points)[12] == Decimal("0.2040")
    # A dangling supersession pointer still means "not current".
    old.superseded_by = uuid4()
    assert not is_active(old, AS_OF)


def test_other_orgs_overlays_never_compose(db_session: Session) -> None:
    bank_one = _bank(db_session)
    bank_two = _bank(db_session, org_id=ORG_2, name="Rival Bank")
    _overlay(db_session, bank_two, value="500")  # same curve name, other tenant

    assert active_curve_overlays(db_session, ORG_1, bank_one.id, AS_OF, curve_name=CURVE) == []
    # Even a same-org sibling bank's overlays stay per-bank.
    bank_sibling = _bank(db_session, name="Sibling Bank")
    _overlay(db_session, bank_sibling, value="75")
    assert active_curve_overlays(db_session, ORG_1, bank_one.id, AS_OF, curve_name=CURVE) == []


def test_no_active_overlays_returns_none_not_duplicate_base(db_session: Session) -> None:
    assert compose_curve(BASE_POINTS, []) is None
