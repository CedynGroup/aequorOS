"""Loan-attribute data gaps close through the Data Engine — hermetic proof.

The BoG loan returns need statements the typed position schema has no home
for: ``sector`` (BSD4), ``bog_classification`` / ``interest_in_suspense_ghs`` /
``ecl_provision_ghs`` (BSD8), ``borrower_class`` (BSD2 §8 / BSD4). They ride
as position ``attributes`` (docs/API_INTEGRATION.md §3.4 "BoG prudential-return
conventions"). This suite proves, through the REAL ingestion API and the REAL
package pipeline, that:

1. the pure ``attributes_from_columns`` seam captures ``attributes.<key>``
   columns and mapping ``attribute_columns`` (and only non-null cells);
2. a loans **push** whose records carry the documented attributes lands them on
   the position snapshots, and a **re-push** of the same facilities (same
   source references, same as-of) supersedes the prior generation whole —
   balances identical, one current snapshot per position, attributes replaced;
3. an uploaded **CSV** in the ``loans_template.csv`` shape (``attributes.*``
   headers, identity field names, no attribute mapping) lands the same keys via
   the Excel/CSV adapter;
4. BSD4 / BSD8 / BSD2 generated via ``POST /regulatory-packages`` flip the cells
   that were ``input_required`` on the unclassified book to ``mapped`` with the
   expected values once the classified book is in.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.domain.ingestion.attributes import attribute_key, attributes_from_columns
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.models import CanonicalPosition, CanonicalPositionSnapshot
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

TEMPLATE = Path(__file__).resolve().parents[3] / "onboarding" / "sample_bank" / "loans_template.csv"

# ---------------------------------------------------------------------------
# 1. the pure seam
# ---------------------------------------------------------------------------


def test_attribute_key_recognises_only_the_documented_prefix() -> None:
    assert attribute_key("attributes.sector") == "sector"
    assert attribute_key("attributes. bog_classification ") == "bog_classification"
    assert attribute_key("attributes.") is None
    assert attribute_key("sector") is None
    assert attribute_key("Attributes.sector") is None  # exact prefix, like the push client


def test_attributes_from_columns_captures_prefixed_and_mapped_columns_only() -> None:
    row = {
        "source_reference": "LN-1",
        "attributes.sector": "agriculture.cocoa_production",
        "attributes.days_past_due": "0",
        "attributes.interest_in_suspense_ghs": "",
        "ECL_PROV": "12500.50",
        "CCF": None,
        "attributes.ECL_PROV": "99",  # prefixed statement wins over attribute_columns
    }
    captured = attributes_from_columns(
        row, ["ECL_PROV", "CCF", "MISSING"], is_null=lambda v: v in (None, "")
    )
    assert captured == {
        "ECL_PROV": "99",
        "sector": "agriculture.cocoa_production",
        "days_past_due": "0",
    }
    assert attributes_from_columns({"source_reference": "LN-1"}) == {}


# ---------------------------------------------------------------------------
# hermetic book: entities pushed through the real API
# ---------------------------------------------------------------------------

GL = [
    {
        "source_reference": "1304",
        "account_code": "1304",
        "name": "Loans and advances",
        "account_class": "ASSET",
        "currency": "GHS",
    }
]
PRODUCTS = [
    {"source_reference": "LN.CORP", "product_code": "LN.CORP", "name": "Corporate loan"},
    {"source_reference": "LN.RET", "product_code": "LN.RET", "name": "Personal loan"},
]
COUNTERPARTIES = [
    {"source_reference": "CP-GOG", "name": "Government of Ghana", "counterparty_type": "SOVEREIGN"},
    {
        "source_reference": "CP-UNI",
        "name": "University of Ghana",
        "counterparty_type": "GOVERNMENT_ENTITY",
    },
    {"source_reference": "CP-ECG", "name": "ECG", "counterparty_type": "GOVERNMENT_ENTITY"},
    {"source_reference": "CP-CORP", "name": "Kumasi Foods Ltd", "counterparty_type": "CORPORATE"},
    {"source_reference": "CP-FOR", "name": "Atlantic Mining Plc", "counterparty_type": "CORPORATE"},
    {"source_reference": "CP-AMA", "name": "Ama Mensah", "counterparty_type": "RETAIL_INDIVIDUAL"},
    {
        "source_reference": "CP-KOFI",
        "name": "Kofi Boateng",
        "counterparty_type": "RETAIL_INDIVIDUAL",
    },
]


def _loan(  # noqa: PLR0913 — fixture builder
    ref: str,
    counterparty: str,
    balance: str,
    product: str = "LN.CORP",
    attributes: dict[str, Any] | None = None,
    stage: int | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source_reference": ref,
        "position_type": "LOAN",
        "currency": "GHS",
        "balance": balance,
        "counterparty_reference": counterparty,
        "product_code": product,
        "gl_account_code": "1304",
        "origination_date": "2025-06-01",
        "contractual_maturity": "2028-06-01",
        "interest_rate": 0.28,
        "rate_type": "FIXED",
    }
    if stage is not None:
        record["ifrs9_stage"] = stage
    if attributes:
        record["attributes"] = attributes
    return record


# the same seven facilities: unclassified (v1) and classified (v2)
BALANCES = {
    "LN-GOG-1": "10000000",
    "LN-UNI-1": "3000000",
    "LN-ECG-1": "7000000",
    "LN-CORP-1": "5000000",
    "LN-FOR-1": "6000000",
    "LN-AMA-1": "800000",
    "LN-KOFI-1": "200000",
}
UNCLASSIFIED = [
    _loan("LN-GOG-1", "CP-GOG", BALANCES["LN-GOG-1"], attributes={"branch_id": "BR-001"}),
    _loan("LN-UNI-1", "CP-UNI", BALANCES["LN-UNI-1"]),
    _loan("LN-ECG-1", "CP-ECG", BALANCES["LN-ECG-1"]),
    _loan("LN-CORP-1", "CP-CORP", BALANCES["LN-CORP-1"]),
    _loan("LN-FOR-1", "CP-FOR", BALANCES["LN-FOR-1"]),
    _loan("LN-AMA-1", "CP-AMA", BALANCES["LN-AMA-1"], product="LN.RET"),
    _loan("LN-KOFI-1", "CP-KOFI", BALANCES["LN-KOFI-1"], product="LN.RET"),
]
CLASSIFIED = [
    _loan(
        "LN-GOG-1",
        "CP-GOG",
        BALANCES["LN-GOG-1"],
        stage=1,
        attributes={
            "branch_id": "BR-001",
            "sector": "agriculture.cocoa_production",
            "bog_classification": "current",
            "days_past_due": 0,
            "ecl_provision_ghs": 100000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-UNI-1",
        "CP-UNI",
        BALANCES["LN-UNI-1"],
        stage=1,
        attributes={
            "sector": "services.other_incl_government",
            "borrower_class": "public_institution",
            "bog_classification": "current",
            "days_past_due": 0,
            "ecl_provision_ghs": 30000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-ECG-1",
        "CP-ECG",
        BALANCES["LN-ECG-1"],
        stage=2,
        attributes={
            "sector": "utilities.electricity",
            "borrower_class": "public_enterprise",
            "bog_classification": "olem",
            "days_past_due": 45,
            "ecl_provision_ghs": 700000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-CORP-1",
        "CP-CORP",
        BALANCES["LN-CORP-1"],
        stage=1,
        attributes={
            "sector": "commerce.import.other",
            "bog_classification": "current",
            "days_past_due": 0,
            "ecl_provision_ghs": 50000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-FOR-1",
        "CP-FOR",
        BALANCES["LN-FOR-1"],
        stage=1,
        attributes={
            "sector": "mining.gold",
            "ownership": "foreign",
            "bog_classification": "current",
            "days_past_due": 12,
            "ecl_provision_ghs": 60000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-AMA-1",
        "CP-AMA",
        BALANCES["LN-AMA-1"],
        product="LN.RET",
        stage=1,
        attributes={
            "sector": "services.salary_credit",
            "bog_classification": "current",
            "days_past_due": 0,
            "ecl_provision_ghs": 8000,
            "interest_in_suspense_ghs": 0,
        },
    ),
    _loan(
        "LN-KOFI-1",
        "CP-KOFI",
        BALANCES["LN-KOFI-1"],
        product="LN.RET",
        stage=3,
        attributes={
            "sector": "services.salary_credit",
            "bog_classification": "doubtful",
            "days_past_due": 210,
            "ecl_provision_ghs": 100000,
            "interest_in_suspense_ghs": 25000,
        },
    ),
]


def _materialize(db_client: TestClient) -> str:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return str(periods[0]["period_end"])


def _push(
    db_client: TestClient, key: str, as_of: str, entities: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    opened = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches",
        headers=headers(),
        json={"as_of_date": as_of, "idempotency_key": key, "reason": f"data-gaps test {key}"},
    )
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/records",
        headers=headers(),
        json={"entities": entities},
    )
    assert staged.status_code == 200, staged.text
    committed = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/commit", headers=headers()
    )
    assert committed.status_code == 201, committed.text
    body = committed.json()
    assert body["batch"]["status"] in ("accepted", "accepted_with_warnings"), body["batch"]
    return body["batch"]


def _generate(db_client: TestClient, code: str, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": code, "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{code}: {response.status_code} {response.text[:300]}"
    detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{response.json()['id']}",
        headers=headers(),
    ).json()
    return detail["snapshot"]


def _status(snapshot: dict[str, Any], sheet: str, cell: str) -> str:
    for section in snapshot["sections"]:
        if section["title"] != sheet:
            continue
        for row in section["rows"]:
            if row["cell"] == cell:
                return str(row["status"])
    msg = f"{sheet}!{cell} not declared"
    raise AssertionError(msg)


def _num(snapshot: dict[str, Any], sheet: str, cell: str) -> float:
    value = snapshot["bog_form"]["cells"][sheet].get(cell)
    return 0.0 if value in (None, "") else float(value)


def _current_loan_snapshots() -> dict[str, CanonicalPositionSnapshot]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        rows = session.execute(
            select(CanonicalPositionSnapshot)
            .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
            .where(
                CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
                CanonicalPositionSnapshot.superseded_by.is_(None),
                CanonicalPosition.superseded_by.is_(None),
                CanonicalPosition.position_type == "LOAN",
            )
        ).scalars()
        current = {row.source_reference: row for row in rows}
        # detach: attributes/balances are plain values, safe after close
        for row in current.values():
            _ = (row.attributes, row.balance, row.position_id, row.ifrs9_stage)
            session.expunge(row)
        return current
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 2 + 4. push, re-push (supersession), returns flip
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_storage")
def test_loan_attribute_repush_lands_on_snapshots_and_flips_bsd4_bsd8_bsd2(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    as_of = _materialize(db_client)

    # --- v1: the unclassified book (no sector / classification attributes)
    _push(
        db_client,
        "loans-v1",
        as_of,
        {
            "gl_account": GL,
            "product": PRODUCTS,
            "counterparty": COUNTERPARTIES,
            "position": UNCLASSIFIED,
        },
    )
    before = _current_loan_snapshots()
    assert set(before) == set(BALANCES)
    assert before["LN-GOG-1"].attributes == {"branch_id": "BR-001"}
    assert before["LN-UNI-1"].attributes == {}
    positions_v1 = {ref: snap.position_id for ref, snap in before.items()}

    bsd4_v1 = _generate(db_client, "BSD4", as_of)
    bsd8_v1 = _generate(db_client, "BSD8", as_of)
    bsd2_v1 = _generate(db_client, "BSD2", as_of)
    # every BSD4 sector cell is honest input_required (nothing inferred from names)
    assert _status(bsd4_v1, "BSD4", "B10") == "input_required"
    assert _status(bsd4_v1, "BSD4", "F91") == "input_required"
    assert _status(bsd4_v1, "BSD4", "AH90") == "input_required"
    # BSD8: an unclassified book blanks every as-at bucket of items 8(b) / 10 / 13
    # (item 1 "previous balance" reads the prior month-end, where no book exists)
    for cell in ("C15", "G15", "C17", "F17", "C21", "F21"):
        assert _status(bsd8_v1, "BSD8", cell) == "input_required", cell
    # BSD2 8(b)/(c): GOVERNMENT_ENTITY loans without borrower_class land nowhere
    assert _num(bsd2_v1, "BSD2", "B62") == 0
    assert _num(bsd2_v1, "BSD2", "B65") == 0

    # --- v2: the SAME facilities re-pushed with the documented attributes
    batch = _push(db_client, "loans-v2-classified", as_of, {"position": CLASSIFIED})
    assert batch["records_accepted"] == len(CLASSIFIED)

    after = _current_loan_snapshots()
    assert set(after) == set(BALANCES)
    for ref, expected in BALANCES.items():
        # supersession: same position, ONE current snapshot, identical balance
        assert after[ref].position_id == positions_v1[ref], ref
        assert Decimal(str(after[ref].balance)) == Decimal(expected), ref
    # attributes are replaced whole by the re-push (v1's branch_id was resent)
    assert after["LN-GOG-1"].attributes["branch_id"] == "BR-001"
    assert after["LN-GOG-1"].attributes["sector"] == "agriculture.cocoa_production"
    assert after["LN-KOFI-1"].attributes["bog_classification"] == "doubtful"
    assert Decimal(str(after["LN-KOFI-1"].attributes["interest_in_suspense_ghs"])) == 25000
    assert after["LN-KOFI-1"].ifrs9_stage == 3
    assert after["LN-UNI-1"].attributes["borrower_class"] == "public_institution"
    assert after["LN-FOR-1"].attributes["ownership"] == "foreign"
    assert after["LN-ECG-1"].attributes["days_past_due"] == 45

    # --- BSD4: sector × borrower-class cells now mapped with the loans' cedis
    bsd4 = _generate(db_client, "BSD4", as_of)
    assert not bsd4["bog_form"]["errors"]
    section = next(s for s in bsd4["sections"] if s["title"] == "BSD4")
    assert {row["status"] for row in section["rows"]} == {"mapped"}
    assert _num(bsd4, "BSD4", "B10") == 10_000_000  # cocoa · central government · perf.
    assert _num(bsd4, "BSD4", "F91") == 3_000_000  # public institution · other services
    assert _num(bsd4, "BSD4", "J53") == 7_000_000  # public enterprise · electricity
    assert _num(bsd4, "BSD4", "Z21") == 6_000_000  # foreign-owned private corp · gold
    assert _num(bsd4, "BSD4", "AD61") == 5_000_000  # indigenous private corp · imports
    assert _num(bsd4, "BSD4", "AH90") == 800_000  # households · salary credit · perf.
    assert _num(bsd4, "BSD4", "AI90") == 200_000  # households · salary credit · NPL
    assert _num(bsd4, "BSD4", "AK90") == 2  # two household customers
    assert _num(bsd4, "BSD4", "AR95") == sum(int(v) for v in BALANCES.values())

    # --- BSD8: items 8(b) / 10 / 13 (as-at) fill per bucket from the attributes
    bsd8 = _generate(db_client, "BSD8", as_of)
    assert not bsd8["bog_form"]["errors"]
    for cell in ("C15", "G15", "C17", "F17", "C21", "F21"):
        assert _status(bsd8, "BSD8", cell) == "mapped", cell
    # item 13 provisions: current = 100000+30000+50000+60000+8000 · olem 700000 · doubtful 100000
    assert _num(bsd8, "BSD8", "C21") == 248_000
    assert _num(bsd8, "BSD8", "D21") == 700_000
    assert _num(bsd8, "BSD8", "E21") == 0
    assert _num(bsd8, "BSD8", "F21") == 100_000
    assert _num(bsd8, "BSD8", "G21") == 0
    assert _num(bsd8, "BSD8", "H21") == 1_048_000  # BoG's own SUM(C21:G21)
    # item 10 interest in suspense lands in the doubtful bucket only
    assert _num(bsd8, "BSD8", "F17") == 25_000
    assert _num(bsd8, "BSD8", "C17") == 0
    # annexure: adversely classified facilities ranked by cedi balance
    assert _num(bsd8, "BSD8-Annexure", "G5") == 7_000_000  # ECG (olem) first
    assert bsd8["bog_form"]["cells"]["BSD8-Annexure"]["N5"] == "Olem"
    assert bsd8["bog_form"]["cells"]["BSD8-Annexure"]["D5"] == "utilities.electricity"
    assert _num(bsd8, "BSD8-Annexure", "G6") == 200_000  # Kofi (doubtful)
    assert bsd8["bog_form"]["cells"]["BSD8-Annexure"]["N6"] == "Doubtful"
    assert _num(bsd8, "BSD8-Annexure", "O6") == 100_000
    assert _status(bsd8, "BSD8-Annexure", "G7") == "mapped"  # positively empty tail

    # --- BSD2 §8: borrower_class places the GOVERNMENT_ENTITY loans
    bsd2 = _generate(db_client, "BSD2", as_of)
    assert _num(bsd2, "BSD2", "B62") == 3_000_000  # 8(b) public institutions
    assert _num(bsd2, "BSD2", "B65") == 7_000_000  # 8(c)(ii) public enterprises — others


# ---------------------------------------------------------------------------
# 3. the CSV upload path in the loans_template.csv shape
# ---------------------------------------------------------------------------


def _template_row(**overrides: str) -> dict[str, str]:
    with TEMPLATE.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(line for line in handle if not line.startswith("#")))
    row = dict.fromkeys(header, "")
    for key, value in overrides.items():
        assert key in row, key
        row[key] = value
    return row


@pytest.mark.usefixtures("fake_storage")
def test_uploaded_loans_csv_in_template_shape_lands_attributes(
    db_client: TestClient, tmp_path: Path
) -> None:
    as_of = _materialize(db_client)
    _push(
        db_client,
        "entities-for-upload",
        as_of,
        {"gl_account": GL, "product": PRODUCTS, "counterparty": COUNTERPARTIES},
    )
    # identity mapping for the template's sheet — no attribute_columns at all:
    # the attributes.* headers are the seam
    identity: dict[str, str | list[str]] = {
        name: name
        for name in (
            "source_reference",
            "position_type",
            "currency",
            "balance",
            "counterparty_reference",
            "product_code",
            "gl_account_code",
            "origination_date",
            "contractual_maturity",
            "interest_rate",
            "rate_type",
            "ifrs9_stage",
        )
    }
    mapping = MappingConfig(
        field_mappings={"position": EntityMapping(source_table="loans", fields=identity)},
        options={"dayfirst": False},
    )
    activated = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/mapping-configs",
        headers=headers(),
        json={
            "source_system": "EXCEL_CSV",
            "name": "Loans template",
            "config": mapping.model_dump(mode="json"),
            "activate": True,
            "reason": "data-gaps test: loans template upload",
        },
    )
    assert activated.status_code == 200, activated.text

    rows = [
        _template_row(
            source_reference="LN-UP-1",
            position_type="LOAN",
            currency="GHS",
            balance="1500000",
            counterparty_reference="CP-CORP",
            product_code="LN.CORP",
            gl_account_code="1304",
            origination_date="2025-06-01",
            contractual_maturity="2028-06-01",
            interest_rate="0.28",
            rate_type="FIXED",
            ifrs9_stage="1",
            **{
                "attributes.sector": "manufacturing.home.food_drink_tobacco",
                "attributes.bog_classification": "current",
                "attributes.days_past_due": "0",
                "attributes.ecl_provision_ghs": "15000",
                "attributes.facility_type": "scheduled",
            },
        ),
        _template_row(
            source_reference="LN-UP-2",
            position_type="LOAN",
            currency="GHS",
            balance="400000",
            counterparty_reference="CP-AMA",
            product_code="LN.RET",
            gl_account_code="1304",
            origination_date="2024-01-15",
            contractual_maturity="2027-01-15",
            interest_rate="0.31",
            rate_type="FIXED",
            ifrs9_stage="3",
            **{
                "attributes.sector": "services.salary_credit",
                "attributes.bog_classification": "loss",
                "attributes.days_past_due": "400",
                "attributes.interest_in_suspense_ghs": "38000",
                "attributes.ecl_provision_ghs": "400000",
            },
        ),
    ]
    path = tmp_path / "loans.csv"
    with TEMPLATE.open(encoding="utf-8") as src:
        header_line = next(line for line in src if not line.startswith("#"))
    with path.open("w", newline="", encoding="utf-8") as out:
        out.write(header_line)
        writer = csv.DictWriter(out, fieldnames=header_line.strip().split(","))
        writer.writerows(rows)

    with path.open("rb") as handle:
        staged = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/ingestion-uploads",
            headers=headers(),
            files={"file": (path.name, handle, "text/csv")},
        )
    assert staged.status_code == 201, staged.text
    started = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/ingestion-batches",
        headers=headers(),
        json={
            "source_system": "EXCEL_CSV",
            "as_of_date": as_of,
            "location": staged.json()["location"],
            "reason": "data-gaps test: loans template upload",
        },
    )
    assert started.status_code == 201, started.text
    batch = started.json()["batch"]
    assert batch["status"] in ("accepted", "accepted_with_warnings"), batch
    assert batch["records_accepted"] == 2

    snapshots = _current_loan_snapshots()
    up1, up2 = snapshots["LN-UP-1"], snapshots["LN-UP-2"]
    # values are stringified by the spreadsheet path (numbers verbatim); blanks omitted
    assert up1.attributes == {
        "sector": "manufacturing.home.food_drink_tobacco",
        "bog_classification": "current",
        "days_past_due": "0",
        "ecl_provision_ghs": "15000",
        "facility_type": "scheduled",
    }
    assert up2.attributes["bog_classification"] == "loss"
    assert up2.attributes["interest_in_suspense_ghs"] == "38000"
    assert "facility_type" not in up2.attributes
    assert up2.ifrs9_stage == 3

    # the uploaded (text-valued) attributes feed the same return cells
    bsd8 = _generate(db_client, "BSD8", as_of)
    assert _status(bsd8, "BSD8", "G21") == "mapped"
    assert _num(bsd8, "BSD8", "G21") == 400_000  # loss-bucket provision
    assert _num(bsd8, "BSD8", "G17") == 38_000  # loss-bucket interest in suspense
    assert _num(bsd8, "BSD8", "C21") == 15_000
    bsd4 = _generate(db_client, "BSD4", as_of)
    assert _num(bsd4, "BSD4", "AD38") == 1_500_000  # indigenous corp · home food/drink
    assert _num(bsd4, "BSD4", "AI90") == 400_000  # household NPL · salary credit
