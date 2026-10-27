"""Keep encryption locks inside the disposable schema that owns a test's rows."""

from __future__ import annotations

import hashlib
from typing import cast

from sqlalchemy import Connection, Engine, event, text


def isolate_encryption_locks(engine: Engine) -> None:
    with engine.connect() as connection:
        schema = cast(str, connection.scalar(text("SELECT current_schema()")))

    def namespace_key(
        _connection: Connection,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> tuple[str, object]:
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
            hashlib.sha256(f"{schema}:{key}".encode()).digest()[:8], signed=True
        )
        return statement, {**values, "key": isolated}

    event.listen(engine, "before_cursor_execute", namespace_key, retval=True)
