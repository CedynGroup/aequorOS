"""``bi_fact_gl_monthly`` mirrors BSD7's own P&L semantics (D-021), and R4 proves it.

A small INCOME/EXPENSE ledger is inserted beside the canonical fixture — the
shape ``tests/services/bog_forms/test_bsd7.py`` uses, moved to the fixture's
fiscal year — with every case the h_b5 contract names: a year-to-date account
with a prior month (movement = difference), one booked only this month
(``missing_prior``, never a fabricated month), a foreign-currency account, a
``period``-basis account fed weekly (H-006: the mid-month row is a month-to-date
figure superseded by the month-end one), a register-mapped account with a
negative sign, an unmapped account, a prior-year generation and a superseded
generation (never read). The mart's per-line sums are then compared with the
``bsd7.pl_line`` resolver itself — exact equality, which is what R4 grades.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.domain.bi import extract
from app.domain.gl import pl_mapping
from app.models import (
    Bank,
    BankReportingPeriod,
    BiDimGlAccount,
    BiFactGlMonthly,
    BiMartBuild,
    BiReconciliationResult,
    CanonicalReferenceRow,
)
from app.models.canonical import CanonicalGlAccount
from app.services.bi import reconciliation
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.api.helpers import ORG_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_mart_builder import AS_OF, CTX, MID_MONTH, build, new_batch, seed_book

M = 1_000_000
_APR, _MAY, _JUN = date(2026, 4, 30), date(2026, 5, 31), AS_OF

#: (account_code, class, own tag, currency, {as_of: balance})
_LEDGER: tuple[tuple[str, str, str | None, str | None, dict[date, int]], ...] = (
    ("4001", "INCOME", "1a", "GHS", {_APR: 100, _MAY: 210, _JUN: 330}),
    ("4002", "INCOME", "1c", None, {_JUN: 150}),  # booked only this month
    ("4101", "INCOME", "5", "USD", {_MAY: 20, _JUN: 30}),
    ("5301", "EXPENSE", None, "GHS", {_APR: 400, MID_MONTH: 200, _JUN: 550}),  # period basis
    ("5302", "EXPENSE", None, "GHS", {_MAY: 50, _JUN: 80}),  # register prefix, sign -1
    ("6001", "EXPENSE", None, "GHS", {_JUN: 7}),  # mapped nowhere
    ("4001", "INCOME", "1a", "GHS", {date(2025, 12, 31): 9_999}),  # prior fiscal year
)
_REGISTER: tuple[dict[str, Any], ...] = (
    {"gl_account_code": "5301", "bsd7_item": "16", "balance_basis": "period"},
    {"gl_prefix": "53", "bsd7_item": "17", "sign": "-1"},
)


def _seed_ledger(db: Session) -> None:
    common = new_batch(db, AS_OF)
    for code, account_class, tag, currency, balances in _LEDGER:
        for as_of, balance in balances.items():
            db.add(
                CanonicalGlAccount(
                    **{**common, "as_of_date": as_of},
                    source_reference=f"GL/{code}/{as_of.isoformat()}",
                    account_code=code,
                    name=f"P&L {code}",
                    account_class=account_class,
                    currency=currency,
                    balance=Decimal(balance * M),
                    attributes={pl_mapping.LINE_ATTRIBUTE: tag} if tag else {},
                )
            )
    # a superseded June generation of 4001: never read by the return, never by the mart
    db.add(
        CanonicalGlAccount(
            **common,
            source_reference="GL/4001/superseded",
            account_code="4001",
            name="P&L 4001",
            account_class="INCOME",
            currency="GHS",
            balance=Decimal(9_999 * M),
            attributes={pl_mapping.LINE_ATTRIBUTE: "1a"},
            superseded_by=uuid4(),
        )
    )
    for index, payload in enumerate(_REGISTER):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=common["ingestion_batch_id"],
                as_of_date=AS_OF,
                dataset_kind=pl_mapping.MAPPING_KIND,
                row_index=index,
                payload=payload,
                source_reference=f"coa#{index}",
                lineage_id=common["lineage_id"],
            )
        )
    db.flush()


def _rows(db: Session) -> dict[tuple[str, str], BiFactGlMonthly]:
    return {
        (row.gl_account_code, row.currency): row
        for row in db.scalars(
            select(BiFactGlMonthly).where(BiFactGlMonthly.bank_id == SAMPLE_BANK_ID)
        )
    }


def test_gl_monthly_rows_follow_bsd7s_ytd_and_period_rules(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    db_session.commit()
    outcome = build(db_session)
    assert outcome.row_counts["bi_fact_gl_monthly"] == 6
    rows = _rows(db_session)
    assert set(rows) == {
        ("4001", ""),
        ("4002", ""),
        ("4101", "USD"),
        ("5301", ""),
        ("5302", ""),
        ("6001", ""),
    }
    assert {row.month_end for row in rows.values()} == {_JUN}
    assert {row.calendar_month for row in rows.values()} == {date(2026, 6, 1)}

    loans_interest = rows[("4001", "")]
    assert (loans_interest.ytd_rc, loans_interest.prior_ytd_rc, loans_interest.movement_rc) == (
        Decimal(330 * M),
        Decimal(210 * M),
        Decimal(120 * M),
    )
    assert (loans_interest.missing_prior, loans_interest.balance_basis) == (False, "ytd")
    assert (loans_interest.pl_line, loans_interest.pl_sign, loans_interest.account_class) == (
        "1a",
        1,
        "INCOME",
    )

    only_this_month = rows[("4002", "")]
    assert only_this_month.ytd_rc == Decimal(150 * M)
    assert only_this_month.prior_ytd_rc is None and only_this_month.movement_rc is None
    assert only_this_month.missing_prior is True  # honestly blank, never the YTD as a month
    assert only_this_month.currency == ""  # an unstated currency IS the reporting currency

    fees_usd = rows[("4101", "USD")]
    assert (fees_usd.ytd_rc, fees_usd.movement_rc, fees_usd.pl_line) == (
        Decimal(30 * M),
        Decimal(10 * M),
        "5",
    )

    occupancy = rows[("5301", "")]
    assert occupancy.balance_basis == "period"
    # H-006: Apr 400 + Jun 550 — the 15-June 200 is superseded by the month-end row
    assert (occupancy.ytd_rc, occupancy.prior_ytd_rc, occupancy.movement_rc) == (
        Decimal(950 * M),
        Decimal(400 * M),
        Decimal(550 * M),
    )
    assert occupancy.missing_prior is False and occupancy.pl_line == "16"

    signed = rows[("5302", "")]
    assert (signed.ytd_rc, signed.prior_ytd_rc, signed.movement_rc) == (
        Decimal(80 * M),
        Decimal(50 * M),
        Decimal(30 * M),
    )
    assert (signed.pl_line, signed.pl_sign) == (
        "17",
        -1,
    )  # the ledger's balance, the mapping's sign

    unmapped = rows[("6001", "")]
    assert unmapped.pl_line is None and unmapped.pl_sign is None
    assert unmapped.ytd_rc == Decimal(7 * M)

    accounts = {
        row.account_code: row
        for row in db_session.scalars(
            select(BiDimGlAccount).where(BiDimGlAccount.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert accounts["4001"].pl_line == "1a"
    assert accounts["5302"].pl_line == "17"
    assert accounts["6001"].pl_line is None
    assert accounts["1301"].pl_line is None


def test_r4_is_exactly_bsd7s_own_period_to_date_figure(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    db_session.commit()
    outcome = build(db_session)
    assert outcome.trust["R4"] == reconciliation.GREEN
    r4 = db_session.scalar(
        select(BiReconciliationResult).where(
            BiReconciliationResult.bank_id == SAMPLE_BANK_ID,
            BiReconciliationResult.check_id == "R4",
        )
    )
    assert r4 is not None
    assert r4.difference == Decimal(0) and r4.tolerance == Decimal(0)
    assert r4.detail["month_end"] == _JUN.isoformat()
    # 1a domestic, 1c domestic, 5 foreign, 16 domestic, 17 domestic + the zero-valued
    # opposite slice of every mapped line (mapped, nothing arose) = 10 comparisons
    assert r4.detail["lines_compared"] == 10
    assert "mismatches" not in r4.detail

    # the same equality, asserted here against the resolver itself
    period = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        period_start=date(2026, 6, 1),
        period_end=_JUN,
        label="2026-06",
        status="open",
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    resolve = get_resolver("bsd7.pl_line")
    rows = _rows(db_session)

    def mart(tag: str, classes: list[str], currency: str) -> Decimal | None:
        """None when the line selects no account; else the slice's sum (0 when empty)."""
        selected = [
            row for row in rows.values() if row.pl_line == tag and row.account_class in classes
        ]
        if not selected:
            return None
        in_slice = [row for row in selected if (row.currency == "") == (currency == "")]
        return sum((Decimal(row.pl_sign or 0) * row.ytd_rc for row in in_slice), Decimal(0))

    expected = {
        ("1a", "ptd_domestic"): Decimal(330 * M),
        ("1a", "ptd_foreign"): Decimal(0),
        ("1c", "ptd_domestic"): Decimal(150 * M),
        ("5", "ptd_domestic"): Decimal(0),
        ("5", "ptd_foreign"): Decimal(30 * M),
        ("16", "ptd_domestic"): Decimal(950 * M),
        ("17", "ptd_domestic"): Decimal(-80 * M),
        ("1b", "ptd_domestic"): None,  # no account feeds it: None on both sides
    }
    classes_of = {
        "1a": ["INCOME"],
        "1c": ["INCOME"],
        "5": ["INCOME"],
        "16": ["EXPENSE"],
        "17": ["EXPENSE"],
        "1b": ["INCOME"],
    }
    for (tag, column), value in expected.items():
        rc = ResolveContext(
            db=db_session, ctx=CTX, bank=bank, period=period, column=column, cache={}
        )
        from_return = resolve(rc, {"line": tag, "gl_classes": classes_of[tag]})
        from_mart = mart(tag, classes_of[tag], "" if column.endswith("_domestic") else "USD")
        assert from_return == value, (tag, column, from_return)
        assert from_mart == value, (tag, column, from_mart)


def test_gl_monthly_row_produces_every_mart_column() -> None:
    columns = set(Base.metadata.tables["bi_fact_gl_monthly"].columns.keys())
    assert {f.name for f in fields(extract.GlMonthlyFactRow)} == columns - {
        "builder_version",
        "built_at",
    }


def test_gl_monthly_row_is_pure_and_keys_currency_by_the_latest_generation() -> None:
    generations = [
        pl_mapping.Generation("4001", _APR, "XRC", Decimal(100), ""),
        pl_mapping.Generation("4001", _MAY, "XRC", Decimal(210), ""),
        pl_mapping.Generation("4001", _JUN, None, Decimal(330), ""),  # unstated = base
    ]
    row = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=pl_mapping.MappingRule("1a", Decimal("-1"), None),
        base_currency="XRC",
    )
    assert row is not None
    assert (row.currency, row.calendar_month, row.month_end) == ("", date(2026, 6, 1), _JUN)
    assert (row.ytd_rc, row.prior_ytd_rc, row.movement_rc, row.missing_prior) == (
        Decimal(330),
        Decimal(210),
        Decimal(120),
        False,
    )
    assert (row.pl_line, row.pl_sign, row.balance_basis) == ("1a", -1, "ytd")
    # the rule's basis wins over whatever the caller stamped on the generations
    period = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=pl_mapping.MappingRule("16", Decimal(1), pl_mapping.PERIOD),
        base_currency="XRC",
    )
    assert period is not None
    assert (period.ytd_rc, period.movement_rc, period.balance_basis) == (
        Decimal(640),
        Decimal(330),
        "period",
    )
    unmapped = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=None,
        base_currency="XRC",
    )
    assert unmapped is not None and unmapped.pl_line is None and unmapped.pl_sign is None
    assert (
        extract.gl_monthly_row(
            generations,
            organization_id="OR-X",
            bank_id="BK-X",
            month_end=date(2026, 3, 31),
            fy_start=date(2026, 1, 1),
            account_class="INCOME",
            rule=None,
            base_currency="XRC",
        )
        is None
    )


# --- the two signs (A5-08) -----------------------------------------------------------------


def test_a_non_integral_register_sign_is_refused_not_truncated(db_session: Session) -> None:
    """``int(Decimal("0.5"))`` is 0, which would drop account 5301 from item 16
    while BSD7 kept filing it at half weight. The build fails, naming the row."""
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    db_session.add(
        CanonicalGlAccount(
            **common,
            source_reference="GL/7001/refused",
            account_code="7001",
            name="P&L 7001",
            account_class="EXPENSE",
            currency="GHS",
            balance=Decimal(5 * M),
        )
    )
    db_session.add(
        CanonicalReferenceRow(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            ingestion_batch_id=common["ingestion_batch_id"],
            as_of_date=AS_OF,
            dataset_kind=pl_mapping.MAPPING_KIND,
            row_index=0,
            payload={"gl_account_code": "7001", "bsd7_item": "18", "sign": "0.5"},
            source_reference="coa#half",
            lineage_id=common["lineage_id"],
        )
    )
    db_session.commit()

    with pytest.raises(pl_mapping.PlSignError, match="7001"):
        build(db_session)
    records = {
        row.scope: row
        for row in db_session.scalars(
            select(BiMartBuild).where(BiMartBuild.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert records and all(row.status == "failed" for row in records.values())
    assert all("PlSignError" in (row.error or "") for row in records.values())
    assert db_session.scalar(select(func.count()).select_from(BiFactGlMonthly)) == 0
    # the register's own contract forbids the value, so a validated push cannot reach here
    from app.domain.ingestion.reference_schemas.gl_mapping_bsd7 import (  # noqa: PLC0415
        validate_mapping_row,
    )

    assert validate_mapping_row({"gl_account_code": "7001", "bsd7_item": "18", "sign": "0.5"})


def test_r4_stays_green_when_a_line_map_declares_its_own_sign(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BSD7 multiplies the LINE MAP's ``sign`` into the whole line; R4's mart side
    must too, or a line map with ``sign=-1`` turns the check red with no defect."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    db_session.commit()
    real = reconciliation._bsd7a_line_params  # noqa: SLF001 - the seam under test

    def negated() -> list[dict[str, Any]]:
        params = real()
        assert all("sign" not in row for row in params), (
            "no BSD7A line declares a sign today; this test exists for the day one does"
        )
        return [{**row, "sign": -1} if row.get("line") == "1a" else row for row in params]

    monkeypatch.setattr(reconciliation, "_bsd7a_line_params", negated)
    outcome = build(db_session)
    assert outcome.trust["R4"] == reconciliation.GREEN
    r4 = db_session.scalar(
        select(BiReconciliationResult).where(
            BiReconciliationResult.bank_id == SAMPLE_BANK_ID,
            BiReconciliationResult.check_id == "R4",
        )
    )
    assert r4 is not None and r4.difference == Decimal(0)
    assert "mismatches" not in r4.detail
    # and the sign really did move both sides: item 1a is now negative
    period = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        period_start=date(2026, 6, 1),
        period_end=_JUN,
        label="2026-06",
        status="open",
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    rc = ResolveContext(
        db=db_session, ctx=CTX, bank=bank, period=period, column="ptd_domestic", cache={}
    )
    resolved = get_resolver("bsd7.pl_line")(
        rc, {"line": "1a", "gl_classes": ["INCOME"], "sign": -1}
    )
    assert resolved == Decimal(-330 * M)
