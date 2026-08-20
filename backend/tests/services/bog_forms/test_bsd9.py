"""BSD9 (Consolidated Balance Sheet) line map.

Hermetic: the canonical test book, BSD9 generated through the real package
pipeline (BSD2 is computed first as its dependency). Proves:

1. every official input cell of ``BSD9`` is bound and the Annexure grid rows are
   declared (input_required — inter-company transactions need a subsidiary book);
2. cross-form equalities with BSD2 evaluated by BoG's own arithmetic on both
   sides: Total Assets (BSD9 25 == BSD2 13), Shareholders' funds (BSD9 29 ==
   BSD2 16), net loans (BSD9 9 == BSD2 8), cash and BoG balances, other assets,
   contingent liabilities, managed funds; Total Liabilities == BSD2 31 + item 17;
3. the ``bsd9.bsd2_lines`` resolver picks the column's BSD2 letter, sums rows,
   and stays blank when BSD2 is missing or every source cell is blank.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import Bank, BankReportingPeriod
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)


def _materialize(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _latest_period_end(db_client: TestClient) -> str:
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]["period_end"]


def _generate(db_client: TestClient, code: str, reporting_date: str) -> dict:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{code}: {response.status_code} {response.text[:300]}"
    package = response.json()
    return db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()["snapshot"]


def _num(cells: dict[str, Any], ref: str) -> float:
    value = cells.get(ref)
    return 0.0 if value is None else float(value)


# ---------------------------------------------------------------------------
# 1. structure
# ---------------------------------------------------------------------------


def test_bsd9_binds_every_official_input_cell_and_the_annexure_grid() -> None:
    layout = load_layout("BSD9")
    maps = line_maps_for("BSD9")
    main = maps["BSD9"]
    bound = {ref for line in main for ref in line.cells.values()}
    assert bound == {c.ref for c in layout.sheet("BSD9").input_cells}
    assert len(bound) == 62  # noqa: PLR2004 — 31 leaf rows × Domestic/Foreign
    by_source: dict[str | None, int] = {}
    for line in main:
        by_source[line.source] = by_source.get(line.source, 0) + len(line.cells)
    # minority interests read the subsidiaries register (data-gap closure 2026-08-16)
    assert by_source == {"bsd9.bsd2_lines": 60, "refs.sum": 2}
    # every BSD2-fed line names its BSD2 rows and the parent-only basis
    for line in main:
        if line.source == "bsd9.bsd2_lines":
            assert line.params["rows"], line.code
            assert "parent (solo)" in line.notes
    # Annexure: DATE / TYPE / SUBSIDIARY / AMOUNT grid rows 11–28, every cell declared
    annex = maps["Annexure"]
    # one LineSpec per cell (each column reads a different register field) — data-gap
    # closure 2026-08-16: the Annexure reads the subsidiaries register via refs.field
    assert len(annex) == 72  # noqa: PLR2004
    assert all(line.source == "refs.field" for line in annex)
    assert {ref for line in annex for ref in line.cells.values()} == {
        f"{col}{row}" for row in range(11, 29) for col in "ABCD"
    }
    assert "Sheet3" not in maps  # empty placeholder tab: nothing to bind


# ---------------------------------------------------------------------------
# 2. cross-form equalities with BSD2 through the real pipeline
# ---------------------------------------------------------------------------


def test_bsd9_lines_equal_the_bsd2_totals(db_client: TestClient) -> None:
    _materialize(db_client)
    reporting_date = _latest_period_end(db_client)
    bsd2 = _generate(db_client, "BSD2", reporting_date)["bog_form"]["cells"]["BSD2"]
    snapshot = _generate(db_client, "BSD9", reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    assert payload["missing_dependencies"] == []
    assert payload["basis"] == "consolidated"
    bsd9 = payload["cells"]["BSD9"]

    equalities = {
        # BSD9 cell: BSD2 cells summed
        "25": ["124"],  # Total Assets == BSD2 13. Total Assets (A + B)
        "29": ["135"],  # Shareholders' funds == BSD2 16
        "17": ["60"],  # Loans, overdrafts and other advances (net) == BSD2 8 (net)
        "18": ["68"],  # gross
        "14": ["14", "15"],  # cash + claims on BoG
        "15": ["21", "30", "33"],
        "16": ["34"],
        "21": ["72"],
        "22": ["102"],
        "23": ["113"],  # other assets
        "24": ["114"],
        "27": ["128"],
        "28": ["129"],
        "32": ["142", "168"],
        "34": ["138", "184"],
        "35": ["196"],
        "36": ["226"],
        "37": ["146"],
        "38": ["259"],
        "39": ["274"],
        "40": ["277"],
        "41": ["278"],
        "43": ["282"],
        "44": ["283"],
        "42": ["280", "136"],  # Total Liabilities == BSD2 31 + 17 (minority blank)
    }
    for row, bsd2_rows in equalities.items():
        for col in "BCD":
            expected = sum(_num(bsd2, f"{col}{r}") for r in bsd2_rows)
            assert abs(_num(bsd9, f"{col}{row}") - expected) < 1e-6, (
                f"{col}{row} vs BSD2 {bsd2_rows}"
            )
    # the link carries real data: the fixture's other assets, cash and paid-up capital
    assert _num(bsd9, "D23") == _num(bsd2, "D113") > 0
    assert _num(bsd9, "D14") == _num(bsd2, "D14") + _num(bsd2, "D15") > 0
    assert _num(bsd9, "D27") == _num(bsd2, "D128") > 0
    assert _num(bsd9, "D25") > 0
    # BSD9's own TOTAL column is BoG's =B+C on every leaf row
    for row in range(8, 45):
        if f"D{row}" in bsd9 and f"B{row}" in bsd9:
            assert (
                abs(_num(bsd9, f"D{row}") - (_num(bsd9, f"B{row}") + _num(bsd9, f"C{row}"))) < 1e-6
            )
    # minority interests + Annexure stay input_required
    statuses = {
        (section["title"], row["cell"]): row["status"]
        for section in snapshot["sections"]
        for row in section["rows"]
    }
    assert statuses[("BSD9", "B30")] == "input_required"
    assert statuses[("Annexure", "D11")] == "input_required"
    assert statuses[("BSD9", "B23")] == "mapped"
    assert bsd9["B30"] is None


# ---------------------------------------------------------------------------
# 3. resolver
# ---------------------------------------------------------------------------


def _rc(column: str, dependencies: dict[str, dict[tuple[str, str], Any]]) -> ResolveContext:
    bank = Bank(
        id=SAMPLE_BANK_ID,
        organization_id=ORG_1,
        name="Sample Bank Ltd",
        short_name="Sample Bank",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    period = BankReportingPeriod(period_start=date(2026, 3, 1), period_end=date(2026, 3, 31))
    return ResolveContext(
        db=None,  # type: ignore[arg-type] — the resolver reads only the dependency values
        ctx=TenantContext(organization_id=ORG_1),
        bank=bank,
        period=period,
        column=column,
        dependencies=dependencies,
    )


def test_bsd2_lines_resolver_selects_column_and_sums_rows() -> None:
    resolve = get_resolver("bsd9.bsd2_lines")
    dep = {
        "BSD2": {
            ("BSD2", "B14"): 100.0,
            ("BSD2", "C14"): 7.0,
            ("BSD2", "D14"): 107.0,
            ("BSD2", "B15"): 50.0,
            ("BSD2", "C15"): None,
            ("BSD2", "D15"): 50.0,
            ("BSD2", "B69"): None,
            ("BSD2", "C69"): None,
            ("BSD2", "B71"): None,
            ("BSD2", "C71"): None,
        }
    }
    assert resolve(_rc("domestic", dep), {"rows": [14, 15]}) == 150
    assert resolve(_rc("foreign", dep), {"rows": [14, 15]}) == 7
    assert resolve(_rc("total", dep), {"rows": [14, 15]}) == 157
    assert resolve(_rc("domestic", dep), {"rows": [14], "sign": -1}) == -100
    # every source cell blank → blank (input_required), never a zero
    assert resolve(_rc("domestic", dep), {"rows": [69, 71]}) is None
    # BSD2 not computed → blank
    assert resolve(_rc("domestic", {}), {"rows": [14]}) is None
