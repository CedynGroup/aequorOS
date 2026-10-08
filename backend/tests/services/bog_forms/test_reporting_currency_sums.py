"""BoG return sums never add a foreign-currency amount as cedis.

Audit finding I21-1: ``positions.sum`` (BSD2, BSD5A and others) fell back to the
native balance when a foreign-currency position carried no ``balance_ghs``, so a
USD balance was summed as cedis; BSD1 silently took the same row as zero. A row
that cannot be stated in the reporting currency now refuses the cell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable, OutcomeState, outcome
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    RegulatoryPackage,
)
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services.market_data import FxRateView, SourceAttribution
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.bog_forms.catalog import form_spec
from app.services.regulatory_reporting.bog_forms.engine import compute_form
from app.services.regulatory_reporting.bog_forms.sources import (
    ResolveContext,
    Resolver,
    get_resolver,
    reporting_currency_value,
)
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

pytestmark = pytest.mark.usefixtures("return_generation_authority")


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
        counterparty = CanonicalCounterparty(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            as_of_date=period.period_end,
            source_system="EXCEL_CSV",
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
            validation_status="accepted",
            source_reference="CP/IAS21",
            name="Reporting Currency Borrower",
            counterparty_type="CORPORATE",
            resident=True,
        )
        db.add(counterparty)
        db.flush()
        self.counterparty_id = counterparty.id

    def cash(
        self, ref: str, currency: str, balance: str, balance_ghs: str | None
    ) -> CanonicalPositionSnapshot:
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
        snapshot = CanonicalPositionSnapshot(
            **common,
            source_reference=ref,
            position_id=position.id,
            balance=Decimal(balance),
            attributes=attributes,
        )
        self.db.add(snapshot)
        self.db.flush()
        return snapshot

    def position(
        self, ref: str, position_type: str, attributes: dict[str, str], currency: str = "USD"
    ) -> CanonicalPositionSnapshot:
        snapshot = self.cash(ref, currency, "100000", None)
        position = self.db.get(CanonicalPosition, snapshot.position_id)
        assert position is not None
        position.position_type = position_type
        snapshot.attributes = attributes
        snapshot.ifrs9_stage = 1
        snapshot.interest_rate = Decimal("0.10")
        snapshot.deposit_account_type = "SAVINGS" if position_type == "DEPOSIT" else None
        snapshot.counterparty_id = self.counterparty_id
        self.db.flush()
        return snapshot

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


@pytest.fixture
def no_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_spot(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.market_data.public.preferred_fx_spot", missing_spot)


@pytest.mark.parametrize(
    ("code", "position_type", "previous"),
    [
        ("BSD4", "LOAN", False),
        ("BSD8", "LOAN", False),
        ("BSD8", "LOAN", True),
        ("BSD8", "LC_GUARANTEE", False),
        ("BSD8", "COMMITMENT_UNDRAWN", False),
        ("BSD14", "LOAN", False),
        ("BSD14", "DEPOSIT", False),
        ("BSD11", "LOAN", False),
        ("BSD11", "LC_GUARANTEE", False),
    ],
)
@pytest.mark.usefixtures("no_spot")
def test_return_loaders_refuse_unstated_foreign_amounts(
    book: _Book, code: str, position_type: str, previous: bool
) -> None:
    snapshot = book.position(
        "POSITION/UNSTATED",
        position_type,
        {"sector": "agriculture.cocoa_production", "bog_classification": "loss"},
    )
    missing = "balance_ghs"
    if previous:
        snapshot.as_of_date = book.period.period_start - timedelta(days=1)
    if position_type in ("LC_GUARANTEE", "COMMITMENT_UNDRAWN"):
        snapshot.balance = Decimal("0")
        snapshot.notional = Decimal("100000")
        snapshot.attributes = {"balance_ghs": "0"}
        missing = "notional_ghs"
        if code == "BSD8":
            book.position(
                "LOAN/STATED",
                "LOAN",
                {"balance_ghs": "1000000", "bog_classification": "loss"},
            )
    book.db.flush()
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)

    with pytest.raises(HTTPException) as refused:
        compute_form(book.db, ctx, book.bank, book.period, form_spec(code))

    assert refused.value.status_code == 409
    detail = cast(dict[str, str], refused.value.detail)
    assert detail["error_code"] == "foreign_amount_not_stated"
    assert "POSITION/UNSTATED" in detail["message"]
    assert f"Ingest {missing}" in detail["message"]


@pytest.mark.parametrize("missing", [{}, {"notional_ghs": None}, {"notional_ghs": ""}])
@pytest.mark.usefixtures("no_spot")
def test_usd_contingent_without_cedi_notional_blocks_bsd2_package(
    db_session: Session, missing: dict[str, str | None]
) -> None:
    book = _Book(db_session)
    snapshot = book.position("LC/USD", "LC_GUARANTEE", {"balance_ghs": "0"})
    snapshot.balance = Decimal("0")
    snapshot.notional = Decimal("100000")
    snapshot.attributes = {"balance_ghs": "0", **missing}
    db_session.flush()
    before = set(db_session.scalars(select(RegulatoryPackage.id)))
    ctx = TenantContext(
        organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
    )

    with pytest.raises(HTTPException) as refused:
        generation.generate_package(
            db_session,
            ctx,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="BSD2", reporting_date=book.period.period_end),
        )

    assert refused.value.status_code == 409
    detail = cast(dict[str, str], refused.value.detail)
    assert detail["error_code"] == "foreign_amount_not_stated"
    assert "LC/USD" in detail["message"]
    assert "Ingest notional_ghs" in detail["message"]
    assert set(db_session.scalars(select(RegulatoryPackage.id))) == before


def test_preexisting_not_computable_cells_remain_input_required(
    book: _Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_computable(_rc: ResolveContext, _params: dict[str, Any]) -> None:
        raise NotComputable(
            outcome(
                OutcomeState.POLICY_UNRESOLVED,
                metric_id="existing_metric",
                reason="Existing policy is unresolved",
            )
        )

    def resolve(_name: str) -> Resolver:
        return not_computable

    monkeypatch.setattr("app.services.regulatory_reporting.bog_forms.engine.get_resolver", resolve)
    spec = form_spec("BSD2")
    sheet = spec.sheets[0]
    line = next(line for line in sheet.lines if line.source == "positions.sum")
    spec = replace(spec, sheets=(replace(sheet, lines=(line,)),))
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    result = compute_form(book.db, ctx, book.bank, book.period, spec)

    assert result.lines
    assert all(line.status == "input_required" and line.value is None for line in result.lines)
    assert len(result.errors) == len(result.lines)
    assert all("Existing policy is unresolved" in error for error in result.errors)


@pytest.mark.parametrize("ghs_attr", ["balance_ghs", "notional_ghs"])
@pytest.mark.parametrize("stated", [None, "0", "1250000.50"])
def test_shared_conversion_preserves_stated_amounts_and_uses_governed_quotes(
    book: _Book, monkeypatch: pytest.MonkeyPatch, ghs_attr: str, stated: str | None
) -> None:
    snapshot = book.position("POSITION/QUOTED", "LOAN", {})
    snapshot.notional = Decimal("200000")
    if stated is not None:
        snapshot.attributes = {ghs_attr: stated}
    position = book.db.get(CanonicalPosition, snapshot.position_id)
    assert position is not None
    quote = FxRateView(
        base_currency="USD",
        quote_currency="GHS",
        rate=Decimal("12"),
        as_of_date=book.period.period_end,
        attribution=SourceAttribution(
            source_system="EXCEL_CSV",
            ingestion_batch_id=book.batch_id,
            ingested_at=datetime(2026, 3, 31, tzinfo=UTC),
            stale=False,
            age_seconds=0,
        ),
    )

    def quoted_spot(*_args: object, **_kwargs: object) -> FxRateView:
        return quote

    monkeypatch.setattr("app.market_data.public.preferred_fx_spot", quoted_spot)
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    rc = ResolveContext(db=book.db, ctx=ctx, bank=book.bank, period=book.period, column="total")
    expected = (
        Decimal(stated)
        if stated is not None
        else Decimal("2400000")
        if ghs_attr == "notional_ghs"
        else Decimal("1200000")
    )

    assert reporting_currency_value(rc, snapshot, position, ghs_attr=ghs_attr) == expected


@pytest.mark.usefixtures("no_spot")
@pytest.mark.parametrize("currency", ["GHS", "USD"])
def test_bsd11_preserves_stated_contingent_amounts(book: _Book, currency: str) -> None:
    attributes = (
        {"balance_ghs": "1000000"}
        if currency == "GHS"
        else {"balance_ghs": "0", "notional_ghs": "1000000"}
    )
    snapshot = book.position("LC/STATED", "LC_GUARANTEE", attributes, currency=currency)
    if currency == "USD":
        snapshot.balance = Decimal("0")
        snapshot.notional = Decimal("200000")
    book.db.flush()

    assert book.resolver("bsd11.register", "off_balance")(
        {"register": "large_exposures", "rank": 1}
    ) == Decimal("1000000")
