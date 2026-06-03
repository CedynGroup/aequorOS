from __future__ import annotations

from datetime import UTC, datetime
from secrets import randbits
from uuid import UUID, uuid4


def new_uuid4() -> UUID:
    return uuid4()


def new_uuid7() -> UUID:
    unix_ts_ms = int(datetime.now(UTC).timestamp() * 1000)
    if unix_ts_ms >= 1 << 48:
        raise ValueError("UUIDv7 timestamp exceeds 48 bits")

    uuid_int = unix_ts_ms << 80
    uuid_int |= 0x7 << 76
    uuid_int |= randbits(12) << 64
    uuid_int |= 0b10 << 62
    uuid_int |= randbits(62)
    return UUID(int=uuid_int)
