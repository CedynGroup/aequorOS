"""BoG return sums never add a foreign-currency amount as cedis.

Audit finding I21-1: ``positions.sum`` (BSD2, BSD5A and others) fell back to the
native balance when a foreign-currency position carried no ``balance_ghs``, so a
USD balance was summed as cedis; BSD1 silently took the same row as zero. A row
that cannot be stated in the reporting currency now refuses the cell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast
from uuid import UUID

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
    CanonicalFxRate,
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
from app.services.regulatory_reporting.bog_forms.sources_ext.bsd8 import load_loans
from app.services.sdi_views import get_liquidity_monitoring
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


class _ExposureRow(Protocol):
    currency: str
    notional_ghs: Decimal | None
    undrawn_ccf_ghs: Decimal
    balance_ghs: Decimal
    counterparty_id: UUID | None


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


@pytest.fixture
def dated_spots(book: _Book) -> None:
    rates = {
        book.period.period_end - timedelta(days=offset): rate
        for offset, rate in ((0, "14"), (1, "13"), (7, "12"), (8, "11"))
    }
    rates[book.period.period_start - timedelta(days=1)] = "10"
    for day, rate in rates.items():
        book.db.add(
            CanonicalFxRate(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                as_of_date=day,
                source_system="EXCEL_CSV",
                ingestion_batch_id=book.batch_id,
                lineage_id=book.lineage_id,
                validation_status="accepted",
                source_reference=f"FX/USD/{day.isoformat()}",
                base_currency="USD",
                quote_currency="GHS",
                rate_type="spot",
                rate=Decimal(rate),
            )
        )
    book.db.flush()


@pytest.mark.usefixtures("dated_spots")
@pytest.mark.parametrize(("column", "offset"), [("wed", 0), ("tue", 1)])
def test_bsd1_values_daily_and_weekly_changes_at_each_business_date(
    book: _Book, column: str, offset: int
) -> None:
    for days_before in (offset, offset + 7):
        snapshot = book.position(f"DEPOSIT/DAY-{days_before}", "DEPOSIT", {"fixture": "dated"})
        snapshot.as_of_date = book.period.period_end - timedelta(days=days_before)
    book.db.flush()
    daily = book.resolver("bsd1.daily", column)
    params: dict[str, object] = {
        "position_types": ["DEPOSIT"],
        "attribute_eq": {"fixture": "dated"},
        "currency": "USD",
    }
    current = Decimal("1400000") if offset == 0 else Decimal("1300000")
    previous = Decimal("1200000") if offset == 0 else Decimal("1100000")

    assert daily(params) == current
    assert daily({**params, "week": "previous"}) == previous
    assert daily({**params, "days_before": offset + 7}) == previous
    assert daily({**params, "week_change": True}) == Decimal("200000")
    assert daily({**params, "week_change": True, "sign": -1}) == Decimal("-200000")


@pytest.mark.usefixtures("dated_spots")
def test_bsd8_values_opening_and_closing_balances_at_their_cutoff(book: _Book) -> None:
    snapshot = book.position("LOAN/HISTORICAL-FX", "LOAN", {})
    snapshot.as_of_date = book.period.period_start - timedelta(days=2)
    book.db.flush()
    rc = ResolveContext(
        db=book.db,
        ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
        bank=book.bank,
        period=book.period,
        column="current",
    )

    closing = next(loan for loan in load_loans(rc) if loan.snapshot.id == snapshot.id)
    opening = next(loan for loan in load_loans(rc, "previous") if loan.snapshot.id == snapshot.id)
    assert closing.amount_ghs == Decimal("1400000")
    assert opening.amount_ghs == Decimal("1000000")


@pytest.mark.usefixtures("dated_spots")
@pytest.mark.parametrize("ghs_attr", ["balance_ghs", "notional_ghs"])
def test_reporting_currency_conversion_caches_quotes_by_valuation_date(
    book: _Book, ghs_attr: str
) -> None:
    snapshot = book.position("POSITION/HISTORICAL-FX", "LOAN", {})
    snapshot.notional = Decimal("100000")
    position = book.db.get(CanonicalPosition, snapshot.position_id)
    assert position is not None
    rc = ResolveContext(
        db=book.db,
        ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
        bank=book.bank,
        period=book.period,
        column="total",
    )
    prior_day = book.period.period_end - timedelta(days=7)
    expected_by_date: tuple[tuple[date | None, Decimal], ...] = (
        (None, Decimal("1400000")),
        (prior_day, Decimal("1200000")),
        (book.period.period_end, Decimal("1400000")),
        (prior_day, Decimal("1200000")),
    )
    for valuation_date, expected in expected_by_date:
        assert (
            reporting_currency_value(
                rc, snapshot, position, ghs_attr=ghs_attr, valuation_date=valuation_date
            )
            == expected
        )


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
    attributes = {"notional_ghs": "1000000"} if currency == "GHS" else {"notional_ghs": "1000000"}
    snapshot = book.position("LC/STATED", "LC_GUARANTEE", attributes, currency=currency)
    if currency == "USD":
        snapshot.balance = Decimal("0")
        snapshot.notional = Decimal("200000")
    book.db.flush()

    assert book.resolver("bsd11.register", "off_balance")(
        {"register": "large_exposures", "rank": 1}
    ) == Decimal("1000000")


@pytest.mark.parametrize("position_type", ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"])
@pytest.mark.parametrize(
    ("resolver", "column", "params"),
    [
        ("positions.sum", "total", {"position_types": ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"]}),
        (
            "positions.sum",
            "foreign",
            {"position_types": ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"], "measure": "notional"},
        ),
        (
            "bsd1.daily",
            "total",
            {"days_before": 0, "position_types": ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"]},
        ),
        ("bsd3.rank", "total", {"kind": "non_monetary_exposure", "rank": 1, "field": "total"}),
        ("bsd11.register", "off_balance", {"register": "large_exposures", "rank": 1}),
        (
            "bsd6.bucket",
            "total",
            {
                "bsd2_column": "foreign",
                "side": "liability",
                "components": [
                    {
                        "source": "positions.sum",
                        "params": {"position_types": ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"]},
                    }
                ],
            },
        ),
    ],
)
@pytest.mark.usefixtures("no_spot")
def test_off_balance_consumers_use_notional_without_requiring_a_drawn_balance(
    book: _Book, position_type: str, resolver: str, column: str, params: dict[str, object]
) -> None:
    snapshot = book.position("OBS/USD", position_type, {"notional_ghs": "123456789.50"})
    snapshot.notional = Decimal("100000")
    book.db.flush()
    # Isolate sums from LC/1 while retaining the ordinary fixture for roster loaders.
    if resolver in ("positions.sum", "bsd1.daily"):
        params = {**params, "attribute_eq": {"notional_ghs": "123456789.50"}}
    elif resolver == "bsd6.bucket":
        params = {
            **params,
            "components": [
                {
                    "source": "positions.sum",
                    "params": {
                        "position_types": [position_type],
                        "attribute_eq": {"notional_ghs": "123456789.50"},
                    },
                }
            ],
        }
    assert book.resolver(resolver, column)(params) == Decimal("123456789.50")

    snapshot.attributes = {}
    book.db.flush()
    # The same row is now unmeasurable; do not filter it out by its removed attribute.
    if resolver in ("positions.sum", "bsd1.daily"):
        params = {**params, "attribute_eq": {}}
    elif resolver == "bsd6.bucket":
        params = {
            **params,
            "components": [
                {"source": "positions.sum", "params": {"position_types": [position_type]}}
            ],
        }
    with pytest.raises((NotComputable, HTTPException)) as refused:
        book.resolver(resolver, column)(params)
    detail = str(refused.value)
    assert "OBS/USD" in detail
    assert "notional_ghs" in detail
    assert "Ingest" in detail


@pytest.mark.parametrize("position_type", ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"])
@pytest.mark.usefixtures("no_spot")
def test_liquidity_monitoring_accepts_stated_off_balance_notional_and_refuses_unstated(
    book: _Book, position_type: str
) -> None:
    snapshot = book.position("OBS/MONITOR", position_type, {"notional_ghs": "2000000"})
    snapshot.notional = Decimal("100000")
    book.db.flush()
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    monitoring = get_liquidity_monitoring(book.db, ctx, book.bank, book.period.period_end)
    assert monitoring.as_of == book.period.period_end
    assert monitoring.maturity_ladder

    snapshot.attributes = {}
    book.db.flush()
    with pytest.raises(HTTPException) as refused:
        get_liquidity_monitoring(book.db, ctx, book.bank, book.period.period_end)
    assert refused.value.status_code == 409
    detail = cast(dict[str, str], refused.value.detail)
    assert detail["error_code"] == "foreign_amount_not_stated"
    assert "OBS/MONITOR (notional_ghs)" in detail["message"]


@pytest.mark.parametrize("position_type", ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"])
def test_off_balance_consumers_can_use_a_governed_notional_quote(
    book: _Book, monkeypatch: pytest.MonkeyPatch, position_type: str
) -> None:
    snapshot = book.position("OBS/QUOTED", position_type, {"fixture": "quoted_obs"})
    snapshot.notional = Decimal("10000000")
    book.db.flush()
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
    params: dict[str, object] = {
        "position_types": [position_type],
        "attribute_eq": {"fixture": "quoted_obs"},
    }
    assert book.resolver("positions.sum", "foreign")(params) == Decimal("120000000")
    assert book.resolver("bsd1.daily", "total")({**params, "days_before": 0}) == Decimal(
        "120000000"
    )
    assert book.resolver("bsd6.bucket", "total")(
        {
            "bsd2_column": "foreign",
            "side": "liability",
            "components": [{"source": "positions.sum", "params": params}],
        }
    ) == Decimal("120000000")
    assert book.resolver("bsd3.rank", "total")(
        {"kind": "non_monetary_exposure", "rank": 1, "field": "total"}
    ) == Decimal("120000000")
    assert book.resolver("bsd11.register", "off_balance")(
        {"register": "large_exposures", "rank": 1}
    ) == Decimal("120000000")


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("currencies", [("USD",), ("USD", "EUR")])
def test_canonical_loader_looks_up_each_foreign_notional_quote_once(
    book: _Book,
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    currencies: tuple[str, ...],
) -> None:
    for currency in currencies:
        for index in range(20):
            snapshot = book.position(
                f"OBS/{currency}/{index}",
                "LC_GUARANTEE" if index % 2 == 0 else "COMMITMENT_UNDRAWN",
                {"credit_conversion_factor": "0.5"},
                currency=currency,
            )
            snapshot.notional = Decimal("100000")
    book.db.flush()
    calls: list[tuple[str, str, date]] = []

    def quoted_spot(*args: object) -> FxRateView | None:
        currency, base, as_of = cast(tuple[str, str, date], args[3:])
        calls.append((currency, base, as_of))
        if not available:
            return None
        return FxRateView(
            base_currency=currency,
            quote_currency=base,
            rate=Decimal("12" if currency == "USD" else "14"),
            as_of_date=as_of,
            attribution=SourceAttribution(
                source_system="EXCEL_CSV",
                ingestion_batch_id=book.batch_id,
                ingested_at=datetime(2026, 3, 31, tzinfo=UTC),
                stale=False,
                age_seconds=0,
            ),
        )

    monkeypatch.setattr("app.market_data.public.preferred_fx_spot", quoted_spot)
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    rc = ResolveContext(db=book.db, ctx=ctx, bank=book.bank, period=book.period, column="total")
    resolve = get_resolver("bsd3.rank")
    params: dict[str, object] = {"kind": "non_monetary_exposure", "rank": 1, "field": "total"}
    if available:
        resolve(rc, params)
        rows = [
            row
            for row in cast(list[_ExposureRow], rc.cache["bsd3:rows"])
            if row.counterparty_id == book.counterparty_id
        ]
        assert [(row.currency, row.notional_ghs, row.undrawn_ccf_ghs) for row in rows] == [
            (
                currency,
                Decimal("1200000" if currency == "USD" else "1400000"),
                Decimal("600000" if currency == "USD" else "700000"),
            )
            for currency in sorted(currencies)
            for _ in range(20)
        ]
        assert all(row.balance_ghs == 0 for row in rows)
    else:
        with pytest.raises(HTTPException) as refused:
            resolve(rc, params)
        assert refused.value.status_code == 409
        detail = cast(dict[str, str], refused.value.detail)
        assert detail["error_code"] == "foreign_amount_not_stated"
        for currency in currencies:
            for index in range(20):
                assert f"OBS/{currency}/{index} (notional_ghs)" in detail["message"]
        assert "Ingest" in detail["message"]

    assert sorted(calls) == [
        (currency, book.bank.currency, book.period.period_end) for currency in sorted(currencies)
    ]


@pytest.mark.parametrize("position_type", ["LC_GUARANTEE", "COMMITMENT_UNDRAWN"])
@pytest.mark.parametrize(
    "amounts",
    [
        (None, None, "600000", "600000", "600000"),
        (None, None, "700000", "700000", "600000"),
        (None, None, None, "600000", "600000"),
        ("200000", None, "700000", "200000", "200000"),
        ("0", None, "700000", "0", "0"),
        (None, "0", "700000", "0", "0"),
        ("200000", "300000", "700000", "300000", "300000"),
        (None, "300000", "700000", "300000", "300000"),
    ],
)
def test_domestic_off_balance_exposures_preserve_each_returns_balance_fallback(
    book: _Book,
    position_type: str,
    amounts: tuple[str | None, str | None, str | None, str, str],
) -> None:
    notional, notional_ghs, balance_ghs, expected, bsd8_expected = amounts
    attributes: dict[str, str] = {}
    if notional_ghs is not None:
        attributes["notional_ghs"] = notional_ghs
    if balance_ghs is not None:
        attributes["balance_ghs"] = balance_ghs
    snapshot = book.position("OBS/DOMESTIC", position_type, attributes, currency="GHS")
    snapshot.balance = Decimal("600000")
    snapshot.notional = Decimal(notional) if notional is not None else None
    book.db.flush()

    assert book.resolver("bsd3.rank", "total")(
        {"kind": "non_monetary_exposure", "rank": 1, "field": "total"}
    ) == Decimal(expected)
    assert book.resolver("bsd11.register", "off_balance")(
        {"register": "large_exposures", "rank": 1}
    ) == Decimal(expected)

    book.position("LOAN/ADVERSE", "LOAN", {"bog_classification": "doubtful"}, currency="GHS")
    assert book.resolver("bsd8.annexure", "obs")({"rank": 1}) == Decimal(bsd8_expected)


@pytest.mark.parametrize("position_type", ["LOAN", "LC_GUARANTEE", "COMMITMENT_UNDRAWN"])
@pytest.mark.parametrize("notional", [None, "0", "200000"])
@pytest.mark.parametrize("stated", [False, True])
def test_domestic_sums_and_buckets_preserve_their_requested_measures(
    book: _Book, position_type: str, notional: str | None, stated: bool
) -> None:
    attributes = {"fixture": "domestic_measure"}
    if stated:
        attributes.update(balance_ghs="700000", notional_ghs="300000")
    snapshot = book.position("POSITION/DOMESTIC", position_type, attributes, currency="GHS")
    snapshot.balance = Decimal("600000")
    snapshot.notional = Decimal(notional) if notional is not None else None
    book.db.flush()
    params: dict[str, object] = {
        "position_types": [position_type],
        "attribute_eq": {"fixture": "domestic_measure"},
    }
    native_notional = Decimal(notional) if notional is not None else Decimal("0")
    assert book.resolver("positions.sum", "domestic")(params) == Decimal(
        "700000" if stated else "600000"
    )
    assert book.resolver("positions.sum", "domestic")({**params, "measure": "notional"}) == (
        Decimal("300000") if stated else native_notional
    )
    assert book.resolver("bsd1.daily", "total")({**params, "days_before": 0}) == Decimal("600000")
    for measure, expected in (("balance", Decimal("600000")), ("notional", native_notional)):
        assert (
            book.resolver("bsd6.bucket", "total")(
                {
                    "bsd2_column": "domestic",
                    "side": "liability",
                    "components": [
                        {"source": "positions.sum", "params": {**params, "measure": measure}}
                    ],
                }
            )
            == expected
        )


@pytest.mark.parametrize("position_type", ["CASH", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("currency", ["GHS", "USD"])
def test_bsd1_native_balances_preserve_the_official_population_measure(
    book: _Book, position_type: str, currency: str
) -> None:
    snapshot = book.position(
        "POSITION/NATIVE",
        position_type,
        {"fixture": "native_measure", "balance_ghs": "700000"},
        currency=currency,
    )
    snapshot.balance = Decimal("600000")
    book.db.flush()
    assert book.resolver("bsd1.daily", "total")(
        {
            "days_before": 0,
            "position_types": [position_type],
            "measure": "native",
            "attribute_eq": {"fixture": "native_measure"},
        }
    ) == Decimal("600000")
