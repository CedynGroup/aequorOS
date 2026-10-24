"""Missing official inputs stay missing even when the live book is populated."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankFinancialFact, BankReportingPeriod, CurrentFinancialFact
from app.reporting.service.bog_forms.catalog import form_spec
from app.reporting.service.bog_forms.engine import compute_form
from app.reporting.service.bog_forms.sources import ResolveContext, get_resolver
from tests.support.helpers import ORG_1, USER_1

REPORTING_DATE = date(2026, 3, 31)


@pytest.fixture
def live_book(db_session: Session) -> ResolveContext:
    bank = Bank(
        id="BK-FCT00001",
        organization_id=ORG_1,
        name="Synthetic Fact Source Bank",
        short_name="Fact Source",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    period = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=bank.id,
        period_start=date(2026, 3, 1),
        period_end=REPORTING_DATE,
        label="March 2026",
        status="open",
    )
    db_session.add(period)
    db_session.add(
        CurrentFinancialFact(
            organization_id=ORG_1,
            bank_id=bank.id,
            source_as_of_date=REPORTING_DATE,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            currency="GHS",
            amount=Decimal("2000000"),
        )
    )
    db_session.flush()
    return ResolveContext(
        db_session,
        TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank,
        period,
        "domestic",
    )


def _official_cash(rc: ResolveContext, amount: str) -> None:
    rc.db.add(
        BankFinancialFact(
            organization_id=rc.ctx.organization_id,
            bank_id=rc.bank.id,
            reporting_period_id=rc.period.id,
            fact_group="balance_sheet",
            category="cash_vault",
            currency=rc.bank.currency,
            amount=Decimal(amount),
        )
    )
    rc.db.flush()


def test_live_cash_does_not_invent_an_official_zero(live_book: ResolveContext) -> None:
    assert (
        get_resolver("facts.sum")(
            live_book, {"group": "balance_sheet", "categories": ["cash_vault"]}
        )
        is None
    )


@pytest.mark.parametrize("amount", ["0", "2000000"])
def test_stated_official_cash_including_zero_is_mapped(
    live_book: ResolveContext, amount: str
) -> None:
    _official_cash(live_book, amount)
    result = compute_form(
        live_book.db, live_book.ctx, live_book.bank, live_book.period, form_spec("BSD2")
    )
    cash = next(line for line in result.lines if line.sheet == "BSD2" and line.cell == "B14")
    assert cash.status == "mapped"
    assert cash.value == float(amount)


@pytest.mark.parametrize("column", ["domestic", "foreign", "total"])
def test_absent_category_stays_missing_in_each_currency_column(
    live_book: ResolveContext, column: str
) -> None:
    _official_cash(live_book, "2000000")
    assert (
        get_resolver("facts.sum")(
            replace(live_book, column=column),
            {"group": "balance_sheet", "categories": ["bog_required_reserves"]},
        )
        is None
    )


def test_another_reporting_date_cannot_supply_the_official_cash(
    live_book: ResolveContext,
) -> None:
    prior = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=live_book.bank.id,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        label="February 2026",
        status="open",
    )
    live_book.db.add(prior)
    live_book.db.flush()
    _official_cash(replace(live_book, period=prior), "9000000")
    assert (
        get_resolver("facts.sum")(
            live_book, {"group": "balance_sheet", "categories": ["cash_vault"]}
        )
        is None
    )


def test_currency_filter_does_not_fill_missing_foreign_cash_with_zero(
    live_book: ResolveContext,
) -> None:
    _official_cash(live_book, "2000000")
    assert (
        get_resolver("facts.sum")(
            replace(live_book, column="foreign"),
            {"group": "balance_sheet", "categories": ["cash_vault"]},
        )
        is None
    )
    assert get_resolver("facts.sum")(
        live_book, {"group": "balance_sheet", "categories": ["cash_vault"], "sign": -1}
    ) == Decimal("-2000000")


@pytest.mark.parametrize("cash_first", [True, False])
def test_maturity_total_does_not_hide_a_missing_fact_with_a_partial_sum(
    live_book: ResolveContext, cash_first: bool
) -> None:
    _official_cash(live_book, "2000000")
    categories = ["cash_vault", "bog_required_reserves"]
    if not cash_first:
        categories.reverse()
    components = [
        {
            "source": "facts.sum",
            "params": {"group": "balance_sheet", "categories": [category]},
            "bucket": "overdue",
        }
        for category in categories
    ]
    assert (
        get_resolver("bsd6.bucket")(
            replace(live_book, column="total"),
            {"side": "asset", "bsd2_column": "domestic", "components": components},
        )
        is None
    )


@pytest.mark.parametrize(
    ("code", "sheet", "cell"),
    [
        ("BSD2", "BSD2", "B14"),
        ("BSD5A", "CAR FORMAT", "E29"),
        ("BSD5A", "CAR FORMAT", "E56"),
        ("BSD6", "BSD6A", "C7"),
    ],
)
def test_return_cells_preserve_missing_official_sources(
    live_book: ResolveContext, code: str, sheet: str, cell: str
) -> None:
    result = compute_form(
        live_book.db, live_book.ctx, live_book.bank, live_book.period, form_spec(code)
    )
    value = next(line for line in result.lines if line.sheet == sheet and line.cell == cell)
    assert value.status == "input_required"
    assert value.value is None
