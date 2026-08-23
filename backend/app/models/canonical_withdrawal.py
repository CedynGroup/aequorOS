"""Governed withdrawal of a source system's canonical book for a business date.

The gap this closes
-------------------
The platform's diagnosis of duplicated source books ends with an instruction:
"withdraw the other system's data for this date". Nothing could perform it.
``superseded_by`` retires a row only by naming its REPLACEMENT, and a duplicated
book has no replacement — it should simply never have been counted. There was no
representation for "this record is gone", no batch status meaning withdrawn, and
the ``extraction_mode == 'full'`` omission rule that could have retired absent
rows reads GL accounts only. ``docs/data_engine.md`` §5.3 has required
"soft-delete markers in canonical" since it was written.

What a withdrawal is
--------------------
An explicit, maker-checker act with a mandatory reason, scoped to
``(source_system, as_of_date, entity[, position_type])`` for one bank. Approval
mints a real ``ingestion_batches`` row carrying a ``SUPERSESSION`` lineage node —
so a withdrawal is walkable in lineage exactly like an ingestion — and stamps
``withdrawn_at`` / ``withdrawn_by_batch_id`` / ``withdrawal_reason`` on every
current-generation canonical row in scope.

What it is NOT
--------------
* **Not supersession.** Supersession stays scoped per source system
  (``ingestion.py``), because a bank legitimately splits its book across systems
  and cross-source supersession would delete a real book.
* **Never automatic.** No heuristic, detector or job may retire data. The
  overlap detector is advisory and stays advisory; the register names the
  violation; a human requests and a second human approves.

Append-only
-----------
No row is deleted and no business field is rewritten — the marker is stamped
exactly as ``superseded_by`` is stamped today. A withdrawal is reversible, and
the reversal is another governed act: this record survives with
``status='reversed'``, both lineage nodes survive, and both audit events survive.
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
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin
from app.domain.ingestion.constants import POSITION_TYPES, SOURCE_SYSTEMS

#: The canonical entities a withdrawal may retire. Deliberately the BANK-BOOK
#: entities only — the tables whose rows are a bank's own reported balance sheet.
#:
#: Market-data canonicals (curves, FX, indices, ratings) carry the same mixin
#: columns but are vendor state written by ``pull_runner``; retiring a vendor
#: series is a market-data-adapter concern with its own supersession discipline,
#: and admitting it here would silently widen the blast radius of this feature.
#: ``tests/architecture/test_current_generation_predicate.py`` pins this set, so
#: extending it is a decision someone has to make on purpose.
#:
#: ``position`` retires POSITION SNAPSHOTS, not position identities: a withdrawal
#: is dated and a snapshot is the dated record, while ``canonical_positions`` is
#: a dateless identity shared by every business date. Withdrawing the identity
#: would orphan other dates' snapshots; withdrawing the snapshot removes the row
#: from every derivation for that date, which is exactly the remedy asked for.
WITHDRAWABLE_ENTITIES: tuple[str, ...] = (
    "position",
    "gl_account",
    "counterparty",
    "product",
)

#: ``pending`` — requested, nothing stamped. ``applied`` — approved and stamped.
#: ``reversed`` — a later governed act restored the rows. There is no ``rejected``
#: state: a request that should not proceed is left pending and revoked, so the
#: ask stays visible.
WITHDRAWAL_STATUSES: tuple[str, ...] = ("pending", "applied", "reversed")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class CanonicalWithdrawal(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One requested, approved and applied retirement of a source system's book."""

    __tablename__ = "canonical_withdrawals"
    __table_args__ = (
        CheckConstraint(
            f"entity IN ({_values(WITHDRAWABLE_ENTITIES)})",
            name="ck_canonical_withdrawals_entity",
        ),
        CheckConstraint(
            f"source_system IN ({_values(SOURCE_SYSTEMS)})",
            name="ck_canonical_withdrawals_source_system",
        ),
        CheckConstraint(
            f"status IN ({_values(WITHDRAWAL_STATUSES)})",
            name="ck_canonical_withdrawals_status",
        ),
        CheckConstraint(
            f"position_type IS NULL OR position_type IN ({_values(POSITION_TYPES)})",
            name="ck_canonical_withdrawals_position_type",
        ),
        # A withdrawal without a reason cannot exist, at any layer.
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_canonical_withdrawals_reason_present",
        ),
        # ...and neither can one that took effect without a named approver. This
        # is the database's half of "impossible to withdraw a book without a
        # reason and an approver"; the service layer additionally refuses
        # self-approval, which SQL cannot express.
        CheckConstraint(
            "status = 'pending' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_canonical_withdrawals_approver_present",
        ),
        CheckConstraint(
            "status <> 'applied' OR withdrawal_batch_id IS NOT NULL",
            name="ck_canonical_withdrawals_batch_present",
        ),
        CheckConstraint(
            "status <> 'reversed' OR "
            "(reversed_by IS NOT NULL AND reversed_at IS NOT NULL "
            "AND length(trim(coalesce(reversal_reason, ''))) > 0)",
            name="ck_canonical_withdrawals_reversal_evidence",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        Index(
            "ix_canonical_withdrawals_scope",
            "organization_id",
            "bank_id",
            "as_of_date",
            "source_system",
            "entity",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- scope --------------------------------------------------------------
    #: The system whose book is being retired.
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Narrows a ``position`` withdrawal to one type — the grain the duplicated-
    #: book diagnosis and the system-of-record register both work in, because a
    #: bank's answer is per type ("loans from FLEXCUBE, deposits from T24").
    #: NULL means the whole entity for that source system and date.
    position_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- justification ------------------------------------------------------
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: The approved ``system_of_record_declarations`` row this withdrawal
    #: enforces, when there is one. Optional: a bank may withdraw a mis-loaded
    #: batch with no register entry involved, and demanding a declaration for
    #: that would be governance theatre.
    declaration_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # --- maker-checker ------------------------------------------------------
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- effect -------------------------------------------------------------
    #: The ``ingestion_batches`` row minted at approval, carrying the
    #: ``SUPERSESSION`` lineage node. Every withdrawn canonical row points back
    #: at it through ``withdrawn_by_batch_id``.
    withdrawal_batch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    rows_withdrawn: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )

    # --- reversal (another governed act, never a rollback) ------------------
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reversed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reversal_batch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    rows_restored: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
