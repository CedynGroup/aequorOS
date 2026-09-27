"""Compiled SQL snapshots for representative queries (SQLite dialect).

Each query below compiles to exactly the text in ``snapshots/<name>.sql``,
rendered against the SQLite dialect with every bound parameter shown as ``?``
(so no value — tenant id, date, filter — is in the snapshot, only the shape).
A change in emitted SQL fails the test; when the change is deliberate,
regenerate with ``BI_SNAPSHOT_UPDATE=1`` and review the diff like code.

The snapshot text is the SQLite rendering: subtotals appear as the UNION ALL
emulation, not ``ROLLUP`` (``tests/db/test_bi_query_execution.py`` proves the
Postgres form).
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue, catalogue
from app.models import Bank
from app.models.bi import BiDimDate, BiFactPositionDaily
from app.models.bi_content import BiMeasure
from app.schemas.bi import BiFilter, BiQuery
from app.services.bi.compiler import compile_query
from tests.api.helpers import ORG_1, USER_1

SNAPSHOTS = Path(__file__).parent / "snapshots"
AS_OF = date(2026, 9, 18)
RANGE = {"start": date(2026, 7, 1), "end": date(2026, 9, 30)}
PRIOR = date(2026, 6, 30)

#: name → (query payload, injected filters)
QUERIES: dict[str, tuple[dict[str, Any], list[BiFilter]]] = {
    "sum_as_of_by_region_aggregate": (
        {
            "measures": ["loans.balance_rc", "loans.count"],
            "dimensions": ["branch.region"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    "ratio_by_sector_fact": (
        {
            "measures": ["loans.npl_ratio_pct", "loans.npl_exposure_rc"],
            "dimensions": ["loan.sector"],
            "time": {"as_of": AS_OF},
            "sort": [{"member": "loans.npl_exposure_rc", "direction": "desc"}],
        },
        [],
    ),
    "weighted_average_rate": (
        {
            "measures": ["loans.weighted_average_rate"],
            "dimensions": ["product.code"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    "hhi_and_top_share_by_branch": (
        {
            "measures": ["loans.sector_hhi", "loans.balance_rc"],
            "dimensions": ["branch.code"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    "top_n_with_other": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["loan.sector"],
            "time": {"as_of": AS_OF},
            "top_n": {"dimension": "loan.sector", "n": 5},
        },
        [],
    ),
    "compare_to_as_of": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["branch.region"],
            "time": {"as_of": AS_OF, "compare_to": PRIOR},
        },
        [],
    ),
    "stock_range_by_calendar_month": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["time.calendar_month"],
            "time": {"range": RANGE},
        },
        [],
    ),
    "flow_range_events_by_type": (
        {
            "measures": ["events.amount_rc", "events.count"],
            "dimensions": ["event.type"],
            "time": {"range": RANGE},
        },
        [],
    ),
    "subtotals_region_then_branch": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["geography"],
            "time": {"as_of": AS_OF},
            "subtotals": True,
        },
        [],
    ),
    "pivot_by_sector": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["branch.code"],
            "time": {"as_of": AS_OF},
            "pivot": {"dimension": "loan.sector"},
        },
        [],
    ),
    "engine_last_value_by_month": (
        {
            "measures": ["engine.car_pct.crd.official"],
            "dimensions": ["time.calendar_month"],
            "time": {"range": RANGE},
        },
        [],
    ),
    # A5-01: sorting by a row dimension — the shape that used to crash.
    "sort_by_dimension_desc": (
        {
            "measures": ["loans.balance_rc"],
            "dimensions": ["branch.code"],
            "time": {"as_of": AS_OF},
            "sort": [{"member": "branch.code", "direction": "desc"}],
        },
        [],
    ),
    # D-042: the answerable count, and a denominator restricted to the rows
    # whose arrears are known.
    "par_90_pct_answerable": (
        {
            "measures": ["loans.par_90_pct", "loans.par_90_exposure_rc"],
            "dimensions": ["branch.code"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    # A bank-certified calculated measure. No period function, so it is plain
    # arithmetic over the query's own window: two aggregates and a NULL-safe divide.
    "calculated_ratio": (
        {
            "measures": ["custom.loans_to_deposits"],
            "dimensions": ["branch.region"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    # The same measure's period-comparing sibling (D-195): each figure is
    # aggregated once per period the formula declares, under that period's own
    # window predicate, in ONE grouped select — the period axis ``compare_to``
    # already uses, not a join of two grouped results.
    "calculated_month_on_month": (
        {
            "measures": ["loans.balance_rc", "custom.loan_growth"],
            "time": {"as_of": AS_OF},
        },
        [],
    ),
    "every_operator_with_injected_scope": (
        {
            "measures": ["loans.balance_rc"],
            "time": {"as_of": AS_OF},
            "filters": [
                {"member": "branch.code", "op": "eq", "values": ["B1"]},
                {"member": "branch.code", "op": "ne", "values": ["B9"]},
                {"member": "loan.grade", "op": "in", "values": ["standard", "olem"]},
                {"member": "loan.grade", "op": "not_in", "values": ["loss"]},
                {"member": "loan.ifrs9_stage", "op": "gt", "values": [0]},
                {"member": "loan.ifrs9_stage", "op": "gte", "values": [1]},
                {"member": "loan.ifrs9_stage", "op": "lt", "values": [4]},
                {"member": "loan.ifrs9_stage", "op": "lte", "values": [3]},
                {"member": "loan.ifrs9_stage", "op": "between", "values": [1, 3]},
                {"member": "counterparty.id", "op": "not_null", "values": []},
                {"member": "loan.employer", "op": "is_null", "values": []},
                {"member": "loan.sector", "op": "contains", "values": ["agri"]},
                {"member": "loan.non_performing", "op": "eq", "values": [False]},
            ],
        },
        [BiFilter(member="branch.region", op="in", values=["North", "South"])],
    ),
}


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


def _certified(bank: Bank, key: str, label: str, expression: str) -> BiMeasure:
    """A certified calculated measure, so the compiler's own arm has a snapshot.

    Written directly rather than through the promotion path: what the snapshot is
    about is the SQL a certified formula compiles to, and the promotion has its own
    tests. ``approved_expression`` is what the compiler reads.
    """
    digest = hashlib.sha256(expression.encode("utf-8")).hexdigest()
    return BiMeasure(
        organization_id=ORG_1,
        bank_id=bank.id,
        measure_key=key,
        owner_user_id=USER_1,
        label=label,
        description="",
        expression=expression,
        expression_digest=digest,
        referenced_members=[],
        value_type="fraction",
        favourable_direction="neutral",
        state="bank_certified",
        proposed_by_user_id=USER_1,
        proposed_at=datetime(2026, 9, 19, tzinfo=UTC),
        proposed_expression_digest=digest,
        proposal_reason="Snapshot fixture.",
        approved_by_user_id=UUID("22222222-2222-4222-8222-222222222222"),
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        approved_expression=expression,
        approved_expression_digest=digest,
        approval_reason="Snapshot fixture.",
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
        updated_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


@pytest.fixture
def bank(db_session: Session) -> Bank:
    """A bank with two sectors, so the pivot probe finds two columns."""
    bank = Bank(
        organization_id=ORG_1,
        name="Snapshot Bank",
        short_name="snap",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    for sector in ("agri", "trade"):
        db_session.add(
            BiFactPositionDaily(
                as_of_date=AS_OF,
                snapshot_id=uuid4(),
                position_id=uuid4(),
                organization_id=ORG_1,
                bank_id=bank.id,
                source_system="API_PUSH",
                source_reference=f"ref-{sector}",
                position_type="LOAN",
                currency="GHS",
                balance_native=Decimal("1"),
                balance_rc=Decimal("1"),
                fx_unconverted=False,
                classification_exposure_rc=Decimal("1"),
                sector=sector,
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    # The month ends a period comparison is read at (``is_last_in_month`` is the
    # last date WITH DATA in the month, D-014) and the two certified measures whose
    # compiled SQL is snapshotted.
    for day in (date(2026, 7, 31), date(2026, 8, 31), AS_OF):
        db_session.add(
            BiDimDate(
                organization_id=ORG_1,
                bank_id=bank.id,
                date=day,
                has_data=True,
                is_last_in_month=True,
                is_last_in_quarter=False,
                is_last_in_year=False,
                calendar_month=day.replace(day=1),
                calendar_quarter=date(day.year, 3 * ((day.month - 1) // 3) + 1, 1),
                calendar_year=day.year,
                fiscal_year=day.year,
                fiscal_quarter=(day.month - 1) // 3 + 1,
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    db_session.add_all(
        [
            _certified(
                bank,
                "custom.loans_to_deposits",
                "Loans to deposits",
                "SAFE_DIV([m:loans.balance_rc], [m:deposits.balance_rc])",
            ),
            _certified(
                bank,
                "custom.loan_growth",
                "Loan growth on the month",
                "PCT_CHANGE([m:loans.balance_rc], MONTH)",
            ),
        ]
    )
    db_session.flush()
    return bank


def _render(db: Session, cat: Catalogue, bank: Bank, name: str) -> str:
    if db.get_bind().dialect.name != "sqlite":
        pytest.skip("snapshots are the SQLite rendering (subtotals compile per dialect)")
    payload, injected = QUERIES[name]
    compiled = compile_query(
        db,
        cat,
        BiQuery.model_validate(payload),
        organization_id=ORG_1,
        bank_id=bank.id,
        injected_filters=injected,
    )
    statement, _ = compiled.with_row_cap(5_000)
    text = str(
        statement.compile(dialect=sqlite.dialect(), compile_kwargs={"render_postcompile": True})
    )
    # One clause per line, stable whitespace: the diff then reads like SQL.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip() + "\n"


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_compiled_sql_matches_snapshot(
    db_session: Session, cat: Catalogue, bank: Bank, name: str
) -> None:
    rendered = _render(db_session, cat, bank, name)
    path = SNAPSHOTS / f"{name}.sql"
    if os.environ.get("BI_SNAPSHOT_UPDATE") == "1":
        path.write_text(rendered, encoding="utf-8")
    assert path.exists(), (
        f"missing snapshot {path.name}; run with BI_SNAPSHOT_UPDATE=1 to create it"
    )
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"compiled SQL for {name} changed; if deliberate, regenerate with BI_SNAPSHOT_UPDATE=1"
    )


def test_snapshots_carry_no_values(db_session: Session, cat: Catalogue, bank: Bank) -> None:
    """Every bound value renders as ``?``: no tenant id, date or filter value in a snapshot."""
    for name in QUERIES:
        rendered = _render(db_session, cat, bank, name)
        assert ORG_1 not in rendered
        assert bank.id not in rendered
        assert "2026" not in rendered
        assert "North" not in rendered
        assert "agri" not in rendered


def test_every_snapshot_file_belongs_to_a_query() -> None:
    on_disk = {path.stem for path in SNAPSHOTS.glob("*.sql")}
    assert on_disk == set(QUERIES), "a snapshot file has no query (or a query has no snapshot)"
