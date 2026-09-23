"""Postgres proof of the BI execution guards (S13) and the Postgres-only SQL shapes.

What SQLite cannot show (D-011): that ``statement_timeout`` really cancels a
statement and surfaces as ``BiQueryTimeout``; that both LOCAL settings hold
inside the guarded statement and are gone afterwards — and that a cancelled
statement leaves the enclosing session usable, because the savepoint is
rolled back; that a write attempted inside the guarded statement is refused
by the read-only transaction; that ``GROUP BY ROLLUP`` / ``grouping()``
compile and run; that the comparison compiles without a join and runs; and
that a configured BI pool carries the caller's tenant into the RLS GUC.

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
from app.models.bi import BiDimBranch, BiFactPositionDaily
from app.schemas.bi import BiQuery
from app.services.bi import execution
from app.services.bi.compiler import compile_query
from app.services.bi.errors import BiQueryTimeout
from app.services.bi.execution import execute, run_select
from tests.api.helpers import ORG_1

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
