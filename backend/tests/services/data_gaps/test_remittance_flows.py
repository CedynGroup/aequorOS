"""``remittance_flows`` — foreign remittances by corridor / recipient / channel → BSD17.

1. the Sample Bank month file pushed through the REAL API (three-call push
   flow, the generic ``scripts/ingest_push.py`` reader, ``as_of_date`` = the
   reporting month-end) lands under the kind with batch lineage;
2. BSD17 generated through ``POST /regulatory-packages`` for that period
   carries the register's inbound US$ totals — Sheet 1 by recipient class
   (rows 8–13) and its total (row 15), Sheet 2 by sending region (rows 6–11)
   and its total (row 12); the two totals agree, equal the sum of their rows,
   and exclude the outbound rows; export writes them in US$ (no scaling);
3. the register schema rejects a malformed row honestly (unknown region /
   recipient class, non-numeric amount, missing corridor).
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.domain.ingestion.reference_schemas import schema_for
from app.domain.ingestion.reference_schemas.remittance_flows import (
    RECIPIENT_CLASSES,
    REGIONS,
    SCHEMA,
)
from app.models import Bank, CanonicalReferenceRow
from app.services.regulatory_reporting.bog_forms.linemaps import bsd17, line_maps_for
from app.services.regulatory_reporting.exports import render_bog_form_xlsx
from scripts.ingest_push import read_rows
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SAMPLE_DIR = Path(__file__).resolve().parents[3] / "onboarding" / "sample_bank"
CSV = SAMPLE_DIR / "remittance_flows.csv"
S1, S2 = "BSG17-SHEET 1", "BSD17 -SHEET 2"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prepare(db_client: TestClient) -> str:
    """Materialise the hermetic book; return the latest period end (ISO)."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    periods = db_client.get(f"{BASE}/reporting-periods", headers=headers()).json()["periods"]
    return max(p["period_end"] for p in periods)


def _month_rows(month_end: str) -> list[dict[str, Any]]:
    """The Sample Bank rows for one reporting month, re-dated to ``month_end``
    when the hermetic period has no file of its own (see the ATM test)."""
    rows = read_rows(CSV, entity=False)
    months = sorted({r["month"] for r in rows})
    source = month_end if month_end in months else months[-1]
    return [{**r, "month": month_end} for r in rows if r["month"] == source]


def _push_reference(
    db_client: TestClient, kind: str, rows: list[dict[str, Any]], *, as_of: str, key: str
) -> dict[str, Any]:
    opened = db_client.post(
        f"{BASE}/push-batches",
        headers=headers(),
        json={"as_of_date": as_of, "idempotency_key": key, "reason": f"Sample Bank {kind}"},
    )
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = db_client.post(
        f"{BASE}/push-batches/{push_id}/records",
        headers=headers(),
        json={"reference": {kind: rows}},
    )
    assert staged.status_code == 200, staged.text
    committed = db_client.post(f"{BASE}/push-batches/{push_id}/commit", headers=headers())
    assert committed.status_code == 201, committed.text
    return committed.json()


def _generate(db_client: TestClient, code: str, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"{BASE}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{code}: {response.status_code} {response.text[:300]}"
    package = response.json()
    detail = db_client.get(f"{BASE}/regulatory-packages/{package['id']}", headers=headers()).json()
    return detail["snapshot"]


def _export(code: str, snapshot: dict[str, Any]) -> openpyxl.Workbook:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        payload = render_bog_form_xlsx(code, snapshot, bank, datetime(2026, 8, 16, tzinfo=UTC))
    finally:
        session.close()
    return openpyxl.load_workbook(io.BytesIO(payload), data_only=False)


def _landed(kind: str) -> list[CanonicalReferenceRow]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        return list(
            session.scalars(
                select(CanonicalReferenceRow)
                .where(
                    CanonicalReferenceRow.bank_id == SAMPLE_BANK_ID,
                    CanonicalReferenceRow.dataset_kind == kind,
                )
                .order_by(CanonicalReferenceRow.row_index)
            )
        )
    finally:
        session.close()


def _usd(rows: list[dict[str, Any]], **where: str) -> float:
    return float(
        sum(
            Decimal(r["amount_usd"])
            for r in rows
            if r["direction"] == "inbound" and all(r[k] == v for k, v in where.items())
        )
    )


# ---------------------------------------------------------------------------
# 1. the Sample Bank register and the line map
# ---------------------------------------------------------------------------


def test_sample_bank_register_is_twelve_months_of_plausible_corridors() -> None:
    rows = read_rows(CSV, entity=False)
    months = sorted({r["month"] for r in rows})
    assert len(months) == 12  # noqa: PLR2004
    assert months[0] == "2025-07-31" and months[-1] == "2026-06-30"
    for row in rows:
        assert SCHEMA.validate_row(row) == [], row
        assert len(row["corridor_country"]) == 2  # ISO-3166 alpha-2  # noqa: PLR2004
        assert Decimal(row["amount_ghs"]) > Decimal(row["amount_usd"]) > 0
    for month in months:
        subset = [r for r in rows if r["month"] == month]
        inbound = _usd(subset)
        assert 9_000_000 < inbound < 20_000_000, (month, inbound)  # noqa: PLR2004
        # every official row of both sheets is non-empty for every month
        assert all(_usd(subset, recipient_class=c) > 0 for c in RECIPIENT_CLASSES)
        assert all(_usd(subset, region=r) > 0 for r in REGIONS)
        assert any(r["direction"] == "outbound" for r in subset)
        month_file = SAMPLE_DIR / f"remittance_flows_{month[:7]}.csv"
        assert {r["month"] for r in read_rows(month_file, entity=False)} == {month}
    # ISO → region assignment is consistent across the file
    by_country: dict[str, set[str]] = {}
    for r in rows:
        by_country.setdefault(r["corridor_country"], set()).add(r["region"])
    assert all(len(regions) == 1 for regions in by_country.values())
    assert by_country["GB"] == {"uk"} and by_country["NG"] == {"ecowas"}


def test_line_map_binds_every_amount_cell_to_inbound_usd_sums() -> None:
    maps = line_maps_for("BSD17")
    by_cell = {
        ref: line for lines in maps.values() for line in lines for ref in line.cells.values()
    }
    for row, recipient in bsd17.RECIPIENT_ROWS.items():
        line = by_cell[f"C{row}"]
        assert line.source == "refs.sum"
        assert line.params == {
            "kind": "remittance_flows",
            "value_field": "amount_usd",
            "filters": {"direction": "inbound", "recipient_class": recipient},
        }
    for row, region in bsd17.REGION_ROWS.items():
        assert by_cell[f"B{row}"].params["filters"] == {"direction": "inbound", "region": region}
    for total in ("C15", "B12"):
        assert by_cell[total].params["filters"] == {"direction": "inbound"}
    assert set(bsd17.RECIPIENT_ROWS.values()) == set(RECIPIENT_CLASSES)
    assert set(bsd17.REGION_ROWS.values()) == set(REGIONS)
    assert len(by_cell) == 21  # noqa: PLR2004 — 7 item numbers + 7 + 7 amounts


# ---------------------------------------------------------------------------
# 2. push through the real API → BSD17 fills
# ---------------------------------------------------------------------------


def test_pushed_month_fills_bsd17_by_recipient_and_region(db_client: TestClient) -> None:
    latest = _prepare(db_client)
    rows = _month_rows(latest)
    result = _push_reference(
        db_client, "remittance_flows", rows, as_of=latest, key=f"remit-{latest}"
    )
    batch = result["batch"]
    assert batch["status"] in ("accepted", "accepted_with_warnings"), batch["validation_report"]
    assert batch["validation_report"]["summary"]["reference_rows"] == {
        "remittance_flows": len(rows)
    }
    landed = _landed("remittance_flows")
    assert len(landed) == len(rows)
    assert {str(r.ingestion_batch_id) for r in landed} == {batch["id"]}
    assert all(r.lineage_id is not None for r in landed)
    assert landed[0].as_of_date == date.fromisoformat(latest)
    assert landed[0].payload["direction"] == "inbound"

    snapshot = _generate(db_client, "BSD17", latest)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    assert payload["status_counts"] == {
        "mapped": 21,
        "input_required": 0,
        "unmapped": 0,
        "derived": 0,
    }
    cells = payload["cells"]
    total = _usd(rows)
    for row, recipient in bsd17.RECIPIENT_ROWS.items():
        assert cells[S1][f"A{row}"] == row - 7
        assert cells[S1][f"C{row}"] == pytest.approx(_usd(rows, recipient_class=recipient))
    assert cells[S1]["A15"] == 7  # noqa: PLR2004
    assert cells[S1]["C15"] == pytest.approx(total)
    assert cells[S1]["C15"] == pytest.approx(sum(cells[S1][f"C{r}"] for r in range(8, 14)))
    for row, region in bsd17.REGION_ROWS.items():
        assert cells[S2][f"B{row}"] == pytest.approx(_usd(rows, region=region))
    assert cells[S2]["B12"] == pytest.approx(total)
    assert cells[S2]["B12"] == pytest.approx(sum(cells[S2][f"B{r}"] for r in range(6, 12)))
    # the outbound book is excluded from the return
    everything = float(sum(Decimal(r["amount_usd"]) for r in rows))
    assert everything > total
    assert cells[S1]["C8"] > cells[S1]["C9"]  # individuals dominate the inbound book

    # export: US$ as-is (units sheet), item numbers kept, no formulas
    wb = _export("BSD17", snapshot)
    ws1, ws2 = wb[S1], wb[S2]
    assert ws1["C8"].value == pytest.approx(cells[S1]["C8"])
    assert ws1["C15"].value == pytest.approx(total)
    assert ws1["A8"].value == 1 and ws1["A15"].value == 7  # noqa: PLR2004
    assert ws2["B6"].value == pytest.approx(cells[S2]["B6"])
    assert ws2["B12"].value == pytest.approx(total)
    assert ws2["A6"].value == "United Kingdom"
    assert not any(
        isinstance(c.value, str) and c.value.startswith("=")
        for ws in (ws1, ws2)
        for row in ws.iter_rows()
        for c in row
    )


def test_form_without_a_register_stays_input_required(db_client: TestClient) -> None:
    latest = _prepare(db_client)
    payload = _generate(db_client, "BSD17", latest)["bog_form"]
    assert payload["status_counts"]["mapped"] == 7  # the item numbers only  # noqa: PLR2004
    assert payload["status_counts"]["input_required"] == 14  # noqa: PLR2004
    assert payload["cells"][S1]["C15"] is None and payload["cells"][S2]["B12"] is None


# ---------------------------------------------------------------------------
# 3. malformed rows are reported honestly
# ---------------------------------------------------------------------------


def test_schema_reports_malformed_rows() -> None:
    schema = schema_for("remittance_flows")
    assert schema is not None and schema is SCHEMA
    good = {
        "month": "2026-06-30",
        "direction": "inbound",
        "corridor_country": "GB",
        "region": "uk",
        "recipient_class": "individual",
        "channel": "mto",
        "currency": "GBP",
        "amount_fx": "1000",
        "amount_usd": "1270",
        "amount_ghs": "14400",
    }
    assert schema.validate_row(good) == []
    problems = schema.validate_row(
        {**good, "corridor_country": "", "region": "europe", "amount_usd": "lots"}
    )
    assert "missing required field 'corridor_country'" in problems
    assert any("'region' must be one of" in p for p in problems)
    assert "field 'amount_usd' must be numeric (got 'lots')" in problems
    assert schema.validate_row({**good, "recipient_class": "diaspora"}) == [
        "field 'recipient_class' must be one of "
        "['individual', 'exporter', 'service_provider', 'ngo', 'embassy', 'other'] "
        "(got 'diaspora')"
    ]
    assert schema.validate_row({**good, "direction": "INBOUND"}) != []  # enums are exact
