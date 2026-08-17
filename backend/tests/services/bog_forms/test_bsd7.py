"""BSD7A (Current Year Results) + BSD7B (Consolidated Results) line maps.

Hermetic: the canonical test book (bank_facts only) plus a small INCOME/EXPENSE
ledger inserted here — P&L general-ledger accounts tagged with the official
item (``attributes.bsd7_line``), fiscal-year-to-date balances at three month
ends — generated through the real package pipeline. Proves:

1. every official input cell of both sheets is bound (structure never dropped);
2. the ledger → line resolver: period-to-date = latest YTD balance, month =
   difference of consecutive YTD balances, Domestic/Foreign by ledger currency,
   a line without a prior-month generation stays blank for the month;
3. the template's own arithmetic over our inputs: Total = Domestic + Foreign,
   Net Profit = Net Income − Opex − Provisions − losses, month ≤ period to date;
4. BSD7B period-to-date == BSD7A period-to-date total (same ledger, same date),
   BSD7B quarter == period-to-date in the fiscal year's first quarter;
5. the averages block (mean of month-end balance_sheet / capital facts).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import Bank, BankReportingPeriod
from app.models.canonical import CanonicalGlAccount
from app.models.ingestion import IngestionBatch, LineageRecord
from app.models.regulatory import BankFinancialFact
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from app.services.regulatory_reporting.bog_forms.sources_ext.bsd7 import (
    LINE_ATTRIBUTE,
    window_start,
)
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

M = 1_000_000
REPORTING_DATE = "2026-03-31"  # the fixture's latest period (fiscal Q1 → quarter == PTD)

_JAN, _FEB, _MAR = date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)

#: (account_code, class, tag, currency, {as_of: fiscal-YTD balance})
_LEDGER: tuple[tuple[str, str, str, str | None, dict[date, int]], ...] = (
    ("4001", "INCOME", "1a", "GHS", {_JAN: 100, _FEB: 210, _MAR: 330}),
    ("4002", "INCOME", "1c", None, {_JAN: 50, _FEB: 100, _MAR: 150}),
    ("4101", "INCOME", "5", "USD", {_JAN: 10, _FEB: 20, _MAR: 30}),
    ("5001", "EXPENSE", "2a_savings", "GHS", {_JAN: 30, _FEB: 60, _MAR: 90}),
    ("5101", "EXPENSE", "12", "GHS", {_JAN: 40, _FEB: 80, _MAR: 130}),
    ("5201", "EXPENSE", "21", "GHS", {_JAN: 5, _FEB: 12, _MAR: 20}),
    # tax charge booked only at quarter end: PTD known, the March month is not
    ("5301", "EXPENSE", "28", "GHS", {_MAR: 15}),
    # a prior fiscal year generation must never leak into the current year
    ("4001", "INCOME", "1a", "GHS", {date(2025, 12, 31): 9_999}),
)


def _materialize(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _insert_ledger(session: Session) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=date(2026, 3, 31),
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="bsd7-test-ledger",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    for code, account_class, tag, currency, balances in _LEDGER:
        for as_of, balance in balances.items():
            session.add(
                CanonicalGlAccount(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    as_of_date=as_of,
                    source_system="EXCEL_CSV",
                    source_reference=f"GL/{code}/{as_of.isoformat()}",
                    ingestion_batch_id=batch.id,
                    lineage_id=lineage.id,
                    validation_status="accepted",
                    account_code=code,
                    name=f"P&L {code}",
                    account_class=account_class,
                    currency=currency,
                    balance=Decimal(balance * M),
                    attributes={LINE_ATTRIBUTE: tag},
                )
            )
    session.flush()


def _seed(db_client: TestClient) -> None:
    _materialize(db_client)
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        _insert_ledger(session)
        session.commit()
    finally:
        session.close()


def _generate(db_client: TestClient, code: str, reporting_date: str = REPORTING_DATE) -> dict:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{code}: {response.status_code} {response.text[:300]}"
    package = response.json()
    detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()
    return detail["snapshot"]


def _num(cells: dict[str, Any], ref: str) -> float:
    value = cells.get(ref)
    return 0.0 if value is None else float(value)


# ---------------------------------------------------------------------------
# 1. structure: every input cell bound; the mapped share the doc claims
# ---------------------------------------------------------------------------


def _bound_by_source(code: str, sheet: str) -> dict[str | None, set[str]]:
    by_source: dict[str | None, set[str]] = {}
    for line in line_maps_for(code)[sheet]:
        by_source.setdefault(line.source, set()).update(line.cells.values())
    return by_source


def test_bsd7a_binds_every_official_input_cell() -> None:
    layout = load_layout("BSD7A").sheet("BSD7A")
    by_source = _bound_by_source("BSD7A", "BSD7A")
    bound = set().union(*by_source.values())
    assert bound == {c.ref for c in layout.input_cells}
    assert len(bound) == 209  # noqa: PLR2004 — the official sheet's input cells
    # 34 P&L leaf rows × 4 columns from the ledger, 3 reserve rows + PPE / interest
    # classes / staff (30 cells) input_required, 2 average rows × 2 from facts,
    # 51 item numbers (template literals)
    assert len(by_source["bsd7.pl_line"]) == 31 * 4
    assert len(by_source["bsd7.average_facts"]) == 2 * 2
    assert len(by_source["constant"]) == 51
    assert len(by_source[None]) == 3 * 4 + 3 * 2 + 6 * 2


def test_bsd7b_binds_every_official_input_cell() -> None:
    layout = load_layout("BSD7B").sheet("BSD7B")
    by_source = _bound_by_source("BSD7B", "BSD7B")
    bound = set().union(*by_source.values())
    assert bound == {c.ref for c in layout.input_cells}
    assert len(bound) == 105  # noqa: PLR2004
    assert len(by_source["bsd7.pl_line"]) == 31 * 2
    assert len(by_source["constant"]) == 37
    assert len(by_source[None]) == 3 * 2
    # BSD7B is the BSD7A spine two rows lower, same tags
    a = {int(line.cells["month_domestic"][1:]): line.params.get("line") for line in
         line_maps_for("BSD7A")["BSD7A"] if "month_domestic" in line.cells}  # fmt: skip
    b = {int(line.cells["quarter"][1:]): line.params.get("line") for line in
         line_maps_for("BSD7B")["BSD7B"] if "quarter" in line.cells}  # fmt: skip
    assert {row + 2: tag for row, tag in a.items()} == b


# ---------------------------------------------------------------------------
# 2–3. BSD7A end to end: ledger → lines → BoG's arithmetic
# ---------------------------------------------------------------------------


def test_bsd7a_ledger_lines_month_vs_period_to_date_and_currency(db_client: TestClient) -> None:
    _seed(db_client)
    snapshot = _generate(db_client, "BSD7A")
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    cells = payload["cells"]["BSD7A"]

    # 1(a) loans interest: month = 330 − 210, PTD = 330 (prior-year 9,999 ignored)
    assert _num(cells, "C6") == 120 * M
    assert _num(cells, "F6") == 330 * M
    # 1(c) investments (currency unstated ⇒ base currency)
    assert _num(cells, "C8") == 50 * M
    assert _num(cells, "F8") == 150 * M
    # fees in USD land in the FOREIGN column; the domestic slice reads 0 (mapped)
    assert _num(cells, "D18") == 10 * M
    assert _num(cells, "G18") == 30 * M
    assert cells["C18"] == 0 and cells["F18"] == 0
    # tax charge booked only at quarter end: PTD known, month honestly blank
    assert _num(cells, "F41") == 15 * M
    assert cells["C41"] is None
    # untagged lines stay blank (input_required), never zero-filled
    assert cells["C7"] is None and cells["F7"] is None  # 1(b) bills — no tagged account
    line_status = {
        (row["cell"]): row["status"]
        for section in snapshot["sections"]
        for row in section["rows"]
        if section["title"] == "BSD7A"
    }
    assert line_status["C41"] == "input_required"
    assert line_status["C7"] == "input_required"
    assert line_status["C6"] == "mapped"
    assert line_status["A5"] == "mapped"  # item number literal

    # item numbers ride through as the template's own literals (unscaled)
    assert cells["A5"] == 1 and cells["A50"] == 37 and cells["A72"] == 51

    # month ≤ period-to-date on every mapped P&L line (YTD balances are cumulative)
    layout = load_layout("BSD7A").sheet("BSD7A")
    for row in range(6, 46):
        for month_col, ptd_col in (("C", "F"), ("D", "G")):
            if layout.by_ref.get(f"{month_col}{row}") is None:
                continue
            m, p = cells.get(f"{month_col}{row}"), cells.get(f"{ptd_col}{row}")
            if m is not None and p is not None:
                assert float(m) <= float(p) + 1e-6, f"row {row} {month_col}>{ptd_col}"


def test_bsd7a_net_profit_is_the_templates_own_chain(db_client: TestClient) -> None:
    _seed(db_client)
    cells = _generate(db_client, "BSD7A")["bog_form"]["cells"]["BSD7A"]
    for cols in (("C", "D", "E"), ("F", "G", "H")):
        dom, fx, tot = cols
        # every P&L row: Total = Domestic + Foreign (template =C+D / =F+G)
        for row in range(5, 51):
            if f"{tot}{row}" in cells and cells[f"{tot}{row}"] is not None:
                assert (
                    abs(
                        _num(cells, f"{tot}{row}")
                        - (_num(cells, f"{dom}{row}") + _num(cells, f"{fx}{row}"))
                    )
                    < 1e-6
                ), row
        for col in (dom, fx, tot):
            income = _num(cells, f"{col}5")  # 1 = SUM(6:8)
            assert abs(income - sum(_num(cells, f"{col}{r}") for r in (6, 7, 8))) < 1e-6
            interest_paid = _num(cells, f"{col}9")  # 2 = 2(a) + 2(b)
            assert (
                abs(
                    interest_paid
                    - (
                        sum(_num(cells, f"{col}{r}") for r in (11, 12, 13, 14))
                        + _num(cells, f"{col}15")
                    )
                )
                < 1e-6
            )
            nii = _num(cells, f"{col}16")
            assert abs(nii - (income - interest_paid)) < 1e-6
            net_income = _num(cells, f"{col}24")
            assert abs(net_income - sum(_num(cells, f"{col}{r}") for r in range(16, 24))) < 1e-6
            opex = _num(cells, f"{col}32")
            provisions = _num(cells, f"{col}36")
            net_profit = _num(cells, f"{col}40")
            expected = (
                net_income - opex - provisions - sum(_num(cells, f"{col}{r}") for r in (37, 38, 39))
            )
            assert abs(net_profit - expected) < 1e-6, col
            assert abs(_num(cells, f"{col}42") - (net_profit - _num(cells, f"{col}41"))) < 1e-6
    # the concrete figures our ledger implies (base units)
    assert _num(cells, "C16") == (120 + 50 - 30) * M  # month NII domestic
    assert _num(cells, "C40") == (140 - 50 - 8) * M  # month net profit domestic
    assert _num(cells, "F40") == (480 - 90 - 130 - 20) * M  # PTD net profit domestic
    assert _num(cells, "F42") == (240 - 15) * M  # after tax
    assert _num(cells, "G40") == 30 * M  # PTD foreign (fees only)
    assert _num(cells, "H40") == 270 * M


# ---------------------------------------------------------------------------
# 4. BSD7B: consolidated P&L carries the parent's ledger figures
# ---------------------------------------------------------------------------


def test_bsd7b_period_to_date_equals_bsd7a_total_and_quarter_in_q1(db_client: TestClient) -> None:
    _seed(db_client)
    a = _generate(db_client, "BSD7A")["bog_form"]["cells"]["BSD7A"]
    b_snapshot = _generate(db_client, "BSD7B")
    assert not b_snapshot["bog_form"]["errors"]
    assert b_snapshot["bog_form"]["basis"] == "consolidated"  # the form spec's Guide basis
    b = b_snapshot["bog_form"]["cells"]["BSD7B"]
    checked = 0
    for row in range(5, 51):
        if f"H{row}" not in a:
            continue
        # BSD7B row = BSD7A row + 2; period to date (D) = BSD7A period-to-date TOTAL (H)
        assert abs(_num(b, f"D{row + 2}") - _num(a, f"H{row}")) < 1e-6, row
        # March is the fiscal year's first quarter end: quarter (C) == period to date (D)
        assert abs(_num(b, f"C{row + 2}") - _num(b, f"D{row + 2}")) < 1e-6, row
        checked += 1
    assert checked >= 40  # noqa: PLR2004 — the whole spine incl. subtotals
    assert _num(b, "D42") == 270 * M  # net profit PTD, all currencies
    # every parent P&L line says so in its note (consolidation not on platform)
    notes = [
        row["notes"]
        for section in b_snapshot["sections"]
        for row in section["rows"]
        if row["source"] == "bsd7.pl_line"
    ]
    assert notes and all("parent (solo)" in note for note in notes)


# ---------------------------------------------------------------------------
# resolver unit tests: fiscal windows, quarter ≠ PTD, averages
# ---------------------------------------------------------------------------


def _rc(session: Session, period: BankReportingPeriod, column: str) -> ResolveContext:
    bank = session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return ResolveContext(
        db=session,
        ctx=TenantContext(organization_id=ORG_1),
        bank=bank,
        period=period,
        column=column,
    )


def test_window_start_follows_the_fiscal_year() -> None:
    p = BankReportingPeriod(period_start=date(2025, 5, 1), period_end=date(2025, 5, 31))
    assert window_start(p, "ptd", 1) == date(2025, 1, 1)
    assert window_start(p, "quarter", 1) == date(2025, 4, 1)
    assert window_start(p, "month", 1) == date(2025, 5, 1)
    # a July fiscal year: May sits in Q4 (Apr–Jun), PTD from the previous July
    assert window_start(p, "ptd", 7) == date(2024, 7, 1)
    assert window_start(p, "quarter", 7) == date(2025, 4, 1)
    p2 = BankReportingPeriod(period_start=date(2025, 8, 1), period_end=date(2025, 8, 31))
    assert window_start(p2, "quarter", 7) == date(2025, 7, 1)
    assert window_start(p2, "quarter", 1) == date(2025, 7, 1)


def test_pl_line_quarter_differs_from_period_to_date_off_q1(db_client: TestClient) -> None:
    _materialize(db_client)
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        # YTD balances Mar 300 → Apr 400 → May 550 for one GHS income account
        batch = IngestionBatch(
            organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, source_system="EXCEL_CSV",
            adapter_version="1.0", extraction_mode="full", status="accepted",
            as_of_date=date(2025, 5, 31),
        )  # fmt: skip
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1, ingestion_batch_id=batch.id, operation_type="ADAPTER_TRANSLATE",
            operation_ref="q", input_lineage_ids=[],
        )  # fmt: skip
        session.add(lineage)
        session.flush()
        for as_of, balance in (
            (date(2025, 3, 31), 300),
            (date(2025, 4, 30), 400),
            (date(2025, 5, 31), 550),
        ):
            session.add(
                CanonicalGlAccount(
                    organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of_date=as_of,
                    source_system="EXCEL_CSV", source_reference=f"GL/4001/{as_of}",
                    ingestion_batch_id=batch.id, lineage_id=lineage.id,
                    validation_status="accepted",
                    account_code="4001", name="Loan interest", account_class="INCOME",
                    currency="GHS", balance=Decimal(balance), attributes={LINE_ATTRIBUTE: "1a"},
                )
            )  # fmt: skip
        session.flush()
        period = session.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == date(2025, 5, 31),
            )
        )
        assert period is not None
        resolve = get_resolver("bsd7.pl_line")
        params = {"line": "1a", "gl_classes": ["INCOME"]}
        assert resolve(_rc(session, period, "ptd"), params) == Decimal(550)
        assert resolve(_rc(session, period, "quarter"), params) == Decimal(250)  # Apr–May
        assert resolve(_rc(session, period, "month_domestic"), params) == Decimal(150)
        assert resolve(_rc(session, period, "month_foreign"), params) == Decimal(0)
        # sign + prefix selection + period-movement basis
        assert resolve(_rc(session, period, "ptd"), {**params, "sign": -1}) == Decimal(-550)
        assert resolve(_rc(session, period, "ptd"), {"account_code_prefixes": ["40"]}) == Decimal(
            550
        )
        assert resolve(
            _rc(session, period, "quarter"), {**params, "balance_basis": "period"}
        ) == Decimal(950)  # Apr 400 + May 550 as movements
        # nothing tagged for another line → None (input_required), never 0
        assert resolve(_rc(session, period, "ptd"), {"line": "1b"}) is None
    finally:
        session.rollback()
        session.close()


def test_average_facts_means_month_end_observations(db_client: TestClient) -> None:
    _materialize(db_client)
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        period = session.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == date(2025, 8, 31),
            )
        )
        assert period is not None
        periods = session.scalars(
            select(BankReportingPeriod).where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        ).all()

        def assets(p: BankReportingPeriod) -> Decimal:
            facts = session.scalars(
                select(BankFinancialFact).where(
                    BankFinancialFact.reporting_period_id == p.id,
                    BankFinancialFact.fact_group == "balance_sheet",
                )
            ).all()
            return sum((f.amount for f in facts if f.attributes.get("side") == "asset"), Decimal(0))

        quarter = [p for p in periods if date(2025, 7, 1) <= p.period_end <= date(2025, 8, 31)]
        ptd = [p for p in periods if date(2025, 1, 1) <= p.period_end <= date(2025, 8, 31)]
        assert len(quarter) == 2 and len(ptd) == 5  # noqa: PLR2004 — fixture starts 2025-04
        resolve = get_resolver("bsd7.average_facts")
        params = {"group": "balance_sheet", "attribute_eq": {"side": "asset"}}
        got_q = resolve(_rc(session, period, "quarter"), params)
        got_p = resolve(_rc(session, period, "ptd"), params)
        assert got_q is not None and got_p is not None
        assert abs(Decimal(got_q) - sum((assets(p) for p in quarter), Decimal(0)) / 2) < Decimal(
            "0.01"
        )
        assert abs(Decimal(got_p) - sum((assets(p) for p in ptd), Decimal(0)) / 5) < Decimal("0.01")
        assert got_q != got_p  # the fixture's balance sheet grows month on month
        # shareholders' funds: CET1 components before deductions
        sf = resolve(
            _rc(session, period, "ptd"),
            {"group": "capital_component", "capital_tiers": ["CET1"], "exclude_deductions": True},
        )
        assert sf is not None and Decimal(sf) > 0
        assert resolve(_rc(session, period, "ptd"), {"group": "no_such_group"}) is None
    finally:
        session.close()
