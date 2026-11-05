"""Evidence: what the bytes actually are, and what withdrawal does and does not do.

This module's other-suite cases live in ``test_attachments.py``.
"""

from __future__ import annotations

__all__ = ["_OOXML"]
import io
import zipfile
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.db.base import utc_now
from app.icaap.models.workspace import IcaapAttachment, IcaapAttachmentWithdrawal, IcaapCycle
from app.icaap.schemas.workspace import (
    IcaapAttachmentWithdraw,
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapManualColumn,
    IcaapManualRow,
    IcaapManualTablePut,
)
from app.icaap.service import attachments, blocks
from app.models import AuditEvent, Bank, RegulatoryPackage
from app.storage.client import StorageLocation
from app.storage.public import DocumentBinding, DocumentFamily, find_sealed_document
from tests.support.inmemory_storage import InMemoryStorageClient
from tests.support.sealed_documents import sealed_document

REPORT = "senior_management_report"
RESOLUTION = "board_resolution"
EVIDENCE = "manual_table_evidence"


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


_OOXML = "application/vnd.openxmlformats-officedocument"
XLSX_MAIN = f"{_OOXML}.spreadsheetml.sheet.main+xml"


def pdf_bytes(marker: str = "one") -> bytes:
    return b"%PDF-1.7\n" + marker.encode() + b"\n%%EOF\n"


def ooxml(*names: str, main: tuple[str, str] | None = None) -> bytes:
    """A minimal OOXML container: the parts named, and a manifest.

    ``main`` is the (part, declared content type) pair the manifest announces —
    how a real Office file says what it is. The earlier fixture wrote a bare
    ``<Types/>``, which is not a shape any writer produces: it left the sniffer
    nothing to read, so the macro test below passed only because its archive
    omitted ``xl/workbook.xml``, which a genuine ``.xlsm`` carries.
    """
    buffer = io.BytesIO()
    parts = list(names)
    overrides = ""
    if main is not None:
        part, content_type = main
        parts.append(part)
        overrides = f'<Override PartName="/{part}" ContentType="{content_type}"/>'
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", f"<Types>{overrides}</Types>")
        for name in parts:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


def xlsx_bytes() -> bytes:
    return ooxml(main=("xl/workbook.xml", XLSX_MAIN))


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


def _upload(  # noqa: PLR0913 - an upload is its named parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
    *,
    kind: str = REPORT,
    content: bytes | None = None,
    filename: str = "report.pdf",
):
    return attachments.upload_attachment(
        db,
        access,
        cycle.id,
        storage,
        kind=kind,
        title="Senior Management report",
        filename=filename,
        content=pdf_bytes() if content is None else content,
    )


def test_an_upload_records_the_hash_of_what_was_stored(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    assert uploaded.kind == REPORT
    assert uploaded.media_type == "application/pdf"
    assert len(uploaded.sha256) == 64
    assert uploaded.byte_size == len(pdf_bytes())
    row = canonical_book.get(IcaapAttachment, uploaded.id)
    assert row is not None
    assert row.object_path.startswith(f"icaap/{cycle.fiscal_year}/{cycle.id}/")
    assert row.object_path.endswith(".pdf")
    assert row.storage_tier == "outputs"


def test_a_file_renamed_to_look_right_is_still_refused(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    with pytest.raises(HTTPException) as caught:
        _upload(
            canonical_book,
            access,
            cycle,
            storage,
            content=b"totally not a pdf",
            filename="board-resolution.pdf",
        )
    assert caught.value.status_code == 415
    assert _detail(caught)["error_code"] == "unsupported_media_type"


def test_a_document_in_a_format_that_kind_does_not_accept_is_refused(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    """The Board resolution is a signed PDF, not a spreadsheet."""
    with pytest.raises(HTTPException) as caught:
        _upload(
            canonical_book,
            access,
            cycle,
            storage,
            kind=RESOLUTION,
            content=xlsx_bytes(),
            filename="resolution.xlsx",
        )
    assert caught.value.status_code == 415


def test_a_kind_the_framework_does_not_ask_for_is_refused(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    with pytest.raises(HTTPException) as caught:
        _upload(canonical_book, access, cycle, storage, kind="holiday_photos")
    assert _detail(caught)["error_code"] == "attachment_kind_unknown"


def test_the_same_file_is_not_attached_twice(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    _upload(canonical_book, access, cycle, storage)
    with pytest.raises(HTTPException) as caught:
        _upload(canonical_book, access, cycle, storage)
    assert _detail(caught)["error_code"] == "attachment_duplicate"


def test_the_listing_says_which_documents_are_still_missing(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    listing = attachments.list_attachments(canonical_book, access, cycle.id)
    report = next(entry for entry in listing.requirements if entry.kind == REPORT)
    assert (report.gate, report.active_count, report.satisfied) == ("freeze", 0, False)

    _upload(canonical_book, access, cycle, storage)
    after = attachments.list_attachments(canonical_book, access, cycle.id)
    report = next(entry for entry in after.requirements if entry.kind == REPORT)
    assert (report.active_count, report.satisfied) == (1, True)
    assert len(after.attachments) == 1
    assert after.attachments[0].withdrawn is False


def test_withdrawal_is_an_event_and_leaves_the_upload_record_alone(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    withdrawn = attachments.withdraw_attachment(
        canonical_book,
        access,
        cycle.id,
        uploaded.id,
        IcaapAttachmentWithdraw(reason="Superseded by the signed copy"),
    )
    assert withdrawn.withdrawn is True
    assert withdrawn.withdrawal_reason == "Superseded by the signed copy"
    assert withdrawn.sha256 == uploaded.sha256
    row = canonical_book.get(IcaapAttachment, uploaded.id)
    assert row is not None and row.sha256 == uploaded.sha256
    events = canonical_book.scalars(
        select(IcaapAttachmentWithdrawal).where(
            IcaapAttachmentWithdrawal.attachment_id == uploaded.id
        )
    ).all()
    assert len(events) == 1


def test_a_withdrawn_document_no_longer_satisfies_the_requirement(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    attachments.withdraw_attachment(
        canonical_book, access, cycle.id, uploaded.id, IcaapAttachmentWithdraw(reason="Wrong file")
    )
    listing = attachments.list_attachments(canonical_book, access, cycle.id)
    report = next(entry for entry in listing.requirements if entry.kind == REPORT)
    assert (report.active_count, report.satisfied) == (0, False)


def test_the_same_file_can_be_attached_again_after_a_withdrawal(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    attachments.withdraw_attachment(
        canonical_book,
        access,
        cycle.id,
        uploaded.id,
        IcaapAttachmentWithdraw(reason="Uploaded to the wrong cycle"),
    )
    again = _upload(canonical_book, access, cycle, storage)
    assert again.id != uploaded.id
    assert again.sha256 == uploaded.sha256


def test_evidence_a_table_relies_on_cannot_be_withdrawn_from_under_it(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    evidence = attachments.upload_attachment(
        canonical_book,
        access,
        cycle.id,
        storage,
        kind=EVIDENCE,
        title="Audited accounts",
        filename="accounts.pdf",
        content=pdf_bytes("accounts"),
    )
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="financials")
    )
    blocks.put_manual_table(
        canonical_book,
        access,
        cycle.id,
        block.id,
        IcaapManualTablePut(
            columns=[IcaapManualColumn(key="fy0", label="2025", kind="amount")],
            rows=[IcaapManualRow(key="total_assets", label="Total assets", cells={"fy0": "1"})],
            evidence_attachment_id=evidence.id,
            reason="From the audited accounts",
        ),
    )
    with pytest.raises(HTTPException) as caught:
        attachments.withdraw_attachment(
            canonical_book,
            access,
            cycle.id,
            evidence.id,
            IcaapAttachmentWithdraw(reason="Replacing"),
        )
    assert _detail(caught)["error_code"] == "attachment_in_use"


def _row(db: Session, attachment_id: UUID) -> IcaapAttachment:
    row = db.get(IcaapAttachment, attachment_id)
    assert row is not None
    return row


def test_an_upload_publishes_once_and_binds_its_row_to_the_catalogue(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    """The upload is a publication, not a put: the row keeps a stable identity."""
    uploaded = _upload(canonical_book, access, cycle, storage)
    row = _row(canonical_book, uploaded.id)

    document = find_sealed_document(
        canonical_book,
        row.organization_id,
        row.bank_id,
        DocumentBinding(DocumentFamily.ICAAP_ATTACHMENT, row.id),
    )
    assert document is not None
    assert document.sha256 == row.sha256
    assert document.byte_length == row.byte_size
    assert document.context_path == row.object_path
    assert document.physical_key.startswith("sealed/v1/")


@pytest.mark.parametrize("examiner", [False, True])
def test_a_download_is_recorded(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
    examiner: bool,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    if examiner:
        reader = IcaapAccess(
            ctx=TenantContext(
                access.ctx.organization_id,
                impersonation_context="inspection",
                actor_operator="staff@example.test",
            ),
            bank=access.bank,
            examiner=True,
        )
        with pytest.raises(HTTPException) as refused:
            attachments.read_attachment(canonical_book, reader, cycle.id, uploaded.id, storage)
        assert refused.value.status_code == 404
        cycle_row = canonical_book.get(IcaapCycle, cycle.id)
        assert cycle_row is not None
        package = RegulatoryPackage(
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            return_family="icaap",
            return_code="ICAAP-REPORT",
            reporting_date=cycle_row.as_of_date,
            frequency="annual",
            basis=cycle_row.basis,
            status="generated",
            version=1,
            snapshot={},
            source_runs=[],
            generated_by=cycle_row.created_by,
        )
        canonical_book.add(package)
        canonical_book.flush()
        cycle_row.package_id = package.id
        cycle_row.status = "frozen"
        cycle_row.frozen_at = utc_now()
        canonical_book.commit()
        access = reader
    row, stream = attachments.read_attachment(
        canonical_book, access, cycle.id, uploaded.id, storage
    )
    assert row.id == uploaded.id
    with stream:
        assert stream.read() == pdf_bytes()
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.attachment.downloaded")
    ).all()
    assert events and events[-1].details["sha256"] == uploaded.sha256


def test_a_tampered_object_is_not_downloaded_or_recorded(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    """The read is proven against the row before the audit event exists.

    A download that failed verification never happened, so recording it would put
    a false read in front of an examiner — and serving it would hand them bytes
    that are not the evidence the cycle filed.
    """
    uploaded = _upload(canonical_book, access, cycle, storage)
    row = _row(canonical_book, uploaded.id)
    slug = canonical_book.scalar(select(Bank.storage_slug).where(Bank.id == row.bank_id))
    assert slug
    document = sealed_document(canonical_book, slug=slug, context_path=row.object_path)
    storage.corrupt(
        StorageLocation(document.storage_slug, "outputs", document.physical_key),
        b"% tampered",
    )

    with pytest.raises(HTTPException) as caught:
        attachments.read_attachment(canonical_book, access, cycle.id, uploaded.id, storage)
    assert caught.value.status_code == 404
    assert not canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.attachment.downloaded")
    ).all()


def test_evidence_can_still_be_attached_while_the_report_is_under_review(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    """A reviewer asking for the Board minutes is how they get attached."""

    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None
    row.status = "in_review"
    canonical_book.flush()
    uploaded = _upload(canonical_book, access, cycle, storage)
    assert uploaded.kind == REPORT
