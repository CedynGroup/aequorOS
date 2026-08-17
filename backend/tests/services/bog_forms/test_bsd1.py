"""BSD1 — Liquidity Reserve Return: line map, daily ladder resolver, template arithmetic.

Proves, on the hermetic book generated through the REAL package pipeline:

1. every input cell of ``BSD1 ``, ``BSD1-Annex1`` and ``BSD1-Annex3`` is bound
   (mapped or explicit input_required) and the mapped share matches the doc;
2. a month-end-only book fills ONLY the reporting-date column of the rows the
   fact spine describes (cash on hand, BoG current account, total deposits) —
   the other six days stay ``input_required``, never a copied balance;
3. with a daily position ladder seeded for the two weeks, every day column
   fills from its own rung, a day without a rung stays blank, and the
   template's own formulas hold: primary-reserve total (row 36) = Σ its inputs,
   sub-total domestic deposits (row 16) = Σ rows 10–15, actual primary reserve
   ratio (row 67) = row 35 / row 22 × 100, TOTAL = Σ days and AVERAGE = TOTAL/7;
4. Annex 1's exchange-rate memoranda fill only from a SAME-DAY market-data
   observation (no carry-forward).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import BankReportingPeriod, IngestionBatch, LineageRecord
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalFxRate,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

SHEET = "BSD1 "
DAYS = ("B", "C", "D", "E", "F", "G", "H")  # THU … WED; H = reporting date
CASH_VAULT = Decimal("45000000")
BOG_BALANCES = Decimal("245000000")  # bog_required_reserves 175M + bog_excess_reserves 70M
DEPOSITS_TOTAL = Decimal("1900000000")  # Σ of the five balance_sheet deposit facts

# ---- seeded daily ladder (base units) -----------------------------------------
LADDER_CURRENT = Decimal("1000000000")  # current-account deposits per day (previous week)
LADDER_CALL = Decimal("100000000")
LADDER_FIXED = Decimal("500000000")
LADDER_SAVINGS = Decimal("300000000")
LADDER_USD_DEP_GHS = Decimal("120000000")  # cedi equivalent of a USD deposit
LADDER_VAULT_CASH = Decimal("40000000")
LADDER_BOG_CURRENT = Decimal("180000000")
LADDER_BOG_FX_GHS = Decimal("30000000")
LADDER_BOG_BILL_91 = Decimal("250000000")
LADDER_TBILL_91 = Decimal("150000000")
LADDER_USD_NOSTRO_USD = Decimal("2500000")  # native USD units (Annex 1 balances block)
LADDER_BOG_USD_NATIVE = Decimal("2500000")  # the BoG USD current account, native units
MISSING_RUNG_COLUMN = "E"  # Sunday: no snapshot in either week ⇒ input_required


def _materialize(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _latest_period(db_client: TestClient) -> dict[str, Any]:
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _generate(db_client: TestClient, code: str, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, response.text[:400]
    package = response.json()
    detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()
    return detail["snapshot"]


def _lines(snapshot: dict[str, Any], sheet: str) -> dict[str, dict[str, Any]]:
    """{cell: row} for the given official sheet's declared lines."""
    for section in snapshot["sections"]:
        if section["title"] == sheet:
            return {row["cell"]: row for row in section["rows"]}
    raise AssertionError(f"no section for sheet {sheet!r}")


def _cells(snapshot: dict[str, Any], sheet: str) -> dict[str, Any]:
    return snapshot["bog_form"]["cells"][sheet]


def _num(value: Any) -> Decimal:
    return Decimal(str(value or 0))


# ---------------------------------------------------------------------------
# 1. structure: every input cell bound
# ---------------------------------------------------------------------------


def test_bsd1_line_map_binds_every_input_cell_of_every_sheet() -> None:
    layout = load_layout("BSD1")
    maps = line_maps_for("BSD1")
    for sheet in layout.sheets:
        bound = {ref for line in maps.get(sheet.name, ()) for ref in line.cells.values()}
        official = {c.ref for c in sheet.input_cells}
        assert official <= bound, f"{sheet.name}: unbound {sorted(official - bound)[:8]}"
    main = maps[SHEET]
    assert len(main) == 41  # noqa: PLR2004 — 41 leaf rows × 7 days = 287 cells
    assert sum(len(line.cells) for line in main) == 287  # noqa: PLR2004
    assert all(
        set(line.cells) == {"thu", "fri", "sat", "sun", "mon", "tue", "wed"} for line in main
    )
    mapped_rows = [line for line in main if line.source]
    assert len(mapped_rows) == 39  # noqa: PLR2004 — only accrued interest (13/14) is input_required
    assert all(line.source == "bsd1.daily" for line in mapped_rows)
    annex1 = maps["BSD1-Annex1"]
    assert sum(len(line.cells) for line in annex1) == 84  # noqa: PLR2004
    assert {line.source for line in annex1} == {"bsd1.daily", "bsd1.fx_spot", None}
    annex3 = maps["BSD1-Annex3"]
    assert {ref for line in annex3 for ref in line.cells.values()} >= {
        f"G{r}" for r in (*range(22, 30), *range(37, 45), *range(51, 59))
    }


# ---------------------------------------------------------------------------
# 2. month-end-only book: only the reporting-date column, only fact-backed rows
# ---------------------------------------------------------------------------


def test_bsd1_month_end_book_fills_reporting_date_only_and_never_copies_a_day(
    db_client: TestClient,
) -> None:
    _materialize(db_client)
    period = _latest_period(db_client)
    snapshot = _generate(db_client, "BSD1", period["period_end"])
    lines = _lines(snapshot, SHEET)
    cells = _cells(snapshot, SHEET)

    # reporting date (WED = column H): cash on hand, BoG current account, total deposits
    assert lines["H32"]["status"] == "mapped"
    assert _num(cells["H32"]) == CASH_VAULT
    assert _num(cells["H33"]) == BOG_BALANCES
    assert _num(cells["H94"]) == DEPOSITS_TOTAL
    # the other six days of the same rows: unknown, not copied
    for col in DAYS[:-1]:
        for row in (32, 33, 94):
            assert lines[f"{col}{row}"]["status"] == "input_required", f"{col}{row}"
            assert cells.get(f"{col}{row}") is None
    # rows the fact spine cannot describe (previous-week deposits, FX BoG account,
    # securities by tenor) stay input_required on every day
    for row in (10, 11, 18, 34, 48, 53):
        for col in DAYS:
            assert lines[f"{col}{row}"]["status"] == "input_required", f"{col}{row}"
    counts = snapshot["bog_form"]["status_counts"]
    assert counts["mapped"] == 3  # noqa: PLR2004 — H32, H33, H94 only
    assert not snapshot["bog_form"]["errors"]
    # template arithmetic over blanks: primary total = cash + BoG current account
    assert _num(cells["H35"]) == BOG_BALANCES  # 20 = 18 + 19 (19 blank ⇒ 0)
    assert _num(cells["H36"]) == CASH_VAULT + BOG_BALANCES  # 21 = 17 + 20
    # ratio 29 = 20/12 × 100 with an empty deposit base ⇒ Excel #DIV/0 ⇒ 0
    assert _num(cells["H67"]) == 0


# ---------------------------------------------------------------------------
# 3. daily ladder: every day from its own rung; BoG's formulas hold
# ---------------------------------------------------------------------------


class _Ladder:
    """Seeds one snapshot per position per business date (the EOD ladder)."""

    def __init__(self, db: Session, reporting_date: date) -> None:
        self.db = db
        self.reporting_date = reporting_date
        batch = IngestionBatch(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            source_system="API_PUSH",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=reporting_date,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=DEMO_ORG_ID,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="bsd1-ladder",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        self.common: dict[str, Any] = {
            "organization_id": DEMO_ORG_ID,
            "bank_id": SAMPLE_BANK_ID,
            "source_system": "API_PUSH",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }
        self.bog = self.counterparty("CP/BOG", "Bank of Ghana", "CENTRAL_BANK")
        self.corp = self.counterparty("CP/CORP", "Volta Agro Ltd", "CORPORATE")

    def counterparty(self, ref: str, name: str, kind: str) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            as_of_date=self.reporting_date,
            source_reference=ref,
            name=name,
            counterparty_type=kind,
            resident=True,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def days(self, days_before: range) -> list[date]:
        return [self.reporting_date - timedelta(days=d) for d in days_before]

    def position(  # noqa: PLR0913 — keyword-only ladder builder
        self,
        ref: str,
        position_type: str,
        *,
        currency: str,
        balance: Decimal,
        balance_ghs: Decimal | None,
        days: list[date],
        counterparty: CanonicalCounterparty | None = None,
        deposit_account_type: str | None = None,
        attributes: dict[str, Any] | None = None,
        skip_column: str | None = MISSING_RUNG_COLUMN,
    ) -> None:
        position = CanonicalPosition(
            **self.common,
            as_of_date=min(days),
            source_reference=ref,
            position_type=position_type,
            currency=currency,
        )
        self.db.add(position)
        self.db.flush()
        for day in days:
            if skip_column and self._column_for(day) == skip_column:
                continue  # no EOD rung that day
            attrs = dict(attributes or {})
            if balance_ghs is not None:
                attrs["balance_ghs"] = str(balance_ghs)
            self.db.add(
                CanonicalPositionSnapshot(
                    **self.common,
                    as_of_date=day,
                    source_reference=f"{ref}@{day.isoformat()}",
                    position_id=position.id,
                    counterparty_id=counterparty.id if counterparty is not None else None,
                    balance=balance,
                    deposit_account_type=deposit_account_type,
                    attributes=attrs,
                )
            )
        self.db.flush()

    def _column_for(self, day: date) -> str:
        offset = (self.reporting_date - day).days % 7
        return DAYS[6 - offset]

    def fx_spot(self, currency: str, rate: Decimal, day: date) -> None:
        self.db.add(
            CanonicalFxRate(
                **self.common,
                as_of_date=day,
                source_reference=f"FX/{currency}/{day.isoformat()}",
                base_currency=currency,
                quote_currency="GHS",
                rate_type="spot",
                tenor_months=None,
                rate=rate,
            )
        )
        self.db.flush()


def _seed_ladder(reporting_date: date) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        ladder = _Ladder(session, reporting_date)
        previous = ladder.days(range(7, 14))  # THU…WED of the previous week
        current = ladder.days(range(0, 7))  # THU…WED of the current week
        # DEPOSITS (previous week block)
        ladder.position(
            "DEP/CUR", "DEPOSIT", currency="GHS", balance=LADDER_CURRENT, balance_ghs=None,
            days=previous, counterparty=ladder.corp, deposit_account_type="CURRENT",
        )  # fmt: skip
        ladder.position(
            "DEP/CALL", "DEPOSIT", currency="GHS", balance=LADDER_CALL, balance_ghs=None,
            days=previous, deposit_account_type="CALL",
        )  # fmt: skip
        ladder.position(
            "DEP/FIX", "DEPOSIT", currency="GHS", balance=LADDER_FIXED, balance_ghs=None,
            days=previous, deposit_account_type="FIXED",
        )  # fmt: skip
        ladder.position(
            "DEP/SAV", "DEPOSIT", currency="GHS", balance=LADDER_SAVINGS, balance_ghs=None,
            days=previous, deposit_account_type="SAVINGS",
        )  # fmt: skip
        ladder.position(
            "DEP/USD", "DEPOSIT", currency="USD", balance=Decimal("10000000"),
            balance_ghs=LADDER_USD_DEP_GHS, days=previous, deposit_account_type="SAVINGS",
        )  # fmt: skip
        # LIQUID ASSETS (current week block)
        ladder.position(
            "CASH/VAULT", "CASH", currency="GHS", balance=LADDER_VAULT_CASH, balance_ghs=None,
            days=current,
        )  # fmt: skip
        ladder.position(
            "CASH/BOG", "CASH", currency="GHS", balance=LADDER_BOG_CURRENT, balance_ghs=None,
            days=current, counterparty=ladder.bog,
        )  # fmt: skip
        ladder.position(
            "CASH/BOG-USD", "CASH", currency="USD", balance=LADDER_BOG_USD_NATIVE,
            balance_ghs=LADDER_BOG_FX_GHS, days=current, counterparty=ladder.bog,
        )  # fmt: skip
        ladder.position(
            "SEC/BOG-91", "SECURITY_HOLDING", currency="GHS", balance=LADDER_BOG_BILL_91,
            balance_ghs=None, days=current, counterparty=ladder.bog,
            attributes={"instrument": "bog_bill", "tenor_days": 91},
        )  # fmt: skip
        gov = ladder.counterparty("CP/GOG", "Government of Ghana", "SOVEREIGN")
        ladder.position(
            "SEC/TBILL-91", "SECURITY_HOLDING", currency="GHS", balance=LADDER_TBILL_91,
            balance_ghs=None, days=current, counterparty=gov,
            attributes={"instrument": "tbill", "tenor_days": 91},
        )  # fmt: skip
        # Annex 1 balances block: a USD nostro (native units) every day
        ladder.position(
            "NOSTRO/USD", "INTERBANK_PLACEMENT", currency="USD", balance=LADDER_USD_NOSTRO_USD,
            balance_ghs=Decimal("30000000"), days=current,
        )  # fmt: skip
        # Annex 1 exchange rates: a same-day quote on the reporting date ONLY
        ladder.fx_spot("USD", Decimal("12.5"), reporting_date)
        session.commit()
    finally:
        session.close()


def test_bsd1_daily_ladder_feeds_every_day_and_bogs_reserve_arithmetic_holds(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    _materialize(db_client)
    period = _latest_period(db_client)
    reporting_date = date.fromisoformat(period["period_end"])
    _seed_ladder(reporting_date)
    snapshot = _generate(db_client, "BSD1", period["period_end"])
    assert not snapshot["bog_form"]["errors"], snapshot["bog_form"]["errors"]
    lines = _lines(snapshot, SHEET)
    cells = _cells(snapshot, SHEET)

    live_days = [c for c in DAYS if c != MISSING_RUNG_COLUMN]
    for col in live_days:
        # DEPOSITS — previous week, per day from that day's rung
        assert _num(cells[f"{col}10"]) == LADDER_CURRENT + LADDER_CALL, col  # 1. Demand
        assert _num(cells[f"{col}11"]) == LADDER_FIXED, col  # 2. Time
        assert _num(cells[f"{col}12"]) == LADDER_SAVINGS, col  # 3. Savings
        assert _num(cells[f"{col}13"]) == 0, col  # CDs: rung exists, none tagged
        assert _num(cells[f"{col}18"]) == LADDER_USD_DEP_GHS, col  # 8. FX deposits (cedi)
        # LIQUID ASSETS — current week
        assert _num(cells[f"{col}32"]) == LADDER_VAULT_CASH, col  # 17. cash on hand
        assert _num(cells[f"{col}33"]) == LADDER_BOG_CURRENT, col  # 18. BoG current a/c
        assert _num(cells[f"{col}34"]) == LADDER_BOG_FX_GHS, col  # 19. BoG current a/c (FX)
        assert _num(cells[f"{col}48"]) == LADDER_BOG_BILL_91, col  # 24. BoG 91-day bill
        assert _num(cells[f"{col}53"]) == LADDER_TBILL_91, col  # 25(a) GoG 91-day bill
        assert lines[f"{col}10"]["status"] == "mapped"
        # --- BoG's own formulas over these inputs ---------------------------
        domestic = LADDER_CURRENT + LADDER_CALL + LADDER_FIXED + LADDER_SAVINGS
        assert _num(cells[f"{col}16"]) == domestic  # 7 = SUM(1..6)
        assert _num(cells[f"{col}21"]) == LADDER_USD_DEP_GHS  # 11 = 8+9+10
        assert _num(cells[f"{col}22"]) == domestic + LADDER_USD_DEP_GHS  # 12 = 7+11
        primary = LADDER_BOG_CURRENT + LADDER_BOG_FX_GHS
        assert _num(cells[f"{col}35"]) == primary  # 20 = 18+19
        assert _num(cells[f"{col}36"]) == LADDER_VAULT_CASH + primary  # 21 = 17+20 (Σ inputs)
        secondary = LADDER_BOG_BILL_91 + LADDER_TBILL_91
        assert _num(cells[f"{col}65"]) == secondary  # 27 = 22+23+24+25+26
        deposit_base = domestic + LADDER_USD_DEP_GHS
        expected_ratio = primary / deposit_base * 100
        assert abs(_num(cells[f"{col}67"]) - expected_ratio) < Decimal("1e-6")  # 29 = 20/12 %
        assert abs(_num(cells[f"{col}73"]) - deposit_base * Decimal("0.09")) < Decimal("1e-6")
        assert abs(_num(cells[f"{col}80"]) - (primary - deposit_base * Decimal("0.09"))) < Decimal(
            "1e-6"
        )  # 37 = 20 − 33
    # the day with no rung is unknown — blank, not a copy of its neighbours
    for row in (10, 32, 33, 48):
        assert lines[f"{MISSING_RUNG_COLUMN}{row}"]["status"] == "input_required"
        assert cells.get(f"{MISSING_RUNG_COLUMN}{row}") is None
    # TOTAL = Σ the seven days (blank = 0) and AVERAGE = TOTAL / 7 — the template's own
    week_total = LADDER_BOG_CURRENT * len(live_days)
    assert _num(cells["I33"]) == week_total
    assert abs(_num(cells["J33"]) - week_total / 7) < Decimal("1e-6")

    # ---- Annex 1: FX deposits by currency (cedi), balances (native), rates (same-day)
    a1_lines = _lines(snapshot, "BSD1-Annex1")
    a1 = _cells(snapshot, "BSD1-Annex1")
    for col in live_days:
        assert _num(a1[f"{col}10"]) == LADDER_USD_DEP_GHS  # 1. US Dollar deposits (¢)
        assert _num(a1[f"{col}11"]) == 0  # 2. Euros: rung exists, no EUR deposits
        # USD balances (nostro + BoG USD account) in USD units
        assert _num(a1[f"{col}19"]) == LADDER_USD_NOSTRO_USD + LADDER_BOG_USD_NATIVE
    assert _num(a1["H27"]) == Decimal("12.5")  # same-day USD quote on the reporting date
    assert a1_lines["H27"]["status"] == "mapped"
    for col in DAYS[:-1]:  # no observation on the other days ⇒ blank, never carried
        assert a1_lines[f"{col}27"]["status"] == "input_required", col
    # export unit: Annex 1 balances are native units (unscaled), deposits ¢'Million
    a1_rows = {row["cell"]: row for row in a1_lines.values()}
    assert Decimal(a1_rows["H19"]["value"]) == LADDER_USD_NOSTRO_USD + LADDER_BOG_USD_NATIVE
    assert Decimal(a1_rows["H10"]["value"]) == LADDER_USD_DEP_GHS / Decimal(1_000_000)

    # ---- Annex 3: weekly movement in cedi deposits = Σ rung(reporting date) − Σ rung(−7)
    a3 = _cells(snapshot, "BSD1-Annex3")
    a3_lines = _lines(snapshot, "BSD1-Annex3")
    # the current-week rungs carry no DEPOSIT snapshots at all: the deposit population
    # has no rung on the reporting date, so the movement is unknown — never a fabricated
    # "−(whole previous-week stock)" (rungs are scoped to the line's population)
    assert a3_lines["G8"]["status"] == "input_required"
    assert a3.get("G8") is None
    # the loan rows have no rung either way ⇒ input_required
    assert a3_lines["G13"]["status"] == "input_required"
    # the ranked name/amount rows have no platform source
    assert a3_lines["G22"]["status"] == "input_required"
    assert a3_lines["B22"]["status"] == "input_required"


# ---------------------------------------------------------------------------
# 4. resolver unit test: exact-day rung, previous-week block, facts fallback
# ---------------------------------------------------------------------------


def test_bsd1_daily_resolver_reads_only_the_cells_own_business_date(
    db_client: TestClient,
) -> None:
    from app.api.deps import TenantContext  # noqa: PLC0415 — test-local wiring
    from app.models import Bank  # noqa: PLC0415
    from app.services.regulatory_reporting.bog_forms.sources import (  # noqa: PLC0415
        ResolveContext,
        get_resolver,
    )
    from tests.fixtures.canonical_bank_fixture import DEMO_USER_ID  # noqa: PLC0415

    _materialize(db_client)
    period = _latest_period(db_client)
    reporting_date = date.fromisoformat(period["period_end"])
    _seed_ladder(reporting_date)
    daily = get_resolver("bsd1.daily")
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        period_row = session.scalar(
            select(BankReportingPeriod).where(BankReportingPeriod.id == UUID(period["id"]))
        )
        assert period_row is not None
        ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)

        def rc(column: str) -> ResolveContext:
            return ResolveContext(db=session, ctx=ctx, bank=bank, period=period_row, column=column)

        deposits = {"week": "previous", "position_types": ["DEPOSIT"], "currency": "GHS"}
        # previous-week WED (reporting date − 7) has a rung: Σ cedi deposits
        assert (
            daily(rc("wed"), deposits)
            == LADDER_CURRENT + LADDER_CALL + LADDER_FIXED + LADDER_SAVINGS
        )
        # current-week WED (the reporting date) has liquid-asset rungs but NO DEPOSIT
        # snapshot: the deposit population has no rung that day ⇒ None (input_required),
        # never a fabricated 0 (a nightly liquid-asset push must not zero the deposit book)
        assert daily(rc("wed"), {**deposits, "week": "current"}) is None
        # a population that IS on the day's rung but has no match reads 0: CDs among the
        # previous-week deposits (rung exists, none tagged)
        assert daily(rc("wed"), {**deposits, "attribute_eq": {"instrument": "cd"}}) == 0
        # the Sunday of the previous week has no rung at all ⇒ None (input_required)
        assert daily(rc("sun"), deposits) is None
        # facts fallback applies ONLY on the reporting date and only without a rung:
        # a date 30 days back has no rung and is not the reporting date ⇒ None
        assert (
            daily(
                rc("wed"),
                {
                    "days_before": 30,
                    "position_types": ["CASH"],
                    "facts": {"group": "balance_sheet", "categories": ["cash_vault"]},
                },
            )
            is None
        )
        # explicit ISO currency + native measure (Annex 1 balances)
        assert (
            daily(
                rc("wed"),
                {
                    "week": "current",
                    "position_types": ["CASH", "INTERBANK_PLACEMENT"],
                    "currency": "USD",
                    "measure": "native",
                },
            )
            == LADDER_USD_NOSTRO_USD + LADDER_BOG_USD_NATIVE
        )
        # a non-day column with no explicit offset resolves to nothing
        assert daily(rc("amount"), deposits) is None
    finally:
        session.close()


@pytest.mark.parametrize("column", ["thu", "wed"])
def test_bsd1_fx_spot_is_same_day_only(db_client: TestClient, column: str) -> None:
    from app.api.deps import TenantContext  # noqa: PLC0415
    from app.models import Bank  # noqa: PLC0415
    from app.services.regulatory_reporting.bog_forms.sources import (  # noqa: PLC0415
        ResolveContext,
        get_resolver,
    )
    from tests.fixtures.canonical_bank_fixture import DEMO_USER_ID  # noqa: PLC0415

    _materialize(db_client)
    period = _latest_period(db_client)
    reporting_date = date.fromisoformat(period["period_end"])
    _seed_ladder(reporting_date)  # one USD quote, dated the reporting date
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        period_row = session.scalar(
            select(BankReportingPeriod).where(BankReportingPeriod.id == UUID(period["id"]))
        )
        assert period_row is not None
        rc = ResolveContext(
            db=session,
            ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
            bank=bank,
            period=period_row,
            column=column,
        )
        value = get_resolver("bsd1.fx_spot")(rc, {"week": "current", "currency": "USD"})
        # WED = the reporting date ⇒ the quote; THU (six days earlier) has no observation
        # of its own ⇒ None even though a later quote exists (never carried backwards or forwards)
        assert value == (Decimal("12.5") if column == "wed" else None)
    finally:
        session.close()
