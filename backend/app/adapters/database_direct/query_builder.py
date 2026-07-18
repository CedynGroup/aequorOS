"""A safe, parameterized SELECT builder — no string concatenation of values.

Every value that varies at runtime (incremental cursors, equality filters) is
bound as a driver parameter, never interpolated into SQL text. Identifiers
(schema, table, column names) come only from the onboarding-time
:class:`~app.adapters.database_direct.config.ExtractionSpec`, and even those are
validated against a conservative identifier grammar and quoted per backend
dialect, so a malformed configuration is rejected loudly rather than producing
injectable SQL.

The builder is read-only by construction: it emits ``SELECT`` statements and
nothing else. There is no code path here that can produce an ``INSERT``,
``UPDATE``, ``DELETE``, ``MERGE``, or DDL — writing to a bank's source system is
categorically outside this adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.adapters.database_direct.config import Backend, TableExtraction

# Identifiers may contain letters, digits, underscore, and the vendor-common
# ``$``/``#`` (Oracle/DB2), separated by dots for schema qualification. Anything
# else — quotes, whitespace, semicolons, comment markers — is rejected before it
# can reach SQL text. This is the injection guard for the one place identifiers,
# not values, are composed.
_IDENTIFIER_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


class ParamStyle(Enum):
    """DBAPI ``paramstyle`` for the target driver (PEP 249).

    ``QMARK`` (``?``) is used by ``pyodbc`` and ``jaydebeapi``; ``NAMED``
    (``:name``) is used by ``oracledb``. The builder emits placeholders and a
    matching parameter container for whichever style the driver declares.
    """

    QMARK = "qmark"
    NAMED = "named"


_BACKEND_PARAMSTYLE: dict[Backend, ParamStyle] = {
    "oracle": ParamStyle.NAMED,
    "sqlserver": ParamStyle.QMARK,
    "jdbc": ParamStyle.QMARK,
    "odbc": ParamStyle.QMARK,
    # Snowflake uses ANSI double-quote identifier quoting (handled by the default
    # branch in _quote_part) and binds with '?' when the connector's paramstyle is
    # set to 'qmark'; FETCH FIRST n ROWS ONLY is supported.
    "snowflake": ParamStyle.QMARK,
}


def paramstyle_for(backend: Backend) -> ParamStyle:
    return _BACKEND_PARAMSTYLE[backend]


class IdentifierError(ValueError):
    """A configured identifier failed the safe-identifier grammar."""


def _quote_part(part: str, backend: Backend) -> str:
    if not _IDENTIFIER_PART.match(part):
        msg = f"unsafe SQL identifier part {part!r}; refusing to build a query."
        raise IdentifierError(msg)
    if backend == "sqlserver":
        return f"[{part}]"
    # Oracle and the ANSI-quoting generic bridges (jdbc/odbc) use double quotes.
    return f'"{part}"'


def quote_qualified(name: str, backend: Backend) -> str:
    """Quote a possibly schema-qualified identifier (``SCHEMA.TABLE``).

    Each dot-separated part is validated and quoted independently, so a
    malformed part fails fast and no part is ever concatenated unquoted.
    """
    parts = name.split(".")
    return ".".join(_quote_part(part, backend) for part in parts)


@dataclass(frozen=True)
class BuiltQuery:
    """A ready-to-execute read query.

    ``sql`` carries placeholders in the driver's paramstyle. ``parameters`` is a
    ``list`` for QMARK drivers (positional) or a ``dict`` for NAMED drivers
    (keyed). ``order matters``: for QMARK the list order matches the ``?`` order.
    """

    sql: str
    parameters: list[Any] | dict[str, Any]
    paramstyle: ParamStyle


class _Binder:
    """Accumulates bound values and emits paramstyle-correct placeholders."""

    def __init__(self, style: ParamStyle) -> None:
        self._style = style
        self._positional: list[Any] = []
        self._named: dict[str, Any] = {}

    def bind(self, value: Any) -> str:
        if self._style is ParamStyle.QMARK:
            self._positional.append(value)
            return "?"
        key = f"p{len(self._named)}"
        self._named[key] = value
        return f":{key}"

    def parameters(self) -> list[Any] | dict[str, Any]:
        return self._positional if self._style is ParamStyle.QMARK else self._named


def _select_columns(columns: tuple[str, ...], backend: Backend) -> str:
    if not columns:
        return "*"
    return ", ".join(quote_qualified(column, backend) for column in columns)


def build_select(
    extraction: TableExtraction,
    backend: Backend,
    *,
    incremental_since: Any | None = None,
    row_limit: int | None = None,
) -> BuiltQuery:
    """Build the read query for one table extraction.

    - Columns: the configured projection, or ``*`` when discovery-driven.
    - Joins: when the extraction names detail tables, they are LEFT/INNER-JOINed
      and their columns projected under their own names (see ``_build_joined``).
    - Filters: each configured equality filter becomes a bound ``col = :p``.
    - Incremental: when ``incremental_since`` is given AND the extraction names
      an ``incremental_column``, a bound ``cursor > :since`` predicate is added
      and results are ordered by the cursor so paging/restart is deterministic.
    - ``row_limit``: an optional server-side cap, expressed per dialect
      (SQL Server ``TOP`` / Oracle ``FETCH FIRST`` / ANSI ``FETCH FIRST`` for
      the generic bridges), used for schema-sampling and safety caps.

    Note (verifiability): ``FETCH FIRST n ROWS ONLY`` is ANSI SQL and supported
    by Oracle 12c+ and modern SQL Server; the generic bridges emit the same
    ANSI clause. Where a target predates it, ``row_limit`` should be left unset
    and the pull relies on the server-side statement timeout instead.
    """
    if extraction.joins:
        return _build_joined(
            extraction, backend, incremental_since=incremental_since, row_limit=row_limit
        )

    style = paramstyle_for(backend)
    binder = _Binder(style)

    projection = _select_columns(extraction.columns, backend)
    table = quote_qualified(extraction.table, backend)

    top_clause = ""
    if row_limit is not None and backend == "sqlserver":
        # SQL Server takes the row cap up front as TOP (n); n is a validated int.
        top_clause = f"TOP {int(row_limit)} "

    where_terms: list[str] = []
    for column, value in extraction.filters.items():
        where_terms.append(f"{quote_qualified(column, backend)} = {binder.bind(value)}")
    if incremental_since is not None and extraction.incremental_column:
        cursor = quote_qualified(extraction.incremental_column, backend)
        where_terms.append(f"{cursor} > {binder.bind(incremental_since)}")

    sql = f"SELECT {top_clause}{projection} FROM {table}"
    if where_terms:
        sql += " WHERE " + " AND ".join(where_terms)
    if extraction.incremental_column:
        sql += f" ORDER BY {quote_qualified(extraction.incremental_column, backend)}"

    if row_limit is not None and backend != "sqlserver":
        # Oracle 12c+ and ANSI generic bridges: FETCH FIRST n ROWS ONLY.
        sql += f" FETCH FIRST {int(row_limit)} ROWS ONLY"

    return BuiltQuery(sql=sql, parameters=binder.parameters(), paramstyle=style)


def build_count_select(table: str, backend: Backend) -> BuiltQuery:
    """A safe ``SELECT COUNT(*)`` for schema discovery — no filters, no values.

    The result column is aliased ``ROW_COUNT`` so the caller reads it by name
    regardless of how the dialect would otherwise label ``COUNT(*)``.
    """
    style = paramstyle_for(backend)
    alias = _quote_part("ROW_COUNT", backend)
    sql = f"SELECT COUNT(*) AS {alias} FROM {quote_qualified(table, backend)}"
    return BuiltQuery(sql=sql, parameters=[] if style is ParamStyle.QMARK else {}, paramstyle=style)


def build_sample_select(table: str, backend: Backend, *, row_limit: int) -> BuiltQuery:
    """A safe, bounded ``SELECT *`` for schema-discovery sampling.

    Reads at most ``row_limit`` rows (dialect-correct ``TOP`` / ``FETCH FIRST``)
    so the operator can see representative values while mapping. Read-only and
    identifier-guarded like every builder here.
    """
    style = paramstyle_for(backend)
    limit = int(row_limit)
    quoted = quote_qualified(table, backend)
    if backend == "sqlserver":
        sql = f"SELECT TOP {limit} * FROM {quoted}"
    else:
        sql = f"SELECT * FROM {quoted} FETCH FIRST {limit} ROWS ONLY"
    return BuiltQuery(sql=sql, parameters=[] if style is ParamStyle.QMARK else {}, paramstyle=style)


# --- Joined extraction -----------------------------------------------------

_BASE_ALIAS = "t0"


def _aliased(alias: str, column: str, backend: Backend) -> str:
    """``alias.<quoted bare column>`` — the alias is a generated safe token."""
    return f"{alias}.{_quote_part(column, backend)}"


def _build_joined(
    extraction: TableExtraction,
    backend: Backend,
    *,
    incremental_since: Any | None,
    row_limit: int | None,
) -> BuiltQuery:
    """Build a read query that LEFT/INNER-joins detail tables onto the base.

    The base table is aliased ``t0`` and each join ``j1``, ``j2``, ...; base
    columns are projected as ``t0.col`` (or ``t0.*`` when discovery-driven) and
    each join column as ``jN.col AS col`` so the mapping reads it under its bare
    name. Join columns must not collide with each other or with an explicit base
    projection; a detectable collision is rejected (with ``t0.*`` the base names
    are unknown at build time, so onboarding must keep them disjoint). Filters,
    the incremental predicate, and ``ORDER BY`` all bind to the base ``t0`` so
    joins never change which base rows are returned.
    """
    style = paramstyle_for(backend)
    binder = _Binder(style)

    # Projection: base columns first, then each join's columns aliased to bare.
    base_projection = (
        "t0.*"
        if not extraction.columns
        else ", ".join(_aliased(_BASE_ALIAS, c, backend) for c in extraction.columns)
    )
    seen: set[str] = set(extraction.columns)
    join_projection: list[str] = []
    from_clause = f"{quote_qualified(extraction.table, backend)} {_BASE_ALIAS}"
    for index, join in enumerate(extraction.joins, start=1):
        alias = f"j{index}"
        for column in join.columns:
            if column in seen:
                msg = (
                    f"join column {column!r} on {join.table!r} collides with an "
                    "existing column; rename or drop it from the join projection."
                )
                raise IdentifierError(msg)
            seen.add(column)
            join_projection.append(
                f"{_aliased(alias, column, backend)} AS {_quote_part(column, backend)}"
            )
        on_terms = [
            f"{_aliased(_BASE_ALIAS, base_col, backend)} = {_aliased(alias, detail_col, backend)}"
            for base_col, detail_col in join.on.items()
        ]
        keyword = "INNER JOIN" if join.kind == "inner" else "LEFT JOIN"
        from_clause += (
            f" {keyword} {quote_qualified(join.table, backend)} {alias} ON "
            + " AND ".join(on_terms)
        )

    projection = base_projection
    if join_projection:
        projection += ", " + ", ".join(join_projection)

    top_clause = ""
    if row_limit is not None and backend == "sqlserver":
        top_clause = f"TOP {int(row_limit)} "

    where_terms: list[str] = []
    for column, value in extraction.filters.items():
        where_terms.append(f"{_aliased(_BASE_ALIAS, column, backend)} = {binder.bind(value)}")
    if incremental_since is not None and extraction.incremental_column:
        cursor = _aliased(_BASE_ALIAS, extraction.incremental_column, backend)
        where_terms.append(f"{cursor} > {binder.bind(incremental_since)}")

    sql = f"SELECT {top_clause}{projection} FROM {from_clause}"
    if where_terms:
        sql += " WHERE " + " AND ".join(where_terms)
    if extraction.incremental_column:
        sql += f" ORDER BY {_aliased(_BASE_ALIAS, extraction.incremental_column, backend)}"
    if row_limit is not None and backend != "sqlserver":
        sql += f" FETCH FIRST {int(row_limit)} ROWS ONLY"

    return BuiltQuery(sql=sql, parameters=binder.parameters(), paramstyle=style)
