"""Namespace application advisory locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from functools import wraps
from typing import Protocol, cast

from psycopg import sql
from psycopg.abc import AdaptContext, Params, Query
from sqlalchemy import Engine, event


class _NativeConnection(Protocol):
    cursor_factory: type[object]
    server_cursor_factory: type[object]


#: Single-parameter advisory-lock statements whose key belongs to the test's
#: schema: the literal ``%(key)s`` forms and the compiled
#: ``select(func.pg_advisory_xact_lock/lock/unlock(key))`` forms the application
#: builds for the scheduler's session purge and the liquidity publication locks,
#: where SQLAlchemy generates the bind name and a label alias. Contention probes
#: keep the original database-global identity through another bind name
#: (``probe_key``), a second parameter or a literal key.
_NAMESPACED_LOCK = re.compile(
    r"SELECT pg_advisory_(?:xact_lock_shared|xact_lock|lock|unlock)"
    r"\(%\((key|pg_advisory_[a-z_]+_\d+)\)s(?:::\w+)?\)(?: AS \w+)?"
)


def _statement_text(statement: object) -> str | None:
    if isinstance(statement, str):
        return statement
    if isinstance(statement, bytes):
        try:
            return statement.decode()
        except UnicodeDecodeError:
            return None
    return None


def _matched_name(
    query: Query | str, schema: str, cursor: object
) -> tuple[Query | str, str | None]:
    """Return the (possibly cast-widened) query and the bind name to namespace."""
    statement = (
        query.as_string(cast(AdaptContext, cursor)) if isinstance(query, sql.Composable) else query
    )
    text_statement = _statement_text(statement)
    if text_statement is None:
        return query, None
    match = _NAMESPACED_LOCK.fullmatch(text_statement)
    if match is None:
        return query, None
    name = cast(str, match.group(1))
    # The psycopg dialect renders small func-built keys with an ``::INTEGER``
    # bind cast; the namespaced key is a full bigint, so widen the cast — the
    # single-argument advisory functions accept bigint either way.
    widened = text_statement.replace(f"%({name})s::INTEGER", f"%({name})s::BIGINT")
    if widened == text_statement:
        return query, name
    return widened.encode() if isinstance(statement, bytes) else widened, name


def _namespaced_params(name: str, parameters: Params | None, schema: str) -> Params | None:
    assert isinstance(parameters, Mapping)
    values = cast(Mapping[str, object], parameters)
    key = values[name]
    assert isinstance(key, int)
    namespace = f"-csearch_path={schema}"
    isolated = int.from_bytes(
        hashlib.sha256(f"{namespace}:{key}".encode()).digest()[:8], signed=True
    )
    return {**values, name: isolated}


def _isolate(
    query: Query | str, parameters: Params | None, schema: str, cursor: object
) -> tuple[Query | str, Params | None]:
    isolated_query, name = _matched_name(query, schema, cursor)
    if name is None:
        return query, parameters
    return isolated_query, _namespaced_params(name, parameters, schema)


def _cursor_factory(factory: type[object], schema: str) -> type[object]:
    methods: dict[str, object] = {}

    def single(original: Callable[..., object]) -> Callable[..., object]:
        @wraps(original)
        def execute(
            cursor: object, query: Query, params: Params | None = None, **options: object
        ) -> object:
            isolated_query, isolated_params = _isolate(query, params, schema, cursor)
            return original(cursor, isolated_query, isolated_params, **options)

        return execute

    def multiple(original: Callable[..., object]) -> Callable[..., object]:
        @wraps(original)
        def executemany(
            cursor: object, query: Query, params_seq: Iterable[Params], **options: object
        ) -> object:
            isolated_query, name = _matched_name(query, schema, cursor)
            if name is None:
                return original(cursor, query, params_seq, **options)
            isolated_seq = (_namespaced_params(name, params, schema) for params in params_seq)
            return original(cursor, isolated_query, isolated_seq, **options)

        return executemany

    for name in ("execute", "stream", "executemany"):
        wrap = multiple if name == "executemany" else single
        methods[name] = wrap(cast(Callable[..., object], getattr(factory, name)))
    return type(f"Isolated{factory.__name__}", (factory,), methods)


def isolate_advisory_locks(engine: Engine, schema: str) -> None:
    """Namespace native cursors before checkout, including async driver cursors.

    Keep the driver's cursor classes and row factories so native and SQLAlchemy
    callers share one transformation without replacing their connection objects.
    """

    def connected(raw: object, _record: object) -> None:
        connection = cast(_NativeConnection, getattr(raw, "driver_connection", raw))
        connection.cursor_factory = _cursor_factory(connection.cursor_factory, schema)
        connection.server_cursor_factory = _cursor_factory(connection.server_cursor_factory, schema)

    event.listen(engine, "connect", connected)
