"""Index the de-duplication probe on ``desk_source_captures``.

The nightly market-desk capture job re-fetches the same BoG/GFIM artifacts
every night, and every re-fetch stored a second inline base64 copy of bytes
already on the table. ``_persist_capture`` now asks, once per artifact, "has
this source already stored these exact bytes?" — a lookup on
``(source_key, content_sha256)``.

The existing index is ``(source_key, as_of_date)``, which cannot serve that
probe. Without this one the lookup is a sequential scan whose cost grows with
the table it exists to stop growing.

Index only. No row is read, written or rewritten: the captures already on the
table keep their payloads exactly as harvested, and de-duplication applies to
future writes alone.

Revision ID: 202608230043
Revises: 202608230040
"""

from __future__ import annotations

from alembic import op

revision = "202608230043"
down_revision = "202608230040"
branch_labels = None
depends_on = None

_TABLE = "desk_source_captures"
_INDEX = "ix_desk_source_captures_source_key_sha256"


def upgrade() -> None:
    op.create_index(_INDEX, _TABLE, ["source_key", "content_sha256"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
