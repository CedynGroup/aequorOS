"""Postgres partition lifecycle for the BI marts — a thin wrapper, no DDL of its own.

Migration ``202609220066`` owns the partitioned parents and the four
``SECURITY DEFINER`` functions that are the ONLY sanctioned way any role
creates or drops a child (D-039): ``bi_ensure_month_partition`` /
``bi_ensure_year_partition`` (create the child with ENABLE + FORCE RLS and the
tenant policy, idempotent) and ``bi_drop_month_partition`` /
``bi_drop_year_partition``. This module calls them through SQLAlchemy Core —
no ``text()`` anywhere in the BI plane — and is a no-op wherever the parents
are not partitioned: SQLite (the hermetic suite, the Playwright stack) and a
Postgres schema built with ``create_all`` (plain tables, D-011). It never
names ``bi_fact_position_eom`` in a drop.

Why the builder ensures partitions BEFORE its slice transaction, and commits
the DDL on its own: ``CREATE TABLE … PARTITION OF`` takes an ACCESS EXCLUSIVE
lock on the parent, which a long build transaction would then hold against
every BI read and every other tenant's build for that month. A child that
already exists costs one catalogue lookup and no lock. And a month whose rows
already sit in the DEFAULT partition cannot get a child at all (the migration
documents the conflict), which is why the ensure runs before the first row of
a slice is written.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import Column, MetaData, String, Table, cast, func, literal, select
from sqlalchemy.dialects.postgresql import OID, REGCLASS
from sqlalchemy.orm import Session

from app.models.bi import (
    BiAggPositionDaily,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
)

#: The monthly parents the BUILDER writes (``bi_query_log`` is the query path's).
MONTHLY_BUILD_PARENTS: tuple[str, ...] = (
    BiFactPositionDaily.__tablename__,
    BiAggPositionDaily.__tablename__,
    BiFactLoanEvent.__tablename__,
)
YEARLY_BUILD_PARENTS: tuple[str, ...] = (BiFactPositionEom.__tablename__,)

#: The child name the definer functions derive: ``<parent>_y2026m09`` / ``<parent>_y2026``.
_MONTH_CHILD = re.compile(r"^(?P<parent>.+)_y(?P<year>\d{4})m(?P<month>\d{2})$")

_catalog = MetaData()
_PG_CLASS = Table(
    "pg_class",
    _catalog,
    Column("oid", OID),
    Column("relname", String),
    Column("relkind", String),
    schema="pg_catalog",
)
_PG_INHERITS = Table(
    "pg_inherits",
    _catalog,
    Column("inhrelid", OID),
    Column("inhparent", OID),
    schema="pg_catalog",
)


def is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def _regclass(name: str):  # noqa: ANN202 - a SQLAlchemy expression
    return cast(literal(name), REGCLASS)


def is_partitioned(db: Session, parent: str) -> bool:
    """Whether ``parent`` is a partitioned table here (``relkind = 'p'``).

    False on SQLite and on a Postgres schema built by ``create_all`` (plain
    tables), which is how a build on either runs without the definer functions.
    """
    if not is_postgres(db):
        return False
    relkind = db.scalar(select(_PG_CLASS.c.relkind).where(_PG_CLASS.c.oid == _regclass(parent)))
    return relkind == "p"


def month_children(db: Session, parent: str) -> list[tuple[str, date]]:
    """``(child name, month start)`` for every month child of ``parent``.

    Read from ``pg_inherits`` so the list is what the database holds, not what
    a builder remembers; the DEFAULT partition has no month suffix and is
    never returned.
    """
    rows = db.execute(
        select(_PG_CLASS.c.relname)
        .join(_PG_INHERITS, _PG_INHERITS.c.inhrelid == _PG_CLASS.c.oid)
        .where(_PG_INHERITS.c.inhparent == _regclass(parent))
        .order_by(_PG_CLASS.c.relname)
    ).scalars()
    children: list[tuple[str, date]] = []
    for name in rows:
        match = _MONTH_CHILD.match(str(name))
        if match is None or match.group("parent") != parent:
            continue
        children.append((str(name), date(int(match.group("year")), int(match.group("month")), 1)))
    return children


def ensure_month_partition(db: Session, parent: str, month: date) -> str | None:
    """Create ``parent``'s child for ``month`` if absent; the child's name, or
    ``None`` when the parent is not partitioned here."""
    if not is_partitioned(db, parent):
        return None
    created = db.scalar(
        select(func.bi_ensure_month_partition(_regclass(parent), literal(month.replace(day=1))))
    )
    return str(created) if created is not None else None


def ensure_year_partition(db: Session, parent: str, year: date) -> str | None:
    if not is_partitioned(db, parent):
        return None
    created = db.scalar(
        select(func.bi_ensure_year_partition(_regclass(parent), literal(year.replace(day=1))))
    )
    return str(created) if created is not None else None


def drop_month_partition(db: Session, parent: str, month: date) -> bool:
    """Drop ``parent``'s child for ``month`` through the definer; False when absent."""
    if not is_partitioned(db, parent):
        return False
    dropped = db.scalar(
        select(func.bi_drop_month_partition(_regclass(parent), literal(month.replace(day=1))))
    )
    return bool(dropped)


def ensure_for_build(db: Session, *, as_of: date) -> list[str]:
    """Every partition a build for ``as_of`` will write into; the names ensured.

    The daily fact, the daily aggregate and the loan-event fact take the month
    of ``as_of`` (an event slice is the events dated ``as_of``); the month-end
    fact takes its year. Empty on any database without partitioned parents.
    """
    ensured: list[str] = []
    for parent in MONTHLY_BUILD_PARENTS:
        child = ensure_month_partition(db, parent, as_of)
        if child is not None:
            ensured.append(child)
    for parent in YEARLY_BUILD_PARENTS:
        child = ensure_year_partition(db, parent, as_of.replace(month=1, day=1))
        if child is not None:
            ensured.append(child)
    return ensured


__all__ = [
    "MONTHLY_BUILD_PARENTS",
    "YEARLY_BUILD_PARENTS",
    "drop_month_partition",
    "ensure_for_build",
    "ensure_month_partition",
    "ensure_year_partition",
    "is_partitioned",
    "is_postgres",
    "month_children",
]
