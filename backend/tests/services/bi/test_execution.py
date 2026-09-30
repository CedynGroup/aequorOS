"""The execution wrapper on SQLite: caps, truncation, paging, session choice.

The Postgres-only guards (``statement_timeout``, read-only transaction, the
savepoint that resets them) are proven in ``tests/db/test_bi_query_execution.py``
against a real server (D-011); here the SQLite path is shown to skip them
rather than pretend, and the error mapping is exercised on a synthetic
driver error.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.bi.catalogue import Catalogue, catalogue
from app.models import Bank
from app.models.bi import BiFactPositionDaily
from app.schemas.bi import BiQuery
from app.services.bi import execution
from app.services.bi.compiler import CompiledQuery, compile_query
from app.services.bi.errors import BiQueryError, BiQueryTimeout
from app.services.bi.execution import QueryResult, execute
from tests.api.helpers import ORG_1

AS_OF = date(2026, 9, 18)


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Exec Bank",
        short_name="exec",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    for sector, balance in (("S1", "100"), ("S2", "200"), ("S3", "300")):
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
                balance_native=Decimal(balance),
                balance_rc=Decimal(balance),
                fx_unconverted=False,
                classification_exposure_rc=Decimal(balance),
                sector=sector,
                builder_version=1,
                built_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        )
    db_session.flush()
    return bank


def _by_sector(db: Session, cat: Catalogue, bank: Bank, **overrides: Any) -> CompiledQuery:
    payload: dict[str, Any] = {
        "measures": ["loans.balance_rc"],
        "dimensions": ["loan.sector"],
        "time": {"as_of": AS_OF},
    }
    payload.update(overrides)
    query = BiQuery.model_validate(payload)
    return compile_query(db, cat, query, organization_id=ORG_1, bank_id=bank.id)


def test_row_cap_truncates_and_says_so(db_session: Session, cat: Catalogue, bank: Bank) -> None:
    compiled = _by_sector(db_session, cat, bank)
    capped = execute(db_session, compiled, timeout_ms=1_000, row_cap=2)
    assert isinstance(capped, QueryResult)
    assert [row[0] for row in capped.rows] == ["S1", "S2"]
    assert capped.truncated is True
    exact = execute(db_session, compiled, timeout_ms=1_000, row_cap=3)
    assert len(exact.rows) == 3
    assert exact.truncated is False


def test_the_client_limit_only_lowers_the_cap(
    db_session: Session, cat: Catalogue, bank: Bank
) -> None:
    lowered = execute(
        db_session, _by_sector(db_session, cat, bank, limit=1), timeout_ms=1_000, row_cap=100
    )
    assert len(lowered.rows) == 1
    assert lowered.truncated is True
    raised = execute(
        db_session, _by_sector(db_session, cat, bank, limit=999_999), timeout_ms=1_000, row_cap=2
    )
    assert len(raised.rows) == 2
    assert raised.truncated is True


def test_offset_pages_through_the_result(db_session: Session, cat: Catalogue, bank: Bank) -> None:
    page = execute(
        db_session,
        _by_sector(db_session, cat, bank, limit=2, offset=2),
        timeout_ms=1_000,
        row_cap=100,
    )
    assert [row[0] for row in page.rows] == ["S3"]
    assert page.truncated is False


def test_result_carries_columns_timing_and_source(
    db_session: Session, cat: Catalogue, bank: Bank
) -> None:
    compiled = _by_sector(db_session, cat, bank)
    result = execute(db_session, compiled, timeout_ms=1_000, row_cap=100)
    assert result.columns == compiled.columns
    assert result.used_aggregate is compiled.used_aggregate
    assert isinstance(result.elapsed_ms, int)
    assert result.elapsed_ms >= 0
    assert all(isinstance(row, tuple) for row in result.rows)


def test_timeout_and_cap_must_be_positive(db_session: Session, cat: Catalogue, bank: Bank) -> None:
    compiled = _by_sector(db_session, cat, bank)
    with pytest.raises(ValueError, match="timeout_ms"):
        execute(db_session, compiled, timeout_ms=0, row_cap=10)
    with pytest.raises(ValueError, match="row_cap"):
        execute(db_session, compiled, timeout_ms=10, row_cap=0)


def test_sqlite_skips_the_postgres_guards(
    db_session: Session, cat: Catalogue, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``set_config`` on SQLite: the statement simply runs (D-011)."""
    if db_session.get_bind().dialect.name != "sqlite":
        pytest.skip("the SQLite path; tests/db proves the Postgres guards")
    issued: list[str] = []
    connection = db_session.connection()
    original = connection.exec_driver_sql

    def _spy(statement: str, *args: Any, **kwargs: Any) -> Any:
        issued.append(statement)
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(connection, "exec_driver_sql", _spy)
    execute(db_session, _by_sector(db_session, cat, bank), timeout_ms=1_000, row_cap=10)
    assert issued == []


def test_bi_session_is_used_when_configured_and_carries_the_tenant(
    db_session: Session, cat: Catalogue, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a BI pool configured, the statement runs on a session whose
    ``info`` names the CALLER's organization, so the RLS listener fires."""
    seen: list[dict[str, Any]] = []

    class _Recording(Session):
        def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            seen.append(dict(self.info))
            return super().execute(statement, *args, **kwargs)

    maker = sessionmaker(
        bind=db_session.connection(), class_=_Recording, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(execution, "get_bi_sessionmaker", lambda: maker)
    result = execute(db_session, _by_sector(db_session, cat, bank), timeout_ms=1_000, row_cap=10)
    assert len(result.rows) == 3
    assert seen == [{"organization_id": ORG_1}]


def test_the_request_session_is_used_when_no_bi_pool_is_configured(
    db_session: Session, cat: Catalogue, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    original = db_session.execute

    def _spy(statement: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(statement)
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(execution, "get_bi_sessionmaker", lambda: None)
    monkeypatch.setattr(db_session, "execute", _spy)
    execute(db_session, _by_sector(db_session, cat, bank), timeout_ms=1_000, row_cap=10)
    assert len(calls) == 1


class _Cancelled(Exception):
    sqlstate = "57014"


class _Other(Exception):
    sqlstate = "42P01"


class _Legacy(Exception):
    pgcode = "57014"


@pytest.mark.parametrize(
    ("origin", "cancelled"),
    [(_Cancelled(), True), (_Other(), False), (_Legacy(), True), (Exception("plain"), False)],
)
def test_only_query_canceled_maps_to_the_timeout(origin: Exception, cancelled: bool) -> None:
    error = DBAPIError("SELECT 1", {}, origin)
    assert execution._is_cancelled(error) is cancelled


def test_the_timeout_error_is_504_shaped_and_names_no_sql() -> None:
    error = BiQueryTimeout(10_000)
    assert isinstance(error, BiQueryError)
    assert error.status_code == 504
    assert error.code == "bi_query_timeout"
    assert "10000" in str(error)
    assert "SELECT" not in str(error)
    assert error.timeout_ms == 10_000
