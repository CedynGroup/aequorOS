"""EOD position ladder → BSD1 daily columns (data-gap closure, 2026-08-16).

BSD1's grid needs one position rung per day of the reporting week. The Data
Engine's ladder path is a nightly push of the same positions with that day's
``as_of_date``: this suite proves, through the REAL push API and the REAL
package pipeline, that

1. re-pushing a ``source_reference`` with a DIFFERENT ``as_of_date`` ADDS a
   snapshot rung (the position row is reused; the reporting-date rung is
   untouched — snapshot supersession is scoped to the same as-of);
2. BSD1 generated via ``POST /regulatory-packages`` fills exactly the day
   columns that have their own rung — from that rung — while the days without
   one stay ``input_required`` (never a copied balance), and rows whose
   population is NOT on the rung (deposits, when only the liquid-asset book was
   pushed) stay ``input_required`` instead of reading a fabricated ``0``;
3. the Sample Bank ladder files (``onboarding/sample_bank/eod_ladder/``) are
   seven consistent daily extracts of the same liquid-asset book (one file per
   calendar day of the week ending on the reporting date; positions keep their
   ``source_reference`` across days; securities accrete daily; a placement
   appears only from its value date).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import get_sessionmaker
from app.models.canonical import CanonicalPosition, CanonicalPositionSnapshot
from scripts.ingest_push import read_rows
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
LADDER_DIR = Path(__file__).resolve().parents[3] / "onboarding" / "sample_bank" / "eod_ladder"
SHEET = "BSD1 "
#: BSD1 day columns THU…WED = reporting date − 6 … reporting date
DAYS = ("B", "C", "D", "E", "F", "G", "H")
COUNTERPARTIES = [
    {
        "source_reference": "CP/BOG",
        "name": "Bank of Ghana",
        "counterparty_type": "CENTRAL_BANK",
        "resident": True,
    },
    {
        "source_reference": "CP/GOG",
        "name": "Government of Ghana",
        "counterparty_type": "SOVEREIGN",
        "resident": True,
    },
]
VAULT = Decimal("40000000")
BOG = Decimal("180000000")
TBILL = Decimal("150000000")
# per-rung movement (cedis) applied on top of the base values by rung index
STEP = Decimal("1000000")


def _prepare(db_client: TestClient) -> str:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    periods = db_client.get(f"{BASE}/reporting-periods", headers=headers()).json()["periods"]
    return max(p["period_end"] for p in periods)


def _positions(step: int) -> list[dict[str, Any]]:
    """The liquid-asset book for one rung: vault cash, BoG current account, a
    91-day GoG bill — the same source references every day, that day's balances."""
    bump = STEP * step
    return [
        {
            "source_reference": "CASH/VAULT",
            "position_type": "CASH",
            "currency": "GHS",
            "balance": str(VAULT + bump),
        },
        {
            "source_reference": "CASH/BOG-CURRENT",
            "position_type": "CASH",
            "currency": "GHS",
            "balance": str(BOG + bump),
            "counterparty_reference": "CP/BOG",
        },
        {
            "source_reference": "SEC/GOG-TB91",
            "position_type": "SECURITY_HOLDING",
            "currency": "GHS",
            "balance": str(TBILL + bump),
            "counterparty_reference": "CP/GOG",
            "attributes": {"instrument": "tbill", "tenor_days": 91},
        },
    ]


def _push(
    db_client: TestClient, key: str, as_of: str, entities: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    opened = db_client.post(
        f"{BASE}/push-batches",
        headers=headers(),
        json={"as_of_date": as_of, "idempotency_key": key, "reason": f"EOD ladder {as_of}"},
    )
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = db_client.post(
        f"{BASE}/push-batches/{push_id}/records", headers=headers(), json={"entities": entities}
    )
    assert staged.status_code == 200, staged.text
    committed = db_client.post(f"{BASE}/push-batches/{push_id}/commit", headers=headers())
    assert committed.status_code == 201, committed.text
    batch = committed.json()["batch"]
    assert batch["status"] in ("accepted", "accepted_with_warnings"), batch["validation_report"]
    return batch


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"{BASE}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD1", "reporting_date": reporting_date},
    )
    assert response.status_code == 201, f"{response.status_code} {response.text[:300]}"
    package = response.json()
    detail = db_client.get(f"{BASE}/regulatory-packages/{package['id']}", headers=headers()).json()
    return detail["snapshot"]


def _lines(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    for section in snapshot["sections"]:
        if section["title"] == SHEET:
            return {row["cell"]: row for row in section["rows"]}
    raise AssertionError("no BSD1 sheet section")


def _rungs() -> dict[str, dict[date, Decimal]]:
    """{source_reference: {as_of_date: balance}} over the CURRENT snapshot generation."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        rows = session.execute(
            select(
                CanonicalPosition.source_reference,
                CanonicalPositionSnapshot.as_of_date,
                CanonicalPositionSnapshot.balance,
            )
            .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
            .where(
                CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
                CanonicalPositionSnapshot.superseded_by.is_(None),
                CanonicalPosition.superseded_by.is_(None),
            )
        ).all()
        positions = session.scalar(
            select(func.count())
            .select_from(CanonicalPosition)
            .where(
                CanonicalPosition.bank_id == SAMPLE_BANK_ID,
                CanonicalPosition.superseded_by.is_(None),
            )
        )
    finally:
        session.close()
    ladder: dict[str, dict[date, Decimal]] = {}
    for ref, day, balance in rows:
        ladder.setdefault(ref, {})[day] = Decimal(str(balance))
    assert positions == len(ladder)  # one position row per source reference, reused per rung
    return ladder


# ---------------------------------------------------------------------------
# 1 + 2. dated pushes add rungs; BSD1 fills exactly those days
# ---------------------------------------------------------------------------


def test_dated_pushes_add_rungs_and_bsd1_fills_only_those_days(  # noqa: PLR0915 — one journey
    db_client: TestClient,
) -> None:
    reporting_date = _prepare(db_client)
    end = date.fromisoformat(reporting_date)
    # three rungs: the reporting date (H), the day before (G), three days before (E)
    rung_days = {"H": end, "G": end - timedelta(days=1), "E": end - timedelta(days=3)}
    steps = {"E": 0, "G": 1, "H": 2}
    first = True
    for column in ("E", "G", "H"):
        day = rung_days[column]
        entities: dict[str, list[dict[str, Any]]] = {"position": _positions(steps[column])}
        if first:
            entities["counterparty"] = COUNTERPARTIES
            first = False
        _push(db_client, f"ladder-{day.isoformat()}", day.isoformat(), entities)

    ladder = _rungs()
    for ref in ("CASH/VAULT", "CASH/BOG-CURRENT", "SEC/GOG-TB91"):
        assert set(ladder[ref]) == set(rung_days.values()), ref  # one rung per pushed day, kept
    assert ladder["CASH/VAULT"][end] == VAULT + STEP * 2
    assert ladder["CASH/VAULT"][end - timedelta(days=3)] == VAULT  # the earlier rung is intact

    snapshot = _generate(db_client, reporting_date)
    payload = snapshot["bog_form"]
    assert not payload["errors"], payload["errors"]
    lines = _lines(snapshot)
    cells = payload["cells"][SHEET]

    for column, step in steps.items():
        bump = STEP * step
        assert lines[f"{column}32"]["status"] == "mapped", column  # 17. cash on hand
        assert Decimal(str(cells[f"{column}32"])) == VAULT + bump
        assert Decimal(str(cells[f"{column}33"])) == BOG + bump  # 18. BoG current account
        assert Decimal(str(cells[f"{column}53"])) == TBILL + bump  # 25(a) GoG 91-day bill
        # BoG's own arithmetic over the rung: primary total = cash + BoG current account
        assert Decimal(str(cells[f"{column}36"])) == VAULT + BOG + 2 * bump
    # the reporting-date column reads the ladder, not the month-end fact spine
    assert Decimal(str(cells["H32"])) == VAULT + STEP * 2
    # days without a rung: unknown — blank, never a neighbour's balance
    for column in ("B", "C", "D", "F"):
        for row in (32, 33, 53):
            assert lines[f"{column}{row}"]["status"] == "input_required", f"{column}{row}"
            assert cells.get(f"{column}{row}") is None
    # a population NOT on the rung (deposits) stays input_required on the rung days too —
    # a nightly liquid-asset push must never zero the deposit book …
    for column in ("E", "G"):
        assert lines[f"{column}94"]["status"] == "input_required", column
        assert cells.get(f"{column}94") is None
    # … while the reporting date still falls back to the period-end deposit facts
    assert lines["H94"]["status"] == "mapped"
    assert Decimal(str(cells["H94"])) > 0
    # TOTAL and AVERAGE are the template's own: Σ the filled days, / 7
    week_total = sum((VAULT + STEP * s for s in steps.values()), Decimal(0))
    assert Decimal(str(cells["I32"])) == week_total
    assert abs(Decimal(str(cells["J32"])) - week_total / 7) < Decimal("1e-6")


# ---------------------------------------------------------------------------
# 3. the Sample Bank ladder files
# ---------------------------------------------------------------------------


def test_sample_bank_ladder_is_seven_consistent_daily_extracts() -> None:
    # the directory also holds the header-only onboarding template, which is not
    # a dated extract — the ladder is exactly the positions_<ISO date>.csv files
    files = sorted(
        path for path in LADDER_DIR.glob("positions_*.csv") if path.stem != "positions_template"
    )
    days = [date.fromisoformat(f.stem.split("_", 1)[1]) for f in files]
    assert len(days) == 7  # noqa: PLR2004 — every calendar day of the week ending on the date
    assert days == [days[-1] - timedelta(days=6 - i) for i in range(7)]
    assert days[-1] == date(2026, 6, 30)  # the Sample Bank's latest reporting date
    books = {day: read_rows(path, entity=True) for day, path in zip(days, files, strict=True)}
    by_ref: dict[str, dict[date, dict[str, Any]]] = {}
    for day, rows in books.items():
        assert rows, day
        refs = [r["source_reference"] for r in rows]
        assert len(set(refs)) == len(refs), f"duplicate source_reference on {day}"
        for row in rows:
            assert row["position_type"] in ("SECURITY_HOLDING", "INTERBANK_PLACEMENT")
            assert row["currency"] in ("GHS", "USD", "EUR", "GBP")
            assert Decimal(str(row["balance"])) > 0
            assert Decimal(str(row["attributes"]["balance_ghs"])) > 0
            if row["currency"] == "GHS":
                assert Decimal(str(row["attributes"]["balance_ghs"])) == Decimal(
                    str(row["balance"])
                )
            assert row["product_code"] and row["gl_account_code"]
            assert row["counterparty_reference"]
            by_ref.setdefault(row["source_reference"], {})[day] = row
    # every position on the reporting-date file is on every day from its value date on
    last = books[days[-1]]
    assert len(last) > 1000  # noqa: PLR2004 — the API-push liquid-asset book
    for row in last:
        value_date = date.fromisoformat(row["origination_date"])
        expected_days = [d for d in days if d >= value_date]
        assert sorted(by_ref[row["source_reference"]]) == expected_days, row["source_reference"]
    # securities accrete daily (Guide BSD1 24–25: cost + discount/interest earned to date)
    securities = [r for r in last if r["position_type"] == "SECURITY_HOLDING"]
    assert securities
    for row in securities[:50]:
        series = [
            Decimal(str(by_ref[row["source_reference"]][d]["balance"]))
            for d in days
            if d in by_ref[row["source_reference"]]
        ]
        assert series == sorted(series), row["source_reference"]
        assert series[-1] == Decimal(str(row["balance"]))
    # a placement appears only from its value date
    placements = [r for r in last if r["position_type"] == "INTERBANK_PLACEMENT"]
    late = [r for r in placements if date.fromisoformat(r["origination_date"]) > days[0]]
    assert late, "the sample week should include a placement made mid-week"
    for row in late:
        assert days[0] not in by_ref[row["source_reference"]]
    # every file states its own business date in the provenance comment
    for day, path in zip(days, files, strict=True):
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#") and f"as_of_date={day.isoformat()}" in first_line
    readme = (LADDER_DIR / "README.md").read_text(encoding="utf-8")
    assert "as_of_date" in readme and "BSD1" in readme
    assert (LADDER_DIR / "positions_template.csv").read_text(encoding="utf-8").startswith(
        "source_reference,position_type,currency,balance"
    )


@pytest.mark.parametrize("day", ["2026-06-24", "2026-06-30"])
def test_sample_bank_ladder_rows_pass_the_push_contract_shape(day: str) -> None:
    rows = read_rows(LADDER_DIR / f"positions_{day}.csv", entity=True)
    for row in rows:
        assert set(row) >= {"source_reference", "position_type", "currency", "balance"}
        assert isinstance(row["balance"], (int, float))
        assert isinstance(row["attributes"], dict)
        assert date.fromisoformat(row["contractual_maturity"]) >= date.fromisoformat(day)
