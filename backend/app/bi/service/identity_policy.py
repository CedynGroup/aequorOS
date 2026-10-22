"""Explicit numeric-only BI policy for connected-key banks (issue 595 follows up)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.bi.service.errors import BiQueryError
from app.core.key_management.models import BankEncryptionKey

IDENTITY_MEMBERS = frozenset(
    {
        "counterparty.id",
        "counterparty.name",
        "counterparty.source_reference",
        "counterparty.group",
    }
)
IDENTITY_REASON = "bank_key_identity_bi_unavailable"
IDENTITY_MESSAGE = (
    "This bank uses bank-held encryption. Identity dimensions and identity filters "
    "are unavailable in BI, including saved views. Numeric BI remains available. "
    "Remove the identity fields and filters to continue."
)


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
        error = BiQueryError(IDENTITY_MESSAGE, members=refused)
        error.code = IDENTITY_REASON
        raise error
