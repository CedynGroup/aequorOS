"""SDI liquidity regime — Table-1 floors resolve for the institution class
(docs/sdi.md §4.1, Phase D).

Proves the three widened ``"bank"`` filters now bind an SDI tenant against the
SDI LMTD Table-1 floors (via the regulatory-parameter control plane), while a
bank keeps the bank floors — with no board row on either side.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services import liquidity_thresholds
from app.services.regulatory_reporting.le_generation import (
    _table1_thresholds,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)
_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)

# The published Table-1 floors (docs/sdi.md §4.1) — bank vs SDI, per code.
_BANK_FLOORS = {
    "narrow_to_volatile": Decimal("80"),
    "broad_to_volatile": Decimal("100"),
    "narrow_to_short_term": Decimal("50"),
    "broad_to_short_term": Decimal("70"),
    "narrow_to_total_assets": Decimal("30"),
    "broad_to_total_assets": Decimal("50"),
    "narrow_to_total_deposits": Decimal("60"),
    "broad_to_total_deposits": Decimal("80"),
}
_SDI_FLOORS = {
    "narrow_to_volatile": Decimal("90"),
    "broad_to_volatile": Decimal("100"),
    "narrow_to_short_term": Decimal("50"),
    "broad_to_short_term": Decimal("60"),
    "narrow_to_total_assets": Decimal("30"),
    "broad_to_total_assets": Decimal("40"),
    "narrow_to_total_deposits": Decimal("60"),
    "broad_to_total_deposits": Decimal("70"),
}


def _bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Liq",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_table1_thresholds_bind_sdi_floors_for_an_sdi_tenant(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    resolved = _table1_thresholds(db_session, _CTX, sdi, _AS_OF)
    for code, floor in _SDI_FLOORS.items():
        assert resolved[code][0] == floor, code
        assert resolved[code][1] == "regulatory_default", code


def test_table1_thresholds_keep_bank_floors_for_a_bank_tenant(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    resolved = _table1_thresholds(db_session, _CTX, bank, _AS_OF)
    for code, floor in _BANK_FLOORS.items():
        assert resolved[code][0] == floor, code


def test_monitoring_register_surfaces_sdi_floors(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="finance_house")
    register = liquidity_thresholds.get_register(db_session, _CTX, sdi.id, _AS_OF)
    by_code = {t.threshold_code: t for t in register.thresholds}
    assert by_code["narrow_to_volatile"].threshold_pct == Decimal("90")
    assert by_code["narrow_to_volatile"].institution_class == "sdi"
    assert by_code["broad_to_total_deposits"].threshold_pct == Decimal("70")
    # The bank floors never leak into an SDI register.
    assert by_code["broad_to_short_term"].threshold_pct == Decimal("60")


def test_monitoring_register_keeps_bank_floors_for_a_bank(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    register = liquidity_thresholds.get_register(db_session, _CTX, bank.id, _AS_OF)
    by_code = {t.threshold_code: t for t in register.thresholds}
    assert by_code["narrow_to_volatile"].threshold_pct == Decimal("80")
    assert by_code["narrow_to_volatile"].institution_class == "bank"
