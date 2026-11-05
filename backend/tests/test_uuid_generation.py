"""Tests for test_uuid_generation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import RFC_4122, UUID

from app.core.ids import new_uuid4, new_uuid7


def uuid7_timestamp_ms(value: UUID) -> int:
    return value.int >> 80


def test_uuid_helpers_generate_expected_versions() -> None:
    before_ms = int(datetime.now(UTC).timestamp() * 1000)
    uuid4_value = new_uuid4()
    uuid7_value = new_uuid7()
    after_ms = int(datetime.now(UTC).timestamp() * 1000)

    assert uuid4_value.version == 4
    assert uuid4_value.variant == RFC_4122
    assert uuid7_value.version == 7
    assert uuid7_value.variant == RFC_4122
    assert before_ms <= uuid7_timestamp_ms(uuid7_value) <= after_ms
