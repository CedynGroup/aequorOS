"""Keep encryption locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from functools import wraps
from typing import Protocol, cast

from psycopg import sql
from psycopg.abc import AdaptContext, Params, Query
from sqlalchemy import Engine, event


class _NativeConnection(Protocol):
    cursor_factory: type[object]
    server_cursor_factory: type[object]


def _namespace_key(
    query: Query, parameters: Params | None, schema: str, cursor: object
) -> Params | None:
    statement = (
        query.as_string(cast(AdaptContext, cursor)) if isinstance(query, sql.Composable) else query
    )
    if statement not in (
        "SELECT pg_advisory_xact_lock(%(key)s)",
        "SELECT pg_advisory_xact_lock_shared(%(key)s)",
        b"SELECT pg_advisory_xact_lock(%(key)s)",
        b"SELECT pg_advisory_xact_lock_shared(%(key)s)",
    ):
        return parameters
    assert isinstance(parameters, Mapping)
    values = cast(Mapping[str, object], parameters)
    key = values["key"]
    assert isinstance(key, int)
    namespace = f"-csearch_path={schema}"
    isolated = int.from_bytes(
        hashlib.sha256(f"{namespace}:{key}".encode()).digest()[:8], signed=True
    )
    return {**values, "key": isolated}


def _cursor_factory(factory: type[object], schema: str) -> type[object]:
    methods: dict[str, object] = {}

    def single(original: Callable[..., object]) -> Callable[..., object]:
        @wraps(original)
        def execute(
            cursor: object, query: Query, params: Params | None = None, **options: object
        ) -> object:
            return original(cursor, query, _namespace_key(query, params, schema, cursor), **options)

        return execute

    def multiple(original: Callable[..., object]) -> Callable[..., object]:
        @wraps(original)
        def executemany(
            cursor: object, query: Query, params_seq: Iterable[Params], **options: object
        ) -> object:
            return original(
                cursor,
                query,
                (_namespace_key(query, params, schema, cursor) for params in params_seq),
                **options,
            )

        return executemany

    for name in ("execute", "stream", "executemany"):
        wrap = multiple if name == "executemany" else single
        methods[name] = wrap(cast(Callable[..., object], getattr(factory, name)))
    return type(f"Isolated{factory.__name__}", (factory,), methods)


def isolate_encryption_locks(engine: Engine, schema: str) -> None:
    """Namespace native cursors before checkout, including async driver cursors.

    Keep the driver's cursor classes and row factories so native and SQLAlchemy
    callers share one transformation without replacing their connection objects.
    """

    def connected(raw: object, _record: object) -> None:
        connection = cast(_NativeConnection, getattr(raw, "driver_connection", raw))
        connection.cursor_factory = _cursor_factory(connection.cursor_factory, schema)
        connection.server_cursor_factory = _cursor_factory(connection.server_cursor_factory, schema)

    event.listen(engine, "connect", connected)
