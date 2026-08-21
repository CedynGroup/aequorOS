"""SDI primary/secondary liquidity-reserve check (NBFI r.11; docs/sdi.md §2.2,
§4.1, Phase D).

The check is SDI-class only — the floors are seeded only for the ``sdi`` class, so
it is skipped for a bank (bank output unchanged). Primary reserve = cash + central-
bank balances; secondary = eligible sovereign securities; floors from the control
plane.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.orm import Session

from app.models import Bank
from app.services.regulatory_reporting.le_generation import (
    _append_liquidity_reserve_check,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.helpers import ORG_1

_AS_OF = date(2026, 6, 30)


def _bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Res",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _row(**kw: Any) -> Any:
    base = {
        "position_type": "CASH",
        "counterparty_type": None,
        "balance_ghs": Decimal("0"),
        "regulatory_category": None,
        "encumbered": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _run(db: Session, bank: Bank, rows: list[Any], deposits: str):
    sections: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    _append_liquidity_reserve_check(
        db, bank, _AS_OF, cast(Any, rows), Decimal(deposits), sections, totals, findings
    )
    return totals, findings


def test_sdi_primary_reserve_shortfall_fires(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    rows = [
        _row(position_type="CASH", counterparty_type=None, balance_ghs=Decimal("50")),  # vault
        _row(
            position_type="CASH", counterparty_type="CENTRAL_BANK", balance_ghs=Decimal("40")
        ),  # BoG
        _row(
            position_type="SECURITY_HOLDING",
            regulatory_category="SOVEREIGN_BILL",
            balance_ghs=Decimal("100"),
        ),
    ]
    totals, findings = _run(db_session, sdi, rows, "1000")
    codes = {f["rule"] for f in findings}
    # primary = 90/1000 = 9% < 10% floor → breach; cumulative = 190/1000 = 19% ≥ 15% → ok.
    assert "lmt.primary_liquidity_reserve_below_minimum" in codes
    assert "lmt.secondary_liquidity_reserve_below_minimum" not in codes
    by_code = {t["code"]: t for t in totals}
    assert Decimal(by_code["primary_liquidity_reserve_pct"]["value"]) == Decimal("9")


def test_sdi_reserves_within_floors_are_silent(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="finance_house")
    rows = [
        _row(position_type="CASH", counterparty_type="CENTRAL_BANK", balance_ghs=Decimal("150")),
        _row(
            position_type="SECURITY_HOLDING",
            regulatory_category="SOVEREIGN_BOND",
            balance_ghs=Decimal("100"),
        ),
    ]
    _, findings = _run(db_session, sdi, rows, "1000")
    # primary 15% ≥ 10%, cumulative 25% ≥ 15% → no breach.
    assert [f for f in findings if "below_minimum" in f["rule"]] == []


def test_bank_reserve_check_is_skipped(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    rows = [_row(position_type="CASH", counterparty_type=None, balance_ghs=Decimal("1"))]
    totals, findings = _run(db_session, bank, rows, "1000")
    # The reserve floors are not seeded for the bank class → check skipped entirely.
    assert totals == []
    assert findings == []


def test_encumbered_sovereign_securities_do_not_count_as_secondary(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    rows = [
        _row(position_type="CASH", counterparty_type="CENTRAL_BANK", balance_ghs=Decimal("120")),
        _row(
            position_type="SECURITY_HOLDING",
            regulatory_category="SOVEREIGN_BILL",
            balance_ghs=Decimal("500"),
            encumbered=True,  # pledged — excluded from the secondary reserve
        ),
    ]
    _, findings = _run(db_session, sdi, rows, "1000")
    # primary 12% ≥ 10% ok; cumulative excludes the encumbered 500 → 12% < 15% breach.
    codes = {f["rule"] for f in findings}
    assert "lmt.secondary_liquidity_reserve_below_minimum" in codes
    assert "lmt.primary_liquidity_reserve_below_minimum" not in codes
