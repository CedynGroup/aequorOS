"""Explicit numeric-only BI policy for connected-key banks (issue 595 follows up)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.bi.domain.catalogue import Catalogue
from app.bi.domain.identity import IDENTITY_MEMBERS
from app.bi.schemas.query import BiQuery
from app.bi.service.errors import BiQueryError
from app.core.key_management.models import BankEncryptionKey

IDENTITY_REASON = "bank_key_identity_bi_unavailable"
IDENTITY_MESSAGE = (
    "This bank uses bank-held encryption. Identity dimensions and identity filters "
    "are unavailable in BI, including saved views. Numeric BI remains available. "
    "Remove the identity fields and filters to continue."
)


def query_identity_members(cat: Catalogue, query: BiQuery) -> tuple[str, ...]:
    """Identity requested in results/filters, not opaque internal aggregation.

    A numeric concentration measure may group opaque UUIDs internally. It emits
    no identity, so it remains available subject to its usual authorization.
    """
    roots = [
        *query.measures,
        *query.dimensions,
        *(item.member for item in query.filters),
        *(item.member for item in query.sort),
    ]
    if query.pivot is not None:
        roots.append(query.pivot.dimension)
    if query.top_n is not None:
        roots.append(query.top_n.dimension)
    hierarchies = {hierarchy.id: hierarchy.levels for hierarchy in cat.hierarchies()}
    return tuple(
        dict.fromkeys(
            member
            for root in roots
            for member in hierarchies.get(root, (root,))
            if member in IDENTITY_MEMBERS
        )
    )


class IdentityBiUnavailable(BiQueryError):
    status_code = 403
    code = IDENTITY_REASON

    def __init__(self, members: Iterable[str]) -> None:
        super().__init__(IDENTITY_MESSAGE, members=tuple(members))


def connected(db: Session, organization_id: str, bank_id: str) -> bool:
    """Policy depends on registration, including when its provider is unavailable."""
    return bool(
        db.scalar(
            select(
                exists().where(
                    BankEncryptionKey.organization_id == organization_id,
                    BankEncryptionKey.bank_id == bank_id,
                )
            )
        )
    )


def refused_identity(
    db: Session, organization_id: str, bank_id: str, members: Iterable[str]
) -> tuple[str, ...]:
    refused = tuple(dict.fromkeys(member for member in members if member in IDENTITY_MEMBERS))
    return refused if refused and connected(db, organization_id, bank_id) else ()


def require_numeric_bi(
    db: Session, organization_id: str, bank_id: str, members: Iterable[str]
) -> None:
    refused = refused_identity(db, organization_id, bank_id, members)
    if refused:
        raise IdentityBiUnavailable(refused)
