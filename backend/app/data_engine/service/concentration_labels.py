"""Representative group labels for snapshot-backed reports."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only
from sqlalchemy.sql.elements import ColumnElement

from app.core.key_management.concentration_identity import counterparty_group_key
from app.core.key_management.presentation import LOCKED_TEXT, contains_placeholder
from app.core.tenancy import TenantContext
from app.data_engine.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)


def counterparty_group_labels(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *snapshot_conditions: ColumnElement[bool],
    group_keys: set[str],
) -> dict[str, str]:
    if not group_keys:
        return {}
    ranked = (
        select(
            CanonicalCounterparty.id,
            CanonicalCounterparty.concentration_group_blind_index.label("token"),
            func.row_number()
            .over(
                partition_by=CanonicalCounterparty.concentration_group_blind_index,
                order_by=(CanonicalPositionSnapshot.as_of_date.desc(), CanonicalCounterparty.id),
            )
            .label("rank"),
        )
        .join(
            CanonicalPositionSnapshot,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .where(
            CanonicalCounterparty.organization_id == ctx.organization_id,
            CanonicalCounterparty.bank_id == bank_id,
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(("accepted", "warning")),
            *snapshot_conditions,
        )
        .subquery()
    )
    representatives = db.execute(
        select(
            cast(ColumnElement[UUID], ranked.c.id),
            cast(ColumnElement[str | None], ranked.c.token),
        ).where(cast(ColumnElement[int], ranked.c.rank) == 1)
    ).tuples()
    ids = [
        record_id
        for record_id, token in representatives
        if token is not None and token in group_keys
    ]
    if not ids:
        return {}
    parties = db.scalars(
        select(CanonicalCounterparty)
        .where(
            CanonicalCounterparty.organization_id == ctx.organization_id,
            CanonicalCounterparty.bank_id == bank_id,
            CanonicalCounterparty.id.in_(ids),
        )
        .options(
            load_only(
                CanonicalCounterparty.id,
                CanonicalCounterparty.organization_id,
                CanonicalCounterparty.bank_id,
                CanonicalCounterparty.concentration_group_blind_index,
                CanonicalCounterparty.name,
                CanonicalCounterparty.group_reference,
                CanonicalCounterparty.attributes,
                raiseload=True,
            )
        )
    )
    return {
        party.concentration_group_blind_index: LOCKED_TEXT
        if contains_placeholder([party.name, party.group_reference, party.attributes])
        else counterparty_group_key(party.name, party.group_reference, party.attributes or {})
        for party in parties
        if party.concentration_group_blind_index is not None
    }
