from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import SchemaItem

from app.core.ids import new_uuid4, new_uuid7


def utc_now() -> datetime:
    return datetime.now(UTC)


#: Each model's flush-order key, by class name: ``"module.ClassName"`` as of the
#: module the model lived in when the key was recorded.
#:
#: A session flushes the rows of models that share no ``relationship()`` (none in
#: this codebase do) in the order of their mappers' keys, which SQLAlchemy derives
#: from the class's module. A flush that adds an organization and its bank works
#: only because that order puts the parent first, so moving a model to another
#: module would silently reorder flushes and could break a foreign key. Pinning the
#: key keeps flush order independent of where the code lives. A new model records
#: its key here; ``tests/architecture/test_model_flush_order.py`` enforces it.
FLUSH_ORDER = cast(
    dict[str, str],
    json.loads(Path(__file__).with_name("flush_order.json").read_text(encoding="utf-8")),
)

#: A model's ``__table_args__``: its constraints and indexes. ``DeclarativeBase``
#: types the attribute as ``Any``; annotating it keeps a model strict.
type TableArgs = tuple[SchemaItem, ...]


class Base(DeclarativeBase):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        mapper = cls.__dict__.get("__mapper__")
        if mapper is not None and cls.__name__ in FLUSH_ORDER:
            mapper._sort_key = FLUSH_ORDER[cls.__name__]


class UuidV4PrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid4,
    )


class UuidV7PrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid7,
    )


UuidPrimaryKeyMixin = UuidV4PrimaryKeyMixin


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
