"""BoG return sums never add a foreign-currency amount as cedis.

Audit finding I21-1: ``positions.sum`` (BSD2, BSD5A and others) fell back to the
native balance when a foreign-currency position carried no ``balance_ghs``, so a
USD balance was summed as cedis; BSD1 silently took the same row as zero. A row
that cannot be stated in the reporting currency now refuses the cell.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

#: Isolates this file's positions from the rest of the canonical book.
_TAG = {"fixture": "ias21"}
_POSITIONS_SUM: dict[str, object] = {"position_types": ["CASH"], "attribute_eq": _TAG}
_BSD1_DAILY: dict[str, object] = {
    "days_before": 0,
    "position_types": ["CASH"],
    "attribute_eq": _TAG,
}


class _Book:
    def __init__(self, db: Session) -> None:
        materialize_canonical_test_book(db)
        bank = db.get(Bank, SAMPLE_BANK_ID)
        period = db.scalar(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == DEMO_ORG_ID,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert bank is not None and period is not None
        self.db, self.bank, self.period = db, bank, period
        batch = IngestionBatch(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=period.period_end,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=DEMO_ORG_ID,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="ias21-fixture",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        self.batch_id, self.lineage_id = batch.id, lineage.id

    def cash(self, ref: str, currency: str, balance: str, balance_ghs: str | None) -> None:
        common = {
            "organization_id": DEMO_ORG_ID,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": self.period.period_end,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": self.batch_id,
            "lineage_id": self.lineage_id,
            "validation_status": "accepted",
        }
        position = CanonicalPosition(
            **common, source_reference=ref, position_type="CASH", currency=currency
        )
        self.db.add(position)
        self.db.flush()
        attributes: dict[str, str] = dict(_TAG)
        if balance_ghs is not None:
            attributes["balance_ghs"] = balance_ghs
        self.db.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                attributes=attributes,
            )
        )
        self.db.flush()

    def resolver(self, name: str, column: str) -> Callable[[dict[str, object]], object]:
        rc = ResolveContext(
            db=self.db,
            ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
            bank=self.bank,
            period=self.period,
            column=column,
        )
        resolve = get_resolver(name)
        return lambda params: resolve(rc, dict(params))


@pytest.fixture
def book(db_session: Session) -> _Book:
    book = _Book(db_session)
    book.cash("CASH/GHS", "GHS", "5000000", None)
    book.cash("CASH/USD-STATED", "USD", "100000", "1250000.50")
    return book


@pytest.mark.parametrize(
    ("resolver", "params"), [("positions.sum", _POSITIONS_SUM), ("bsd1.daily", _BSD1_DAILY)]
)
def test_stated_foreign_amounts_sum_in_the_reporting_currency(
    book: _Book, resolver: str, params: dict[str, object]
) -> None:
    """IAS 21 ¶23(a) as an input: a foreign-currency row counts at the bank's stated
    reporting-currency amount, exactly (Decimal, not float)."""
    assert book.resolver(resolver, "total")(params) == Decimal("6250000.50")


@pytest.mark.parametrize(
    ("resolver", "params"), [("positions.sum", _POSITIONS_SUM), ("bsd1.daily", _BSD1_DAILY)]
)
def test_foreign_currency_row_without_reporting_amount_is_refused_in_return_sums(
    book: _Book, resolver: str, params: dict[str, object]
) -> None:
    """IAS 21 ¶23(a) as an input: a USD row with no ``balance_ghs`` is neither summed
    at its native amount as cedis nor dropped as zero; the cell refuses and names it."""
    book.cash("CASH/USD-UNSTATED", "USD", "200000", None)

    with pytest.raises(NotComputable) as refused:
        book.resolver(resolver, "total")(params)

    assert "1 foreign-currency position(s) in USD carry no balance_ghs" in str(refused.value)


def test_a_cell_that_excludes_the_unstated_row_still_resolves(book: _Book) -> None:
    """The refusal is the cell's own: the Domestic column never reads the USD row."""
    book.cash("CASH/USD-UNSTATED", "USD", "200000", None)

    assert book.resolver("positions.sum", "domestic")(_POSITIONS_SUM) == Decimal("5000000")
