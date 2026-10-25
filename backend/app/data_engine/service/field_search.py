"""Exact identity lookups through bank-held blind indexes and scoped entity reads."""

from __future__ import annotations

from typing import Literal, cast

from sqlalchemy import JSON, select
from sqlalchemy.orm import Load, Session

from app.core.key_management.fields import blind_index, normalize
from app.core.tenancy import TenantContext
from app.models.canonical import CanonicalCounterparty


def find_counterparties(  # noqa: PLR0913 - explicit bank and identifier namespace
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    field: Literal["name", "source_reference", "external_identifier"],
    value: str,
    namespace: str | None = None,
) -> list[CanonicalCounterparty]:
    normalization = "name" if field == "name" else "identifier"
    if field == "external_identifier":
        if not namespace:
            raise ValueError("External identifier search requires a namespace.")
        domain = "counterparty.identifier:" + namespace
    else:
        domain = "counterparty." + field
    token = blind_index(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank_id,
        domain=domain,
        value=value,
        normalization=normalization,
    )
    if field == "name":
        predicate = CanonicalCounterparty.name_blind_index == token
    elif field == "source_reference":
        predicate = CanonicalCounterparty.source_reference_blind_index == token
    else:
        assert namespace is not None
        identifier = cast(
            JSON.Comparator[object],
            CanonicalCounterparty.external_identifiers_blind_indexes[namespace].comparator,
        )
        predicate = identifier.as_string() == token
    candidates = db.scalars(
        select(CanonicalCounterparty)
        .options(Load(CanonicalCounterparty).undefer_group("bank_identity"))
        .where(
            CanonicalCounterparty.organization_id == ctx.organization_id,
            CanonicalCounterparty.bank_id == bank_id,
            CanonicalCounterparty.superseded_by.is_(None),
            CanonicalCounterparty.withdrawn_at.is_(None),
            predicate,
        )
    )
    confirmed: list[CanonicalCounterparty] = []
    for candidate in candidates:
        if field == "name":
            actual = candidate.name
        elif field == "source_reference":
            actual = candidate.source_reference
        else:
            assert namespace is not None
            actual = candidate.external_identifiers.get(namespace)
        if isinstance(actual, str) and normalize(actual, normalization) == normalize(
            value, normalization
        ):
            confirmed.append(candidate)
    return confirmed
