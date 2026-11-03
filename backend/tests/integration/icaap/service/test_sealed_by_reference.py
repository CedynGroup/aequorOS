"""Evidence carried by reference keeps ONE identity across freeze and revision.

A freeze copies an ICAAP cycle's attachments onto the package it froze into, and
a revision copies them onto the next cycle. Neither is a re-upload: the bytes are
the same bytes, so the copied row must name the same catalogue identity rather
than publishing a second copy of the same document. A row written before stable
references existed carries none, and the copy preserves that instead of
inventing an identity for bytes nobody published.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.icaap.models.workspace import IcaapAttachment, IcaapCycle
from app.icaap.schemas.workspace import (
    IcaapCloneCreate,
    IcaapCycleRead,
    IcaapFreezeCreate,
    IcaapFreezeRead,
)
from app.icaap.service import attachments, clone, cycles, freeze, workflow
from app.models import RegulatoryPackage, RegulatoryPackageAttachment
from app.storage.models import SealedDocumentObject
from app.storage.public import DocumentBinding, DocumentFamily, find_sealed_document
from tests.integration.icaap.service.p3_support import (
    AS_OF,
    access_for,
    annual_payload,
    answer_every_requirement,
    attach,
    decide,
    govern_first_as_of,
    make_p2_ready,
    make_user,
    seal_capital_run,
    submit,
    write_every_section,
)
from tests.integration.icaap.service.test_attachments import pdf_bytes
from tests.support.inmemory_storage import InMemoryStorageClient

pytestmark = pytest.mark.canonical_book_baseline

KIND = "senior_management_report"


def _freeze(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapFreezeRead:
    """Freeze the cycle through the workflow's own digest, as the officer does."""
    digest = workflow.get_stages(db, access, cycle_id).review_digest
    return freeze.freeze_cycle(
        db, access, cycle_id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
    )


def _reviewed_cycle(db: Session) -> tuple[IcaapAccess, IcaapCycleRead]:
    """A cycle past its approval stage and awaiting freeze, with no attachments.

    Built from ``p3_support``'s public steps so this file owns no scenario the
    freeze suite would have to keep in step.
    """
    access = access_for(db)
    govern_first_as_of(db, AS_OF)
    seal_capital_run(db, access)
    cycle = cycles.create_cycle(db, access, annual_payload())
    write_every_section(db, access, cycle.id)
    answer_every_requirement(db, access, cycle.id)
    make_p2_ready(db, access, cycle.id)
    submit(db, access, cycle.id)
    reviewer = access_for(
        db, make_user(db, email="cro@example.com", name="Cro", job_title="Chief Risk Officer")
    )
    decide(db, reviewer, cycle.id, 2, "reviewed")
    approver = access_for(db, make_user(db, email="brc@example.com", name="Brc"))
    decide(db, approver, cycle.id, 3, "approved")
    return access, cycle


def _filed(db: Session, cycle_id: UUID, package_id: UUID) -> None:
    """The filing has gone to the regulator: the year end is no longer open."""
    cycle = db.get(IcaapCycle, cycle_id)
    assert cycle is not None
    cycle.status = "submitted"
    cycle.board_approved_at = utc_now()
    cycle.submitted_at = utc_now()
    package = db.get(RegulatoryPackage, package_id)
    assert package is not None
    package.status = "submitted"
    db.commit()


def _published_attachment(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapAttachment:
    """Attach the freeze-gate document through the real upload path."""
    storage = InMemoryStorageClient()
    attachments.upload_attachment(
        db,
        access,
        cycle_id,
        storage,
        kind=KIND,
        title="Senior Management report",
        filename="report.pdf",
        content=pdf_bytes("by-reference"),
    )
    row = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.cycle_id == cycle_id,
            IcaapAttachment.kind == KIND,
        )
    )
    assert row is not None
    return row


def _cycle_attachment_identity(db: Session, attachment_id: UUID) -> SealedDocumentObject | None:
    row = db.get(IcaapAttachment, attachment_id)
    assert row is not None
    return find_sealed_document(
        db,
        row.organization_id,
        row.bank_id,
        DocumentBinding(DocumentFamily.ICAAP_ATTACHMENT, attachment_id),
    )


def _package_attachment_identity(db: Session, attachment_id: UUID) -> SealedDocumentObject | None:
    row = db.get(RegulatoryPackageAttachment, attachment_id)
    assert row is not None
    return find_sealed_document(
        db,
        row.organization_id,
        row.bank_id,
        DocumentBinding(DocumentFamily.PACKAGE_ATTACHMENT, attachment_id),
    )


def test_a_freeze_copies_the_evidence_by_reference_under_the_same_identity(
    canonical_book: Session, extra_frameworks: None
) -> None:
    db = canonical_book
    access, cycle = _reviewed_cycle(db)
    source = _published_attachment(db, access, cycle.id)
    source_identity = _cycle_attachment_identity(db, source.id)
    assert source_identity is not None

    out = _freeze(db, access, cycle.id)

    copied = db.scalar(
        select(RegulatoryPackageAttachment).where(
            RegulatoryPackageAttachment.package_id == out.package.id,
            RegulatoryPackageAttachment.kind == KIND,
        )
    )
    assert copied is not None
    assert copied.sha256 == source.sha256
    assert _package_attachment_identity(db, copied.id) is source_identity
    # By reference means one object, not two.
    assert db.scalar(select(func.count()).select_from(SealedDocumentObject)) == 1


def test_a_revision_carries_the_same_identity_and_publishes_nothing_new(
    canonical_book: Session, extra_frameworks: None
) -> None:
    db = canonical_book
    access, cycle = _reviewed_cycle(db)
    source = _published_attachment(db, access, cycle.id)
    source_identity = _cycle_attachment_identity(db, source.id)
    assert source_identity is not None

    out = _freeze(db, access, cycle.id)
    # A revision follows a FILING: the year is final at the regulator first.
    _filed(db, cycle.id, out.package.id)

    revised = clone.clone_cycle(
        db,
        access,
        cycle.id,
        IcaapCloneCreate(mode="revision", reason="Correcting the concentration figure."),
    )
    carried = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.cycle_id == revised.id,
            IcaapAttachment.kind == KIND,
        )
    )
    assert carried is not None
    assert carried.id != source.id
    assert carried.sha256 == source.sha256
    assert _cycle_attachment_identity(db, carried.id) is source_identity
    assert (carried.attributes or {})["carried_from"] == {
        "cycle_id": str(cycle.id),
        "attachment_id": str(source.id),
    }
    assert db.scalar(select(func.count()).select_from(SealedDocumentObject)) == 1


def test_evidence_written_before_stable_references_is_carried_without_inventing_one(
    canonical_book: Session, extra_frameworks: None
) -> None:
    """A legacy row has no identity; the copy must not manufacture one for it."""
    db = canonical_book
    access, cycle = _reviewed_cycle(db)
    legacy_id = attach(db, access, cycle.id, KIND, sha="a" * 64)

    out = _freeze(db, access, cycle.id)

    copied = db.scalar(
        select(RegulatoryPackageAttachment).where(
            RegulatoryPackageAttachment.package_id == out.package.id,
            RegulatoryPackageAttachment.kind == KIND,
        )
    )
    assert copied is not None
    assert copied.sha256 == "a" * 64
    assert _package_attachment_identity(db, copied.id) is None
    assert _cycle_attachment_identity(db, legacy_id) is None
    assert db.scalar(select(func.count()).select_from(SealedDocumentObject)) == 0
