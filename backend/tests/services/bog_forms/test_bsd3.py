"""BSD3A / BSD3B — Large Exposures: Advances and Deposits (Wave 2).

Generates both forms through the REAL package pipeline on the hermetic book,
with a canonical counterparty/position slice inserted so the ranked rosters
are meaningful, and proves:

1. every official cell of the three sheets is bound (declared): the 20/10/50
   ranked rows (blank grid cells), the accrued-interest / count / numbering
   rows — nothing unmapped, no engine errors;
2. rank ordering on every sheet (rank 1 ≥ rank 2 ≥ …, non-blank rows first);
3. BoG's own arithmetic over our inputs: Sheet-1 ``G26 = SUM(G6:G25)`` equals
   Σ listed amounts and every row total ``G = E + F`` (Sheet-2 ``F = D + E``,
   Sheet-3 ``G = D + E + F``);
4. reconciliation to positions: Σ ranked amounts = Σ counterparty-attributed
   canonical positions in each population (unattributed / other-date rows
   excluded), the depositor count, the connected-group aggregation, the
   sovereign-in-non-monetary rule, and the FX/cedi split;
5. the values-only xlsx export carries names + ¢'Million-scaled amounts, the
   unscaled count and the template's row numbers;
6. BSD3B (group, per subsidiary): same structure, every roster cell
   input_required (no subsidiary book), only the numbering constants resolve;
7. the ``bsd3.rank`` / ``bsd3.count`` resolvers unit-tested against the rows.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.bog_forms.catalog import form_spec
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from app.services.regulatory_reporting.bog_forms.sources_ext.bsd3 import (
    MONETARY_SECTOR_TYPES,
    build_ranking,
)
from app.services.regulatory_reporting.exports import render_bog_form_xlsx
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

S1, S2, S3 = "BSD3-Sheet-1", "BSD3-Sheet-2", "BSD3-Sheet-3"
M = Decimal("1000000")

# ---------------------------------------------------------------------------
# canonical slice — distinct balances so every ranking is unambiguous
# ---------------------------------------------------------------------------


class _Seeder:
    """Batch + lineage + counterparty/position builders at ``as_of``."""

    def __init__(self, db: Session, as_of: date) -> None:
        self.db = db
        self.as_of = as_of
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=as_of,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="bsd3-fixture",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        self.common: dict[str, Any] = {
            "organization_id": ORG_1,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": as_of,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }

    def counterparty(
        self, ref: str, name: str, counterparty_type: str, *, group: str | None = None
    ) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            source_reference=ref,
            name=name,
            counterparty_type=counterparty_type,
            group_reference=group,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def position(  # noqa: PLR0913 — keyword-only fixture builder
        self,
        ref: str,
        position_type: str,
        amount: Decimal,
        *,
        counterparty: CanonicalCounterparty | None,
        currency: str = "GHS",
        maturity: date | None = None,
        account_type: str | None = None,
        as_of: date | None = None,
        notional: bool = False,
    ) -> None:
        common = dict(self.common)
        if as_of is not None:
            common["as_of_date"] = as_of
        position = CanonicalPosition(
            **common, source_reference=ref, position_type=position_type, currency=currency
        )
        self.db.add(position)
        self.db.flush()
        attributes: dict[str, Any] = {"balance_ghs": str(amount)}
        if notional:
            attributes["notional_ghs"] = str(amount)
        self.db.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                counterparty_id=counterparty.id if counterparty is not None else None,
                balance=amount,
                notional=amount if notional else None,
                contractual_maturity=maturity,
                deposit_account_type=account_type,
                attributes=attributes,
            )
        )
        self.db.flush()


def _seed_book(db: Session, as_of: date) -> None:
    s = _Seeder(db, as_of)
    kumasi = s.counterparty("CP/KUMASI", "Kumasi Traders Ltd", "CORPORATE")
    alpha = s.counterparty("CP/ALPHA", "Volta Alpha Ltd", "CORPORATE", group="VOLTA-GROUP")
    beta = s.counterparty("CP/BETA", "Volta Beta Ltd", "CORPORATE", group="VOLTA-GROUP")
    ama = s.counterparty("CP/AMA", "Ama Mensah", "RETAIL_INDIVIDUAL")
    gcb = s.counterparty("CP/GCB", "GCB Bank Ltd", "BANK_NON_OECD")
    bog = s.counterparty("CP/BOG", "Bank of Ghana", "CENTRAL_BANK")
    fdh = s.counterparty("CP/FDH", "Fidelity Discount House", "NBFI")
    gog = s.counterparty("CP/GOG", "Government of Ghana", "SOVEREIGN")
    old = s.counterparty("CP/OLD", "Old Corp (prior month only)", "CORPORATE")

    # --- depositors (Sheet 1): Kumasi 8.0m > GCB 7.0m > VOLTA-GROUP 6.5m > Ama 1.2m
    s.position("DEP/KUM/CUR", "DEPOSIT", 5 * M, counterparty=kumasi, account_type="CURRENT")
    s.position(
        "DEP/KUM/USD",
        "DEPOSIT",
        3 * M,
        counterparty=kumasi,
        currency="USD",
        account_type="FIXED",
        maturity=date(2027, 6, 30),
    )
    s.position("DEP/GCB", "DEPOSIT", 7 * M, counterparty=gcb, account_type="CALL")
    s.position("DEP/ALPHA", "DEPOSIT", 4 * M, counterparty=alpha, account_type="CURRENT")
    s.position(
        "DEP/BETA",
        "DEPOSIT",
        Decimal("2500000"),
        counterparty=beta,
        account_type="FIXED",
        maturity=date(2026, 12, 31),
    )
    s.position("DEP/AMA", "DEPOSIT", Decimal("1200000"), counterparty=ama, account_type="SAVINGS")
    # pooled retail deposits with no counterparty: real money, not a "depositor"
    s.position("DEP/POOL", "DEPOSIT", 50 * M, counterparty=None, account_type="SAVINGS")

    # --- monetary-sector exposures (Sheet 2): GCB 10.0m > BoG 4.0m > FDH 2.5m
    s.position(
        "PLC/GCB", "INTERBANK_PLACEMENT", 9 * M, counterparty=gcb, maturity=date(2026, 9, 30)
    )
    s.position("SEC/GCB", "SECURITY_HOLDING", 1 * M, counterparty=gcb, currency="USD")
    s.position("SEC/BOG", "SECURITY_HOLDING", 4 * M, counterparty=bog)
    s.position("LOAN/FDH", "LOAN", 2 * M, counterparty=fdh, maturity=date(2027, 3, 31))
    s.position("LC/FDH", "LC_GUARANTEE", Decimal("500000"), counterparty=fdh, notional=True)

    # --- non-monetary exposures (Sheet 3): GoG 20.0m > Kumasi 16.0m > VOLTA 11.0m > Ama 0.3m
    s.position("SEC/GOG", "SECURITY_HOLDING", 20 * M, counterparty=gog)
    s.position("LOAN/KUM", "LOAN", 12 * M, counterparty=kumasi, maturity=date(2028, 3, 31))
    s.position("UND/KUM", "COMMITMENT_UNDRAWN", 3 * M, counterparty=kumasi, notional=True)
    s.position("LC/KUM", "LC_GUARANTEE", 1 * M, counterparty=kumasi, notional=True)
    s.position("LOAN/ALPHA", "LOAN", 6 * M, counterparty=alpha)
    s.position("LOAN/BETA", "LOAN", 5 * M, counterparty=beta)
    s.position("LOAN/AMA", "LOAN", Decimal("300000"), counterparty=ama)
    # a prior-month snapshot: outside the period-end slice, must never rank
    s.position("LOAN/OLD", "LOAN", 99 * M, counterparty=old, as_of=as_of - timedelta(days=31))


def _period_end(db_client: TestClient) -> str:
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]["period_end"]


def _session() -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    return session


@pytest.fixture
def seeded_book(db_client: TestClient) -> str:
    """Hermetic book + the BSD3 canonical slice at the latest period end."""
    session = _session()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    reporting_date = _period_end(db_client)
    session = _session()
    try:
        _seed_book(session, date.fromisoformat(reporting_date))
        session.commit()
    finally:
        session.close()
    return reporting_date


def _generate(db_client: TestClient, code: str, reporting_date: str) -> dict[str, Any]:
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


def _declared_cells(code: str) -> dict[str, set[str]]:
    return {
        sheet: {ref for line in lines for ref in line.cells.values()}
        for sheet, lines in line_maps_for(code).items()
    }


def _ranked(cells: dict[str, Any], rows: range, name_col: str, total_col: str) -> list[float]:
    """Row totals (template formulas) of the rows that carry a ranked name.

    A blank roster row's total formula evaluates to 0 (Excel semantics), so
    the listed amounts are the totals of NAMED rows; the trailing zeros must
    still respect the ordering (asserted by callers over the whole column).
    """
    return [
        float(cells[f"{total_col}{r}"]) for r in rows if cells.get(f"{name_col}{r}") is not None
    ]


def _column(cells: dict[str, Any], rows: range, col: str) -> list[float]:
    return [float(cells.get(f"{col}{r}") or 0) for r in rows]


# ---------------------------------------------------------------------------
# 1. structure: every official cell of the three sheets is bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["BSD3A", "BSD3B"])
def test_every_cell_of_the_three_sheets_is_declared(code: str) -> None:
    layout = load_layout(code)
    declared = _declared_cells(code)
    assert set(declared) == {S1, S2, S3} == set(layout.sheet_names)
    # captured (numeric) input cells: A27:A30 numbering — all declared
    for sheet in layout.sheets:
        for cell in sheet.input_cells:
            assert cell.ref in declared[sheet.name], f"{code}/{sheet.name}!{cell.ref}"
    # the ranked grids: 20 × (B..F,H) + accrued E27/F27 + count G30 + A27:A30
    assert len(declared[S1]) == 20 * 6 + 2 + 1 + 4
    # 10 × (B,C,D,E,G,H,I)
    assert len(declared[S2]) == 10 * 7
    # 50 × (B,C,D,E,F,H,I,J)
    assert len(declared[S3]) == 50 * 8
    for sheet_name, refs in declared.items():
        sheet = layout.sheet(sheet_name)
        for ref in refs:
            cell = sheet.by_ref.get(ref)
            assert cell is None or cell.kind == "input", f"{code}/{sheet_name}!{ref} {cell}"
    # unique codes per sheet
    for lines in line_maps_for(code).values():
        codes = [line.code for line in lines]
        assert len(codes) == len(set(codes))


def test_bsd3a_map_status_split_is_what_the_doc_claims() -> None:
    spec = form_spec("BSD3A")
    per_sheet: dict[str, tuple[int, int]] = {}
    for sheet in spec.sheets:
        mapped = sum(len(line.cells) for line in sheet.lines if line.source is not None)
        pending = sum(len(line.cells) for line in sheet.lines if line.source is None)
        per_sheet[sheet.name] = (mapped, pending)
    assert per_sheet == {S1: (105, 22), S2: (50, 20), S3: (250, 150)}


# ---------------------------------------------------------------------------
# 2–5. BSD3A end to end
# ---------------------------------------------------------------------------


def test_bsd3a_generates_ranks_and_reconciles(  # noqa: PLR0915 — one linear proof over three sheets
    db_client: TestClient, seeded_book: str
) -> None:
    snapshot = _generate(db_client, "BSD3A", seeded_book)
    payload = snapshot["bog_form"]
    assert payload["code"] == "BSD3A"
    assert not payload["errors"], payload["errors"]
    assert payload["unmapped_cells"] == []
    assert payload["missing_dependencies"] == []
    counts = payload["status_counts"]
    declared = _declared_cells("BSD3A")
    assert counts["mapped"] + counts["input_required"] == sum(len(v) for v in declared.values())
    assert counts["unmapped"] == 0
    cells = payload["cells"]
    s1, s2, s3 = cells[S1], cells[S2], cells[S3]

    # ---- Sheet 1: twenty largest depositors -------------------------------
    assert s1["B6"] == "Kumasi Traders Ltd"
    assert float(s1["E6"]) == 3_000_000  # USD fixed deposit, cedi equivalent
    assert float(s1["F6"]) == 5_000_000
    assert float(s1["G6"]) == 8_000_000  # template formula =E6+F6
    assert s1["C6"] == "CURRENT, FIXED"
    assert s1["D6"] == "2027-06-30"  # final (latest) maturity of the aggregate
    assert s1["B7"] == "GCB Bank Ltd" and float(s1["G7"]) == 7_000_000
    assert s1["B8"] == "VOLTA-GROUP" and float(s1["G8"]) == 6_500_000  # connected group
    assert s1["D8"] == "2026-12-31"
    assert s1["B9"] == "Ama Mensah" and float(s1["G9"]) == 1_200_000
    for row in range(10, 26):  # ranks 5–20: blank, never zero-filled
        assert s1.get(f"B{row}") is None and s1.get(f"E{row}") is None
        assert s1.get(f"F{row}") is None
    listed = _ranked(s1, range(6, 26), "B", "G")
    assert listed == [8_000_000, 7_000_000, 6_500_000, 1_200_000]
    column = _column(s1, range(6, 26), "G")
    assert column == sorted(column, reverse=True)  # rank 1 ≥ rank 2 ≥ … (blank rows = 0)
    for row in range(6, 26):  # BoG's row arithmetic
        g, e, f = s1.get(f"G{row}"), s1.get(f"E{row}") or 0, s1.get(f"F{row}") or 0
        assert abs(float(g or 0) - (float(e) + float(f))) < 1e-6
    assert abs(float(s1["G26"]) - sum(listed)) < 1e-6  # =SUM(G6:G25)
    assert float(s1["G26"]) == 22_700_000
    assert s1.get("E27") is None and s1.get("F27") is None  # accrued interest: input_required
    assert float(s1["G28"]) == float(s1["G26"])  # =G26+G27 (blank accrued → 0)
    assert abs(float(s1["G29"]) - 1.0) < 1e-9  # =G26/G28
    assert float(s1["G30"]) == 5  # distinct depositors (pooled deposit unattributed)
    assert [s1[f"A{r}"] for r in (27, 28, 29, 30)] == [22, 23, 24, 25]

    # ---- Sheet 2: ten largest monetary-sector exposures --------------------
    assert s2["B6"] == "GCB Bank Ltd"
    assert float(s2["D6"]) == 1_000_000 and float(s2["E6"]) == 9_000_000
    assert float(s2["F6"]) == 10_000_000  # =D6+E6
    assert float(s2["G6"]) == 10_000_000  # of which on balance sheet
    assert s2["C6"] == "2026-09-30"
    assert s2["B7"] == "Bank of Ghana" and float(s2["F7"]) == 4_000_000
    assert s2["B8"] == "Fidelity Discount House"
    assert float(s2["F8"]) == 2_500_000 and float(s2["G8"]) == 2_000_000  # LC is off-balance
    for row in range(9, 16):
        assert s2.get(f"B{row}") is None
    ranked2 = _ranked(s2, range(6, 16), "B", "F")
    assert ranked2 == [10_000_000, 4_000_000, 2_500_000]
    column2 = _column(s2, range(6, 16), "F")
    assert column2 == sorted(column2, reverse=True)
    for row in range(6, 16):
        assert s2.get(f"H{row}") is None and s2.get(f"I{row}") is None  # security / remarks

    # ---- Sheet 3: fifty largest non-monetary-sector exposures --------------
    assert s3["B5"] == "Government of Ghana" and float(s3["G5"]) == 20_000_000
    assert s3["B6"] == "Kumasi Traders Ltd"
    assert float(s3["D6"]) == 12_000_000  # drawn
    assert float(s3["E6"]) == 3_000_000  # undrawn facility
    assert float(s3["F6"]) == 1_000_000  # other contingent
    assert float(s3["G6"]) == 16_000_000  # =D6+E6+F6
    assert s3["C6"] == "Guarantee / contingent; Loan / advance; Undrawn commitment"
    assert s3["B7"] == "VOLTA-GROUP" and float(s3["G7"]) == 11_000_000
    assert s3["B8"] == "Ama Mensah" and float(s3["G8"]) == 300_000
    for row in range(9, 55):
        assert s3.get(f"B{row}") is None
    names = [s3.get(f"B{r}") for r in range(5, 55)]
    assert "Old Corp (prior month only)" not in names  # prior-month snapshot excluded
    ranked3 = _ranked(s3, range(5, 55), "B", "G")
    assert ranked3 == [20_000_000, 16_000_000, 11_000_000, 300_000]
    column3 = _column(s3, range(5, 55), "G")
    assert column3 == sorted(column3, reverse=True)

    # ---- reconciliation to the canonical positions -------------------------
    session = _session()
    try:
        period_end = date.fromisoformat(seeded_book)
        rows = session.execute(
            select(
                CanonicalPosition.position_type,
                CanonicalPositionSnapshot.balance,
                CanonicalCounterparty.counterparty_type,
            )
            .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
            .join(
                CanonicalCounterparty,
                CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
            )
            .where(
                CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
                CanonicalPositionSnapshot.as_of_date == period_end,
            )
        ).all()
    finally:
        session.close()
    deposits = sum(float(b) for t, b, _ in rows if t == "DEPOSIT")
    monetary = sum(float(b) for t, b, c in rows if t != "DEPOSIT" and c in MONETARY_SECTOR_TYPES)
    non_monetary = sum(
        float(b) for t, b, c in rows if t != "DEPOSIT" and c not in MONETARY_SECTOR_TYPES
    )
    assert abs(sum(listed) - deposits) < 1e-6
    assert abs(sum(ranked2) - monetary) < 1e-6
    assert abs(sum(ranked3) - non_monetary) < 1e-6

    # ---- export: names, ¢'Million scaling, unscaled count, row numbers -----
    session = _session()
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        xlsx = render_bog_form_xlsx("BSD3A", snapshot, bank, datetime(2026, 8, 16, tzinfo=UTC))
    finally:
        session.close()
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), data_only=False)
    ws1 = wb[S1]
    assert ws1["B6"].value == "Kumasi Traders Ltd"
    assert ws1["E6"].value == 3.0 and ws1["F6"].value == 5.0  # ¢'Million
    assert ws1["G26"].value == 22.7
    assert ws1["G30"].value == 5  # a count — not scaled
    assert ws1["A27"].value == 22 and ws1["A30"].value == 25
    assert ws1["B10"].value is None
    ws3 = wb[S3]
    assert ws3["B5"].value == "Government of Ghana" and ws3["G5"].value == 20.0
    assert wb.sheetnames[-1] == "Completion notes"


# ---------------------------------------------------------------------------
# 6. BSD3B — group basis, per subsidiary: structure only until a subsidiary book
# ---------------------------------------------------------------------------


def test_bsd3b_is_structure_only_until_a_subsidiary_book_exists(
    db_client: TestClient, seeded_book: str
) -> None:
    snapshot = _generate(db_client, "BSD3B", seeded_book)
    payload = snapshot["bog_form"]
    assert payload["basis"] == "consolidated"
    assert not payload["errors"], payload["errors"]
    assert payload["unmapped_cells"] == []
    counts = payload["status_counts"]
    assert counts["mapped"] == 4  # only the template's own row numbers A27:A30
    assert counts["unmapped"] == 0
    declared = _declared_cells("BSD3B")
    assert counts["input_required"] == sum(len(v) for v in declared.values()) - 4
    cells = payload["cells"]
    # the bank's OWN depositors never leak into a subsidiary's roster
    assert all(cells[S1].get(f"B{r}") is None for r in range(6, 26))
    assert all(cells[S3].get(f"B{r}") is None for r in range(5, 55))
    assert [cells[S1][f"A{r}"] for r in (27, 28, 29, 30)] == [22, 23, 24, 25]
    subsidiary_notes = [
        row["notes"]
        for section in snapshot["sections"]
        for row in section["rows"]
        if row["status"] == "input_required" and "subsidiary" in row["notes"]
    ]
    assert len(subsidiary_notes) == 20 * 5 + 1 + 10 * 5 + 50 * 5


# ---------------------------------------------------------------------------
# 7. the resolvers, against the inserted rows
# ---------------------------------------------------------------------------


def test_bsd3_rank_and_count_resolvers(db_client: TestClient, seeded_book: str) -> None:
    session = _session()
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        period = session.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == date.fromisoformat(seeded_book),
            )
        )
        assert bank is not None and period is not None
        ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
        cache: dict[str, Any] = {}

        def rc(column: str) -> ResolveContext:
            return ResolveContext(
                db=session, ctx=ctx, bank=bank, period=period, column=column, cache=cache
            )

        rank = get_resolver("bsd3.rank")
        count = get_resolver("bsd3.count")
        # field defaults to the bound column key
        assert rank(rc("name"), {"kind": "depositor", "rank": 1}) == "Kumasi Traders Ltd"
        assert rank(rc("cedi"), {"kind": "depositor", "rank": 1}) == Decimal("5000000")
        assert rank(rc("foreign"), {"kind": "depositor", "rank": 1}) == Decimal("3000000")
        assert rank(rc("currency"), {"kind": "depositor", "rank": 1}) == "GHS, USD"
        # explicit field override wins over the column
        assert rank(rc("name"), {"kind": "depositor", "rank": 3, "field": "amount"}) == Decimal(
            "6500000"
        )
        assert rank(rc("name"), {"kind": "depositor", "rank": 3}) == "VOLTA-GROUP"
        # beyond the population: None for every field (row stays blank)
        assert rank(rc("name"), {"kind": "depositor", "rank": 5}) is None
        assert rank(rc("cedi"), {"kind": "depositor", "rank": 20}) is None
        # monetary / non-monetary populations
        assert rank(rc("name"), {"kind": "monetary_exposure", "rank": 1}) == "GCB Bank Ltd"
        assert rank(rc("on_balance"), {"kind": "monetary_exposure", "rank": 3}) == Decimal(
            "2000000"
        )
        assert rank(rc("contingent"), {"kind": "monetary_exposure", "rank": 3}) == Decimal("500000")
        assert rank(rc("name"), {"kind": "non_monetary_exposure", "rank": 2}) == (
            "Kumasi Traders Ltd"
        )
        assert rank(rc("undrawn"), {"kind": "non_monetary_exposure", "rank": 2}) == Decimal(
            "3000000"
        )
        assert rank(rc("maturity"), {"kind": "non_monetary_exposure", "rank": 2}) == "2028-03-31"
        assert rank(rc("maturity"), {"kind": "non_monetary_exposure", "rank": 1}) is None
        # counts
        assert count(rc("count"), {"kind": "depositor"}) == 5
        assert count(rc("count"), {"kind": "monetary_exposure"}) == 3
        # Kumasi + Alpha + Beta + Ama + GoG (VOLTA-GROUP is one line, two counterparties)
        assert count(rc("count"), {"kind": "non_monetary_exposure"}) == 5
        # one canonical load per form computation (memoised)
        assert "bsd3:rows" in cache and "bsd3:ranking:depositor" in cache
        with pytest.raises(ValueError, match="unknown field"):
            rank(rc("nope"), {"kind": "depositor", "rank": 1})
        with pytest.raises(ValueError, match="unknown kind"):
            build_ranking(cache["bsd3:rows"], "lenders", "GHS")
    finally:
        session.close()
