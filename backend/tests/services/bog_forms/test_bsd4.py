"""BSD4 — Sectoral Analysis of Overdrafts, Loans and Other Advances.

Proves the line map + resolvers against the official layout and a loan book
inserted on top of the hermetic Sample Bank:

1. every INPUT cell of sheet ``BSD4`` (1,890) is bound — 63 sector leaf rows ×
   10 borrower-class groups × (performing, non-performing, No. of Cust.) — and
   no formula cell is; the two annexes' blank data grids are declared;
2. the form generates through the REAL package pipeline; every bound cell is
   ``mapped`` once the book carries the documented ``sector`` attribute;
3. BoG's own arithmetic holds on the exported values: group TOTAL = PERFORMING +
   NON-PERFORMING per row, the AP:AS grand columns = Σ groups, section
   subtotals = Σ leaves, and the GRAND TOTAL reconciles to Σ resident LOAN
   positions in cedis; the No. of Cust. cells are DISTINCT counterparty counts,
   never sums; Annex 4a's total is BoG's SUM and re-cuts the SAME book;
4. a book with no ``sector`` attribute stays ``input_required`` on every sector
   cell — nothing is guessed from products or names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl.utils.cell import coordinate_from_string

from app.db.session import get_sessionmaker
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources_ext import bsd4 as ext
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

# ---------------------------------------------------------------------------
# 1. structure: every input cell bound, formula cells untouched
# ---------------------------------------------------------------------------

LEAF_ROWS = 63
GROUPS = 10
INPUTS_PER_ROW = GROUPS * 3
TOTAL_INPUTS = LEAF_ROWS * INPUTS_PER_ROW  # 1,890


def test_line_map_binds_every_input_cell_of_the_grid() -> None:
    layout = load_layout("BSD4").sheet("BSD4")
    lines = line_maps_for("BSD4")["BSD4"]
    bound = {ref for line in lines for ref in line.cells.values()}
    inputs = {c.ref for c in layout.input_cells}
    assert len(inputs) == TOTAL_INPUTS
    assert bound == inputs, sorted(inputs - bound)[:10]
    # every bound cell is a template INPUT, never a formula
    for ref in bound:
        assert layout.by_ref[ref].kind == "input", ref
    # 63 leaf rows, each bound twice (amount line + count line)
    rows = {int(coordinate_from_string(ref)[1]) for ref in bound}
    assert rows == set(ext.SECTOR_ROWS)
    assert len(rows) == LEAF_ROWS
    for line in lines:
        assert line.source == "bsd4.cell"
        assert line.params["sector"] in ext.SECTOR_KEYS
        counts = all(key.endswith(".customer_count") for key in line.cells)
        amounts = all(not key.endswith(".customer_count") for key in line.cells)
        assert counts or amounts
        assert line.unscaled is counts, line.code  # counts are never ¢'Million-scaled
        assert "sector classification attribute required" in line.notes


def test_column_groups_follow_the_official_header_row() -> None:
    layout = load_layout("BSD4").sheet("BSD4")
    heads = {c.ref: str(c.value).strip() for c in layout.cells if c.row == 7}
    for perf, npl, count in ext.BORROWER_GROUPS.values():
        assert heads[f"{perf}7"] == "PERFORMING"
        assert heads[f"{npl}7"] == "NON-PERFORMING"
        assert heads[f"{count}7"] == "No. of Cust."
    # the annexes are blank grids: their data cells are declared explicitly
    annex_a = line_maps_for("BSD4")["4a Annexure"]
    assert {ref for line in annex_a for ref in line.cells.values()} == {
        f"{col}{row}" for row in range(7, 14) for col in "CD"
    }
    annex_b = line_maps_for("BSD4")["4b Annexure"]
    assert {ref for line in annex_b for ref in line.cells.values()} == {
        f"{col}{row}" for row in (7, 8, 10, 11, 12, 13, 14, 15, 16) for col in "CD"
    }
    for line in annex_a + annex_b:
        assert line.unscaled is ("share" in line.cells)


# ---------------------------------------------------------------------------
# 2. resolver classification (pure)
# ---------------------------------------------------------------------------


def _cp(ctype: str, *, resident: bool | None = None, country: str | None = None) -> Any:
    return CanonicalCounterparty(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_reference="x",
        name="x",
        counterparty_type=ctype,
        resident=resident,
        country_code=country,
    )


def test_sector_keys_accept_documented_keys_and_unique_official_labels() -> None:
    assert ext.sector_key("agriculture.cocoa_production") == "agriculture.cocoa_production"
    assert ext.sector_key("(i)  Cocoa Production") == "agriculture.cocoa_production"
    assert ext.sector_key("Salary Credit") == "services.salary_credit"
    assert ext.sector_key("MINING.GOLD") == "mining.gold"
    # repeated between EXPORT and HOME MARKET → not unique → not aliased
    assert ext.sector_key("Food, Drink & Tobacco") is None
    assert ext.sector_key("") is None
    assert ext.sector_key("not a sector") is None


def test_borrower_groups_follow_counterparty_type_and_documented_attributes() -> None:
    bg = ext.borrower_group
    assert bg(_cp("SOVEREIGN"), {}) == "central_government"
    assert bg(_cp("GOVERNMENT_ENTITY"), {}) is None  # needs the BSD2 §8 split
    assert bg(_cp("GOVERNMENT_ENTITY"), {"borrower_class": "public_institution"}) == (
        "public_institutions"
    )
    assert bg(_cp("GOVERNMENT_ENTITY"), {"borrower_class": "public_enterprise"}) == (
        "public_enterprises"
    )
    assert bg(_cp("CORPORATE"), {"scheme": "cocoa_syndicated"}) == "public_enterprises"
    assert bg(_cp("BANK_OECD"), {}) == "commercial_banks"
    assert bg(_cp("NBFI"), {"institution_class": "rural_bank"}) == "other_depository_institutions"
    assert bg(_cp("NBFI"), {}) == "other_financial_institutions"
    assert bg(_cp("CORPORATE"), {"ownership": "foreign"}) == "private_foreign"
    assert bg(_cp("SME"), {}) == "private_indigenous"
    assert bg(_cp("RETAIL_INDIVIDUAL"), {}) == "households"
    assert bg(_cp("OTHER"), {"borrower_class": "NPISH"}) == "npish"
    assert bg(_cp("OTHER"), {}) is None
    assert bg(_cp("CENTRAL_BANK"), {}) is None
    assert bg(None, {}) is None


def test_annex_4b_regions() -> None:
    def regions(country: str | None, resident: bool | None = None) -> tuple[str, ...]:
        loan = ext.Loan(
            snapshot=None,  # type: ignore[arg-type]
            position=None,  # type: ignore[arg-type]
            counterparty=_cp("CORPORATE", resident=resident, country=country),
            amount_ghs=Decimal(1),
            attrs={},
        )
        return ext.annex_4b_regions(loan, "GH")

    assert regions("GH") == ("domestic",)
    assert regions(None, resident=True) == ("domestic",)
    assert regions("US") == ("advanced_economies",)
    assert regions("NG") == ("africa", "africa.sub_saharan")
    assert regions("EG") == ("africa",)
    assert regions("RU") == ("europe", "europe.fsu")
    assert regions("EE") == ("advanced_economies",)  # Baltics: advanced first
    assert regions("TR") == ("europe",)
    assert regions("AE") == ("middle_east",)
    assert regions("CN") == ("asia",)
    assert regions("BR") == ("western_hemisphere",)
    assert regions("ZZ", resident=False) == ()


# ---------------------------------------------------------------------------
# 3. end-to-end on a sector-classified loan book
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LoanSpec:
    ref: str
    counterparty: str  # key into _COUNTERPARTIES
    amount: str
    sector: str | None = None
    stage: int | None = 1
    currency: str = "GHS"
    attrs: dict[str, Any] | None = None


#: key → (counterparty_type, resident, country_code, attributes)
_COUNTERPARTIES: dict[str, tuple[str, bool | None, str | None, dict[str, Any]]] = {
    "gog": ("SOVEREIGN", True, "GH", {}),
    "uni": ("GOVERNMENT_ENTITY", True, "GH", {"borrower_class": "public_institution"}),
    "ecg": ("GOVERNMENT_ENTITY", True, "GH", {"borrower_class": "public_enterprise"}),
    "bank": ("BANK_NON_OECD", True, "GH", {}),
    "rural": ("NBFI", True, "GH", {"institution_class": "rural_bank"}),
    "insurer": ("NBFI", True, "GH", {"institution_class": "insurance"}),
    "foreign_corp": ("CORPORATE", True, "GH", {"ownership": "foreign"}),
    "local_corp": ("CORPORATE", True, "GH", {"sector": "commerce.import.other"}),
    "sme": ("SME", None, None, {}),
    "ama": ("RETAIL_INDIVIDUAL", True, "GH", {}),
    "kofi": ("RETAIL_INDIVIDUAL", True, "GH", {}),
    "church": ("OTHER", True, "GH", {"borrower_class": "npish"}),
    "abroad": ("CORPORATE", False, "NG", {}),
    "boguse": ("CENTRAL_BANK", True, "GH", {}),
}

_LOANS: tuple[_LoanSpec, ...] = (
    # central government — one performing, one NPL, same customer
    _LoanSpec("L1", "gog", "10000000", "agriculture.cocoa_production"),
    _LoanSpec("L2", "gog", "2000000", "agriculture.cocoa_production", stage=3),
    # public institution / public enterprise
    _LoanSpec("L3", "uni", "3000000", "services.other_incl_government"),
    _LoanSpec("L4", "ecg", "7000000", "utilities.electricity"),
    # commercial bank / ODI / OFI
    _LoanSpec("L5", "bank", "4000000", "commerce.ofi.building_bodies"),
    _LoanSpec("L6", "rural", "1500000", "commerce.other"),
    _LoanSpec("L7", "insurer", "2500000", "commerce.ofi.insurance"),
    # private corporations — foreign vs indigenous; the indigenous one takes
    # its sector from the COUNTERPARTY attributes (Guide: the customer's
    # industry); a USD loan carries balance_ghs
    _LoanSpec("L8", "foreign_corp", "6000000", "mining.gold"),
    _LoanSpec("L9", "local_corp", "5000000"),
    _LoanSpec("L10", "sme", "1000000", "manufacturing.home.food_drink_tobacco", stage=2),
    _LoanSpec(
        "L11",
        "sme",
        "100000",
        "manufacturing.home.food_drink_tobacco",
        currency="USD",
        attrs={"balance_ghs": "1285000"},
    ),
    # households — two customers, one with two loans in the same cell
    _LoanSpec("L12", "ama", "800000", "services.salary_credit"),
    _LoanSpec("L13", "ama", "200000", "services.salary_credit", stage=3),
    _LoanSpec("L14", "kofi", "500000", "services.salary_credit"),
    # NPISH; unrecognised sector value → 9. MISCELLANEOUS (Guide)
    _LoanSpec("L15", "church", "300000", "not-a-bog-sector"),
    # excluded from the main sheet: non-resident (Annex 4a "Non-residents" /
    # 4b Africa-SSA) and a CENTRAL_BANK counterparty (no BSD4 column)
    _LoanSpec("L16", "abroad", "9000000", "commerce.export.cocoa"),
    _LoanSpec("L17", "boguse", "111000", "miscellaneous"),
)

# hand-checked expectations (base units): the 15 resident, placeable loans …
RESIDENT_PLACEABLE_TOTAL = Decimal("45085000")
# … plus the non-resident (9,000,000) and CENTRAL_BANK (111,000) loans
ALL_LOANS_TOTAL = Decimal("54196000")

#: each group's TOTAL column (a template formula between NON-PERFORMING and No. of Cust.)
_GROUP_TOTAL_COLS = ("D", "H", "L", "P", "T", "X", "AB", "AF", "AJ", "AN")


def _seed_loans(session: Any, as_of: date, loans: tuple[_LoanSpec, ...]) -> None:
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
        operation_ref="bsd4-test",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    common |= {"ingestion_batch_id": batch.id, "lineage_id": lineage.id}
    counterparties: dict[str, CanonicalCounterparty] = {}
    for key, (ctype, resident, country, attrs) in _COUNTERPARTIES.items():
        row = CanonicalCounterparty(
            **common,
            source_reference=f"CP/{key}",
            name=key,
            counterparty_type=ctype,
            resident=resident,
            country_code=country,
            attributes=dict(attrs),
        )
        session.add(row)
        counterparties[key] = row
    session.flush()
    for spec in loans:
        position = CanonicalPosition(
            **common,
            source_reference=f"LOAN/{spec.ref}",
            position_type="LOAN",
            currency=spec.currency,
        )
        session.add(position)
        session.flush()
        attributes: dict[str, Any] = dict(spec.attrs or {})
        if spec.sector is not None:
            attributes["sector"] = spec.sector
        session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=f"LOAN/{spec.ref}",
                position_id=position.id,
                counterparty_id=counterparties[spec.counterparty].id,
                balance=Decimal(spec.amount),
                ifrs9_stage=spec.stage,
                attributes=attributes,
            )
        )
    session.flush()


def _prepare(db_client: TestClient, loans: tuple[_LoanSpec, ...]) -> str:
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        materialize_canonical_test_book(session)
        session.commit()
        periods = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
        ).json()["periods"]
        reporting_date = periods[0]["period_end"]
        _seed_loans(session, date.fromisoformat(reporting_date), loans)
        session.commit()
    finally:
        session.close()
    return reporting_date


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD4", "reporting_date": reporting_date},
    )
    assert response.status_code == 201, response.text[:400]
    package = response.json()
    return db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package['id']}", headers=headers()
    ).json()["snapshot"]


def _num(cells: dict[str, Any], ref: str) -> float:
    value = cells.get(ref)
    return 0.0 if value in (None, "") else float(value)


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def test_bsd4_generates_and_bogs_totals_hold_on_a_classified_book(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    reporting_date = _prepare(db_client, _LOANS)
    snapshot = _generate(db_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    grid = payload["cells"]["BSD4"]
    layout = load_layout("BSD4").sheet("BSD4")

    # (a) every bound cell of the grid is mapped (the book carries `sector`)
    section = next(s for s in snapshot["sections"] if s["title"] == "BSD4")
    statuses = {row["status"] for row in section["rows"]}
    assert statuses == {"mapped"}, statuses
    assert len(section["rows"]) == TOTAL_INPUTS
    assert payload["status_counts"]["unmapped"] == 0

    # (b) hand-checked cells (base units — the exporter scales to ¢'Million)
    assert _close(_num(grid, "B10"), 10_000_000)  # cocoa · central gov · performing
    assert _close(_num(grid, "C10"), 2_000_000)  # cocoa · central gov · NPL (stage 3)
    assert _num(grid, "E10") == 1  # ONE customer with two loans — a count, not a sum
    assert _close(_num(grid, "F91"), 3_000_000)  # public institution · services
    assert _close(_num(grid, "J53"), 7_000_000)  # public enterprise · electricity
    assert _close(_num(grid, "N74"), 4_000_000)  # commercial bank
    assert _close(_num(grid, "R75"), 1_500_000)  # rural bank → ODI
    assert _close(_num(grid, "V73"), 2_500_000)  # insurer → OFI
    assert _close(_num(grid, "Z21"), 6_000_000)  # foreign-owned private corp · gold
    assert _close(_num(grid, "AD61"), 5_000_000)  # sector from COUNTERPARTY attributes
    assert _close(_num(grid, "AD38"), 1_000_000 + 1_285_000)  # SME stage 2 + USD balance_ghs
    assert _num(grid, "AG38") == 1  # one SME customer, two loans
    assert _close(_num(grid, "AH90"), 800_000 + 500_000)  # households performing
    assert _close(_num(grid, "AI90"), 200_000)  # households NPL
    assert _num(grid, "AK90") == 2  # two household customers
    assert _close(_num(grid, "AL93"), 300_000)  # NPISH, unrecognised sector → Misc.
    assert _num(grid, "AO93") == 1

    # (c) BoG's arithmetic: group TOTAL = PERFORMING + NON-PERFORMING on every
    # leaf row; grand columns AP/AQ/AS = Σ groups; AR = AP + AQ
    for row in ext.SECTOR_ROWS:
        perf_sum = npl_sum = cust_sum = 0.0
        for (perf, npl, count), total in zip(
            ext.BORROWER_GROUPS.values(), _GROUP_TOTAL_COLS, strict=True
        ):
            assert layout.by_ref[f"{total}{row}"].kind == "formula"
            p, n = _num(grid, f"{perf}{row}"), _num(grid, f"{npl}{row}")
            assert _close(_num(grid, f"{total}{row}"), p + n), f"{total}{row}"
            perf_sum += p
            npl_sum += n
            cust_sum += _num(grid, f"{count}{row}")
        assert _close(_num(grid, f"AP{row}"), perf_sum), row
        assert _close(_num(grid, f"AQ{row}"), npl_sum), row
        assert _close(_num(grid, f"AR{row}"), perf_sum + npl_sum), row
        assert _close(_num(grid, f"AS{row}"), cust_sum), row

    # (d) section subtotals = Σ leaves (template SUM ranges), e.g. Agriculture
    assert _close(_num(grid, "B9"), sum(_num(grid, f"B{r}") for r in range(10, 17)))
    assert _close(
        _num(grid, "AR57"), sum(_num(grid, f"AR{r}") for r in ext.SECTOR_ROWS if 58 < r < 76)
    )

    # (e) GRAND TOTAL reconciles to Σ resident, placeable LOAN positions (cedis)
    grand = _num(grid, "AR95")
    assert _close(grand, float(RESIDENT_PLACEABLE_TOTAL)), grand
    assert _close(_num(grid, "AP95") + _num(grid, "AQ95"), grand)
    assert _close(_num(grid, "AQ95"), 2_000_000 + 200_000)  # all NPLs
    # No. of Cust. grand total = Σ distinct customers per cell (11 cells with
    # customers: gog, uni, ecg, bank, rural, insurer, foreign_corp, local_corp,
    # sme, {ama, kofi}, church) — 12, not the 15 loans
    assert _num(grid, "AS95") == 12
    # non-resident and CENTRAL_BANK loans are NOT on the main sheet
    assert not _close(grand, float(ALL_LOANS_TOTAL))

    # (f) Annex 4a — the SAME book by SNA sector; C15 is BoG's SUM; shares → 100
    annex = payload["cells"]["4a Annexure"]
    assert _close(_num(annex, "C7"), 4_000_000 + 1_500_000)  # deposit-takers
    assert _close(_num(annex, "C8"), 111_000)  # central bank
    assert _close(_num(annex, "C9"), 2_500_000)  # OFCs
    assert _close(_num(annex, "C10"), 12_000_000 + 3_000_000)  # general government
    assert _close(_num(annex, "C11"), 6_000_000 + 5_000_000 + 2_285_000)  # NFCs
    assert _close(_num(annex, "C12"), 7_000_000 + 1_500_000 + 300_000)  # other domestic
    assert _close(_num(annex, "C13"), 9_000_000)  # non-residents
    assert _close(_num(annex, "C15"), sum(_num(annex, f"C{r}") for r in range(7, 14)))
    assert _close(_num(annex, "C15"), float(ALL_LOANS_TOTAL))
    assert _close(sum(_num(annex, f"D{r}") for r in range(7, 14)), 100.0)
    assert _close(_num(annex, "D13"), 9_000_000 / float(ALL_LOANS_TOTAL) * 100)

    # (g) Annex 4b — geography; the SSA "of which" row is additional to Africa
    geo = payload["cells"]["4b Annexure"]
    assert _close(_num(geo, "C7"), float(ALL_LOANS_TOTAL) - 9_000_000)  # domestic
    assert _close(_num(geo, "C10"), 9_000_000)  # Africa
    assert _close(_num(geo, "C11"), 9_000_000)  # of which SSA (Nigeria)
    assert _num(geo, "C8") == 0
    assert _close(_num(geo, "D7") + _num(geo, "D10"), 100.0)

    # (h) sections/units: counts and shares are flagged unscaled in the snapshot
    by_cell = {row["cell"]: row for row in section["rows"]}
    assert by_cell["E10"]["unscaled"] is True
    assert by_cell["B10"]["unscaled"] is False
    assert by_cell["E10"]["value"] == "1"  # not divided by 1e6
    annex_rows = next(s for s in snapshot["sections"] if s["title"] == "4a Annexure")["rows"]
    share = next(r for r in annex_rows if r["cell"] == "D13")
    assert share["unscaled"] is True


def test_bsd4_book_without_sector_attribute_is_input_required_not_guessed(
    db_client: TestClient,
) -> None:
    unclassified = tuple(
        _LoanSpec(s.ref, s.counterparty, s.amount, None, s.stage, s.currency, s.attrs)
        for s in _LOANS
        if s.counterparty != "local_corp"  # its COUNTERPARTY carries a sector
    )
    reporting_date = _prepare(db_client, unclassified)
    snapshot = _generate(db_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    section = next(s for s in snapshot["sections"] if s["title"] == "BSD4")
    assert {row["status"] for row in section["rows"]} == {"input_required"}
    assert all(row["value"] is None for row in section["rows"])
    assert all(
        "sector classification attribute required" in row["notes"] for row in section["rows"]
    )
    # the grand total is blank too — no Miscellaneous dumping ground for an
    # unclassified book
    assert payload["cells"]["BSD4"].get("AR95") in (None, 0, 0.0)
    # the annexes need no sector attribute → they still fill
    annex = payload["cells"]["4a Annexure"]
    assert _close(_num(annex, "C15"), float(ALL_LOANS_TOTAL) - 5_000_000)


@pytest.mark.parametrize("group", sorted(ext.BORROWER_GROUPS))
def test_every_borrower_group_key_is_a_column_triplet(group: str) -> None:
    perf, npl, count = ext.BORROWER_GROUPS[group]
    layout = load_layout("BSD4").sheet("BSD4")
    for col in (perf, npl, count):
        assert layout.by_ref[f"{col}10"].kind == "input"
