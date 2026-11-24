"""Gapless counters, allocated inside the caller's transaction.

On Postgres the counter row is locked with ``SELECT … FOR UPDATE`` until commit; a
rollback returns the number, so a sequence never skips. SQLite serialises writers.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Counter


def next_value(session: Session, name: str) -> int:
    postgres = session.get_bind().dialect.name == "postgresql"
    if postgres:
        session.execute(
            pg_insert(Counter)
            .values(name=name, value=0)
            .on_conflict_do_nothing(index_elements=["name"])
        )
        counter = session.scalars(
            select(Counter).where(Counter.name == name).with_for_update()
        ).one()
    else:
        counter = session.get(Counter, name)
        if counter is None:
            counter = Counter(name=name, value=0)
            session.add(counter)
    counter.value += 1
    session.flush()
    return counter.value


def ensure_at_least(session: Session, name: str, floor: int) -> None:
    """Start a sequence at ``floor + 1`` (used when a numbering continues an earlier register)."""
    counter = session.get(Counter, name)
    if counter is None:
        session.add(Counter(name=name, value=floor))
    elif counter.value < floor:
        counter.value = floor
    session.flush()


def receipt_id(session: Session, at: datetime) -> str:
    return f"SRG-{at.year % 100:02d}-{next_value(session, f'receipt:{at.year}'):07d}"


def query_number(session: Session, at: datetime) -> str:
    return f"QRY-{at.year}-{next_value(session, f'query:{at.year}'):04d}"


def operator_session_number(session: Session, at: datetime) -> str:
    return f"SUP-{at.year}-{next_value(session, f'operator:{at.year}'):04d}"
