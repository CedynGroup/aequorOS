"""Resolution layer: the system-of-record register and what it decides.

``app/services/reconciliation.py`` DETECTS that two source systems are each
pushing a book for the same positions at the same as-of. It cannot say which one
is wrong, so its verdict ends in an instruction to a human. This module holds the
answer to that instruction — a governed, effective-dated, four-eyed declaration
of which source system is the book of record for each position type — and turns
the detector's heuristic into a named rule violation.

Three properties are load-bearing and each is deliberate:

**Absence never blocks.** :func:`assess` reads the register ONLY for position
types the detector already found contested. A bank whose whole book arrives from
one system has an empty contested set, so it is asked for nothing, resolves
nothing, and is never impeded. A register that demanded a declaration from every
bank would be worse than no register at all.

**Nothing auto-resolves.** The assessment names the offending books and the
remedy; it applies neither. Withdrawal is a separate, explicitly requested,
separately approved act (``app/services/canonical_withdrawal.py``). This is not
timidity: during a core-banking migration BOTH books are real, and a rule that
retired the non-declared one on sight would delete live data.

**The assessment is advisory.** Like the overlap diagnosis it consumes, it
returns ``advisory=True`` outcomes and blocks no filing. The balance-sheet
identity control remains the gate; this is the explanation and the accountability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.authority.outcomes import OutcomeDetail, OutcomeState
from app.domain.authority.outcomes import outcome as build_outcome
from app.models import Bank, SystemOfRecordDeclaration
from app.services import reconciliation
from app.services.audit import record_event

#: Metric id for the register's outcomes — distinct from the detector's
#: ``source_overlap`` so a consumer can tell "two books exist" from "two books
#: exist and one of them is not the declared book of record".
SYSTEM_OF_RECORD_METRIC_ID = "system_of_record"

DECLARED_EVENT = "system_of_record.declared"
APPROVED_EVENT = "system_of_record.approved"
REVOKED_EVENT = "system_of_record.revoked"

#: A contested type with no approved declaration covering the as-of date. The
#: platform can size the duplication but cannot attribute it.
FINDING_UNDECLARED = "undeclared"
#: A contested type WITH a declaration, where at least one book arrived from a
#: system that is not the declared book of record. This is the rule violation the
#: register exists to produce.
FINDING_VIOLATED = "violated"

_ZERO = Decimal("0")


class SystemOfRecordError(HTTPException):
    """A refused register mutation, surfaced to the API as a precise status."""


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeFinding:
    """The register's verdict on one contested position type."""

    position_type: str
    finding: str
    #: The declared book of record, when one is declared for this as-of.
    declared_source_system: str | None
    declaration_id: UUID | None
    declaration_confirmation_status: str | None
    #: Books that arrived from a system other than the declared one. For an
    #: undeclared contest this is empty — nothing is a violation until somebody
    #: has said what right looks like.
    offending: tuple[reconciliation.SourceBook, ...]
    #: Every book for this type, largest first (the detector's ordering).
    books: tuple[reconciliation.SourceBook, ...]

    @property
    def offending_total(self) -> Decimal:
        return sum((book.total for book in self.offending), _ZERO)

    @property
    def offending_rows(self) -> int:
        return sum(book.rows for book in self.offending)

    def provenance(self) -> dict[str, Any]:
        return {
            "position_type": self.position_type,
            "finding": self.finding,
            "declared_source_system": self.declared_source_system,
            "declaration_id": str(self.declaration_id) if self.declaration_id else None,
            "declaration_confirmation_status": self.declaration_confirmation_status,
            "offending_rows": self.offending_rows,
            "offending_total": str(self.offending_total),
            "books": [book.provenance() for book in self.books],
        }


@dataclass(frozen=True)
class SystemOfRecordAssessment:
    """What the register decides about a bank's book at one as-of date."""

    bank_id: str
    as_of_date: date
    #: The detector's own outcome, carried through unmodified. When it reports
    #: nothing contested, ``findings`` is empty and there is nothing to say.
    overlap: reconciliation.SourceOverlapOutcome
    findings: tuple[TypeFinding, ...]

    @property
    def contested_types(self) -> int:
        return len(self.findings)

    @property
    def undeclared(self) -> tuple[TypeFinding, ...]:
        return tuple(f for f in self.findings if f.finding == FINDING_UNDECLARED)

    @property
    def violations(self) -> tuple[TypeFinding, ...]:
        return tuple(f for f in self.findings if f.finding == FINDING_VIOLATED)

    @property
    def clean(self) -> bool:
        """True when there is nothing for anyone to do.

        A single-source bank is clean, and so is a bank whose systems partition
        the book properly. Neither is asked to declare anything.
        """
        return not self.findings

    def message(self) -> str | None:
        """The operator-facing verdict, or ``None`` on a clean book."""
        if self.clean:
            return None
        parts: list[str] = []
        for finding in self.violations:
            systems = ", ".join(book.source_system for book in finding.offending)
            parts.append(
                f"{finding.position_type}: {systems} reported a book for this date, but "
                f"{finding.declared_source_system} is the declared book of record "
                f"({finding.offending_rows} record(s), "
                f"{finding.offending_total} in the reporting currency)"
            )
        for finding in self.undeclared:
            systems = ", ".join(book.source_system for book in finding.books)
            parts.append(
                f"{finding.position_type}: {systems} each reported a book for this date "
                "and no system of record has been declared for this type"
            )
        lead = (
            "Part of this balance sheet is counted twice. "
            if self.violations
            else "Part of this balance sheet may be counted twice. "
        )
        tail = (
            " Withdraw the non-authoritative book for this date "
            "(a withdrawal needs a reason and a second approver), or correct the register."
            if self.violations
            else " Declare the book of record for each type, then withdraw the rest."
        )
        return lead + "; ".join(parts) + "." + tail

    def detail(self) -> OutcomeDetail | None:
        """An advisory WS-A outcome, or ``None`` when there is nothing to report.

        ALWAYS advisory. The register explains and attributes; the balance-sheet
        identity control is what stops a filing.
        """
        reason = self.message()
        if reason is None:
            return None
        state = (
            OutcomeState.RECONCILIATION_FAILED
            if self.violations
            else OutcomeState.MISSING_REQUIRED_INPUT
        )
        items = tuple(
            f"position_type:{finding.position_type}:{book.source_system}"
            for finding in self.findings
            for book in (finding.offending or finding.books)
        )
        return build_outcome(
            state,
            metric_id=SYSTEM_OF_RECORD_METRIC_ID,
            reason=reason,
            items=items,
            advisory=True,
            context=self.provenance(),
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "control": SYSTEM_OF_RECORD_METRIC_ID,
            "bank_id": self.bank_id,
            "as_of_date": self.as_of_date.isoformat(),
            "contested_types": self.contested_types,
            "violations": len(self.violations),
            "undeclared": len(self.undeclared),
            "findings": [finding.provenance() for finding in self.findings],
        }


def assess(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    overlap: reconciliation.SourceOverlapOutcome,
) -> SystemOfRecordAssessment:
    """Resolve the register against a detected overlap.

    ``overlap`` is passed in rather than recomputed so the assessment is always
    measured over the SAME population as the derivation that produced it.
    """
    if not overlap.contested:
        # Nothing is contested: one source system, or several that partition the
        # book cleanly. No declaration is required and none is consulted.
        return SystemOfRecordAssessment(
            bank_id=bank.id, as_of_date=as_of, overlap=overlap, findings=()
        )

    declarations = resolve(db, ctx.organization_id, bank.id, as_of)
    findings: list[TypeFinding] = []
    for contest in overlap.contested:
        declaration = declarations.get(contest.position_type)
        if declaration is None:
            findings.append(
                TypeFinding(
                    position_type=contest.position_type,
                    finding=FINDING_UNDECLARED,
                    declared_source_system=None,
                    declaration_id=None,
                    declaration_confirmation_status=None,
                    offending=(),
                    books=contest.books,
                )
            )
            continue
        offending = tuple(
            book for book in contest.books if book.source_system != declaration.source_system
        )
        findings.append(
            TypeFinding(
                position_type=contest.position_type,
                finding=FINDING_VIOLATED,
                declared_source_system=declaration.source_system,
                declaration_id=declaration.id,
                declaration_confirmation_status=declaration.confirmation_status,
                offending=offending,
                books=contest.books,
            )
        )
    return SystemOfRecordAssessment(
        bank_id=bank.id, as_of_date=as_of, overlap=overlap, findings=tuple(findings)
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> dict[str, SystemOfRecordDeclaration]:
    """Approved, un-revoked declarations in force at ``as_of``, keyed by type.

    Effective dating is what makes a core-banking migration expressible: the
    legacy system is the book of record up to the cutover, the new one from it,
    and the same bank's June and October filings legitimately resolve to
    different systems for the same type.
    """
    rows = db.scalars(
        select(SystemOfRecordDeclaration)
        .where(
            SystemOfRecordDeclaration.organization_id == organization_id,
            SystemOfRecordDeclaration.bank_id == bank_id,
            SystemOfRecordDeclaration.status == "approved",
            SystemOfRecordDeclaration.revoked_at.is_(None),
            SystemOfRecordDeclaration.effective_from <= as_of,
            or_(
                SystemOfRecordDeclaration.effective_to.is_(None),
                SystemOfRecordDeclaration.effective_to > as_of,
            ),
        )
        .order_by(SystemOfRecordDeclaration.effective_from)
    ).all()
    # Later generations overwrite earlier ones for the same type; the ORDER BY
    # makes the newest in-force generation win.
    return {row.position_type: row for row in rows}


def list_declarations(
    db: Session,
    organization_id: str,
    bank_id: str,
    *,
    position_type: str | None = None,
    include_drafts: bool = True,
) -> list[SystemOfRecordDeclaration]:
    """Every generation for the bank, newest-effective first."""
    conditions = [
        SystemOfRecordDeclaration.organization_id == organization_id,
        SystemOfRecordDeclaration.bank_id == bank_id,
    ]
    if position_type is not None:
        conditions.append(SystemOfRecordDeclaration.position_type == position_type)
    if not include_drafts:
        conditions.append(SystemOfRecordDeclaration.status == "approved")
    return list(
        db.scalars(
            select(SystemOfRecordDeclaration)
            .where(*conditions)
            .order_by(
                SystemOfRecordDeclaration.position_type,
                SystemOfRecordDeclaration.effective_from.desc(),
            )
        )
    )


def get_declaration(
    db: Session, organization_id: str, declaration_id: UUID
) -> SystemOfRecordDeclaration:
    row = db.scalar(
        select(SystemOfRecordDeclaration).where(
            SystemOfRecordDeclaration.id == declaration_id,
            SystemOfRecordDeclaration.organization_id == organization_id,
        )
    )
    if row is None:
        raise SystemOfRecordError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"System-of-record declaration {declaration_id} not found.",
        )
    return row


# ---------------------------------------------------------------------------
# Maker-checker
# ---------------------------------------------------------------------------


def propose(  # noqa: PLR0913 - a governed declaration names every field explicitly
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    position_type: str,
    source_system: str,
    effective_from: date,
    source_citation: str,
    rationale: str,
    proposed_by: str,
    confirmation_status: str = "pending",
) -> SystemOfRecordDeclaration:
    """Maker step: record a ``draft`` declaration. Invisible to :func:`resolve`."""
    if not source_citation.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A system-of-record declaration requires a source citation — the IT "
                "sign-off, data-owner memo or migration runbook that establishes it."
            ),
        )
    if not rationale.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A system-of-record declaration requires a non-empty rationale.",
        )
    if not proposed_by.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A system-of-record declaration requires a named proposer.",
        )
    existing = db.scalar(
        select(SystemOfRecordDeclaration).where(
            SystemOfRecordDeclaration.organization_id == ctx.organization_id,
            SystemOfRecordDeclaration.bank_id == bank.id,
            SystemOfRecordDeclaration.position_type == position_type,
            SystemOfRecordDeclaration.effective_from == effective_from,
        )
    )
    if existing is not None:
        raise SystemOfRecordError(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A {position_type} declaration already exists effective "
                f"{effective_from.isoformat()} (status {existing.status}). "
                "Choose a later effective date."
            ),
        )
    now = utc_now()
    row = SystemOfRecordDeclaration(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        position_type=position_type,
        source_system=source_system,
        effective_from=effective_from,
        effective_to=None,
        source_citation=source_citation.strip(),
        rationale=rationale.strip(),
        confirmation_status=confirmation_status,
        status="draft",
        proposed_by=proposed_by.strip(),
        proposed_by_user_id=ctx.actor_user_id,
        proposed_at=now,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type=DECLARED_EVENT,
        entity_type="system_of_record_declaration",
        entity_id=row.id,
        details={
            "bank_id": bank.id,
            "position_type": position_type,
            "source_system": source_system,
            "effective_from": effective_from.isoformat(),
            "source_citation": row.source_citation,
            "rationale": row.rationale,
            "proposed_by": row.proposed_by,
        },
    )
    db.flush()
    return row


def approve(
    db: Session,
    ctx: TenantContext,
    declaration_id: UUID,
    *,
    approved_by: str,
) -> SystemOfRecordDeclaration:
    """Checker step: approve a draft (approver ≠ proposer) and close the prior row."""
    row = get_declaration(db, ctx.organization_id, declaration_id)
    if row.status != "draft":
        raise SystemOfRecordError(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Declaration {declaration_id} is already {row.status}; it is immutable.",
        )
    if not approved_by or not approved_by.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A system-of-record declaration requires a named approver.",
        )
    if approved_by.strip().lower() == row.proposed_by.strip().lower() or (
        ctx.actor_user_id is not None
        and row.proposed_by_user_id is not None
        and ctx.actor_user_id == row.proposed_by_user_id
    ):
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A system-of-record declaration cannot be approved by the officer who "
                "proposed it — a second approver is required."
            ),
        )
    prior = db.scalar(
        select(SystemOfRecordDeclaration)
        .where(
            SystemOfRecordDeclaration.organization_id == row.organization_id,
            SystemOfRecordDeclaration.bank_id == row.bank_id,
            SystemOfRecordDeclaration.position_type == row.position_type,
            SystemOfRecordDeclaration.status == "approved",
            SystemOfRecordDeclaration.revoked_at.is_(None),
            SystemOfRecordDeclaration.effective_from < row.effective_from,
            or_(
                SystemOfRecordDeclaration.effective_to.is_(None),
                SystemOfRecordDeclaration.effective_to > row.effective_from,
            ),
        )
        .order_by(SystemOfRecordDeclaration.effective_from.desc())
        .limit(1)
    )
    if prior is not None:
        # Closing the prior window rather than deleting it is what preserves a
        # migration's history: "T24 was the book of record until 1 October".
        prior.effective_to = row.effective_from
    now = utc_now()
    row.status = "approved"
    row.approved_by = approved_by.strip()
    row.approved_by_user_id = ctx.actor_user_id
    row.approved_at = now
    db.flush()
    record_event(
        db,
        ctx,
        event_type=APPROVED_EVENT,
        entity_type="system_of_record_declaration",
        entity_id=row.id,
        details={
            "bank_id": row.bank_id,
            "position_type": row.position_type,
            "source_system": row.source_system,
            "effective_from": row.effective_from.isoformat(),
            "approved_by": row.approved_by,
            "approved_at": now.isoformat(),
            "superseded_declaration_id": str(prior.id) if prior is not None else None,
        },
    )
    db.flush()
    return row


def revoke(
    db: Session,
    ctx: TenantContext,
    declaration_id: UUID,
    *,
    revoked_by: str,
    reason: str,
) -> SystemOfRecordDeclaration:
    """Close a declaration without deleting it (a wrong answer stays visible)."""
    row = get_declaration(db, ctx.organization_id, declaration_id)
    if row.revoked_at is not None:
        raise SystemOfRecordError(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Declaration {declaration_id} is already revoked.",
        )
    if not reason or not reason.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Revoking a system-of-record declaration requires a non-empty reason.",
        )
    if not revoked_by or not revoked_by.strip():
        raise SystemOfRecordError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Revoking a system-of-record declaration requires a named officer.",
        )
    now = utc_now()
    row.revoked_at = now
    row.revoked_by = revoked_by.strip()
    row.revocation_reason = reason.strip()
    db.flush()
    record_event(
        db,
        ctx,
        event_type=REVOKED_EVENT,
        entity_type="system_of_record_declaration",
        entity_id=row.id,
        details={
            "bank_id": row.bank_id,
            "position_type": row.position_type,
            "source_system": row.source_system,
            "revoked_by": row.revoked_by,
            "reason": row.revocation_reason,
            "revoked_at": now.isoformat(),
        },
    )
    db.flush()
    return row
