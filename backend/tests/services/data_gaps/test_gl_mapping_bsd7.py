"""``gl_mapping_bsd7`` — the bank's chart-of-accounts → BSD7A/BSD7B P&L item
register, plus the INCOME/EXPENSE ledger it maps.

Closes every P&L line of BSD7A (Current Year Results) and BSD7B (Consolidated
Results) through the Data Engine:

1. the ``bsd7_item`` vocabulary is exactly the set of item tags the BSD7A line
   map binds (no drift between register and line map);
2. the Sample Bank onboarding CSVs — four month-ends of the P&L ledger
   (``gl_account`` entity records, INCOME/EXPENSE, fiscal-year-to-date
   balances) and the mapping register — pushed through the REAL API by the
   generic ``scripts/ingest_push.py`` client (its ``push()`` three-call flow,
   driven against the TestClient) land as canonical GL generations and
   reference rows with batch lineage; BSD7A / BSD7B generated through
   ``POST /regulatory-packages`` then carry the ledger's figures on every P&L
   line (``input_required`` → ``mapped``) with the register's precedence
   (exact code beats prefix, contra sign, an account's own ``bsd7_line`` tag
   beats the register), month = Δ of consecutive YTD balances, Domestic /
   Foreign by account currency;
3. the register schema rejects a malformed row honestly.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.domain.ingestion.reference_schemas import schema_for
from app.domain.ingestion.reference_schemas.gl_mapping_bsd7 import (
    BSD7_ITEMS,
    SCHEMA,
    validate_mapping_row,
)
from app.models import CanonicalGlAccount, CanonicalReferenceRow
from app.services.regulatory_reporting.bog_forms.linemaps.bsd7a import PL_ROWS
from scripts import ingest_push
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
ONBOARDING = Path(__file__).resolve().parents[3] / "onboarding" / "sample_bank"
MAPPING_CSV = ONBOARDING / "gl_mapping_bsd7.csv"
#: the hermetic book's periods run 2025-04 … 2026-03: push the prior fiscal
#: year-end (must never leak) and the first quarter of 2026
LEDGER_DATES = ("2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31")
REPORTING_DATE = "2026-03-31"
#: BSD7A row ↔ item tag (from the line map) and its Domestic/Foreign cells
ROW_OF_ITEM = {src.params["line"]: row for row, src in PL_ROWS.items() if src.source}
#: an INCOME account inside the ``410`` prefix that the ledger itself tags to
#: item 1b: its own tag must beat the register (pushed alongside the CSV)
TAGGED_ACCOUNT = {
    "source_reference": "4108",
    "account_code": "4108",
    "name": "Discount income on bills booked under loans (tagged 1b)",
    "account_class": "INCOME",
    "currency": "GHS",
    "balance": 5_000_000,
    "attributes": {"bsd7_line": "1b"},
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prepare(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


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


def _push(
    *,
    as_of: str,
    key: str,
    entities: dict[str, list[dict[str, Any]]] | None = None,
    references: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return ingest_push.push(
        base_url="http://testserver",
        token="test-token",
        bank=SAMPLE_BANK_ID,
        as_of=as_of,
        reason="Sample Bank onboarding (hermetic)",
        idempotency_key=key,
        entities=entities or {},
        references=references or {},
    )


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


def _statuses(snapshot: dict[str, Any], sheet: str) -> dict[str, str]:
    return {
        row["cell"]: row["status"]
        for section in snapshot["sections"]
        if section["title"] == sheet
        for row in section["rows"]
    }


def _num(cells: dict[str, Any], ref: str) -> Decimal:
    value = cells.get(ref)
    assert value is not None, ref
    return Decimal(str(value))


def _ledger_rows(as_of: str) -> list[dict[str, Any]]:
    return ingest_push.read_rows(ONBOARDING / f"gl_accounts_pl_{as_of}.csv", entity=True)


def _mapping_rows() -> list[dict[str, Any]]:
    return ingest_push.read_rows(MAPPING_CSV, entity=False)


class _Register:
    """The register's own precedence, re-derived independently for the assertions."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.codes = {r["gl_account_code"]: r for r in rows if r.get("gl_account_code")}
        self.prefixes = sorted(
            (r for r in rows if r.get("gl_prefix")), key=lambda r: -len(r["gl_prefix"])
        )

    def item_and_sign(self, code: str) -> tuple[str, Decimal] | None:
        row = self.codes.get(code)
        if row is None:
            row = next((r for r in self.prefixes if code.startswith(r["gl_prefix"])), None)
        if row is None:
            return None
        return row["bsd7_item"], Decimal(row.get("sign") or "1")


def _expected(item: str, as_of: str, currency: str) -> Decimal:
    """Σ signed YTD balance of the CSV's accounts the register maps to ``item``
    in one currency slice (GHS ⇒ Domestic, other ⇒ Foreign)."""
    register = _Register(_mapping_rows())
    total = Decimal(0)
    for row in _ledger_rows(as_of):
        mapped = register.item_and_sign(str(row["account_code"]))
        if mapped is None or mapped[0] != item:
            continue
        is_ghs = str(row.get("currency") or "GHS") == "GHS"
        if is_ghs != (currency == "GHS"):
            continue
        total += Decimal(str(row["balance"])) * mapped[1]
    return total


# ---------------------------------------------------------------------------
# 1. vocabulary ↔ line map; the register's own shape
# ---------------------------------------------------------------------------


def test_item_vocabulary_is_exactly_the_bsd7a_line_maps_tags() -> None:
    bound = {str(src.params["line"]) for src in PL_ROWS.values() if src.source == "bsd7.pl_line"}
    assert set(BSD7_ITEMS) == bound
    assert len(BSD7_ITEMS) == len(set(BSD7_ITEMS)) == 31  # noqa: PLR2004 — 31 P&L leaf items
    assert schema_for("gl_mapping_bsd7") is SCHEMA


def test_sample_bank_register_is_well_formed_and_covers_every_item() -> None:
    rows = _mapping_rows()
    assert rows and all(validate_mapping_row(row) == [] for row in rows), [
        (row, validate_mapping_row(row)) for row in rows if validate_mapping_row(row)
    ]
    assert {row["bsd7_item"] for row in rows} == set(BSD7_ITEMS)
    register = _Register(rows)
    # every Sample Bank P&L account resolves to an item; the contra row wins over its prefix
    for as_of in LEDGER_DATES:
        for account in _ledger_rows(as_of):
            assert account["account_class"] in ("INCOME", "EXPENSE")
            assert register.item_and_sign(str(account["account_code"])) is not None, account
    assert register.item_and_sign("4109") == ("1a", Decimal(-1))
    assert register.item_and_sign("4101") == ("1a", Decimal(1))
    assert register.item_and_sign("5301") == ("16", Decimal(1))  # exact beats the 530 prefix
    assert register.item_and_sign("5304") == ("18", Decimal(1))


# ---------------------------------------------------------------------------
# 2. push through the real API → BSD7A / BSD7B carry the ledger
# ---------------------------------------------------------------------------


def _push_ledger_and_register(push_client: TestClient) -> list[str]:
    _prepare(push_client)
    batch_ids: list[str] = []
    for as_of in LEDGER_DATES:
        rows = _ledger_rows(as_of)
        if as_of == REPORTING_DATE:
            rows = [*rows, TAGGED_ACCOUNT]
        result = _push(as_of=as_of, key=f"pl-{as_of}", entities={"gl_account": rows})
        batch = result["batch"]
        assert batch["status"] == "accepted", batch["validation_report"]["summary"]
        assert batch["records_accepted"] == len(rows)
        batch_ids.append(batch["id"])
    result = _push(
        as_of=REPORTING_DATE,
        key="gl-mapping-bsd7",
        references={"gl_mapping_bsd7": _mapping_rows()},
    )
    batch = result["batch"]
    assert batch["status"] == "accepted", batch["validation_report"]["summary"]
    assert batch["validation_report"]["summary"]["reference_rows"] == {
        "gl_mapping_bsd7": len(_mapping_rows())
    }
    batch_ids.append(batch["id"])
    return batch_ids


def test_pushed_ledger_and_register_land_with_lineage(push_client: TestClient) -> None:
    batch_ids = _push_ledger_and_register(push_client)
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        gl = session.scalars(
            select(CanonicalGlAccount).where(
                CanonicalGlAccount.bank_id == SAMPLE_BANK_ID,
                CanonicalGlAccount.account_class.in_(["INCOME", "EXPENSE"]),
                CanonicalGlAccount.superseded_by.is_(None),
            )
        ).all()
        # one generation per (account, month-end): 57 accounts × 4 dates + the tagged one
        assert len(gl) == 57 * 4 + 1
        assert {g.as_of_date.isoformat() for g in gl} == set(LEDGER_DATES)
        assert all(g.source_system == "API_PUSH" and g.lineage_id is not None for g in gl)
        assert {str(g.ingestion_batch_id) for g in gl} == set(batch_ids[:4])
        refs = session.scalars(
            select(CanonicalReferenceRow).where(
                CanonicalReferenceRow.bank_id == SAMPLE_BANK_ID,
                CanonicalReferenceRow.dataset_kind == "gl_mapping_bsd7",
            )
        ).all()
        assert len(refs) == len(_mapping_rows())
        assert {str(r.ingestion_batch_id) for r in refs} == {batch_ids[4]}
        assert all(r.lineage_id is not None for r in refs)
        # verbatim payload: the prefix survives as text (no numeric coercion)
        assert any(r.payload.get("gl_prefix") == "410" for r in refs)
    finally:
        session.close()


def test_bsd7a_and_bsd7b_carry_the_ledger_through_the_register(push_client: TestClient) -> None:
    _push_ledger_and_register(push_client)
    snapshot = _generate(push_client, "BSD7A", REPORTING_DATE)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    cells = payload["cells"]["BSD7A"]
    statuses = _statuses(snapshot, "BSD7A")

    # every P&L item is mapped in all four cells (previously input_required until tagged)
    for item, row in ROW_OF_ITEM.items():
        for col in ("C", "D", "F", "G"):
            assert statuses[f"{col}{row}"] == "mapped", (item, col, row)

    # period to date = March YTD, month = March − February, by currency slice
    for item, row in ROW_OF_ITEM.items():
        ptd_dom = _expected(item, "2026-03-31", "GHS")
        ptd_fx = _expected(item, "2026-03-31", "USD")
        feb_dom = _expected(item, "2026-02-28", "GHS")
        feb_fx = _expected(item, "2026-02-28", "USD")
        if item == "1b":
            # own tag beats the 410 prefix; the account first appears in March, so its
            # whole YTD balance is March's movement (opened in the month)
            ptd_dom += Decimal(TAGGED_ACCOUNT["balance"])
        assert abs(_num(cells, f"F{row}") - ptd_dom) < Decimal("0.01"), (item, "F")
        assert abs(_num(cells, f"G{row}") - ptd_fx) < Decimal("0.01"), (item, "G")
        assert abs(_num(cells, f"C{row}") - (ptd_dom - feb_dom)) < Decimal("0.01"), (item, "C")
        assert abs(_num(cells, f"D{row}") - (ptd_fx - feb_fx)) < Decimal("0.01"), (item, "D")

    # the contra account reduces 1(a) (sign −1) and never surfaces elsewhere
    contra = next(
        Decimal(str(r["balance"]))
        for r in _ledger_rows("2026-03-31")
        if str(r["account_code"]) == "4109"
    )
    gross_1a = sum(
        Decimal(str(r["balance"]))
        for r in _ledger_rows("2026-03-31")
        if str(r["account_code"]).startswith("410")
        and str(r["account_code"]) != "4109"
        and r.get("currency") == "GHS"
    )
    assert abs(_num(cells, "F6") - (gross_1a - contra)) < Decimal("0.01")
    # 1(b): the tagged 4108 landed in bills, not in loans (F7 includes it, F6 does not)
    assert _num(cells, "F7") == _expected("1b", "2026-03-31", "GHS") + Decimal(5_000_000)
    # the template's own arithmetic runs over the mapped inputs: NII = 1 − 2 in every column
    for dom, fx, tot in (("C", "D", "E"), ("F", "G", "H")):
        for col in (dom, fx, tot):
            assert abs(
                _num(cells, f"{col}16") - (_num(cells, f"{col}5") - _num(cells, f"{col}9"))
            ) < Decimal("0.01")
        assert abs(_num(cells, f"{tot}5") - (_num(cells, f"{dom}5") + _num(cells, f"{fx}5"))) < (
            Decimal("0.01")
        )
    # sanity of the Sample Bank figures: positive PTD net interest income, positive net profit
    assert _num(cells, "H16") > 0 and _num(cells, "H40") > 0

    # BSD7B: same register, period to date == BSD7A total; Q1 ⇒ quarter == PTD
    b = _generate(push_client, "BSD7B", REPORTING_DATE)
    assert not b["bog_form"]["errors"]
    b_cells = b["bog_form"]["cells"]["BSD7B"]
    b_status = _statuses(b, "BSD7B")
    for item, row in ROW_OF_ITEM.items():
        assert b_status[f"D{row + 2}"] == "mapped", item
        assert abs(_num(b_cells, f"D{row + 2}") - _num(cells, f"H{row}")) < Decimal("0.01"), item
        assert abs(_num(b_cells, f"C{row + 2}") - _num(b_cells, f"D{row + 2}")) < Decimal("0.01")


def test_without_the_register_untagged_ledger_lines_stay_input_required(
    push_client: TestClient,
) -> None:
    _prepare(push_client)
    rows = [r for r in _ledger_rows(REPORTING_DATE) if str(r["account_code"]) != "4108"]
    result = _push(as_of=REPORTING_DATE, key="pl-only", entities={"gl_account": rows})
    assert result["batch"]["status"] == "accepted"
    snapshot = _generate(push_client, "BSD7A", REPORTING_DATE)
    statuses = _statuses(snapshot, "BSD7A")
    # a ledger without tags and without the register names no line: honest blanks
    assert all(statuses[f"F{row}"] == "input_required" for row in ROW_OF_ITEM.values())


# ---------------------------------------------------------------------------
# 3. validation rejects malformed rows
# ---------------------------------------------------------------------------


def test_schema_reports_malformed_rows() -> None:
    assert validate_mapping_row({"gl_prefix": "410", "bsd7_item": "1a"}) == []
    assert validate_mapping_row({"gl_account_code": "4109", "bsd7_item": "1a", "sign": "-1"}) == []
    problems = validate_mapping_row({"gl_prefix": "410"})
    assert any("bsd7_item" in p for p in problems)
    problems = validate_mapping_row({"gl_prefix": "410", "bsd7_item": "3"})  # 3 is a template total
    assert any("bsd7_item" in p and "one of" in p for p in problems)
    problems = validate_mapping_row({"bsd7_item": "5"})
    assert any("gl_account_code" in p and "gl_prefix" in p for p in problems)
    both = {"gl_account_code": "4501", "gl_prefix": "45", "bsd7_item": "5"}
    problems = validate_mapping_row(both)
    assert any("not both" in p for p in problems)
    problems = validate_mapping_row({"gl_prefix": "45", "bsd7_item": "5", "sign": "2"})
    assert any("sign" in p for p in problems)
    monthly = {"gl_prefix": "45", "bsd7_item": "5", "balance_basis": "monthly"}
    problems = validate_mapping_row(monthly)
    assert any("balance_basis" in p for p in problems)
