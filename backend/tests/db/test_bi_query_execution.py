"""Postgres proof of the BI execution guards (S13) and the Postgres-only SQL shapes.

What SQLite cannot show (D-011): that ``statement_timeout`` really cancels a
statement and surfaces as ``BiQueryTimeout``; that both LOCAL settings hold
inside the guarded statement and are gone afterwards — and that a cancelled
statement leaves the enclosing session usable, because the savepoint is
rolled back; that a write attempted inside the guarded statement is refused
by the read-only transaction; that ``GROUP BY ROLLUP`` / ``grouping()``
compile and run; that the comparison compiles without a join and runs; that a
CALCULATED measure's period axis and its NULL-safe divide behave the same on real
Postgres numerics as they do on SQLite (D-195); and that a configured BI pool
carries the caller's tenant into the RLS GUC.

Opt-in with ``TEST_DATABASE_URL`` (a disposable Postgres — never the primary).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, literal, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.bi.catalogue import Catalogue, catalogue
from app.models import Bank
from app.models.bi import BiAggPositionDaily, BiDimBranch, BiDimDate, BiFactPositionDaily
from app.models.bi_content import BiMeasure
from app.schemas.bi import BiQuery
from app.services.bi import execution
from app.services.bi.compiler import compile_query
from app.services.bi.content import expression_digest
from app.services.bi.errors import BiQueryTimeout, InvalidQuery
from app.services.bi.execution import execute, run_select
from tests.support.helpers import ORG_1, USER_1

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres execution guards.",
)

AS_OF = date(2026, 9, 18)
PRIOR = date(2026, 8, 31)


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Guard Bank",
        short_name="guard",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    for as_of, branch, balance in (
        (AS_OF, "B1", "100"),
        (AS_OF, "B2", "250"),
        (PRIOR, "B1", "80"),
    ):
        db_session.add(
            BiFactPositionDaily(
                as_of_date=as_of,
                snapshot_id=uuid4(),
                position_id=uuid4(),
                organization_id=ORG_1,
                bank_id=bank.id,
                source_system="API_PUSH",
                source_reference=f"ref-{branch}-{as_of.isoformat()}",
                position_type="LOAN",
                currency="GHS",
                balance_native=Decimal(balance),
                balance_rc=Decimal(balance),
                fx_unconverted=False,
                classification_exposure_rc=Decimal(balance),
                branch_code=branch,
                sector="agri",
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    for branch, name in (("B1", "North"), ("B2", "South")):
        db_session.add(
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code=branch,
                name=name,
                region=name,
                mapped=True,
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    db_session.commit()
    return bank


def _compile(db: Session, cat: Catalogue, bank: Bank, **overrides: Any):  # noqa: ANN202
    payload: dict[str, Any] = {
        "measures": ["loans.balance_rc"],
        "dimensions": ["loan.sector", "branch.code"],
        "time": {"as_of": AS_OF},
    }
    payload.update(overrides)
    return compile_query(
        db, cat, BiQuery.model_validate(payload), organization_id=ORG_1, bank_id=bank.id
    )


def test_statement_timeout_cancels_and_leaves_the_session_usable(db_session: Session) -> None:
    slow = select(func.pg_sleep(5))
    with pytest.raises(BiQueryTimeout) as excinfo:
        run_select(db_session, slow, organization_id=ORG_1, timeout_ms=150)
    assert excinfo.value.status_code == 504
    assert "SELECT" not in str(excinfo.value)
    # The savepoint was rolled back: the enclosing transaction is not aborted.
    assert db_session.execute(select(literal(1))).scalar() == 1


def test_settings_hold_inside_the_statement_and_reset_afterwards(db_session: Session) -> None:
    inside = select(
        func.current_setting("statement_timeout"),
        func.current_setting("transaction_read_only"),
    )
    ((timeout, read_only),) = run_select(db_session, inside, organization_id=ORG_1, timeout_ms=4321)
    assert timeout.startswith("4321")
    assert read_only == "on"
    after = db_session.execute(inside).one()
    assert not after[0].startswith("4321")
    assert after[1] == "off"


def test_a_write_inside_the_guarded_statement_is_refused(db_session: Session, bank: Bank) -> None:
    rename = (
        update(BiDimBranch)
        .where(BiDimBranch.bank_id == bank.id, BiDimBranch.branch_code == "B1")
        .values(name="Renamed")
    )
    with pytest.raises(DBAPIError) as excinfo:
        execution._run(db_session, rename, timeout_ms=5_000)  # type: ignore[arg-type]
    assert getattr(excinfo.value.orig, "sqlstate", None) == "25006"  # read_only_sql_transaction
    assert not isinstance(excinfo.value, BiQueryTimeout)
    name = db_session.execute(
        select(BiDimBranch.name).where(
            BiDimBranch.bank_id == bank.id, BiDimBranch.branch_code == "B1"
        )
    ).scalar()
    assert name == "North"


def test_subtotals_compile_to_rollup_and_run(
    db_session: Session, cat: Catalogue, bank: Bank
) -> None:
    compiled = _compile(db_session, cat, bank, subtotals=True)
    sql = str(compiled.select.compile(dialect=db_session.get_bind().dialect))
    assert "ROLLUP" in sql
    assert "grouping(" in sql
    assert "UNION ALL" not in sql
    result = execute(db_session, compiled, timeout_ms=5_000, row_cap=100)
    ids = [column.id for column in result.columns]
    assert ids == ["loan.sector", "branch.code", "__level", "loans.balance_rc"]
    rows = [dict(zip(ids, row, strict=True)) for row in result.rows]
    levels = sorted(
        (
            (row["__level"], row["loan.sector"], row["branch.code"], row["loans.balance_rc"])
            for row in rows
        ),
        key=lambda row: (row[0], row[1] or "", row[2] or ""),
    )
    assert levels == [
        (0, None, None, Decimal("350")),
        (1, "agri", None, Decimal("350")),
        (2, "agri", "B1", Decimal("100")),
        (2, "agri", "B2", Decimal("250")),
    ]


def test_comparison_is_a_period_axis_and_runs(
    db_session: Session, cat: Catalogue, bank: Bank
) -> None:
    compiled = _compile(db_session, cat, bank, time={"as_of": AS_OF, "compare_to": PRIOR})
    sql = str(compiled.select.compile(dialect=db_session.get_bind().dialect))
    # A period axis, not a join: Postgres cannot FULL JOIN on IS NOT DISTINCT FROM.
    assert "JOIN (" not in sql
    assert "IS NOT DISTINCT FROM" not in sql
    result = execute(db_session, compiled, timeout_ms=5_000, row_cap=100)
    ids = [column.id for column in result.columns]
    by_branch = {
        row[ids.index("branch.code")]: dict(zip(ids, row, strict=True)) for row in result.rows
    }
    assert by_branch["B1"]["loans.balance_rc"] == Decimal("100")
    assert by_branch["B1"]["loans.balance_rc|prior"] == Decimal("80")
    assert by_branch["B1"]["loans.balance_rc|delta"] == Decimal("20")
    assert by_branch["B1"]["loans.balance_rc|delta_pct"] == pytest.approx(25.0)
    assert by_branch["B2"]["loans.balance_rc|prior"] is None
    assert by_branch["B2"]["loans.balance_rc|delta_pct"] is None


def test_a_stock_range_prunes_to_the_windows_partitions(
    db_session: Session, cat: Catalogue, bank: Bank
) -> None:
    """The window must reach the PLANNER, not only the subquery.

    A stock measure over a range resolves to "the last date with data per grain",
    which is a subquery. Postgres decides partition pruning at PLAN time and a
    subquery result is not known then, so `as_of_date IN (subquery)` alone makes a
    twelve-month question scan every month the mart holds — the benchmark measured
    60 partitions and 124,348 buffers for a twelve-month window.

    `_time_predicate` therefore ANDs a REDUNDANT static bound onto the same
    predicate. Redundant is the point: every date the subquery can return is a
    `max()` taken from inside this same window, so the answer cannot change, while
    the planner gains a constant it can prune on. Measured: 60 partitions to 12,
    4.3× less I/O, byte-identical rows.

    This asserts the bound is IN THE PLAN rather than merely in the SQL text, so
    dropping it or moving it somewhere the planner ignores fails here.
    """
    compiled = _compile(
        db_session,
        cat,
        bank,
        time={"range": {"start": date(AS_OF.year, 1, 1), "end": AS_OF}},
        dimensions=["time.calendar_month"],
    )
    sql = str(compiled.select.compile(dialect=db_session.get_bind().dialect))
    date_column = f"{BiAggPositionDaily.__tablename__}.as_of_date"
    subquery_at = sql.index(" IN (SELECT")
    outer_where = sql[:subquery_at]
    # The bound must be in the OUTER predicate, where the planner sees a constant.
    # Asserting only that "BETWEEN" appears somewhere would pass on the subquery's
    # own bound, which was always there and prunes nothing.
    assert f"{date_column} BETWEEN" in outer_where, (
        "the static window bound is not in the outer WHERE, so the planner has no "
        f"constant to prune on:\n{sql}"
    )
    # And it must still RUN and agree with the same question asked one date at a
    # time, which is what makes the redundancy safe rather than merely plausible.
    ranged = execute(db_session, compiled, timeout_ms=5_000, row_cap=100)
    single = execute(
        db_session,
        _compile(db_session, cat, bank, time={"as_of": AS_OF}, dimensions=["time.calendar_month"]),
        timeout_ms=5_000,
        row_cap=100,
    )
    assert [list(row) for row in ranged.rows] == [list(row) for row in single.rows], (
        "the bounded range answer differs from the single-date answer for the month "
        "the window ends in, so the bound is not redundant after all"
    )


def test_a_configured_bi_pool_carries_the_tenant_into_the_rls_guc(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``session.info["organization_id"]`` on the BI session fires the global
    ``after_begin`` listener, so RLS sees the caller's tenant on that pool too."""
    monkeypatch.setenv("BI_DATABASE_URL", os.environ["DATABASE_URL"])
    get_settings.cache_clear()
    try:
        ((tenant,),) = run_select(
            db_session,
            select(func.current_setting("app.organization_id", True)),
            organization_id=ORG_1,
            timeout_ms=5_000,
        )
    finally:
        get_settings.cache_clear()
    assert tenant == ORG_1


# --- calculated measures (D-195) -----------------------------------------------------------


@pytest.fixture
def calculated(db_session: Session, bank: Bank) -> Bank:
    """The two month ends a period comparison needs, and one certified formula.

    Kept out of the ``bank`` fixture so the other tests in this file keep the exact
    mart they were written against: a calendar row changes what a stock RANGE query
    can see, and the partition-pruning test above measures that.
    """

    for day in (PRIOR, AS_OF):
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
    # The aggregate table carries the same book, so the compiler's choice of source
    # cannot decide whether the figure exists. ``loan.sector`` is deliberately used
    # by one test below to force the FACT path and prove the two agree.
    for as_of, branch, balance in ((AS_OF, "B1", "100"), (AS_OF, "B2", "250"), (PRIOR, "B1", "80")):
        db_session.add(
            BiAggPositionDaily(
                as_of_date=as_of,
                id=uuid4(),
                organization_id=ORG_1,
                bank_id=bank.id,
                position_type="LOAN",
                branch_code=branch,
                currency="GHS",
                row_count=1,
                balance_rc_sum=Decimal(balance),
                classification_exposure_rc_sum=Decimal(balance),
                non_performing_exposure_rc_sum=Decimal("0"),
                provision_required_rc_sum=Decimal("0"),
                provision_held_rc_sum=Decimal("0"),
                collateral_rc_sum=Decimal("0"),
                rate_x_balance_rc_sum=Decimal("0"),
                fx_unconverted_count=0,
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    expression = "PCT_CHANGE([m:loans.balance_rc], MONTH)"
    # The REAL digest of the expression, not a placeholder. It was ``"0" * 64``,
    # which stopped being loadable when ``compiler._certified_measures`` began
    # recomputing the digest of ``approved_expression`` on read and refusing on
    # mismatch (audit A9-10 — the stored digest could previously be doctored
    # while both digest COLUMNS still agreed with each other). The production
    # behaviour is right and the fixture was wrong; the hermetic twin of this
    # test always computed a real digest, which is why only Postgres went red.
    digest = expression_digest(expression)
    db_session.add(
        BiMeasure(
            organization_id=ORG_1,
            bank_id=bank.id,
            measure_key="custom.loan_growth",
            owner_user_id=USER_1,
            label="Loan growth on the month",
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
            proposal_reason="Postgres guard.",
            approved_by_user_id=uuid4(),
            approved_at=datetime(2026, 9, 19, tzinfo=UTC),
            approved_expression=expression,
            approved_expression_digest=digest,
            approval_reason="Postgres guard.",
            created_at=datetime(2026, 9, 19, tzinfo=UTC),
            updated_at=datetime(2026, 9, 19, tzinfo=UTC),
        )
    )
    db_session.commit()
    return bank


def test_a_calculated_period_comparison_runs_on_postgres(
    db_session: Session, cat: Catalogue, calculated: Bank
) -> None:
    """September's book is 100 + 250 = 350; August's is 80. 270 on 80 is 3.375.

    The whole point of running it here: the period axis is two aggregates over one
    scan with a ``max(date)`` subquery per period, and Postgres numerics, NULL
    handling and the ``nullif`` divide are what the figure a bank reads comes out
    of. SQLite agreeing is not evidence about Postgres.
    """

    compiled = compile_query(
        db_session,
        cat,
        BiQuery.model_validate({"measures": ["custom.loan_growth"], "time": {"as_of": AS_OF}}),
        organization_id=ORG_1,
        bank_id=calculated.id,
    )
    assert [column.id for column in compiled.columns] == ["custom.loan_growth"]
    assert compiled.columns[0].format == "fraction"
    assert compiled.used_aggregate
    result = execute(db_session, compiled, timeout_ms=5_000, row_cap=100)
    assert result.rows[0][0] == pytest.approx(3.375)

    # The same question off the FACT table (``loan.sector`` is not in the
    # aggregate's grain), which must give the same figure: "both sources answer
    # alike" is the one invariant aggregate selection rests on.
    on_fact = compile_query(
        db_session,
        cat,
        BiQuery.model_validate(
            {
                "measures": ["custom.loan_growth"],
                "dimensions": ["loan.sector"],
                "time": {"as_of": AS_OF},
            }
        ),
        organization_id=ORG_1,
        bank_id=calculated.id,
    )
    assert not on_fact.used_aggregate
    fact_rows = execute(db_session, on_fact, timeout_ms=5_000, row_cap=100).rows
    assert [(row[0], row[1]) for row in fact_rows] == [("agri", pytest.approx(3.375))]


def test_a_calculated_measure_survives_rollup_on_postgres(
    db_session: Session, cat: Catalogue, calculated: Bank
) -> None:
    """``GROUP BY ROLLUP`` with a formula in the select list: the subtotal row is
    the formula over the subtotal's own aggregates, not a sum of the leaf figures."""

    compiled = compile_query(
        db_session,
        cat,
        BiQuery.model_validate(
            {
                "measures": ["custom.loan_growth"],
                "dimensions": ["branch.code"],
                "time": {"as_of": AS_OF},
                "subtotals": True,
            }
        ),
        organization_id=ORG_1,
        bank_id=calculated.id,
    )
    sql = str(compiled.select.compile(dialect=db_session.get_bind().dialect))
    assert "ROLLUP" in sql
    result = execute(db_session, compiled, timeout_ms=5_000, row_cap=100)
    ids = [column.id for column in result.columns]
    rows = [dict(zip(ids, row, strict=True)) for row in result.rows]
    total = next(row for row in rows if row["__level"] == 0)
    # 350 against 80 for the institution as a whole.
    assert total["custom.loan_growth"] == pytest.approx(3.375)
    # B1 alone: 100 against 80. B2 has no August book, so its change is no value.
    by_branch = {row["branch.code"]: row for row in rows if row["__level"] == 1}
    assert by_branch["B1"]["custom.loan_growth"] == pytest.approx(0.25)
    assert by_branch["B2"]["custom.loan_growth"] is None


def test_a_reporting_date_that_ends_no_period_is_refused_on_postgres(
    db_session: Session, cat: Catalogue, calculated: Bank
) -> None:
    """The period-end probe is a real query against ``bi_dim_date`` (D-195)."""

    with pytest.raises(InvalidQuery) as caught:
        compile_query(
            db_session,
            cat,
            BiQuery.model_validate(
                {"measures": ["custom.loan_growth"], "time": {"as_of": date(2026, 9, 17)}}
            ),
            organization_id=ORG_1,
            bank_id=calculated.id,
        )
    assert "close of a month" in caught.value.message
    assert PRIOR.isoformat() in caught.value.message
