"""Canonical → BankFinancialFact derivation on the compact canonical fixture.

This module's other-suite cases live in ``test_fact_derivation_integration.py``.
"""

from __future__ import annotations

__all__ = ["_Canonical", "_classify_gl_assets"]
from decimal import Decimal
from typing import Any

from app.live.service.fact_derivation import (
    _Canonical,
    _classify_gl_assets,
    _derive_provision_held,
    _LoanRow,
    _PositionRow,
)


def _loan_row(
    ref: str,
    balance_ghs: str,
    *,
    stage: int | None = None,
    attributes: dict[str, Any] | None = None,
) -> _LoanRow:
    row = _PositionRow(
        source_reference=ref,
        source_system="EXCEL_CSV",
        position_type="LOAN",
        currency="GHS",
        balance=Decimal(balance_ghs),
        balance_ghs=Decimal(balance_ghs),
        interest_rate=None,
        rate_type=None,
        contractual_maturity=None,
        next_repricing_date=None,
        ifrs9_stage=stage,
        product_code=None,
        regulatory_category=None,
        counterparty_type=None,
        branch_id=None,
        ecl_ghs=Decimal("0"),
        notional_ghs=Decimal("0"),
        ccf=None,
        attributes=attributes or {},
    )
    return _LoanRow(row=row, category="retail_other", risk_weight_code="RW75")


def test_provision_held_absent_book_derives_nothing_and_reports_skipped() -> None:
    """A book where no loan states a provision yields NO facts + a skipped group
    naming the consequence — coverage is unavailable, never a fabricated 0%."""
    groups: list[Any] = []
    specs = _derive_provision_held(
        [_loan_row("L1", "1000", stage=1), _loan_row("L2", "2000", stage=3)], groups
    )
    assert specs == []
    assert groups[0].group == "provision_held"
    assert groups[0].status == "skipped"
    assert "never a fabricated zero" in (groups[0].note or "")


def test_provision_held_stated_classification_outranks_the_stage_proxy() -> None:
    """An ingested ``bog_classification`` decides the specific/general split even
    when the IFRS 9 stage disagrees; the stage is only the fallback."""
    groups: list[Any] = []
    specs = _derive_provision_held(
        [
            # Stage 1 but classified Sub-standard by the bank: specific.
            _loan_row(
                "L1",
                "1000",
                stage=1,
                attributes={"ecl_provision_ghs": "200", "bog_classification": "Sub-standard"},
            ),
            # Stage 3 but classified OLEM (watch, not NPL): general.
            _loan_row(
                "L2",
                "2000",
                stage=3,
                attributes={"ecl_provision_ghs": "50", "bog_classification": "OLEM"},
            ),
            # No classification stated: the stage-3 proxy makes it specific.
            _loan_row("L3", "3000", stage=3, attributes={"ecl_provision_ghs": "70"}),
            _loan_row("L4", "500", stage=1, attributes={"interest_in_suspense_ghs": "9"}),
        ],
        groups,
    )
    by_category = {spec.category: spec.amount for spec in specs}
    assert by_category == {
        "specific": Decimal("270"),
        "general": Decimal("50"),
        "interest_in_suspense": Decimal("9"),
    }
    assert groups[0].status == "derived"
