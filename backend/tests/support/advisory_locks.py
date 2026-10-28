"""Keep encryption locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
from typing import cast

from sqlalchemy import Connection, Engine, event


def _namespace_key(
    connection: Connection,
    _cursor: object,
    statement: str,
    parameters: object,
    _context: object,
    _executemany: bool,
) -> tuple[str, object]:
    namespace = connection.engine.url.query.get("options")
    if not isinstance(namespace, str) or not namespace.startswith(
        "-csearch_path=risk_service_test_"
    ):
        return statement, parameters
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


def isolate_encryption_locks() -> None:
    # Fixtures clear the engine cache; replacement engines must use the same
    # namespace as the original engine for that disposable schema.
    if not event.contains(Engine, "before_cursor_execute", _namespace_key):
        event.listen(Engine, "before_cursor_execute", _namespace_key, retval=True)
