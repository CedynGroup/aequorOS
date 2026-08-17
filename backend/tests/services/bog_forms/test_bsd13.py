"""BSD13 — Net Open Position (Form FXP).

Proves the line map + resolvers against the official layout, the hermetic
Sample Bank's FX engine run and a small position / contract book inserted on
top of it:

1. every official data cell of the four sheets is bound (the templates ship
   the grids empty; the cell atlas is explicit) and no template formula is;
2. before an FX run the main sheet is honestly ``input_required``; after the
   baseline FX run the NOP cells equal the run's own figures — per-currency
   ``net_ccy`` / ``net_ghs``, AFOP = ``nop_ghs``, NOF = ``tier1_ghs``, AFOP %
   of NOF = ``nop_pct_tier1`` — in the template's units (currency UNITS for
   the named columns, cedi '000 on the main sheet);
3. the main sheet's own identity holds: NOP = (A) Net Assets + (C) Net Trading
   (contingents (B) being ``input_required``);
4. Schedule C is BoG's own arithmetic over the FX contract book: Net Spot =
   Spot Purchase + Spot Sale (sales negative), Net Forward likewise, NET
   TRADING = a + b; the annexure lists the outstanding forward contracts;
5. Schedule A's TA / TL / NET ASSETS formulas roll up the per-currency
   position sums (cash / placements / loans / deposits) and the *Other
   Currencies* column is the cedi 'Million equivalent.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import bsd13 as lm
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources_ext import bsd13 as ext
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAIN, SCH_A, SCH_B, SCH_C = lm.MAIN, lm.SCHEDULE_A, lm.SCHEDULE_B, lm.SCHEDULE_C

# ---------------------------------------------------------------------------
# 1. structure — the cell atlas
# ---------------------------------------------------------------------------

#: Every official data cell, by sheet (see docs/bog_returns/bsd13_line_map.md).
MAIN_CELLS = {f"{col}{row}" for col in "EHKN" for row in (19, 21, 24, 29, 32, 35)} | {
    f"C{row}" for row in (44, 45, 46, 47, 50, 52, 53, 55)
}
SCH_A_ROWS = (8, 10, 11, 12, 15, 16, 17, 19, 20, 21, 28, 30, 31, 33, 34, 35)
SCH_A_CELLS = {f"{col}{row}" for col in "CDEF" for row in SCH_A_ROWS}
SCH_B_CELLS = {f"{col}{row}" for col in "CDEF" for row in (9, 11, 13)}
SCH_C_CELLS = {f"{col}{row}" for col in "CDEF" for row in (9, 11, 16, 18)} | {
    f"{col}{row}" for col in "ABCDEFGHI" for row in (*range(35, 42), *range(48, 55))
}


def _bound(sheet: str) -> dict[str, Any]:
    return {ref: line for line in line_maps_for("BSD13")[sheet] for ref in line.cells.values()}


def test_line_map_binds_every_official_data_cell_and_no_formula() -> None:
    layout = load_layout("BSD13")
    expected = {MAIN: MAIN_CELLS, SCH_A: SCH_A_CELLS, SCH_B: SCH_B_CELLS, SCH_C: SCH_C_CELLS}
    for sheet, cells in expected.items():
        bound = _bound(sheet)
        assert set(bound) == cells, (sheet, sorted(set(bound) ^ cells)[:10])
        for ref in bound:
            cell = layout.sheet(sheet).by_ref.get(ref)
            assert cell is None or cell.kind == "input", (sheet, ref)
    # the template's only captured inputs (Schedule C serial numbers) are bound
    captured = {c.ref for c in layout.sheet(SCH_C).input_cells}
    assert captured <= set(_bound(SCH_C))
    # named-currency columns are foreign-currency UNITS (never scaled); the
    # cedi cells and the annexure amounts are scaled by the sheet unit
    for ref, line in _bound(SCH_A).items():
        assert line.unscaled is (ref[0] in "CDE"), ref
    for ref, line in _bound(MAIN).items():
        if ref[0] in "EHK" and int(ref[1:]) != 32:  # noqa: PLR2004 - row 32 is cedi '000
            assert line.unscaled, ref
        elif ref[0] in "N" or ref in ("C44", "C45", "C46", "C47", "C50", "C52"):
            assert not line.unscaled, ref
    assert _bound(MAIN)["C53"].unscaled and _bound(MAIN)["C55"].unscaled  # percentages
    for ref, line in _bound(SCH_C).items():
        if ref[0] == "E" and int(ref[1:]) >= 35:  # noqa: PLR2004 - annexure amounts
            assert not line.unscaled, ref
    # contingents and the management limit are declared, honestly input_required
    for ref in ("E21", "H21", "K21", "N21", "E35", "N35"):
        assert _bound(MAIN)[ref].source is None
    assert all(line.source is None for line in _bound(SCH_B).values())


# ---------------------------------------------------------------------------
# 2–5. generation through the real package pipeline
# ---------------------------------------------------------------------------

USD_LOAN = Decimal("1000000")
USD_CASH = Decimal("20000")
USD_PLACEMENT_ABROAD = Decimal("300000")
USD_DEPOSIT = Decimal("200000")
EUR_LOAN = Decimal("100000")
EUR_LOAN_GHS = Decimal("1400000")
FWD_SALE_USD = Decimal("600000")
FWD_SALE_RATE = Decimal("13.0")
SPOT_SALE_USD = Decimal("50000")
FWD_PURCHASE_GHS = Decimal("1300000")
FWD_PURCHASE_RATE = Decimal("0.1")  # USD per GHS → 130,000 USD bought
FWD_PURCHASE_USD = FWD_PURCHASE_GHS * FWD_PURCHASE_RATE


def _seed_book(session: Any, as_of: date) -> None:
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": as_of,
        "source_system": "API_PUSH",
        "validation_status": "accepted",
    }
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="API_PUSH",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="bsd13-test",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    common |= {"ingestion_batch_id": batch.id, "lineage_id": lineage.id}
    foreign_bank = CanonicalCounterparty(
        **common,
        source_reference="CP/CITI-NY",
        name="Citibank NA",
        counterparty_type="BANK_OECD",
        resident=False,
        country_code="US",
    )
    corporate = CanonicalCounterparty(
        **common,
        source_reference="CP/CORP",
        name="Volta Agro Ltd",
        counterparty_type="CORPORATE",
        resident=True,
        country_code="GH",
    )
    session.add_all([foreign_bank, corporate])
    session.flush()

    def position(  # noqa: PLR0913 - keyword-only builder
        ref: str,
        position_type: str,
        currency: str,
        balance: Decimal,
        *,
        counterparty: CanonicalCounterparty | None = None,
        maturity: date | None = None,
        origination: date | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        row = CanonicalPosition(
            **common,
            source_reference=ref,
            position_type=position_type,
            currency=currency,
            origination_date=origination,
        )
        session.add(row)
        session.flush()
        session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=row.id,
                counterparty_id=counterparty.id if counterparty else None,
                balance=balance,
                notional=balance,
                contractual_maturity=maturity,
                attributes=dict(attributes or {}),
            )
        )

    # Schedule A — USD book
    position("CASH/USD", "CASH", "USD", USD_CASH)
    position(
        "IBP/USD",
        "INTERBANK_PLACEMENT",
        "USD",
        USD_PLACEMENT_ABROAD,
        counterparty=foreign_bank,
        maturity=as_of + timedelta(days=30),
    )
    position("LOAN/USD", "LOAN", "USD", USD_LOAN, counterparty=corporate)
    position("DEP/USD", "DEPOSIT", "USD", USD_DEPOSIT, counterparty=corporate)
    # Other currencies (cedi 'Million column): a EUR loan with its cedi value
    position("LOAN/EUR", "LOAN", "EUR", EUR_LOAN, attributes={"balance_ghs": str(EUR_LOAN_GHS)})
    # Schedule C — the FX contract book (engine convention: balance = notional
    # in the SELL currency; buy leg = notional × contract_rate)
    position(
        "FWD/SALE",
        "FX_HEDGE",
        "USD",
        FWD_SALE_USD,
        counterparty=foreign_bank,
        maturity=as_of + timedelta(days=90),
        origination=as_of - timedelta(days=10),
        attributes={
            "instrument": "FORWARD",
            "sell_currency": "USD",
            "buy_currency": "GHS",
            "contract_rate": str(FWD_SALE_RATE),
        },
    )
    position(
        "SPOT/SALE",
        "FX_HEDGE",
        "USD",
        SPOT_SALE_USD,
        maturity=as_of + timedelta(days=1),
        attributes={"instrument": "spot", "sell_currency": "USD", "buy_currency": "GHS"},
    )
    position(
        "FWD/PURCHASE",
        "FX_HEDGE",
        "GHS",
        FWD_PURCHASE_GHS,
        counterparty=foreign_bank,
        maturity=as_of + timedelta(days=30),
        attributes={
            "instrument": "forward",
            "sell_currency": "GHS",
            "buy_currency": "USD",
            "contract_rate": str(FWD_PURCHASE_RATE),
        },
    )
    session.flush()


def _prepare(db_client: TestClient) -> tuple[str, str]:
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        materialize_canonical_test_book(session)
        session.commit()
        period = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
        ).json()["periods"][0]
        _seed_book(session, date.fromisoformat(period["period_end"]))
        session.commit()
    finally:
        session.close()
    return period["id"], period["period_end"]


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD13", "reporting_date": reporting_date},
    )
    assert response.status_code == 201, response.text[:400]
    package = response.json()
    return db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()["snapshot"]


def _run_fx(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text[:400]
    baseline = next(r for r in response.json()["runs"] if r["scenario_code"] == "baseline")
    assert baseline["status"] == "succeeded", baseline
    return baseline["metrics"]


def _rows(snapshot: dict[str, Any], sheet: str) -> dict[str, dict[str, Any]]:
    section = next(s for s in snapshot["sections"] if s["title"] == sheet)
    return {row["cell"]: row for row in section["rows"]}


def _statuses(snapshot: dict[str, Any], sheet: str) -> dict[str, str]:
    return {cell: row["status"] for cell, row in _rows(snapshot, sheet).items()}


def _num(cells: dict[str, Any], ref: str) -> float:
    value = cells.get(ref)
    assert value not in (None, ""), f"{ref} is blank"
    return float(value)


def test_bsd13_reports_the_fx_engines_nop_and_bogs_schedule_arithmetic(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period_id, reporting_date = _prepare(db_client)

    # --- before any FX run: run-level figures (AFOP, NOF, %) are honestly
    # input_required; the per-currency NOP falls back to the fx_position fact
    before = _generate(db_client, reporting_date)
    main_before = _statuses(before, MAIN)
    assert main_before["C50"] == "input_required"
    assert main_before["C52"] == "input_required"
    assert main_before["C53"] == "input_required"
    assert main_before["E29"] == "mapped"
    assert not before["bog_form"]["errors"], before["bog_form"]["errors"]

    # --- run the FX engine, then generate --------------------------------
    metrics = _run_fx(db_client, period_id)
    snapshot = _generate(db_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    cells = payload["cells"]  # BASE units (currency units / cedis / percent)
    main, sch_a, sch_c = cells[MAIN], cells[SCH_A], cells[SCH_C]
    by_ccy = {c["currency"]: c for c in metrics["currencies"]}

    # 2. the main sheet == the FX run
    usd, gbp = by_ccy["USD"], by_ccy["GBP"]
    assert _num(main, "E29") == float(usd["net_ccy"])  # NOP in USD units
    assert _num(main, "H29") == float(gbp["net_ccy"])
    assert _num(main, "E32") == float(usd["net_ghs"])  # cedi equivalent
    assert _num(main, "C44") == float(usd["net_ghs"])
    assert _num(main, "C45") == float(gbp["net_ghs"])
    others = sum(float(c["net_ghs"]) for k, c in by_ccy.items() if k not in ext.NAMED_CURRENCIES)
    assert abs(_num(main, "N29") - others) < 1e-6
    assert abs(_num(main, "C47") - others) < 1e-6
    assert _num(main, "C50") == float(metrics["nop_ghs"])  # AFOP
    assert _num(main, "C52") == float(metrics["tier1_ghs"])  # NOF
    assert _num(main, "C53") == float(metrics["nop_pct_tier1"])  # AFOP % of NOF
    assert _num(main, "C55") == float(metrics["nop_aggregate_limit_pct"])
    # …and the exported units are the template's: currency UNITS unscaled on
    # the named columns, cedi '000 on the main sheet's cedi cells, percent as is
    rows = _rows(snapshot, MAIN)
    assert float(rows["E29"]["value"]) == float(usd["net_ccy"])
    assert abs(float(rows["E32"]["value"]) - float(usd["net_ghs"]) / 1000) < 1e-6
    assert abs(float(rows["C50"]["value"]) - float(metrics["nop_ghs"]) / 1000) < 1e-6
    assert float(rows["C53"]["value"]) == float(metrics["nop_pct_tier1"])
    statuses = _statuses(snapshot, MAIN)
    assert statuses["K29"] == "input_required"  # no DEM in the book: blank, never 0
    assert statuses["E21"] == "input_required"  # contingents — no crystallisation flag
    assert statuses["E35"] == "input_required"  # management limit — declared figure

    # 3. NOP = (A) net assets + (C) net trading, per currency (B blank)
    for col in ("E", "H"):
        assert abs(_num(main, f"{col}19") + _num(main, f"{col}24") - _num(main, f"{col}29")) < 1e-6

    # 4. Schedule C — BoG's formulas over the contract book (USD units)
    assert _num(sch_c, "C9") == 0.0  # no spot purchases of USD
    assert _num(sch_c, "C11") == -float(SPOT_SALE_USD)  # spot sale, negative (S −)
    assert _num(sch_c, "C13") == _num(sch_c, "C9") + _num(sch_c, "C11")  # =C9+C11
    assert _num(sch_c, "C16") == float(FWD_PURCHASE_USD)  # forward purchase (buy leg)
    assert _num(sch_c, "C18") == -float(FWD_SALE_USD)  # forward sale, negative
    assert _num(sch_c, "C20") == _num(sch_c, "C16") + _num(sch_c, "C18")  # =C16+C18
    assert _num(sch_c, "C23") == _num(sch_c, "C13") + _num(sch_c, "C20")  # =C13+C20
    assert _num(sch_c, "C23") == float(-SPOT_SALE_USD + FWD_PURCHASE_USD - FWD_SALE_USD)
    # annexure: purchase slot 1 = the 130k USD forward; sale slot 1 = the 600k forward
    assert sch_c["A35"] == 1 and sch_c["A48"] == 1  # template serial numbers kept
    assert sch_c["D35"] == "USD"
    assert _num(sch_c, "E35") == float(FWD_PURCHASE_USD)
    assert abs(_num(sch_c, "G35") - 1 / float(FWD_PURCHASE_RATE)) < 1e-9  # cedis per USD
    assert sch_c["C48"] == "Citibank NA"
    assert sch_c["D48"] == "USD"
    assert _num(sch_c, "E48") == float(FWD_SALE_USD)
    c_rows = _rows(snapshot, SCH_C)
    assert abs(float(c_rows["E48"]["value"]) - float(FWD_SALE_USD) / 1e6) < 1e-9  # ccy 'Million
    assert float(c_rows["C18"]["value"]) == -float(FWD_SALE_USD)  # currency units, unscaled
    assert _num(sch_c, "F48") == 100  # noqa: PLR2004 - 10 days before + 90 after
    assert _num(sch_c, "G48") == float(FWD_SALE_RATE)
    assert abs(_num(sch_c, "H48") - (float(FWD_SALE_RATE) - float(usd["spot_ghs"]))) < 1e-9
    assert sch_c.get("D36") is None and sch_c.get("D49") is None  # empty slots stay blank
    c_statuses = _statuses(snapshot, SCH_C)
    assert c_statuses["D36"] == "input_required" and c_statuses["E49"] == "input_required"

    # 5. Schedule A — per-currency position sums roll up through BoG's formulas
    assert _num(sch_a, "C8") == float(USD_CASH)  # cash on hand (no counterparty)
    assert _num(sch_a, "C16") == float(USD_PLACEMENT_ABROAD)  # placement at overseas bank
    assert _num(sch_a, "C20") == float(USD_LOAN)
    assert _num(sch_a, "C31") == float(USD_DEPOSIT)  # resident FX deposit → internal by default
    assert _num(sch_a, "C23") == float(USD_CASH + USD_PLACEMENT_ABROAD + USD_LOAN)  # TA
    assert _num(sch_a, "C37") == float(USD_DEPOSIT)  # TL
    assert _num(sch_a, "C39") == _num(sch_a, "C23") - _num(sch_a, "C37")  # NET ASSETS = TA − TL
    assert _num(sch_a, "F20") == float(EUR_LOAN_GHS)  # other currencies: cedi equivalent
    assert _num(sch_a, "F23") == float(EUR_LOAN_GHS)
    a_rows = _rows(snapshot, SCH_A)
    assert abs(float(a_rows["F20"]["value"]) - float(EUR_LOAN_GHS) / 1e6) < 1e-9  # 'Million
    assert float(a_rows["C20"]["value"]) == float(USD_LOAN)  # USD units, unscaled
    a_statuses = _statuses(snapshot, SCH_A)
    assert a_statuses["C17"] == "input_required"  # customer-account placements (memo)


def test_contract_classification_and_sector_free_helpers() -> None:
    """Spot vs forward: explicit settlement wins, then instrument, then T+2."""
    as_of = date(2026, 6, 30)
    assert ext._classify({"settlement": "forward"}, as_of + timedelta(days=1), as_of) == "forward"
    assert ext._classify({"instrument": "FX_SPOT"}, as_of + timedelta(days=90), as_of) == "spot"
    assert ext._classify({}, as_of + timedelta(days=2), as_of) == "spot"
    assert ext._classify({}, as_of + timedelta(days=3), as_of) == "forward"
    assert ext._classify({"instrument": "option"}, None, as_of) == "forward"
