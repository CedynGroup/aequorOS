"""BSD1A — Twenty Largest Withdrawals Over the Counter: structure + honest blanks.

The official serial numbers 1…20 are re-emitted (they are the sheet's only
captured input cells); every customer / branch / account type / daily amount
cell of the twenty ranked rows is bound to ``bsd1a.rank`` over the
``teller_withdrawals`` dataset and stays ``input_required`` — naming the
dataset the bank must ingest — until a week's file lands, and the template's
own row totals (``J = SUM(E:I)``) and grand total (``J31``) evaluate over the
blanks. The fed path is proven in tests/services/data_gaps/test_teller_withdrawals.py.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from tests.api.helpers import headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

SHEET = "20 LARGEST WITHDRAWALS"
ROWS = range(11, 31)


def test_bsd1a_binds_serials_and_every_ranked_data_cell() -> None:
    layout = load_layout("BSD1A").sheet(SHEET)
    lines = line_maps_for("BSD1A")[SHEET]
    bound = {ref for line in lines for ref in line.cells.values()}
    assert bound >= {c.ref for c in layout.input_cells}  # the 20 serials
    assert bound == {f"{col}{row}" for row in ROWS for col in "ABCDEFGHI"}
    assert "J11" not in bound and "J31" not in bound  # noqa: PT018 — template formulas
    serials = [line for line in lines if line.source == "constant"]
    assert [line.params["value"] for line in serials] == list(range(1, 21))
    assert all(line.unscaled for line in serials)
    data = [line for line in lines if line.source == "bsd1a.rank"]
    assert len(data) == 20  # noqa: PLR2004 — one ranked line per row, all eight data cells
    assert [line.params["rank"] for line in data] == list(range(1, 21))
    assert all("teller_withdrawals" in line.notes for line in data)


def test_bsd1a_generates_structure_only_with_official_numbering(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD1A", "reporting_date": periods[0]["period_end"]},
    )
    assert response.status_code == 201, response.text[:400]
    snapshot: dict[str, Any] = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{response.json()['id']}",
        headers=headers(),
    ).json()["snapshot"]
    assert not snapshot["bog_form"]["errors"]
    counts = snapshot["bog_form"]["status_counts"]
    assert counts["mapped"] == 20  # noqa: PLR2004 — the serials
    assert counts["input_required"] == 160  # noqa: PLR2004 — 20 rows × 8 data cells
    cells = snapshot["bog_form"]["cells"][SHEET]
    assert [cells[f"A{row}"] for row in ROWS] == list(range(1, 21))
    assert all(cells.get(f"E{row}") is None for row in ROWS)
    # BoG's own arithmetic over the blanks
    assert cells["J11"] == 0
    assert cells["J31"] == 0
    rows = {row["cell"]: row for section in snapshot["sections"] for row in section["rows"]}
    assert rows["A11"]["value"] == "1"  # serial exported unscaled (a count, not ¢'Million)
    assert rows["B11"]["status"] == "input_required"
