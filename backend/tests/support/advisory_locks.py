"""Keep encryption locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
from functools import partial
from typing import cast

from sqlalchemy import Connection, Engine, event


def _namespace_key(
    _connection: Connection,
    _cursor: object,
    statement: str,
    parameters: object,
    _context: object,
    _executemany: bool,
    *,
    schema: str,
) -> tuple[str, object]:
    namespace = f"-csearch_path={schema}"
    if statement not in {
        "SELECT pg_advisory_xact_lock(%(key)s)",
        "SELECT pg_advisory_xact_lock_shared(%(key)s)",
    }:
        return statement, parameters
    assert isinstance(parameters, dict)
    values = cast(dict[str, object], parameters)
    key = values["key"]
    assert isinstance(key, int)
    isolated = int.from_bytes(
        hashlib.sha256(f"{namespace}:{key}".encode()).digest()[:8], signed=True
    )
    return statement, {**values, "key": isolated}


def isolate_encryption_locks(engine: Engine, schema: str) -> None:
    """Apply the schema namespace to every encryption lock from this engine.

    Install on the owning engine, including async engines' sync facades, before
    checkout. The namespace depends only on the schema, so replacement pools and
    role/tenant variants still contend for the same test's protected rows.
    """
    event.listen(
        engine, "before_cursor_execute", partial(_namespace_key, schema=schema), retval=True
    )
