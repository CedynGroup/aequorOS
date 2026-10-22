"""Resumable per-bank operational-field migration; never rewrites audit history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import String, Table, inspect, select, text, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import InstanceState, Session

from app.core.key_management.fields import (
    field_columns,
    mark_for_backfill,
    needs_backfill,
)
from app.core.key_management.preflight import IMMUTABLE_FIELDS, require_clean_immutable_history
from app.core.key_management.registry import bank_key
from app.core.key_management.types import KeyIntegrityError, KeyUnavailableError
from app.db.base import Base


def _remove_derived_identity(db: Session, organization_id: str, bank_id: str) -> None:
    # Rebuildable copies, not the canonical identities. Keep opaque UUID joins
    # and numeric facts; BI explicitly refuses identity queries for this bank.
    for name in ("bi_dim_counterparty", "bi_fact_position_daily", "bi_fact_position_eom"):
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        values: dict[str, object] = {"counterparty_group": None}
        if name == "bi_dim_counterparty":
            values = {
                "name": sql_cast(table.c.counterparty_id, String),
                "source_reference": sql_cast(table.c.counterparty_id, String),
                "group_reference": None,
            }
        _ = db.execute(
            update(table)
            .where(
                table.c.organization_id == organization_id,
                table.c.bank_id == bank_id,
            )
            .values(**values)
        )


@dataclass(frozen=True)
class BackfillResult:
    checked: int
    encrypted: int
    batches: int


def backfill_bank(  # noqa: PLR0912, PLR0915 - one explicit maintenance transaction lifecycle
    db: Session, *, organization_id: str, bank_id: str, batch_size: int = 100
) -> BackfillResult:
    """Commit bounded batches; reruns authenticate and skip completed ciphertext.

    This maintenance entry point owns its transaction boundaries. Call it with a
    dedicated session after importing the application's model registry. An absent
    key or provider failure aborts loudly; completed prior batches stay recoverable.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if db.new or db.dirty or db.deleted:
        raise ValueError("Backfill requires a dedicated clean session.")
    previous_scope = cast(object, db.info.get("organization_id"))
    if previous_scope is not None and previous_scope != organization_id:
        raise KeyIntegrityError("Backfill session belongs to another organization.")
    db.info["organization_id"] = organization_id
    previous_backfill = cast(object, db.info.get("bank_field_backfill"))
    db.info["bank_field_backfill"] = True
    checked = encrypted = batches = 0
    try:
        # The caller may already have begun a clean transaction before setting
        # session.info; after_begin cannot retroactively install its RLS GUC.
        if db.get_bind().dialect.name == "postgresql":
            _ = db.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": organization_id},
            )
        if bank_key(db, organization_id, bank_id, writing=True) is None:
            raise KeyUnavailableError("Backfill requires a connected bank key.")
        require_clean_immutable_history(db, organization_id, bank_id)
        _remove_derived_identity(db, organization_id, bank_id)
        db.commit()
        for mapper in sorted(
            Base.registry.mappers, key=lambda item: cast(Table, item.local_table).name
        ):
            model = cast(type[Base], mapper.class_)
            state = cast(InstanceState[object], inspect(model()))
            columns = field_columns(state)
            if not columns:
                continue
            table = cast(Table, mapper.local_table)
            conditions = [table.c.organization_id == organization_id]
            parent = columns[0].kind.parent
            if parent is None:
                conditions.append(table.c.bank_id == bank_id)
            else:
                parent_name, parent_id = parent
                parent_table = Base.metadata.tables[parent_name]
                conditions.append(
                    table.c[parent_id].in_(
                        select(parent_table.c.id).where(
                            parent_table.c.organization_id == organization_id,
                            parent_table.c.bank_id == bank_id,
                        )
                    )
                )
            after: UUID | None = None
            while True:
                page_conditions = list(conditions)
                if after is not None:
                    page_conditions.append(table.c.id > after)
                statement = (
                    select(model)
                    .where(*page_conditions)
                    .order_by(table.c.id)
                    .limit(batch_size)
                    .execution_options(populate_existing=True)
                )
                immutable = table.name in {name for name, _ in IMMUTABLE_FIELDS}
                if not immutable:
                    statement = statement.with_for_update()
                page = list(db.scalars(statement))
                if not page:
                    break
                for item in page:
                    checked += 1
                    if needs_backfill(item):
                        if immutable:
                            raise KeyUnavailableError(
                                "Bank-key cutover refused: immutable history "
                                "contains legacy plaintext."
                            )
                        mark_for_backfill(item)
                        encrypted += 1
                after = cast(UUID, cast(InstanceState[object], inspect(page[-1])).dict["id"])
                db.commit()
                db.expunge_all()
                batches += 1
        db.commit()
        return BackfillResult(checked, encrypted, batches)
    except BaseException:
        db.rollback()
        raise
    finally:
        if previous_backfill is None:
            db.info.pop("bank_field_backfill", None)
        else:
            db.info["bank_field_backfill"] = previous_backfill
        if previous_scope is None:
            db.info.pop("organization_id", None)
        else:
            db.info["organization_id"] = previous_scope
