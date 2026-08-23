"""Governed exceptions to a fail-closed data-integrity control (audit P0-10).

The reconciliation controls (``app/services/reconciliation.py``) BLOCK the
official/filing plane when a bank's ingested book fails a material integrity
test — today the balance-sheet identity ``Assets = Liabilities + Equity``.

A hard block with no escape valve gets worked around in production (a bank
edits the upload until the check passes, which is worse than a recorded
exception), so the control ships with an explicit, governed escape valve. An
exception is:

* **scoped** — one control, one bank, and a ceiling on how large a breach it
  covers (never a blank cheque);
* **effective-dated** — ``effective_from`` / ``effective_to``, so it expires;
* **four-eyed** — ``requested_by`` records who asked and why; ``approved_by`` /
  ``approval_timestamp`` record who allowed it. The service layer refuses an
  approval by the requester;
* **revocable** — ``revoked_at`` closes it without deleting the record;
* **audited** — every grant, use and revocation writes an ``audit_events`` row
  (which is append-only by DB trigger).

Tenant-scoped and RLS-forced like every other table carrying a bank's data.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin

#: The controls an exception may cover. Extend deliberately — every value here
#: is a place where a material data-integrity failure can be allowed to file.
RECONCILIATION_CONTROLS: tuple[str, ...] = ("balance_sheet_identity",)


class ReconciliationException(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_exceptions"
    __table_args__ = (
        CheckConstraint(
            "control IN ('balance_sheet_identity')",
            name="ck_reconciliation_exceptions_control",
        ),
        CheckConstraint(
            "max_gap_fraction > 0",
            name="ck_reconciliation_exceptions_max_gap_positive",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_reconciliation_exceptions_window",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_reconciliation_exceptions_reason_present",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        Index(
            "ix_reconciliation_exceptions_lookup",
            "organization_id",
            "bank_id",
            "control",
            "effective_from",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    control: Mapped[str] = mapped_column(String(48), nullable=False)
    #: The largest breach this exception covers, as a fraction of the control's
    #: denominator (total assets for the balance-sheet identity). A gap beyond
    #: it blocks even while the exception is live — an exception acknowledges a
    #: known, bounded data defect, it does not disable the control.
    max_gap_fraction: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Free text, not a FK: the approver may be an operator (control plane) or a
    #: tenant officer, and the identity string is what the audit trail shows.
    approved_by: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approval_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
