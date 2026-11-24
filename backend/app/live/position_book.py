"""Accepted canonical position records and their official credit-source provenance."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import (
    ColumnElement,
    Double,
    Integer,
    Numeric,
    Text,
    and_,
    case,
    func,
    literal,
    null,
    or_,
    select,
    type_coerce,
)
from sqlalchemy import (
    cast as sql_cast,
)
from sqlalchemy.orm import Session

from app.core.key_management.fields import ProtectedField
from app.core.tenancy import TenantContext
from app.data_engine.public import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.identity.public import Bank

#: The validation statuses a filed figure may rest on. ``warning`` is included
#: because a warning is a flag on a row that was still accepted, not a rejection.
INCLUDED_VALIDATION_STATUSES: tuple[str, ...] = ("accepted", "warning")

#: Counterparty credit exposures whose source versions govern credit stress.
CREDIT_POSITION_TYPES: tuple[str, ...] = ("LOAN", "INTERBANK_PLACEMENT")


SourceRecord = tuple[
    CanonicalPositionSnapshot,
    CanonicalPosition,
    CanonicalCounterparty | None,
    CanonicalProduct | None,
]


def _current_book(ctx: TenantContext, bank: Bank, as_of: date) -> tuple[ColumnElement[bool], ...]:
    """The accepted, current-generation snapshots of ``bank``'s book at ``as_of``."""
    return (
        CanonicalPositionSnapshot.organization_id == ctx.organization_id,
        CanonicalPositionSnapshot.bank_id == bank.id,
        CanonicalPositionSnapshot.as_of_date == as_of,
        CanonicalPositionSnapshot.superseded_by.is_(None),
        CanonicalPositionSnapshot.withdrawn_at.is_(None),
        CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
    )


def load_position_records(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    position_types: tuple[str, ...] | None = None,
) -> list[SourceRecord]:
    return cast(
        list[SourceRecord],
        (
            db.execute(
                select(
                    CanonicalPositionSnapshot,
                    CanonicalPosition,
                    CanonicalCounterparty,
                    CanonicalProduct,
                )
                .join(
                    CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id
                )
                .outerjoin(
                    CanonicalCounterparty,
                    CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
                )
                .outerjoin(
                    CanonicalProduct,
                    CanonicalPositionSnapshot.product_id == CanonicalProduct.id,
                )
                .where(
                    *_current_book(ctx, bank, as_of),
                    *(
                        (CanonicalPosition.position_type.in_(position_types),)
                        if position_types is not None
                        else ()
                    ),
                )
                .order_by(CanonicalPositionSnapshot.source_reference)
            )
            .tuples()
            .all()
        ),
    )


@dataclass(frozen=True)
class PositionTypeTotal:
    """One position type's share of the book, summed by the database.

    ``stated`` is the sum of every row that states a reporting-currency amount,
    read exactly as ``fact_derivation._position_row`` reads it: the snapshot's
    ``attributes.balance_ghs``, or, when that is absent and the position is
    already in the reporting currency, its own ``balance`` (``0`` when that is
    blank). A foreign-currency row with no ``balance_ghs`` states nothing: it
    counts in ``rows`` and stays out of ``stated``.
    """

    rows: int
    stated_rows: int
    stated: Decimal


#: A JSON integer literal: read exactly, as Python's ``json`` reads it (an int).
_JSON_INTEGER = r"^-?[0-9]+$"
#: A decimal written as a JSON string, in the forms ``Decimal(str)`` and Postgres
#: ``numeric`` both read the same way. Anything else defers to the row-by-row path.
_DECIMAL_TEXT = r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$"


def stated_totals_by_type(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, base_currency: str
) -> dict[str, PositionTypeTotal] | None:
    """Per position type, how many rows the book holds and what they state, in one query.

    The balance-sheet identity needs only these totals, so the database adds them
    up and returns one row per position type instead of every position record.
    ``balance_ghs`` is read the way the row-by-row path reads it: a JSON integer
    exactly; any other JSON number through a double, as Python's ``json`` parses
    it (Postgres prints a double in the same shortest form Python does); a decimal
    string exactly. ``None`` when any row carries a ``balance_ghs`` the query
    cannot read that way (an object, a boolean, an unusual string), so the caller
    takes the row-by-row path and fails exactly as it always has; also ``None``
    off PostgreSQL (the hermetic SQLite suite), where the JSON functions differ.
    """
    if db.get_bind().dialect.name != "postgresql":
        return None
    key = literal("balance_ghs", Text())
    element = type_coerce(CanonicalPositionSnapshot.attributes.op("->")(key), Text())
    kind = type_coerce(func.json_typeof(element), Text())
    raw = type_coerce(CanonicalPositionSnapshot.attributes.op("->>")(key), Text())
    zero = literal(0, Numeric())
    in_base_currency = case(
        (
            CanonicalPosition.currency == base_currency,
            func.coalesce(CanonicalPositionSnapshot.balance, zero),
        ),
        else_=null(),
    )
    balance_ghs = case(
        (or_(kind.is_(None), kind == "null"), in_base_currency),
        (and_(kind == "string", raw == ""), in_base_currency),
        (and_(kind == "number", raw.regexp_match(_JSON_INTEGER)), sql_cast(raw, Numeric())),
        (kind == "number", sql_cast(sql_cast(sql_cast(raw, Double()), Text()), Numeric())),
        (and_(kind == "string", raw.regexp_match(_DECIMAL_TEXT)), sql_cast(raw, Numeric())),
        else_=null(),
    )
    unreadable = case(
        (kind.in_(("object", "array", "boolean")), 1),
        (and_(kind == "string", raw != "", ~raw.regexp_match(_DECIMAL_TEXT)), 1),
        else_=0,
    )
    stated_sum = type_coerce(func.coalesce(func.sum(balance_ghs), zero), Numeric())
    unread_sum = type_coerce(func.coalesce(func.sum(unreadable), 0), Integer())
    rows = db.execute(
        select(
            CanonicalPosition.position_type,
            func.count(),
            func.count(balance_ghs),
            stated_sum,
            unread_sum,
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(*_current_book(ctx, bank, as_of))
        .group_by(CanonicalPosition.position_type)
    ).tuples()
    totals: dict[str, PositionTypeTotal] = {}
    for position_type, count, stated_rows, stated, unread in rows:
        if unread:
            return None
        totals[position_type] = PositionTypeTotal(
            rows=count, stated_rows=stated_rows, stated=Decimal(stated)
        )
    return totals


def _source_value(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC).isoformat()
            if value.tzinfo is None
            else value.astimezone(UTC).isoformat()
        )
    return str(value)


def credit_source_basis(records: Sequence[SourceRecord]) -> str:
    """Serialize the exact accepted credit source records, including an empty book."""
    versions = [
        [
            None
            if model is None
            else {
                column.key: cast(object, getattr(model, column.key))
                for column in model.__table__.columns
                if column.key not in ("created_at", "updated_at", "ingested_at")
                and not isinstance(column.type, ProtectedField)
            }
            for model in record
        ]
        for record in records
        if record[1].position_type in CREDIT_POSITION_TYPES
    ]
    return json.dumps(
        sorted(
            versions, key=lambda version: json.dumps(version, sort_keys=True, default=_source_value)
        ),
        sort_keys=True,
        default=_source_value,
    )
