"""S12: nothing a BI client sends can become SQL text.

Two guards, one static and one generative.

The static guard walks the AST of every module under ``app/services/bi`` and
``app/domain/bi`` and forbids the constructs through which a string could
reach the database as SQL: ``text()`` (and its ``sql_text`` alias),
``literal_column()``, the lightweight ``column()`` / ``table()``
constructors, ``exec_driver_sql()``, ``raw_connection()``,
``from_statement()``, ``execute()`` with anything but an expression object,
and any f-string / ``%`` / ``+`` assembly of text containing a SQL keyword.
Exactly one module is allow-listed for exactly two names:
``app/services/bi/execution.py`` may call ``exec_driver_sql`` with
``_SET_STATEMENT_TIMEOUT_SQL`` or ``_SET_READ_ONLY_SQL``, each of which must
be a module-level string CONSTANT — ``SET`` cannot take a bound parameter,
so the timeout travels through ``set_config`` as a driver parameter and the
statement text itself is fixed at import time.

The generative guard builds random ``BiQuery`` payloads — catalogue ids mixed
with garbage, every operator and some that do not exist, values that are
classic injection strings — and asserts that each one either fails
validation (pydantic, 422), is refused by the compiler with a typed
``BiQueryError`` (422), or compiles to a statement whose text contains NONE
of the strings the client sent and that SQLite executes without error. An
exception from SQLAlchemy's SQL layer, or any other kind, is a failure.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session

from app.db.base import Base
from app.domain.bi.catalogue import Catalogue, catalogue
from app.schemas.bi import BiFilterOp, BiQuery
from app.services.bi.compiler import CompiledQuery, compile_query
from app.services.bi.errors import BiQueryError

BACKEND = Path(__file__).parents[2]
BI_ROOTS = (BACKEND / "app" / "services" / "bi", BACKEND / "app" / "domain" / "bi")

FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "text",
        "sql_text",
        "literal_column",
        "column",
        "table",
        "exec_driver_sql",
        "raw_connection",
        "from_statement",
        "textual",
        "TextClause",
    }
)
#: module (relative to backend) → the ONLY names it may hand to ``exec_driver_sql``.
ALLOWED_LITERAL_SQL: dict[str, frozenset[str]] = {
    "app/services/bi/execution.py": frozenset({"_SET_STATEMENT_TIMEOUT_SQL", "_SET_READ_ONLY_SQL"}),
}
SQL_KEYWORD = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|DROP|ALTER|CREATE|TRUNCATE|GRANT|SET)\b"
)


def _bi_modules() -> list[Path]:
    paths = [path for root in BI_ROOTS for path in sorted(root.rglob("*.py"))]
    assert paths, "no BI modules found"
    return paths


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _string_parts(node: ast.expr) -> list[str]:
    """Literal string fragments an expression is assembled from."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return _string_parts(node.left) + _string_parts(node.right)
    return []


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    constants: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            constants[node.target.id] = node.value
    return constants


def _violations(path: Path) -> list[str]:  # noqa: PLR0912 - one branch per forbidden shape
    relative = path.relative_to(BACKEND).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = ALLOWED_LITERAL_SQL.get(relative, frozenset())
    constants = _module_constants(tree)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names = [alias.name for alias in node.names]
            module = getattr(node, "module", "") or ""
            if module.startswith("sqlalchemy") and any(
                name in {"text", "literal_column", "column", "table"} for name in names
            ):
                found.append(f"{relative}:{node.lineno} imports {names} from {module}")
            continue
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "exec_driver_sql" and allowed:
                first = node.args[0] if node.args else None
                if not (isinstance(first, ast.Name) and first.id in allowed):
                    found.append(
                        f"{relative}:{node.lineno} exec_driver_sql with an unlisted statement"
                    )
                continue
            if name in FORBIDDEN_CALLS:
                found.append(f"{relative}:{node.lineno} calls {name}()")
                continue
            if name in {"execute", "scalar", "scalars"} and node.args:
                first = node.args[0]
                if not isinstance(first, ast.Name | ast.Attribute | ast.Call | ast.Subscript):
                    found.append(
                        f"{relative}:{node.lineno} {name}() with a non-expression argument"
                    )
            continue
        if isinstance(node, ast.JoinedStr | ast.BinOp):
            parts = _string_parts(node)
            if isinstance(node, ast.BinOp) and not parts:
                continue
            if any(SQL_KEYWORD.search(part) for part in parts):
                found.append(f"{relative}:{node.lineno} assembles text containing a SQL keyword")

    for name in allowed:
        value = constants.get(name)
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            found.append(f"{relative}: allow-listed {name} is not a module-level string constant")
        elif "{" in value.value and "%(ms)s" not in value.value:
            found.append(f"{relative}: allow-listed {name} looks interpolated")
    return found


@pytest.mark.parametrize("path", _bi_modules(), ids=lambda p: p.relative_to(BACKEND).as_posix())
def test_bi_modules_build_no_sql_from_strings(path: Path) -> None:
    assert _violations(path) == []


def test_the_allow_list_names_exactly_the_two_settings_statements() -> None:
    path = BACKEND / "app/services/bi/execution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    allowed = ALLOWED_LITERAL_SQL["app/services/bi/execution.py"]
    for name in allowed:
        value = constants[name]
        assert isinstance(value, ast.Constant)
        assert isinstance(value.value, str)
        assert value.value.startswith("SELECT set_config(")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "exec_driver_sql"
    ]
    assert {node.args[0].id for node in calls if isinstance(node.args[0], ast.Name)} == allowed


def test_the_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    """A guard that never fires proves nothing: show it fires on each shape."""
    samples = {
        "text": "from sqlalchemy import text\nq = text('SELECT 1')\n",
        "literal_column": "from sqlalchemy import literal_column as lc\nq = lc(name)\n",
        "fstring": "q = f'SELECT * FROM {table}'\n",
        "concat": "q = 'SELECT * FROM ' + table\n",
        "driver": "conn.exec_driver_sql('SELECT 1')\n",
        "execute_str": "session.execute('SELECT 1')\n",
    }
    for label, body in samples.items():
        path = BACKEND / "app" / "services" / "bi" / f"_guard_probe_{label}.py"
        try:
            path.write_text(body, encoding="utf-8")
            assert _violations(path), label
        finally:
            path.unlink(missing_ok=True)


# --- generative -----------------------------------------------------------------------------


INJECTIONS = (
    "' OR 1=1 --",
    '"; DROP TABLE bi_fact_position_daily; --',
    "1; SELECT * FROM users",
    "%",
    "_",
    "\\",
    "k0",
    "m0",
    "bi_fact_position_daily.balance_rc",
    ") UNION ALL SELECT 1 --",
)

_cat: Catalogue = catalogue()
MEASURE_IDS = tuple(m.id for m in _cat.measures())
DIMENSION_IDS = tuple(d.id for d in _cat.dimensions())
HIERARCHY_IDS = tuple(h.id for h in _cat.hierarchies())
OPS: tuple[str, ...] = BiFilterOp.__args__  # type: ignore[attr-defined]

garbage = st.one_of(
    st.sampled_from(INJECTIONS),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
        min_size=1,
        max_size=24,
    ),
)
member_ids = st.one_of(st.sampled_from(MEASURE_IDS + DIMENSION_IDS + HIERARCHY_IDS), garbage)
values = st.one_of(
    garbage,
    st.integers(min_value=-10, max_value=10),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.booleans(),
    st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)).map(date.isoformat),
)
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
def probe_session() -> Iterator[Session]:
    """An empty in-memory schema: compile needs a dialect and a pivot probe."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
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
