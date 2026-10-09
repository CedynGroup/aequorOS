"""Accepted canonical position records and their official credit-source provenance."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

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
                    CanonicalPositionSnapshot.organization_id == ctx.organization_id,
                    CanonicalPositionSnapshot.bank_id == bank.id,
                    CanonicalPositionSnapshot.as_of_date == as_of,
                    CanonicalPositionSnapshot.superseded_by.is_(None),
                    CanonicalPositionSnapshot.withdrawn_at.is_(None),
                    CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
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
