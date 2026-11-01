"""Persisted reconciliation proposal, approval and revocation contracts.

The server records both authenticated identities. Request payloads accept only
proposal values or audit labels, never caller-selected authority identifiers.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReconciliationExceptionCreate(ClosedModel):
    """Propose a bounded exception. The server records the authenticated maker."""

    #: Why this breach is known, bounded and acceptable. Never blank.
    reason: str = Field(min_length=1, max_length=2000)
    #: The LARGEST breach this exception covers, as a fraction of the control's
    #: denominator. An exception acknowledges a bounded defect; it never
    #: disables the control, so a gap beyond the ceiling still blocks.
    max_gap_fraction: Decimal = Field(gt=0, le=1)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def require_ordered_window(self) -> ReconciliationExceptionCreate:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            msg = "effective_to cannot precede effective_from."
            raise ValueError(msg)
        return self


class ReconciliationExceptionApprove(ClosedModel):
    """An audit label, never authority. The checker identity comes from authentication."""

    approved_by: str = Field(min_length=1, max_length=120)


class ReconciliationExceptionRevoke(ClosedModel):
    """Close a live exception. The record is never deleted."""

    revoked_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class ReconciliationExceptionRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    bank_id: str
    control: str
    max_gap_fraction: Decimal
    effective_from: date
    effective_to: date | None
    reason: str
    requested_by: UUID | None
    requested_at: datetime
    status: str
    approved_by: str | None
    approved_by_user_id: UUID | None
    approval_timestamp: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None


class ReconciliationExceptionListRead(ClosedModel):
    bank_id: str
    control: str
    #: The live, un-revoked exception covering ``as_of``, if any — the one the
    #: filing gate would actually apply. Never inferred from the list: it is the
    #: service's own resolution, widest ceiling first.
    active_exception_id: UUID | None
    as_of: date
    exceptions: list[ReconciliationExceptionRead]
