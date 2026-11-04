"""S12: nothing a BI client sends can become SQL text.

Two guards, one static and one generative.

The static guard walks the AST of every module under ``app/bi/service`` and
``app/bi/domain`` and forbids every construct through which a string can reach
the database as SQL. Stated exactly, because a guard's docstring that promises
more than its rules cover is how the uncovered shape gets written (audit
A360-1):

* **the constructors and escape hatches, by name** — ``text()``,
  ``literal_column()``, the lightweight ``column()`` / ``table()``,
  ``textual()`` / ``TextClause``, ``exec_driver_sql()``, ``raw_connection()``,
  ``from_statement()`` — however they are called;
* **the aliased import of any of them** (``from sqlalchemy import text as
  sql_text``), so renaming the name is not an evasion;
* **reaching them by a computed name** — ``getattr(<any SQLAlchemy name>, …)``
  whatever the second argument, ``getattr(x, "te" + "xt")`` whatever the first,
  and ``importlib.import_module`` / ``__import__`` anywhere in the tree;
* **``execute()`` / ``scalar()`` / ``scalars()`` with anything but an
  expression** — a literal, an f-string, a concatenation, or a NAME whose
  nearest preceding assignment in the same scope is one of those
  (``sql = "SELECT …"; db.execute(sql)``);
* **assembly of text carrying a SQL keyword** through an f-string, ``%``,
  ``+``, ``str.format`` on such a literal, or ``str.join`` over such literals.

What the guard does NOT need to cover, and why: SQLAlchemy 2.x refuses a bare
string handed to ``Session.execute`` / ``Connection.execute``
(``ArgumentError: Textual SQL expression … should be explicitly declared as
text()``), so a string that is neither wrapped by a forbidden constructor nor
handed to a forbidden escape hatch cannot execute. The residue is a module name
assembled at runtime (invisible to every AST guard) and a DBAPI cursor obtained
outside SQLAlchemy, which no BI module has a reason to hold; the executor's
read-only, statement-timed session is the runtime half of the same rule.

Exactly one module is allow-listed for exactly two names:
``app/bi/service/execution.py`` may call ``exec_driver_sql`` with
``_SET_STATEMENT_TIMEOUT_SQL`` or ``_SET_READ_ONLY_SQL``, each of which must
be a module-level string CONSTANT — ``SET`` cannot take a bound parameter,
so the timeout travels through ``set_config`` as a driver parameter and the
statement text itself is fixed at import time.

Every rule is proven able to fire on a planted violation, and the shapes the
compiler legitimately uses (``", ".join(labels)``, ``getattr(row, column)``, a
statement built by ``select()`` and executed through a local name) are proven
NOT to fire, so the guard cannot be silenced for crying wolf.

The generative guard builds random ``BiQuery`` payloads — catalogue ids mixed
with garbage, every operator and some that do not exist, values that are
classic injection strings — and asserts that each one either fails
validation (pydantic, 422), is refused by the compiler with a typed
``BiQueryError`` (422), or compiles to a statement whose text contains NONE
of the strings the client sent and that SQLite executes without error. An
exception from SQLAlchemy's SQL layer, or any other kind, is a failure.

This module's other-suite cases live in ``test_bi_compiler_injection.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session

from app.bi.domain.catalogue import Catalogue, catalogue
from app.bi.schemas.query import BiFilterOp, BiQuery
from app.bi.service.compiler import CompiledQuery, compile_query
from app.bi.service.errors import BiQueryError
from app.db.base import Base
from tests.architecture.test_bi_compiler_injection import garbage, values
from tests.support.database import managed_engine, managed_session_factory

_cat: Catalogue = catalogue()
MEASURE_IDS = tuple(m.id for m in _cat.measures())
DIMENSION_IDS = tuple(d.id for d in _cat.dimensions())
HIERARCHY_IDS = tuple(h.id for h in _cat.hierarchies())
OPS: tuple[str, ...] = BiFilterOp.__args__  # type: ignore[attr-defined]
member_ids = st.one_of(st.sampled_from(MEASURE_IDS + DIMENSION_IDS + HIERARCHY_IDS), garbage)
filters = st.fixed_dictionaries(
    {
        "member": member_ids,
        "op": st.one_of(st.sampled_from(OPS), st.sampled_from(["like", "regex", "=", "OR"])),
        "values": st.lists(values, max_size=4),
    }
)
sorts = st.fixed_dictionaries({"member": member_ids, "direction": st.sampled_from(["asc", "desc"])})
times = st.one_of(
    st.fixed_dictionaries({"as_of": st.dates(date(2024, 1, 1), date(2027, 12, 31))}),
    st.builds(
        lambda start, span: {"range": {"start": start, "end": start + timedelta(days=span)}},
        st.dates(date(2024, 1, 1), date(2027, 1, 1)),
        st.integers(min_value=0, max_value=400),
    ),
)
comparisons = st.one_of(st.none(), st.dates(date(2023, 1, 1), date(2027, 12, 31)))


def _with_comparison(payload: dict[str, Any], compare_to: date | None) -> dict[str, Any]:
    return {**payload, "time": {**payload["time"], "compare_to": compare_to}}


base_queries = st.fixed_dictionaries(
    {
        "measures": st.lists(member_ids, min_size=1, max_size=3),
        "dimensions": st.lists(member_ids, max_size=3),
        "filters": st.lists(filters, max_size=3),
        "time": times,
        "sort": st.lists(sorts, max_size=2),
        "top_n": st.one_of(
            st.none(),
            st.fixed_dictionaries(
                {"dimension": member_ids, "n": st.integers(1, 5), "other": st.booleans()}
            ),
        ),
        "pivot": st.one_of(
            st.none(),
            st.fixed_dictionaries({"dimension": member_ids, "max_columns": st.integers(1, 60)}),
        ),
        "limit": st.one_of(st.none(), st.integers(1, 10)),
        "offset": st.integers(0, 3),
        "subtotals": st.booleans(),
    },
)
queries = st.builds(_with_comparison, base_queries, comparisons)


@pytest.fixture(scope="module")
def probe_session(request: pytest.FixtureRequest) -> Iterator[Session]:
    """An empty in-memory schema: compile needs a dialect and a pivot probe."""
    engine = managed_engine(request, "sqlite://")
    Base.metadata.create_all(engine)
    session = managed_session_factory(request, engine)()
    yield session
    session.close()
    engine.dispose()


def _client_strings(payload: dict[str, Any]) -> set[str]:
    """Every string the client sent that is not a catalogue id."""
    out: set[str] = set()
    known = set(MEASURE_IDS) | set(DIMENSION_IDS) | set(HIERARCHY_IDS)
    for member in (*payload["measures"], *payload["dimensions"]):
        out.add(member)
    for flt in payload["filters"]:
        out.add(flt["member"])
        out.update(v for v in flt["values"] if isinstance(v, str))
    for sort in payload["sort"]:
        out.add(sort["member"])
    if payload["top_n"]:
        out.add(payload["top_n"]["dimension"])
    if payload["pivot"]:
        out.add(payload["pivot"]["dimension"])
    return {s for s in out - known if len(s) >= 2}


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(payload=queries)
def test_random_queries_bind_every_client_value_or_fail_typed(
    probe_session: Session, payload: dict[str, Any]
) -> None:
    try:
        query = BiQuery.model_validate(payload)
    except ValidationError:
        return
    try:
        compiled = compile_query(
            probe_session, _cat, query, organization_id="OR-PROBE001", bank_id="BK-PROBE001"
        )
    except BiQueryError as exc:
        assert exc.status_code in {422, 504}
        message = str(exc)
        assert "SELECT" not in message
        for sent in _client_strings(payload):
            if not sent.replace(".", "").replace("_", "").isalnum():
                assert sent not in message, f"client string {sent!r} echoed in an error"
        return
    assert isinstance(compiled, CompiledQuery)
    statement, _ = compiled.with_row_cap(50)
    sql = str(statement.compile(dialect=sqlite.dialect()))
    for sent in _client_strings(payload):
        assert sent not in sql, f"client string {sent!r} reached the SQL text"
    assert "OR-PROBE001" not in sql
    assert "BK-PROBE001" not in sql
    # The statement is valid SQL for the plane: SQLite runs it over the empty mart.
    rows = probe_session.execute(statement).all()
    assert isinstance(rows, list)
