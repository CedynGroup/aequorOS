"""Reconciliation-control governance contracts (audit 2026-08-22 D-20).

``reconciliation.grant_exception`` / ``revoke_exception`` shipped complete,
maker-checker-enforced and audited — and reachable only from a test factory.
A tenant whose canonical book carries a known, bounded defect was therefore
blocked from every filing act with no way to record the approved exception
through the product; the only remedy was a database write.

These are the request/response shapes for that governance act. They are
deliberately verbose: an exception to a fail-closed control is a decision
somebody has to answer for, so the reason, the named approver, the ceiling and
the window are all required rather than defaulted.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReconciliationExceptionCreate(ClosedModel):
    """Record an approved exception to a fail-closed reconciliation control.

    Four-eyes is enforced in the service (``grant_exception``): the officer who
    requested the exception may not be the approver. ``approved_by_user_id`` is
    what makes that check possible, so supplying it is how a caller proves the
    second pair of eyes exists rather than asserting it.
    """

    #: Why this breach is known, bounded and acceptable. Never blank.
    reason: str = Field(min_length=1, max_length=2000)
    #: The named approver — an operator or a tenant officer. Free text because
    #: the audit trail shows the identity string, not a foreign key.
    approved_by: str = Field(min_length=1, max_length=120)
    #: The approver's user id, when they are a tenant user. Required for the
    #: self-approval refusal to be able to fire.
    approved_by_user_id: UUID | None = None
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
    approved_by: str
    approved_by_user_id: UUID | None
    approval_timestamp: datetime
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
