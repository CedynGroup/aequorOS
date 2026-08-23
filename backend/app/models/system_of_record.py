"""The system-of-record register: which source system owns which book.

Why this exists
---------------
``app/services/reconciliation.py`` can *detect* that two source systems are each
pushing a complete book for the same positions at the same as-of, and it can
size the duplication. What it cannot do is say which of them is WRONG — so its
diagnosis ends in an instruction to a human ("confirm which system is the book
of record for each of these positions"), and every observation stays a heuristic.

This register is the declaration that answers it. Once a bank has declared, per
position type, which source system is its book of record, a second system's book
for that type stops being an anomaly and becomes a RULE VIOLATION with a named
owner: *this type arrived from a system that is not its declared book of record*.

Deliberately NOT a regulatory parameter
---------------------------------------
It lives in the TENANT plane (RLS-forced, ``banks`` FK), not the operator control
plane, because it is a fact about one institution's own IT estate — "our loans
come from FLEXCUBE, our deposits from T24" — not a regulatory number that must be
uniform across tenants. ``regulatory_parameter`` is global, unscoped by bank, and
resolved by institution class; there is no key on it that could carry a per-bank
per-position-type answer without inventing a tenant discriminator on a global
table. What it DOES contribute is its governance shape, mirrored here in full:
effective-dated, four-eyed (approver ≠ proposer), ``source_citation`` NOT NULL,
``confirmation_status`` defaulting to ``pending``, and audited on every mutation.

Absence is not a violation
--------------------------
A bank whose entire book arrives from one system has nothing to declare and is
never asked to. The register is consulted only for CONTESTED position types —
those where ``reconciliation.detect_source_overlap`` finds two systems materially
delivering the same type — so a single-source bank produces an empty contested
set, resolves nothing, and is never blocked. See
``app/services/system_of_record.py::assess``.

Effective-dated because migration is legitimate
-----------------------------------------------
A bank moving from one core to another runs both systems during cutover, and
BOTH books are real. The register handles that by dating the answer: legacy core
is the book of record until the cutover date, the new core from it. Resolution is
by as-of date, so the same bank's June and October filings can name different
authoritative systems for the same type. Nothing auto-resolves and nothing
auto-withdraws — the register names the violation; a human, under maker-checker,
performs the withdrawal (``app/services/canonical_withdrawal.py``).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin
from app.domain.ingestion.constants import POSITION_TYPES, SOURCE_SYSTEMS

#: Maker-checker lifecycle, identical in meaning to ``regulatory_parameter``:
#: ``draft`` rows are invisible to resolution; only ``approved`` rows participate.
DECLARATION_STATUSES: tuple[str, ...] = ("draft", "approved")

#: Whether the bank has CONFIRMED this is its book of record, or the platform
#: recorded a documented working assumption during onboarding. A ``pending``
#: declaration still resolves — it is a stated answer — but every assessment it
#: drives says so, exactly as a ``pending`` regulatory parameter does.
DECLARATION_CONFIRMATION_STATUSES: tuple[str, ...] = ("confirmed", "pending")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class SystemOfRecordDeclaration(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One bank's authoritative source system for one position type, dated."""

    __tablename__ = "system_of_record_declarations"
    __table_args__ = (
        CheckConstraint(
            f"position_type IN ({_values(POSITION_TYPES)})",
            name="ck_system_of_record_declarations_position_type",
        ),
        CheckConstraint(
            f"source_system IN ({_values(SOURCE_SYSTEMS)})",
            name="ck_system_of_record_declarations_source_system",
        ),
        CheckConstraint(
            f"status IN ({_values(DECLARATION_STATUSES)})",
            name="ck_system_of_record_declarations_status",
        ),
        CheckConstraint(
            f"confirmation_status IN ({_values(DECLARATION_CONFIRMATION_STATUSES)})",
            name="ck_system_of_record_declarations_confirmation",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_system_of_record_declarations_window",
        ),
        CheckConstraint(
            "length(trim(source_citation)) > 0",
            name="ck_system_of_record_declarations_citation_present",
        ),
        CheckConstraint(
            "length(trim(rationale)) > 0",
            name="ck_system_of_record_declarations_rationale_present",
        ),
        # The database's half of four-eyes: an approved declaration without a
        # named approver and an approval timestamp cannot exist, whatever a
        # future service path forgets to check.
        CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_system_of_record_declarations_approver_present",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "position_type",
            "effective_from",
            name="uq_system_of_record_declarations_generation",
        ),
        Index(
            "ix_system_of_record_declarations_resolution",
            "organization_id",
            "bank_id",
            "position_type",
            "effective_from",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- the resolution key -------------------------------------------------
    position_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The declared book of record for this type over this window.
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)

    # --- effective dating (the migration-cutover mechanism) -----------------
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Open-ended when null; set to a successor's ``effective_from`` on approval.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- evidence -----------------------------------------------------------
    #: What the bank pointed at when it said so — an IT sign-off, a data-owner
    #: memo, a migration runbook. NOT NULL for the same reason
    #: ``regulatory_parameter.source_citation`` is: a governed declaration with
    #: no cited authority is an opinion.
    source_citation: Mapped[str] = mapped_column(String(240), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="pending"
    )

    # --- maker-checker ------------------------------------------------------
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    #: Free text like ``reconciliation_exceptions.approved_by``: the identity
    #: string the audit trail shows. The UUID columns carry the linkable actor.
    proposed_by: Mapped[str] = mapped_column(String(120), nullable=False)
    proposed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- revocation (a wrong declaration is closed, never deleted) ----------
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
