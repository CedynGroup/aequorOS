"""BSD8 — Advances Subject to Adverse Classification: line map + resolver proof.

Executable form of docs/bog_returns/bsd8_line_map.md:

1. every official input cell of ``BSD8`` and ``BSD8-Annexure`` is bound (the
   Annexure's blank per-customer grid included) and the mapped / input_required
   split is exactly what the doc claims;
2. on a book of LOAN positions carrying the platform's classification
   attributes, the form generates through the REAL package pipeline and BoG's
   own arithmetic holds on the exported values: the bucket totals (H = Σ C:G),
   the movement schedule (item 8 = Σ items 1–7), the net balance (item 12),
   the annexure's per-row and total formulas, and the "% of 50 largest" line;
3. ``BSD8!H22`` equals ``BSD2!D38 + BSD2!D39`` of a BSD2 package generated the
   same way — the only cross-workbook external link in the official set,
   proven end to end;
4. the classification rule is honest: an explicit ``bog_classification`` fills
   all five buckets, an IFRS 9 stage-3-only book leaves Substandard / Doubtful
   / Loss input_required (Current / OLEM still resolve), an unclassified loan
   blanks every bucket.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources_ext import bsd8 as bsd8_sources
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

M = Decimal("1000000")

# ---------------------------------------------------------------------------
# 1. structure: every input cell of both sheets is bound
# ---------------------------------------------------------------------------

BUCKET_COLUMNS = ("C", "D", "E", "F", "G")
SUMMARY_MOVEMENT_ROWS = (8, 9, 10, 11, 12, 13)  # items 2–7: input_required by design
SUMMARY_MAPPED_ROWS = (7, 15, 17, 18, 21)  # items 1, 8(b), 10, 11, 13
SUMMARY_INPUT_ROWS = (16, 20)  # items 9, 12(b)
ANNEXURE_FIELDS = 13
ANNEXURE_ROWS = 50


def test_bsd8_line_map_binds_every_input_cell_of_both_sheets() -> None:
    layout = load_layout("BSD8")
    maps = line_maps_for("BSD8")
    assert set(maps) == {"BSD8", "BSD8-Annexure"}

    # summary sheet: 79 captured input cells (65 bucket cells + 14 item numbers)
    summary = maps["BSD8"]
    bound = {ref for line in summary for ref in line.cells.values()}
    captured = {c.ref for c in layout.sheet("BSD8").input_cells}
    assert captured == bound
    assert len(captured) == 79  # noqa: PLR2004 — official summary sheet
    by_row: dict[int, Any] = {}
    for line in summary:
        if "current" in line.cells:
            by_row[int(line.cells["current"][1:])] = line
    assert set(by_row) == set(SUMMARY_MOVEMENT_ROWS + SUMMARY_MAPPED_ROWS + SUMMARY_INPUT_ROWS)
    for row in SUMMARY_MAPPED_ROWS:
        assert by_row[row].source == "bsd8.bucket", row
        assert set(by_row[row].cells.values()) == {f"{c}{row}" for c in BUCKET_COLUMNS}
    for row in SUMMARY_MOVEMENT_ROWS + SUMMARY_INPUT_ROWS:
        assert by_row[row].source is None, row
        assert by_row[row].notes  # says what the bank must supply
    numbers = [line for line in summary if "no" in line.cells]
    assert len(numbers) == 14  # noqa: PLR2004 — item numbers 1–14 in column A
    assert all(line.source == "constant" and line.unscaled for line in numbers)
    assert [line.params["value"] for line in numbers] == list(range(1, 15))

    # annexure: 50 serial numbers (captured) + 50 × 13 blank-grid detail cells
    annexure = maps["BSD8-Annexure"]
    captured = {c.ref for c in layout.sheet("BSD8-Annexure").input_cells}
    bound = {ref for line in annexure for ref in line.cells.values()}
    assert captured <= bound
    detail = [line for line in annexure if line.source == "bsd8.annexure"]
    assert len(detail) == ANNEXURE_ROWS
    assert all(len(line.cells) == ANNEXURE_FIELDS for line in detail)
    assert [line.params["rank"] for line in detail] == list(range(1, ANNEXURE_ROWS + 1))
    assert detail[0].cells == {
        "name": "B5", "branch": "C5", "sector": "D5", "facility": "E5",
        "expiry_date": "F5", "capital": "G5", "interest": "H5", "obs": "J5",
        "security_value": "L5", "security_type": "M5", "classification": "N5",
        "provision": "O5", "comments": "P5",
    }  # fmt: skip
    # I (Total Funded Credits) / K (Total Exposure) are BoG's formulas — never bound
    assert not any(ref.startswith(("I", "K")) for ref in bound)
    assert len(bound) == ANNEXURE_ROWS * ANNEXURE_FIELDS + ANNEXURE_ROWS


# ---------------------------------------------------------------------------
# 2. the classification rule (pure)
# ---------------------------------------------------------------------------


def test_bucket_of_prefers_explicit_classification_then_stage_proxy() -> None:
    bucket_of = bsd8_sources.bucket_of
    assert bucket_of(3, {"bog_classification": "Sub-standard"}) == "substandard"
    assert bucket_of(1, {"bog_classification": "Other loans especially mentioned"}) == "olem"
    assert bucket_of(None, {"bog_classification": "LOSS"}) == "loss"
    assert bucket_of(1, {}) == "current"
    assert bucket_of(2, {}) == "olem"
    assert bucket_of(3, {}) == bsd8_sources.NPL_UNSPLIT
    assert bucket_of(None, {}) is None
    assert bucket_of(2, {"bog_classification": "watchlist"}) == "olem"  # unknown text ⇒ proxy


# ---------------------------------------------------------------------------
# 3. end to end on a classified loan book
# ---------------------------------------------------------------------------


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


class _Book:
    """Insert canonical LOAN / OBS / security positions for the sample bank."""

    def __init__(self, session: Session, as_of: date) -> None:
        self.session = session
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
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="bsd8-test-book",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        self.common: dict[str, Any] = {
            "organization_id": ORG_1,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": as_of,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }
        self.counterparties: dict[str, CanonicalCounterparty] = {}
        self.products: dict[str, CanonicalProduct] = {}

    def counterparty(
        self, ref: str, name: str, cp_type: str, attributes: dict[str, Any] | None = None
    ) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            source_reference=f"CP/{ref}",
            name=name,
            counterparty_type=cp_type,
            attributes=attributes or {},
        )
        self.session.add(row)
        self.session.flush()
        self.counterparties[ref] = row
        return row

    def product(self, code: str, name: str, category: str) -> CanonicalProduct:
        row = CanonicalProduct(
            **self.common,
            source_reference=f"PRODUCT/{code}",
            product_code=code,
            name=name,
            regulatory_category=category,
        )
        self.session.add(row)
        self.session.flush()
        self.products[code] = row
        return row

    def position(  # noqa: PLR0913 — keyword-only fixture builder
        self,
        ref: str,
        position_type: str,
        currency: str,
        *,
        snapshots: dict[date, str],
        counterparty: str | None = None,
        product: str | None = None,
        stage: int | None = None,
        maturity: date | None = None,
        notional: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        position = CanonicalPosition(
            **self.common,
            source_reference=ref,
            position_type=position_type,
            currency=currency,
        )
        self.session.add(position)
        self.session.flush()
        for as_of, balance in snapshots.items():
            fields = {**self.common, "as_of_date": as_of}
            self.session.add(
                CanonicalPositionSnapshot(
                    **fields,
                    source_reference=ref,
                    position_id=position.id,
                    counterparty_id=(
                        self.counterparties[counterparty].id if counterparty else None
                    ),
                    product_id=self.products[product].id if product else None,
                    balance=Decimal(balance),
                    notional=Decimal(notional) if notional else None,
                    ifrs9_stage=stage,
                    contractual_maturity=maturity,
                    attributes=dict(attributes or {}),
                )
            )
        self.session.flush()


def _seed_classified_book(session: Session, current: date, previous: date) -> None:
    book = _Book(session, current)
    book.counterparty("A", "Kumasi Traders Ltd", "CORPORATE", {"sector": "commerce.other"})
    book.counterparty("B", "Ama Mensah", "RETAIL_INDIVIDUAL")
    book.counterparty("C", "Volta Agro Ltd", "CORPORATE")
    book.counterparty("D", "Tema Steel Ltd", "CORPORATE")
    book.counterparty("GOG", "Government of Ghana", "SOVEREIGN")
    book.product("LN.CORP", "Corporate term loan", "CORPORATE_UNRATED")
    book.product("LN.RET", "Personal loan", "RETAIL_UNSECURED")
    book.product("SEC.GOG", "GoG securities", "SOVEREIGN_LOCAL_CCY")

    # L1 — largest adversely classified: substandard, cash-secured, interest suspended
    book.position(
        "LOAN/1", "LOAN", "GHS",
        snapshots={previous: "50000000", current: "48000000"},
        counterparty="A", product="LN.CORP", stage=3, maturity=date(2027, 6, 30),
        attributes={
            "bog_classification": "Substandard",
            "ecl_provision_ghs": "12000000",
            "interest_in_suspense_ghs": "1000000",
            "crm_collateral_ghs": "5000000",
            "crm_collateral_class": "CASH",
            "branch_id": "BR-001",
        },
    )  # fmt: skip
    # L2 — current retail loan
    book.position(
        "LOAN/2", "LOAN", "GHS",
        snapshots={previous: "8000000", current: "8000000"},
        counterparty="B", product="LN.RET", stage=1,
        attributes={"bog_classification": "current", "ecl_provision_ghs": "80000"},
    )  # fmt: skip
    # L3 — OLEM, USD-denominated (cedi equivalent supplied by the source)
    book.position(
        "LOAN/3", "LOAN", "USD",
        snapshots={previous: "1000000", current: "1000000"},
        counterparty="C", product="LN.CORP", stage=2, maturity=date(2026, 12, 31),
        attributes={
            "bog_classification": "OLEM",
            "balance_ghs": "13000000",
            "ecl_provision_ghs": "1300000",
            "crm_collateral_ghs": "4000000",
            "crm_collateral_class": "SOVEREIGN_DEBT",
        },
    )  # fmt: skip
    # L4 — loss, corporate-debt collateral (not cash/near-cash), interest suspended
    book.position(
        "LOAN/4", "LOAN", "GHS",
        snapshots={previous: "20000000", current: "22000000"},
        counterparty="D", product="LN.CORP", stage=3, maturity=date(2026, 9, 30),
        attributes={
            "bog_classification": "loss",
            "ecl_provision_ghs": "22000000",
            "interest_in_suspense_ghs": "2500000",
            "crm_collateral_ghs": "3000000",
            "crm_collateral_class": "CORPORATE_DEBT",
        },
    )  # fmt: skip
    # L5 — doubtful, NEW this month (no previous snapshot), second facility of A
    book.position(
        "LOAN/5", "LOAN", "GHS",
        snapshots={current: "6000000"},
        counterparty="A", product="LN.RET", stage=3, maturity=date(2026, 11, 30),
        attributes={"bog_classification": "doubtful", "ecl_provision_ghs": "3000000"},
    )  # fmt: skip
    # L6 — stage-only performing loan: the IFRS 9 stage-1 proxy places it in Current
    book.position(
        "LOAN/6", "LOAN", "GHS",
        snapshots={previous: "4000000", current: "4000000"},
        counterparty="B", product="LN.RET", stage=1,
        attributes={"ecl_provision_ghs": "40000"},
    )  # fmt: skip
    # OBS exposure of customers A and D (shown on the customer's first annexure row)
    book.position(
        "LC/A", "LC_GUARANTEE", "USD",
        snapshots={current: "100000"}, counterparty="A", notional="100000",
        attributes={"notional_ghs": "1300000"},
    )  # fmt: skip
    book.position(
        "COMMIT/D", "COMMITMENT_UNDRAWN", "GHS",
        snapshots={current: "2000000"}, counterparty="D", notional="2000000",
    )  # fmt: skip
    # Securities feeding BSD2 rows 38 (1-year GoG bond) and 39 (other bills) —
    # the cells BSD8!H22 links to through [1]BSD2!D38+[1]BSD2!D39
    book.position(
        "SEC/GOG1Y", "SECURITY_HOLDING", "GHS",
        snapshots={current: "15000000"}, counterparty="GOG", product="SEC.GOG",
        attributes={"instrument": "gog_bond", "tenor_years": "1"},
    )  # fmt: skip
    book.position(
        "SEC/TBO", "SECURITY_HOLDING", "GHS",
        snapshots={current: "5000000"}, counterparty="GOG", product="SEC.GOG",
        attributes={"instrument": "tbill_other"},
    )  # fmt: skip


def _cells(snapshot: dict[str, Any], sheet: str) -> dict[str, Any]:
    return snapshot["bog_form"]["cells"][sheet]


def _status(snapshot: dict[str, Any], sheet: str, cell: str) -> str:
    for section in snapshot["sections"]:
        if section["title"] != sheet:
            continue
        for row in section["rows"]:
            if row["cell"] == cell:
                return str(row["status"])
    msg = f"{sheet}!{cell} not declared"
    raise AssertionError(msg)


def _num(value: Any) -> float:
    return float(value or 0)


def _row(cells: dict[str, Any], row: int) -> list[float]:
    return [_num(cells.get(f"{c}{row}")) for c in BUCKET_COLUMNS]


def test_bsd8_generates_from_a_classified_loan_book_and_bogs_arithmetic_holds(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    _materialize(db_client)
    reporting_date = _latest_period_end(db_client)
    current = date.fromisoformat(reporting_date)
    previous = current.replace(day=1) - timedelta(days=1)  # previous month-end
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        _seed_classified_book(session, current, previous)
        session.commit()
    finally:
        session.close()

    bsd8 = _generate(db_client, "BSD8", reporting_date)
    payload = bsd8["bog_form"]
    assert not payload["errors"], payload["errors"]
    assert not payload["missing_dependencies"]
    assert not payload["unresolved_external"]
    assert payload["status_counts"]["unmapped"] == 0
    summary = _cells(bsd8, "BSD8")

    # --- item 1: previous balance by bucket (latest snapshot ≤ previous month-end)
    assert _row(summary, 7) == [12e6, 13e6, 50e6, 0.0, 20e6]  # L5 is new this month
    # H = SUM(C:G) — BoG's total column
    assert _num(summary["H7"]) == pytest.approx(95e6)
    assert _num(summary["H7"]) == pytest.approx(sum(_row(summary, 7)))
    # --- items 2–7 are the bank's movement schedule: declared, input_required
    for row in SUMMARY_MOVEMENT_ROWS:
        for col in BUCKET_COLUMNS:
            assert _status(bsd8, "BSD8", f"{col}{row}") == "input_required"
            assert summary.get(f"{col}{row}") is None
    # --- item 8 = Σ items 1–7 (template SUM) — equals the opening balance until
    # the bank supplies the movements; the total column re-sums the buckets
    assert _row(summary, 14) == _row(summary, 7)
    assert _num(summary["H14"]) == pytest.approx(sum(_row(summary, 14)))
    # --- 8(b) FX share: only the USD loan (cedi equivalent from the source)
    assert _row(summary, 15) == [0.0, 13e6, 0.0, 0.0, 0.0]
    # --- item 10 interest in suspense / item 11 allowable security / item 13 provisions
    assert _row(summary, 17) == [0.0, 0.0, 1e6, 0.0, 2.5e6]
    assert _row(summary, 18) == [0.0, 4e6, 5e6, 0.0, 0.0]  # CORPORATE_DEBT is not near-cash
    assert _row(summary, 21) == [120e3, 1.3e6, 12e6, 3e6, 22e6]
    for row in SUMMARY_MAPPED_ROWS:
        for col in BUCKET_COLUMNS:
            assert _status(bsd8, "BSD8", f"{col}{row}") == "mapped", f"{col}{row}"
    for row in SUMMARY_INPUT_ROWS:
        assert _status(bsd8, "BSD8", f"C{row}") == "input_required"
    # --- item 12 = item 8 − 9 − 10 − 11 per bucket (template formula)
    for i, col in enumerate(BUCKET_COLUMNS):
        expected = (
            _row(summary, 14)[i]
            - _row(summary, 16)[i]
            - _row(summary, 17)[i]
            - _row(summary, 18)[i]
        )
        assert _num(summary[f"{col}19"]) == pytest.approx(expected), col
    assert _num(summary["E19"]) == pytest.approx(44e6)  # 50 − 0 − 1 − 5
    assert _num(summary["H19"]) == pytest.approx(sum(_row(summary, 19)))
    # item numbers reproduce the template's printed literals
    assert [summary[f"A{r}"] for r in (7, 8, 14, 22)] == [1, 2, 8, 14]

    # --- annexure: 50 largest adversely classified advances, largest first
    annex = _cells(bsd8, "BSD8-Annexure")
    assert annex["B5"] == "Kumasi Traders Ltd"
    assert annex["C5"] == "BR-001"
    assert annex["D5"] == "commerce.other"
    assert annex["E5"] == "Corporate term loan"
    assert annex["F5"] == "2027-06-30"
    assert _num(annex["G5"]) == 48e6
    assert annex.get("H5") is None  # interest due: accruals sub-ledger
    assert _status(bsd8, "BSD8-Annexure", "H5") == "input_required"
    assert _num(annex["J5"]) == 1.3e6  # customer A's LC, on A's first row
    assert _num(annex["L5"]) == 5e6
    assert annex["M5"] == "CASH"
    assert annex["N5"] == "Substandard"
    assert _num(annex["O5"]) == 12e6
    assert _status(bsd8, "BSD8-Annexure", "P5") == "input_required"
    assert [annex[f"B{r}"] for r in (6, 7, 8)] == [
        "Tema Steel Ltd", "Volta Agro Ltd", "Kumasi Traders Ltd"
    ]  # fmt: skip
    assert [annex[f"N{r}"] for r in (6, 7, 8)] == ["Loss", "Olem", "Doubtful"]
    assert _num(annex["J6"]) == 2e6  # D's undrawn commitment
    assert _num(annex["J8"]) == 0.0  # A's second facility: OBS already shown on row 5
    # I = G + H and K = I + J on every listed row (BoG's formulas)
    for r in (5, 6, 7, 8):
        assert _num(annex[f"I{r}"]) == pytest.approx(_num(annex[f"G{r}"]) + _num(annex[f"H{r}"]))
        assert _num(annex[f"K{r}"]) == pytest.approx(_num(annex[f"I{r}"]) + _num(annex[f"J{r}"]))
    # rows past the end of the list are positively empty (mapped, blank)
    assert annex.get("B9") in (None, "")
    assert _status(bsd8, "BSD8-Annexure", "B9") == "mapped"
    assert _status(bsd8, "BSD8-Annexure", "G54") == "mapped"
    # TOTAL row and the % line
    assert _num(annex["G55"]) == pytest.approx(48e6 + 22e6 + 13e6 + 6e6)
    assert _num(annex["K55"]) == pytest.approx(_num(annex["I55"]) + _num(annex["J55"]))
    assert _num(annex["K55"]) == pytest.approx(89e6 + 3.3e6)
    assert _num(annex["C56"]) == pytest.approx(_num(annex["K55"]) / _num(summary["H19"]) * 100)
    assert [annex[f"A{r}"] for r in (5, 6, 54)] == [1, 2, 50]

    # --- H22 = [1]BSD2!D38 + [1]BSD2!D39: proven against a BSD2 package
    # generated the same way for the same reporting date
    bsd2 = _generate(db_client, "BSD2", reporting_date)
    spine = _cells(bsd2, "BSD2")
    assert _num(spine["D38"]) == 15e6
    assert _num(spine["D39"]) == 5e6
    assert _num(summary["H22"]) == pytest.approx(_num(spine["D38"]) + _num(spine["D39"]))
    assert _num(summary["H22"]) == pytest.approx(20e6)


def _seed_stage_only_book(session: Session, current: date) -> None:
    book = _Book(session, current)
    book.counterparty("B", "Ama Mensah", "RETAIL_INDIVIDUAL")
    book.counterparty("C", "Volta Agro Ltd", "CORPORATE")
    book.product("LN.RET", "Personal loan", "RETAIL_UNSECURED")
    book.position(
        "LOAN/7", "LOAN", "GHS", snapshots={current: "3000000"},
        counterparty="B", product="LN.RET", stage=3,
        attributes={"ecl_provision_ghs": "900000"},
    )  # fmt: skip
    book.position(
        "LOAN/8", "LOAN", "GHS", snapshots={current: "10000000"},
        counterparty="C", product="LN.RET", stage=1,
        attributes={"ecl_provision_ghs": "100000"},
    )  # fmt: skip
    book.position(
        "LOAN/9", "LOAN", "GHS", snapshots={current: "5000000"},
        counterparty="C", product="LN.RET", stage=2,
        attributes={"ecl_provision_ghs": "500000"},
    )  # fmt: skip


def test_bsd8_stage_only_book_leaves_the_npl_split_input_required(
    db_client: TestClient,
) -> None:
    _materialize(db_client)
    reporting_date = _latest_period_end(db_client)
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        _seed_stage_only_book(session, date.fromisoformat(reporting_date))
        session.commit()
    finally:
        session.close()
    bsd8 = _generate(db_client, "BSD8", reporting_date)
    assert not bsd8["bog_form"]["errors"], bsd8["bog_form"]["errors"]
    summary = _cells(bsd8, "BSD8")
    # provisions: Current (stage 1) and OLEM (stage 2) resolve; the stage-3 loan
    # cannot be split into Substandard / Doubtful / Loss ⇒ those cells are blank
    assert _num(summary["C21"]) == 100e3
    assert _num(summary["D21"]) == 500e3
    for col in ("E", "F", "G"):
        assert summary.get(f"{col}21") is None
        assert _status(bsd8, "BSD8", f"{col}21") == "input_required", col
    assert _status(bsd8, "BSD8", "C21") == "mapped"
    # no interest-in-suspense / collateral attributes anywhere ⇒ input_required, not 0
    assert summary.get("C17") is None
    assert _status(bsd8, "BSD8", "C17") == "input_required"
    assert summary.get("C18") is None
    # annexure: OLEM first (larger), then the stage-3 loan whose classification the
    # bank must state; the row's other details still resolve
    annex = _cells(bsd8, "BSD8-Annexure")
    assert annex["B5"] == "Volta Agro Ltd"
    assert annex["N5"] == "Olem"
    assert annex["B6"] == "Ama Mensah"
    assert _num(annex["G6"]) == 3e6
    assert annex.get("N6") is None
    assert _status(bsd8, "BSD8-Annexure", "N6") == "input_required"
    assert _status(bsd8, "BSD8-Annexure", "B6") == "mapped"


def test_bsd8_bucket_is_undecidable_when_a_loan_is_unclassified() -> None:
    """The decidability rule (pure): an unclassified loan blanks every bucket; a
    stage-3-only loan blanks only the NPL buckets."""
    undecidable = bsd8_sources._undecidable  # noqa: SLF001 — the rule under test

    class _L:
        def __init__(self, bucket: str | None) -> None:
            self.bucket = bucket

    classified = [_L("current"), _L("olem"), _L("loss")]
    for bucket in bsd8_sources.BUCKETS:
        assert undecidable(classified, bucket) is False  # type: ignore[arg-type]
    with_npl = [*classified, _L(bsd8_sources.NPL_UNSPLIT)]
    assert undecidable(with_npl, "current") is False  # type: ignore[arg-type]
    assert undecidable(with_npl, "olem") is False  # type: ignore[arg-type]
    for bucket in ("substandard", "doubtful", "loss"):
        assert undecidable(with_npl, bucket) is True  # type: ignore[arg-type]
    with_unknown = [*classified, _L(None)]
    for bucket in bsd8_sources.BUCKETS:
        assert undecidable(with_unknown, bucket) is True  # type: ignore[arg-type]
