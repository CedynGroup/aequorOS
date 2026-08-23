"""Contracts for the system-of-record register and canonical withdrawal."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.ingestion.constants import PositionType, SourceSystem

DeclarationStatus = Literal["draft", "approved"]
ConfirmationStatus = Literal["confirmed", "pending"]
WithdrawalStatus = Literal["pending", "applied", "reversed"]
WithdrawableEntity = Literal["position", "gl_account", "counterparty", "product"]
TypeFindingKind = Literal["undeclared", "violated"]


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class SystemOfRecordDeclarationCreate(BaseModel):
    """Propose which source system owns a position type from a date.

    ``source_citation`` is mandatory for the same reason it is on a regulatory
    parameter: a governed declaration with no cited authority is an opinion.
    """

    position_type: PositionType
    source_system: SourceSystem
    effective_from: date
    source_citation: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=2000)
    proposed_by: str = Field(min_length=1, max_length=120)
    confirmation_status: ConfirmationStatus = "pending"


class SystemOfRecordApproveRequest(BaseModel):
    """Checker step. The approver must not be the proposer."""

    approved_by: str = Field(min_length=1, max_length=120)


class SystemOfRecordRevokeRequest(BaseModel):
    revoked_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class SystemOfRecordDeclarationRead(BaseModel):
    id: UUID
    bank_id: str
    position_type: str
    source_system: str
    effective_from: date
    effective_to: date | None
    source_citation: str
    rationale: str
    confirmation_status: str
    status: str
    proposed_by: str
    proposed_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    revocation_reason: str | None


class SystemOfRecordRegisterRead(BaseModel):
    bank_id: str
    declarations: list[SystemOfRecordDeclarationRead]


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


class SourceBookRead(BaseModel):
    source_system: str
    rows: int
    total: Decimal


class TypeFindingRead(BaseModel):
    position_type: str
    finding: TypeFindingKind
    declared_source_system: str | None
    declaration_id: UUID | None
    declaration_confirmation_status: str | None
    offending_rows: int
    offending_total: Decimal
    books: list[SourceBookRead]


class SystemOfRecordAssessmentRead(BaseModel):
    """What the register decides about the bank's book at one date.

    ``clean`` is true for a bank with one source system, and for one whose
    systems partition the book properly. Neither is asked to declare anything —
    the register is consulted only for CONTESTED types.
    """

    bank_id: str
    as_of_date: date
    clean: bool
    contested_types: int
    findings: list[TypeFindingRead]
    message: str | None


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


class CanonicalWithdrawalCreate(BaseModel):
    """Request the retirement of one source system's book for one date.

    Nothing is retired by this call: it records a PENDING request that a second
    officer must approve.
    """

    entity: WithdrawableEntity
    source_system: SourceSystem
    as_of_date: date
    reason: str = Field(min_length=1, max_length=2000)
    requested_by: str = Field(min_length=1, max_length=120)
    #: Narrows a ``position`` withdrawal to one type — the grain the register
    #: and the duplicated-book diagnosis both work in.
    position_type: PositionType | None = None
    #: The approved declaration this withdrawal enforces, when there is one.
    declaration_id: UUID | None = None


class CanonicalWithdrawalApproveRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)


class CanonicalWithdrawalReverseRequest(BaseModel):
    reversed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class CanonicalWithdrawalRead(BaseModel):
    id: UUID
    bank_id: str
    entity: str
    source_system: str
    as_of_date: date
    position_type: str | None
    reason: str
    declaration_id: UUID | None
    status: str
    requested_by: str
    requested_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    withdrawal_batch_id: UUID | None
    rows_withdrawn: int
    reversed_at: datetime | None
    reversed_by: str | None
    reversal_reason: str | None
    reversal_batch_id: UUID | None
    rows_restored: int


class CanonicalWithdrawalListRead(BaseModel):
    bank_id: str
    withdrawals: list[CanonicalWithdrawalRead]


class CanonicalWithdrawalScopeRead(BaseModel):
    """How many current records a proposed withdrawal would retire."""

    bank_id: str
    entity: str
    source_system: str
    as_of_date: date
    position_type: str | None
    rows_in_scope: int
