"""``interest_accruals`` — the accrued-interest sub-ledger keyed by the official
BSD2 "Accrued interest" rows.

Closes the 19 accrued-interest lines of BSD2 (Statement of Assets and
Liabilities) through the Data Engine:

1. the ``bsd2_row`` vocabulary is exactly the set of BSD2 rows the line map
   binds to the sub-ledger, every one of them a template row labelled
   "Accrued interest" with Domestic/Foreign input cells;
2. the Sample Bank CSV pushed through the REAL API by the generic
   ``scripts/ingest_push.py`` client lands under the kind with batch lineage;
   BSD2 generated through ``POST /regulatory-packages`` then carries the
   sub-ledger's balances on every accrual line — ``input_required`` → ``mapped``
   — Domestic/Foreign by each row's currency, nil-accrual lines as ``0``, and
   the template's own TOTAL formula adds the two columns;
3. the sub-ledger schema rejects a malformed row honestly (missing field,
   unknown row, side/row disagreement, non-numeric balance).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.domain.ingestion.reference_schemas import schema_for
from app.domain.ingestion.reference_schemas.interest_accruals import (
    ASSET_ROWS,
    BSD2_ROW_LABELS,
    BSD2_ROWS,
    LIABILITY_ROWS,
    SCHEMA,
    validate_accrual_row,
)
from app.models import CanonicalReferenceRow
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from scripts import ingest_push
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
CSV = Path(__file__).resolve().parents[3] / "onboarding" / "sample_bank" / "interest_accruals.csv"
KIND = "interest_accruals"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prepare(db_client: TestClient) -> str:
    """Materialise the hermetic book; return its latest period end (ISO)."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    periods = db_client.get(f"{BASE}/reporting-periods", headers=headers()).json()["periods"]
    return max(p["period_end"] for p in periods)


class _ClientProxy:
    """``httpx.Client`` stand-in so ``ingest_push.push`` runs against the TestClient."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def __enter__(self) -> _ClientProxy:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def post(self, url: str, json: Any = None) -> Any:
        return self._client.post(url, headers=headers(), json=json)


@pytest.fixture
def push_client(db_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(ingest_push.httpx, "Client", lambda **_: _ClientProxy(db_client))
    yield db_client


def _rows(as_of: str) -> list[dict[str, Any]]:
    """The Sample Bank sub-ledger, re-dated to the hermetic book's reporting date."""
    return [{**row, "as_of_date": as_of} for row in ingest_push.read_rows(CSV, entity=False)]


def _landed() -> list[CanonicalReferenceRow]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        return list(
            session.scalars(
                select(CanonicalReferenceRow)
                .where(
                    CanonicalReferenceRow.bank_id == SAMPLE_BANK_ID,
                    CanonicalReferenceRow.dataset_kind == KIND,
                )
                .order_by(CanonicalReferenceRow.row_index)
            )
        )
    finally:
        session.close()


def _push_sub_ledger(rows: list[dict[str, Any]], *, as_of: str, key: str) -> dict[str, Any]:
    """The generic client's three-call push of one sub-ledger; returns the accepted batch."""
    result = ingest_push.push(
        base_url="http://testserver",
        token="test-token",
        bank=SAMPLE_BANK_ID,
        as_of=as_of,
        reason="Sample Bank onboarding: accruals sub-ledger (hermetic)",
        idempotency_key=key,
        entities={},
        references={KIND: rows},
    )
    batch = result["batch"]
    assert batch["status"] == "accepted", batch["validation_report"]["summary"]
    return batch


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    return _generate_form(db_client, "BSD2", reporting_date)


def _generate_form(db_client: TestClient, code: str, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"{BASE}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{response.status_code} {response.text[:300]}"
    package = response.json()
    detail = db_client.get(f"{BASE}/regulatory-packages/{package['id']}", headers=headers()).json()
    return detail["snapshot"]


def _statuses(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        row["cell"]: row["status"]
        for section in snapshot["sections"]
        if section["title"] == "BSD2"
        for row in section["rows"]
    }


def _expected(rows: list[dict[str, Any]], bsd2_row: str, column: str) -> Decimal:
    total = Decimal(0)
    for row in rows:
        if row["bsd2_row"] != bsd2_row:
            continue
        is_ghs = row["currency"] == "GHS"
        if is_ghs == (column == "domestic"):
            total += Decimal(row["accrued_interest_ghs"])
    return total


# ---------------------------------------------------------------------------
# 1. vocabulary ↔ line map ↔ official template
# ---------------------------------------------------------------------------


def test_bsd2_row_vocabulary_is_exactly_the_bound_accrual_lines() -> None:
    bound = [
        line
        for line in line_maps_for("BSD2")["BSD2"]
        if line.source == "refs.sum" and line.params.get("kind") == KIND
    ]
    filters = {line.code: dict(cast("dict[str, Any]", line.params["filters"])) for line in bound}
    rows_bound = {str(f["bsd2_row"]) for f in filters.values()}
    assert rows_bound == set(BSD2_ROWS) == set(BSD2_ROW_LABELS)
    assert len(BSD2_ROWS) == 19  # noqa: PLR2004 — the official sheet's accrued-interest lines
    assert set(ASSET_ROWS).isdisjoint(LIABILITY_ROWS)
    layout = load_layout("BSD2").sheet("BSD2")
    for line in bound:
        row = int(filters[line.code]["bsd2_row"])
        # each is a template row labelled "Accrued interest" (the template itself spells
        # some rows "Acrued") with Domestic B / Foreign C inputs
        assert "crued interest" in layout.label_for_row(row).lower(), row
        assert line.cells == {"domestic": f"B{row}", "foreign": f"C{row}"}
        assert line.params["value_field"] == "accrued_interest_ghs"
        assert line.params["currency_field"] == "currency"
        assert layout.by_ref[f"D{row}"].kind == "formula"  # TOTAL is BoG's own arithmetic
    assert schema_for(KIND) is SCHEMA


def test_sample_bank_sub_ledger_is_well_formed() -> None:
    rows = ingest_push.read_rows(CSV, entity=False)
    assert rows and all(validate_accrual_row(row) == [] for row in rows), [
        (row, validate_accrual_row(row)) for row in rows if validate_accrual_row(row)
    ]
    assert {row["bsd2_row"] for row in rows} == set(BSD2_ROWS)  # every official line addressed
    assert {row["currency"] for row in rows} == {"GHS", "USD"}
    assert all(Decimal(row["accrued_interest_ghs"]) >= 0 for row in rows)


# ---------------------------------------------------------------------------
# 2. push through the real API → BSD2 accrual lines mapped
# ---------------------------------------------------------------------------


def test_pushed_sub_ledger_lands_with_lineage_and_fills_bsd2_accrual_lines(
    push_client: TestClient,
) -> None:
    reporting_date = _prepare(push_client)
    before = _statuses(_generate(push_client, reporting_date))
    for row in BSD2_ROWS:
        assert before[f"B{row}"] == "input_required" and before[f"C{row}"] == "input_required"

    rows = _rows(reporting_date)
    batch = _push_sub_ledger(rows, as_of=reporting_date, key=f"interest-accruals-{reporting_date}")
    assert batch["source_system"] == "API_PUSH"
    assert batch["validation_report"]["summary"]["reference_rows"] == {KIND: len(rows)}

    landed = _landed()
    assert len(landed) == len(rows)
    assert {str(r.ingestion_batch_id) for r in landed} == {batch["id"]}
    assert all(r.lineage_id is not None for r in landed)
    assert all(r.as_of_date.isoformat() == reporting_date for r in landed)
    assert landed[0].payload["bsd2_row"] == "20" and landed[0].payload["side"] == "asset"

    snapshot = _generate(push_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    cells = payload["cells"]["BSD2"]
    after = _statuses(snapshot)
    for row in BSD2_ROWS:
        assert after[f"B{row}"] == "mapped" and after[f"C{row}"] == "mapped", row
        dom, fx = _expected(rows, row, "domestic"), _expected(rows, row, "foreign")
        assert Decimal(str(cells[f"B{row}"])) == dom, (row, "B")
        assert Decimal(str(cells[f"C{row}"])) == fx, (row, "C")
        # TOTAL column is the template's own =B+C
        assert abs(Decimal(str(cells[f"D{row}"])) - (dom + fx)) < Decimal("0.001"), row
    # the concrete Sample Bank figures: placements receivable (GHS + USD slice),
    # time deposits payable, nil BoG accrual carried as 0 (mapped, not blank)
    assert Decimal(str(cells["B29"])) == Decimal("1915000.00")
    assert Decimal(str(cells["C29"])) == Decimal("152500.00")
    assert Decimal(str(cells["B250"])) == Decimal("11240000.00")
    assert Decimal(str(cells["C250"])) == Decimal("384000.00")
    assert cells["B20"] == 0 and cells["C20"] == 0
    # nothing else on the sheet moved: the same non-accrual cells stay as before
    unchanged = [c for c in before if c not in {f"{col}{r}" for r in BSD2_ROWS for col in "BC"}]
    assert unchanged and all(before[c] == after[c] for c in unchanged)

    # BSD6 (FROM BSD2) flows through: its accrued-interest rows carry the sub-ledger
    # in FROM BSD2 / Total; the maturity bands stay with the bank (no Guide band)
    bsd6 = _generate_form(push_client, "BSD6", reporting_date)
    assert not bsd6["bog_form"]["errors"], bsd6["bog_form"]["errors"]
    a = bsd6["bog_form"]["cells"]["BSD6A"]
    accrued_assets = sum(_expected(rows, r, "domestic") for r in ("20", "29", "32"))
    assert Decimal(str(a["B11"])) == Decimal(str(a["C11"])) == accrued_assets  # 1(e)
    assert a["E11"] is None  # bands left to the bank
    b = bsd6["bog_form"]["cells"]["BSD6B"]
    assert Decimal(str(b["C57"])) == _expected(rows, "141", "foreign")  # 18(c), USD

    # a corrected re-push for the SAME reporting date replaces the earlier sub-ledger
    # (reference rows are batch-scoped: the latest batch wins, nothing is added up)
    corrected = [
        {**row, "accrued_interest_ghs": "2000000.00"}
        if row["bsd2_row"] == "29" and row["currency"] == "GHS"
        else row
        for row in rows
    ]
    again = _push_sub_ledger(
        corrected, as_of=reporting_date, key=f"interest-accruals-{reporting_date}-corrected"
    )
    assert again["id"] != batch["id"]
    cells2 = _generate(push_client, reporting_date)["bog_form"]["cells"]["BSD2"]
    assert Decimal(str(cells2["B29"])) == Decimal("2000000.00")  # replaced, not 1.915m + 2m
    assert Decimal(str(cells2["C29"])) == Decimal("152500.00")
    assert Decimal(str(cells2["B250"])) == Decimal("11240000.00")


# ---------------------------------------------------------------------------
# 3. validation rejects malformed rows
# ---------------------------------------------------------------------------


def test_schema_reports_malformed_rows() -> None:
    good = {
        "as_of_date": "2026-06-30",
        "bsd2_row": "250",
        "side": "liability",
        "currency": "GHS",
        "accrued_interest_ghs": "11240000.00",
    }
    assert validate_accrual_row(good) == []
    problems = validate_accrual_row({**good, "accrued_interest_ghs": ""})
    assert any("accrued_interest_ghs" in p and "missing" in p for p in problems)
    problems = validate_accrual_row({**good, "bsd2_row": "251"})  # a deposit line, not an accrual
    assert any("bsd2_row" in p and "one of" in p for p in problems)
    problems = validate_accrual_row({**good, "side": "asset"})
    assert any("liability-side" in p for p in problems)
    problems = validate_accrual_row({**good, "bsd2_row": "29", "side": "liability"})
    assert any("asset-side" in p for p in problems)
    problems = validate_accrual_row({**good, "accrued_interest_ghs": "eleven million"})
    assert any("numeric" in p for p in problems)
    problems = validate_accrual_row({**good, "side": "both"})
    assert any("side" in p and "one of" in p for p in problems)
