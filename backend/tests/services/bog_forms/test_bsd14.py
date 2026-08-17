"""BSD14 — Weekly Return on Interest Rates.

Proves the line map + resolver against the official layout and a deposit /
loan book inserted on top of the hermetic Sample Bank:

1. every official rate cell (5 currency rows × 20 product columns), the BASE
   RATE cell and the template's captured tenor headers are bound; nothing
   else; every rate cell is ``unscaled`` (percent);
2. the form generates through the REAL package pipeline; a rate cell equals
   the balance-weighted mean of the positions' ``interest_rate`` (× 100),
   time deposits land in the NEAREST official tenor column, lending rates
   follow the documented ``sector`` attribute (BSD4 vocabulary) with
   unclassified loans in *Others*, foreign-currency rows read their own
   currency; a product with no position — or no rate — stays
   ``input_required`` (never 0), as does the declared BASE RATE;
3. the tenor headers export verbatim (1 · 2 · 3 · 6 · 12 · 24 · 36).
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
from app.services.regulatory_reporting.bog_forms.linemaps import bsd14 as lm
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources_ext import bsd14 as ext
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

SHEET = lm.SHEET
RATE_CELLS = {f"{col}{row}" for col in lm.RATE_COLUMNS.values() for row in lm.CURRENCY_ROWS}
TENOR_CELLS = {f"{col}14" for col in "DEFGHIJ"}


def test_line_map_binds_the_rate_grid_the_base_rate_and_the_tenor_headers() -> None:
    layout = load_layout("BSD14").sheet(SHEET)
    lines = line_maps_for("BSD14")[SHEET]
    bound = {ref: line for line in lines for ref in line.cells.values()}
    assert set(bound) == RATE_CELLS | TENOR_CELLS | {"B9"}
    assert len(RATE_CELLS) == 100  # noqa: PLR2004 - 5 currency rows × 20 products
    assert {c.ref for c in layout.input_cells} == TENOR_CELLS  # the only captured inputs
    for ref in RATE_CELLS:
        line = bound[ref]
        assert line.source == "bsd14.rate" and line.unscaled, ref
        assert "product rate table required" in line.notes
    assert bound["B9"].source is None  # BASE RATE — declared figure
    assert bound["D14"].source == "bsd14.column_constant"


def test_sector_groups_and_tenor_buckets() -> None:
    assert ext.sector_group("agriculture.cocoa_production") == "agriculture"
    assert ext.sector_group("manufacturing.export.iron_steel") == "exports"
    assert ext.sector_group("commerce.export.cocoa") == "exports"
    assert ext.sector_group("manufacturing.home.food_drink_tobacco") == "manufacturing"
    assert ext.sector_group("commerce.import.other") == "imports"
    assert ext.sector_group("commerce.other") == "commerce"
    assert ext.sector_group("mining.gold") == "mining"
    assert ext.sector_group("Mining/Quarrying") == "mining"
    assert ext.sector_group("construction.building_construction") == "construction"
    assert ext.sector_group("services.business") == "others"
    assert ext.sector_group("transport.road") == "others"
    assert ext.sector_group(None) is None
    assert [ext.tenor_bucket(Decimal(m)) for m in ("1", "1.4", "1.6", "4", "5", "9.5", "40")] == [
        1, 1, 2, 3, 6, 12, 36
    ]  # fmt: skip
    assert ext.tenor_bucket(None) is None


# ---------------------------------------------------------------------------
# the book: (ref, type, currency, balance, rate, account_type, maturity_days,
#            origination_days_ago, attributes)
# ---------------------------------------------------------------------------

_AGR1 = {"sector": "agriculture.cocoa_production"}
_AGR2 = {"sector": "agriculture.other"}
_IMP = {"sector": "commerce.import.other"}
_EXP = {"sector": "commerce.export.cocoa"}
_MINE = {"sector": "mining.gold"}
_BOOK: tuple[
    tuple[str, str, str, str, str | None, str | None, int | None, int | None, dict[str, str]], ...
] = (
    ("DEP/CUR", "DEPOSIT", "GHS", "25000000", "0", "CURRENT", None, None, {}),
    ("DEP/SAV1", "DEPOSIT", "GHS", "20000000", "0.08", "SAVINGS", None, None, {}),
    ("DEP/SAV2", "DEPOSIT", "GHS", "5000000", "0.10", "SAVINGS", None, None, {}),
    ("DEP/TD3", "DEPOSIT", "GHS", "10000000", "0.19", "FIXED", 60, None, {"tenor_months": "3"}),
    ("DEP/TD12", "DEPOSIT", "GHS", "8000000", "0.17", "FIXED", 200, 165, {}),  # 365-day term
    ("DEP/TD12b", "DEPOSIT", "GHS", "2000000", "0.22", "FIXED", 300, 60, {}),  # 360-day term
    ("DEP/CALL", "DEPOSIT", "GHS", "3000000", None, "CALL", None, None, {}),  # no rate
    ("DEP/USD", "DEPOSIT", "USD", "200000", "0.03", "SAVINGS", None, None, {}),
    ("LOAN/AGR1", "LOAN", "GHS", "30000000", "0.26", None, None, None, _AGR1),
    ("LOAN/AGR2", "LOAN", "GHS", "10000000", "0.28", None, None, None, _AGR2),
    ("LOAN/IMP", "LOAN", "GHS", "5000000", "0.30", None, None, None, _IMP),
    ("LOAN/EXP", "LOAN", "GHS", "4000000", "0.24", None, None, None, _EXP),
    ("LOAN/UNCL", "LOAN", "GHS", "2000000", "0.35", None, None, None, {}),  # unclassified
    ("LOAN/USD", "LOAN", "USD", "1000000", "0.10", None, None, None, _MINE),
)

EXPECTED = {
    "B17": 0.0,  # demand deposit — a 0% contractual rate is a rate
    "C17": (20 * 8 + 5 * 10) / 25,  # savings, balance-weighted → 8.4
    "F17": 19.0,  # 3-month TD (explicit tenor_months)
    "H17": (8 * 17 + 2 * 22) / 10,  # 12-month TD bucket (365 / 360-day terms) → 18.0
    "N17": (30 * 26 + 10 * 28) / 40,  # agriculture → 26.5
    "O17": 24.0,  # exports (commerce.export.*)
    "S17": 30.0,  # imports (commerce.import.*)
    "U17": 35.0,  # others (unclassified loan)
    "C28": 3.0,  # USD savings
    "P28": 10.0,  # USD mining
}
BLANK = ("D17", "E17", "K17", "M17", "L17", "B28", "N28", "B29", "B30", "B31", "B9")


def _seed(session: Any, as_of: date) -> None:
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
        operation_ref="bsd14-test",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    common |= {"ingestion_batch_id": batch.id, "lineage_id": lineage.id}
    counterparty = CanonicalCounterparty(
        **common,
        source_reference="CP/RETAIL",
        name="Ama Mensah",
        counterparty_type="RETAIL_INDIVIDUAL",
        resident=True,
    )
    session.add(counterparty)
    session.flush()
    for ref, ptype, currency, balance, rate, account_type, mat_days, orig_days, attrs in _BOOK:
        position = CanonicalPosition(
            **common,
            source_reference=ref,
            position_type=ptype,
            currency=currency,
            origination_date=(as_of - timedelta(days=orig_days)) if orig_days else None,
        )
        session.add(position)
        session.flush()
        session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                counterparty_id=counterparty.id,
                balance=Decimal(balance),
                interest_rate=Decimal(rate) if rate is not None else None,
                deposit_account_type=account_type,
                contractual_maturity=(as_of + timedelta(days=mat_days)) if mat_days else None,
                attributes=dict(attrs),
            )
        )
    session.flush()


def _prepare(db_client: TestClient) -> str:
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        materialize_canonical_test_book(session)
        session.commit()
        reporting_date = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
        ).json()["periods"][0]["period_end"]
        _seed(session, date.fromisoformat(reporting_date))
        session.commit()
    finally:
        session.close()
    return reporting_date


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD14", "reporting_date": reporting_date},
    )
    assert response.status_code == 201, response.text[:400]
    package = response.json()
    return db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()["snapshot"]


def test_rate_cells_are_balance_weighted_means_of_the_positions(db_client: TestClient) -> None:
    reporting_date = _prepare(db_client)
    snapshot = _generate(db_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    cells = payload["cells"][SHEET]
    section = next(s for s in snapshot["sections"] if s["title"] == SHEET)
    rows = {r["cell"]: r for r in section["rows"]}
    for ref, expected in EXPECTED.items():
        assert cells.get(ref) is not None, ref
        assert abs(float(cells[ref]) - expected) < 1e-9, (ref, cells[ref], expected)
        assert rows[ref]["status"] == "mapped"
        assert float(rows[ref]["value"]) == float(cells[ref])  # percent — never scaled
    for ref in BLANK:
        assert cells.get(ref) is None, (ref, cells.get(ref))
        assert rows[ref]["status"] == "input_required", ref
    # the tenor headers are the template's own
    assert [cells[f"{col}14"] for col in "DEFGHIJ"] == [1, 2, 3, 6, 12, 24, 36]
    counts = payload["status_counts"]
    assert counts["mapped"] == len(EXPECTED) + len(TENOR_CELLS)
    assert counts["input_required"] == len(RATE_CELLS) + 1 - len(EXPECTED)
