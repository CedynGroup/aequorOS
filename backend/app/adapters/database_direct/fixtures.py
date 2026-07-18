"""Offline synthetic-DB-dump driver: run the whole pull path without a live DB.

A live database is never available in CI, yet the query builder, normalization,
and bundle assembly must be exercised end-to-end. :class:`OfflineDumpDriver`
implements the same :class:`DatabaseDriver` abstraction as the Oracle/SQL Server/
JDBC/ODBC drivers, but serves rows from a synthetic on-disk *dump* (schema +
rows) instead of a socket. Because it consumes the exact :class:`BuiltQuery` the
query builder emits — parameters and all — a fixture pull proves the built SQL is
well-formed and that filters/incremental predicates bind correctly, all offline.

Dump layout (a directory)::

    schema.json           {"database": "COREBANK",
                           "tables": [{"schema": "DBO", "name": "GL_ACCOUNTS",
                                       "columns": [{"name": "ACCT_CODE",
                                                    "type": "varchar",
                                                    "nullable": false}, ...]}]}
    data/DBO.GL_ACCOUNTS.json   [ {"ACCT_CODE": "1000", ...}, ... ]

The included query interpreter understands ONLY the grammar
:func:`~app.adapters.database_direct.query_builder.build_select` produces
(``SELECT [TOP n] <cols> FROM <table> [WHERE <col op ?> AND ...] [ORDER BY <col>]
[FETCH FIRST n ROWS ONLY]``) — it is a test double, not a general SQL engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from app.adapters.database_direct.config import Backend, ConnectionConfig, ExtractionSpec
from app.adapters.database_direct.drivers.base import (
    ColumnSchema,
    DatabaseDriver,
    DbCredentials,
    DriverCapabilities,
    QueryResult,
    TableSchema,
)
from app.adapters.database_direct.errors import (
    DatabaseDirectError,
    DbDirectErrorCode,
    render_bank_facing,
)
from app.adapters.database_direct.query_builder import BuiltQuery, ParamStyle


@dataclass(frozen=True)
class Dump:
    """A loaded synthetic dump: schema tables plus rows keyed by qualified name."""

    database: str
    tables: tuple[TableSchema, ...]
    rows: dict[str, list[dict[str, Any]]]

    def table_for(self, qualified: str) -> TableSchema | None:
        target = qualified.upper()
        for table in self.tables:
            if table.qualified_name.upper() == target or table.name.upper() == target:
                return table
        return None

    def rows_for(self, qualified: str) -> list[dict[str, Any]]:
        target = qualified.upper()
        for name, rows in self.rows.items():
            if name.upper() == target or name.split(".")[-1].upper() == target.split(".")[-1]:
                return rows
        return []


def load_dump(dump_dir: Path | str) -> Dump:
    """Load a synthetic dump directory into an in-memory :class:`Dump`."""
    directory = Path(dump_dir)
    schema_doc = json.loads((directory / "schema.json").read_text(encoding="utf-8"))
    database = str(schema_doc.get("database", ""))
    tables: list[TableSchema] = []
    rows: dict[str, list[dict[str, Any]]] = {}
    for entry in schema_doc.get("tables", []):
        name = str(entry["name"])
        schema = entry.get("schema")
        if schema is None and "." in name:
            schema, _, name = name.partition(".")
        columns = tuple(
            ColumnSchema(
                name=str(col["name"]),
                data_type=str(col.get("type", "")),
                nullable=bool(col.get("nullable", True)),
            )
            for col in entry.get("columns", [])
        )
        table = TableSchema(name=name, schema=schema, columns=columns)
        tables.append(table)
        data_path = directory / "data" / f"{table.qualified_name}.json"
        if data_path.is_file():
            rows[table.qualified_name] = list(json.loads(data_path.read_text(encoding="utf-8")))
        else:
            rows[table.qualified_name] = []
    return Dump(database=database, tables=tuple(tables), rows=rows)


class OfflineDumpDriver(DatabaseDriver):
    """A :class:`DatabaseDriver` backed by a synthetic dump — no live DB."""

    def __init__(self, dump: Dump, *, backend: Backend = "sqlserver") -> None:
        self._dump = dump
        self._backend: Backend = backend

    def backend(self) -> Backend:
        return self._backend

    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            supports_change_data_capture=False,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> _DumpSession:
        if not credentials.username:
            # Mirror a real driver: a blank credential fails classification, not
            # a silent success, so credential wiring is genuinely exercised.
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CREDENTIAL_INVALID, database=connection.display_label
                ),
                internal_detail="offline dump driver received an empty username",
            )
        return _DumpSession(self._dump, backend=connection.backend)


class _DumpSession:
    """Interprets the query builder's grammar against a loaded dump."""

    def __init__(self, dump: Dump, *, backend: Backend) -> None:
        self._dump = dump
        self._backend = backend

    def __enter__(self) -> _DumpSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def introspect(self, schemas: tuple[str, ...]) -> list[TableSchema]:
        if not schemas:
            return list(self._dump.tables)
        wanted = {s.upper() for s in schemas}
        return [t for t in self._dump.tables if (t.schema or "").upper() in wanted]

    def fetch(self, query: BuiltQuery) -> QueryResult:
        parsed = _parse_select(query.sql)
        rows = list(self._dump.rows_for(parsed.table))
        params = _ordered_params(query)
        rows = _apply_predicates(rows, parsed.predicates, params)
        if parsed.count:
            return QueryResult(columns=("ROW_COUNT",), rows=((len(rows),),))
        if parsed.order_by:
            order_column = parsed.order_by
            rows = sorted(rows, key=lambda r: _sort_key(r.get(order_column)))
        if parsed.limit is not None:
            rows = rows[: parsed.limit]
        columns = self._resolve_columns(parsed, rows)
        tuples = tuple(tuple(row.get(column) for column in columns) for row in rows)
        return QueryResult(columns=columns, rows=tuples)

    def _resolve_columns(
        self, parsed: _ParsedSelect, rows: list[dict[str, Any]]
    ) -> tuple[str, ...]:
        if parsed.columns:
            return parsed.columns
        table = self._dump.table_for(parsed.table)
        if table is not None:
            return tuple(column.name for column in table.columns)
        return tuple(rows[0].keys()) if rows else ()


# --- the minimal SELECT interpreter (test double, not a SQL engine) --------


@dataclass(frozen=True)
class _Predicate:
    column: str
    op: str  # "=" or ">"


@dataclass(frozen=True)
class _ParsedSelect:
    columns: tuple[str, ...]  # empty tuple means "*"
    table: str
    predicates: tuple[_Predicate, ...]
    order_by: str | None
    limit: int | None
    count: bool = False  # SELECT COUNT(*) AS ROW_COUNT — a row-count discovery query


def _unquote(identifier: str) -> str:
    return identifier.replace('"', "").replace("[", "").replace("]", "").strip()


def _parse_select(sql: str) -> _ParsedSelect:
    text = sql.strip()
    upper = text.upper()
    from_at = upper.index(" FROM ")
    select_body = text[len("SELECT ") : from_at].strip()

    limit: int | None = None
    if select_body.upper().startswith("TOP "):
        head, _, rest = select_body.partition(" ")  # "TOP"
        n_token, _, select_body = rest.partition(" ")
        limit = int(n_token)
        _ = head

    columns: tuple[str, ...] = ()
    count = False
    if select_body.upper().startswith("COUNT(*)"):
        count = True
    elif select_body.strip() != "*":
        columns = tuple(_unquote(part) for part in select_body.split(","))

    rest = text[from_at + len(" FROM ") :].strip()
    # Table token runs until the next clause keyword or end of string.
    table_token, tail = _split_first_token(rest)
    table = _unquote(table_token)
    tail = tail.strip()

    predicates: list[_Predicate] = []
    order_by: str | None = None
    tail_upper = tail.upper()
    if tail_upper.startswith("WHERE "):
        where_body, tail = _slice_until(tail[len("WHERE ") :], (" ORDER BY ", " FETCH FIRST "))
        predicates = _parse_predicates(where_body)
        tail = tail.strip()
        tail_upper = tail.upper()
    if tail_upper.startswith("ORDER BY "):
        order_body, tail = _slice_until(tail[len("ORDER BY ") :], (" FETCH FIRST ",))
        order_by = _unquote(order_body)
        tail = tail.strip()
        tail_upper = tail.upper()
    if tail_upper.startswith("FETCH FIRST "):
        after = tail[len("FETCH FIRST ") :].strip()
        limit = int(after.split(" ", 1)[0])

    return _ParsedSelect(
        columns=columns,
        table=table,
        predicates=tuple(predicates),
        order_by=order_by,
        limit=limit,
        count=count,
    )


def _split_first_token(text: str) -> tuple[str, str]:
    stripped = text.strip()
    space = stripped.find(" ")
    if space == -1:
        return stripped, ""
    return stripped[:space], stripped[space:]


def _slice_until(text: str, stops: tuple[str, ...]) -> tuple[str, str]:
    upper = text.upper()
    cut = len(text)
    for stop in stops:
        idx = upper.find(stop)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip(), text[cut:]


def _parse_predicates(where_body: str) -> list[_Predicate]:
    predicates: list[_Predicate] = []
    for term in where_body.split(" AND "):
        tokens = term.strip().split()
        if len(tokens) < 3:  # noqa: PLR2004 - "<col> <op> <placeholder>"
            continue
        predicates.append(_Predicate(column=_unquote(tokens[0]), op=tokens[1]))
    return predicates


def _ordered_params(query: BuiltQuery) -> list[Any]:
    if query.paramstyle is ParamStyle.QMARK:
        return list(query.parameters)  # type: ignore[arg-type]
    # NAMED: parameters is a dict keyed p0, p1, ...; restore positional order.
    named: dict[str, Any] = query.parameters  # type: ignore[assignment]
    return [named[f"p{i}"] for i in range(len(named))]


def _apply_predicates(
    rows: list[dict[str, Any]], predicates: tuple[_Predicate, ...], params: list[Any]
) -> list[dict[str, Any]]:
    if not predicates:
        return rows
    bound = list(zip(predicates, params, strict=False))
    kept: list[dict[str, Any]] = []
    for row in rows:
        if all(_matches(row.get(pred.column), pred.op, value) for pred, value in bound):
            kept.append(row)
    return kept


def _matches(cell: Any, op: str, value: Any) -> bool:
    if op == "=":
        return str(cell) == str(value)
    if op == ">":
        return _sort_key(cell) > _sort_key(value)
    return False


def _sort_key(value: Any) -> tuple[int, float, str]:
    """Order-preserving key: numerics compare numerically, else lexically.

    Returns a tuple so ``None`` sinks first and mixed types never raise; ISO
    date strings sort correctly lexically, which is what incremental cursors use.
    """
    if value is None:
        return (0, 0.0, "")
    text = str(value)
    try:
        return (1, float(text), "")
    except ValueError:
        return (2, 0.0, text)


# --- staging helpers for tests and offline demos ---------------------------


def stage_bundle_from_dump(  # noqa: PLR0913 - test/demo helper mirroring stage_pull_to_path
    dump_dir: Path | str,
    connection: ConnectionConfig,
    spec: ExtractionSpec,
    path: Path,
    *,
    as_of: Any,
    credentials: DbCredentials | None = None,
    **pull_kwargs: Any,
) -> None:
    """Run a full offline pull from a dump and write the staged bundle to ``path``.

    A convenience for tests and offline demos: it wires the
    :class:`OfflineDumpDriver` into the real :func:`stage_pull_to_path`, so the
    written bundle is produced by the same code path a live pull would use.
    """
    from app.adapters.database_direct.pull import stage_pull_to_path  # noqa: PLC0415

    dump = load_dump(dump_dir)
    driver = OfflineDumpDriver(dump, backend=connection.backend)
    stage_pull_to_path(
        driver,
        connection,
        credentials or DbCredentials(username="SVC.AEQUOROS"),
        spec,
        path,
        as_of=as_of,
        **pull_kwargs,
    )
