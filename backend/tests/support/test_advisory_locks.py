"""Unit coverage for the advisory-lock statement matcher in ``tests.support``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from tests.support.advisory_locks import _isolate

NAMESPACED: list[tuple[str | bytes, str]] = [
    ("SELECT pg_advisory_xact_lock(%(key)s)", "key"),
    ("SELECT pg_advisory_xact_lock_shared(%(key)s)", "key"),
    (
        "SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s) AS pg_advisory_xact_lock_1",
        "pg_advisory_xact_lock_2",
    ),
    (
        "SELECT pg_advisory_xact_lock_shared(%(pg_advisory_xact_lock_shared_2)s)"
        " AS pg_advisory_xact_lock_shared_1",
        "pg_advisory_xact_lock_shared_2",
    ),
    (
        "SELECT pg_advisory_lock(%(pg_advisory_lock_2)s) AS pg_advisory_lock_1",
        "pg_advisory_lock_2",
    ),
    (
        "SELECT pg_advisory_unlock(%(pg_advisory_unlock_2)s) AS pg_advisory_unlock_1",
        "pg_advisory_unlock_2",
    ),
    (
        "SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s::INTEGER)"
        " AS pg_advisory_xact_lock_1",
        "pg_advisory_xact_lock_2",
    ),
    (b"SELECT pg_advisory_xact_lock(%(key)s)", "key"),
]

PROBES: list[tuple[str, dict[str, int] | None]] = [
    ("SELECT pg_advisory_xact_lock(%(probe_key)s)", {"probe_key": 456}),
    ("SELECT pg_try_advisory_xact_lock(%(probe_key)s)", {"probe_key": 456}),
    (
        "SELECT pg_advisory_xact_lock(%(class_id)s, %(object_id)s)",
        {"class_id": 12, "object_id": 34},
    ),
    ("SELECT pg_advisory_lock(4130901)", None),
]


@pytest.mark.parametrize(("statement", "name"), NAMESPACED)
def test_single_parameter_locks_are_namespaced_per_schema(
    statement: str | bytes, name: str
) -> None:
    first_query, first = _isolate(statement, {name: 123}, "schema_one", None)
    assert first == _isolate(first_query, {name: 123}, "schema_one", None)[1]
    assert first != {name: 123}
    assert first != _isolate(statement, {name: 123}, "schema_two", None)[1]


def test_integer_bind_cast_is_widened_for_the_namespaced_key() -> None:
    statement = (
        "SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s::INTEGER)"
        " AS pg_advisory_xact_lock_1"
    )
    query, parameters = _isolate(statement, {"pg_advisory_xact_lock_2": 123}, "schema_one", None)
    assert query == (
        "SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s::BIGINT)"
        " AS pg_advisory_xact_lock_1"
    )
    values = cast("Mapping[str, int]", parameters)
    assert values["pg_advisory_xact_lock_2"] != 123


@pytest.mark.parametrize(("statement", "parameters"), PROBES)
def test_probes_and_other_sql_keep_the_original_identity(
    statement: str, parameters: dict[str, int] | None
) -> None:
    query, isolated = _isolate(statement, parameters, "schema_one", None)
    assert query is statement
    assert isolated is parameters
