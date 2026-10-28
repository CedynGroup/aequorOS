"""Keep encryption locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
import shlex
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
    options = connection.engine.url.query.get("options")
    if not isinstance(options, str):
        return statement, parameters
    namespace = ""
    tokens = iter(shlex.split(options))
    for token in tokens:
        setting = next(tokens, "") if token == "-c" else token.removeprefix("-c")
        if setting.startswith("search_path="):
            namespace = setting.removeprefix("search_path=")
    if not namespace.startswith("risk_service_test_") or not namespace.isidentifier():
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
