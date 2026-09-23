"""Run a compiled BI statement under the server-side guards (S13).

Three things happen here and nowhere else in the BI plane:

* **Session choice.** When ``BI_DATABASE_URL`` is set the statement runs on
  the BI pool (``get_bi_sessionmaker``), on a session whose
  ``info["organization_id"]`` is the CALLER's tenant so the global
  ``after_begin`` listener sets the RLS GUC exactly as it does for a request
  session. Unset, the request's own session is used (the hermetic default).
* **Postgres guards.** The statement runs inside a SAVEPOINT with a
  transaction-local ``statement_timeout`` and ``transaction_read_only``,
  both set through ``set_config(..., is_local => true)``. The savepoint is
  ALWAYS rolled back afterwards: that is what resets both settings (``SET
  LOCAL`` survives a RELEASE, not a ROLLBACK TO), and — when the server
  cancels the statement — what returns the enclosing request transaction from
  its aborted state so the query log can still be written. On SQLite neither
  setting exists; the statement simply runs, and ``tests/db`` is where the
  Postgres behaviour is proven (D-011: never mocked as passing on SQLite).
* **Caps.** The executor asks for ``limit + 1`` rows and reports
  ``truncated`` when the extra one arrives; the request's ``limit`` can only
  lower the surface's ``row_cap``, never raise it.

**The two literal statements below are the ONLY SQL text in
``app/services/bi`` and ``app/domain/bi``.** ``tests/architecture/
test_bi_compiler_injection.py`` forbids ``text()``, ``literal_column()``,
``exec_driver_sql()`` and any string-built SQL across both packages and
allow-lists exactly these two constants by name: they are compile-time
constants with no interpolation, and their one variable — the timeout —
travels as a driver-bound parameter. ``SET`` itself cannot take a bound
parameter (it is a utility statement), which is why ``set_config`` is used.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select
from sqlalchemy.engine import Row
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.session import get_bi_sessionmaker
from app.services.bi.errors import BiQueryTimeout

if TYPE_CHECKING:
    from app.services.bi.compiler import ColumnSpec, CompiledQuery

__all__ = ["QueryResult", "execute", "run_select"]

#: Transaction-local statement timeout; the value is a driver-bound parameter.
_SET_STATEMENT_TIMEOUT_SQL = "SELECT set_config('statement_timeout', %(ms)s, true)"
#: Transaction-local read-only mode; nothing a BI statement does may write.
_SET_READ_ONLY_SQL = "SELECT set_config('transaction_read_only', 'on', true)"

#: SQLSTATE 57014 ``query_canceled`` — what ``statement_timeout`` raises.
_QUERY_CANCELED = "57014"


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[ColumnSpec, ...]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_ms: int
    used_aggregate: bool


def _is_cancelled(exc: DBAPIError) -> bool:
    origin = exc.orig
    code = getattr(origin, "sqlstate", None) or getattr(origin, "pgcode", None)
    return code == _QUERY_CANCELED


def _run(session: Session, statement: Select[Any], *, timeout_ms: int) -> Sequence[Row[Any]]:
    if session.get_bind().dialect.name != "postgresql":
        # SQLite: no statement_timeout, no read-only transactions (D-011).
        return session.execute(statement).all()
    # Order matters: the Session emits SAVEPOINT lazily, on the first
    # connection use AFTER begin_nested(). Asking for the connection here is
    # what starts the savepoint, so the two settings land INSIDE it and the
    # rollback below can revert them; asked for earlier, the connection is
    # already bound to the outer transaction, the settings escape the
    # savepoint and outlive the query (proven in tests/db).
    nested = session.begin_nested()
    try:
        connection = session.connection()
        connection.exec_driver_sql(_SET_STATEMENT_TIMEOUT_SQL, {"ms": str(timeout_ms)})
        connection.exec_driver_sql(_SET_READ_ONLY_SQL)
        rows = session.execute(statement).all()
    except DBAPIError as exc:
        nested.rollback()
        if _is_cancelled(exc):
            raise BiQueryTimeout(timeout_ms) from None
        raise
    else:
        # Rolling back the savepoint is what unsets the two LOCAL settings.
        nested.rollback()
        return rows


def run_select(
    db: Session, statement: Select[Any], *, organization_id: str, timeout_ms: int
) -> Sequence[Row[Any]]:
    """Run one read statement under the guards, on the BI session when configured."""
    maker = get_bi_sessionmaker()
    if maker is None:
        return _run(db, statement, timeout_ms=timeout_ms)
    with maker() as bi_session:
        bi_session.info["organization_id"] = organization_id
        return _run(bi_session, statement, timeout_ms=timeout_ms)


def execute(db: Session, compiled: CompiledQuery, *, timeout_ms: int, row_cap: int) -> QueryResult:
    """Run ``compiled`` and return at most ``row_cap`` rows plus a truncation flag."""
    if timeout_ms < 1:
        raise ValueError("timeout_ms must be positive")
    statement, limit = compiled.with_row_cap(row_cap)
    started = time.perf_counter()
    rows = run_select(
        db, statement, organization_id=compiled.organization_id, timeout_ms=timeout_ms
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    truncated = len(rows) > limit
    return QueryResult(
        columns=compiled.columns,
        rows=[tuple(row) for row in rows[:limit]],
        truncated=truncated,
        elapsed_ms=elapsed_ms,
        used_aggregate=compiled.used_aggregate,
    )
