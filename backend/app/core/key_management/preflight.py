"""Refuse bank-key cutover over legacy immutable plaintext; never bypass guards."""

from __future__ import annotations

from typing import cast

from sqlalchemy import JSON, select, type_coerce
from sqlalchemy.engine import ScalarResult
from sqlalchemy.orm import Session

from app.core.key_management.types import FIELD_FORMAT, KeyUnavailableError
from app.db.base import Base

IMMUTABLE_FIELDS = (("bi_dashboard_versions", "spec"), ("regulatory_packages", "snapshot"))


def require_clean_immutable_history(db: Session, organization_id: str, bank_id: str) -> None:
    """Check raw storage before key registration or any backfill mutations.

    Whole documents are protected: do not guess whether an arbitrary nested
    field is an identity.
    """
    with db.no_autoflush:
        for name, column in IMMUTABLE_FIELDS:
            table = Base.metadata.tables.get(name)
            if table is None:
                continue
            values = cast(
                ScalarResult[object],
                db.scalars(
                    select(type_coerce(table.c[column], JSON))
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.bank_id == bank_id,
                    )
                    .execution_options(yield_per=100)
                ),
            )
            for raw in values:
                value = raw
                if isinstance(value, dict) and FIELD_FORMAT in value:
                    continue
                raise KeyUnavailableError(
                    f"Bank-key cutover refused: legacy plaintext in {name}.{column}. "
                    "Immutable history requires a separately approved storage transition; "
                    "existing content guards remain unchanged."
                )
