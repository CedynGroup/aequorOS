"""Evidence: what the bytes actually are, and what withdrawal does and does not do."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import AuditEvent
from app.models.icaap import IcaapAttachment, IcaapAttachmentWithdrawal, IcaapCycle
from app.schemas.icaap import (
    IcaapAttachmentWithdraw,
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapManualColumn,
    IcaapManualRow,
    IcaapManualTablePut,
)
from app.services.icaap import attachments, blocks
from tests.storage.inmemory import InMemoryStorageClient

REPORT = "senior_management_report"
RESOLUTION = "board_resolution"
EVIDENCE = "manual_table_evidence"


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


_OOXML = "application/vnd.openxmlformats-officedocument"
DOCX_MAIN = f"{_OOXML}.wordprocessingml.document.main+xml"
XLSX_MAIN = f"{_OOXML}.spreadsheetml.sheet.main+xml"
#: What a macro-enabled member of the family declares instead.
DOCM_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


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


def docx_bytes() -> bytes:
    return ooxml(main=("word/document.xml", DOCX_MAIN))


def xlsx_bytes() -> bytes:
    return ooxml(main=("xl/workbook.xml", XLSX_MAIN))


def xlsm_bytes() -> bytes:
    """A macro-enabled workbook as Excel actually writes one."""
    return ooxml("xl/vbaProject.bin", main=("xl/workbook.xml", XLSM_MAIN))


def docm_bytes() -> bytes:
    """A macro-enabled document as Word actually writes one."""
    return ooxml("word/vbaProject.bin", main=("word/document.xml", DOCM_MAIN))


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


# --- sniffing --------------------------------------------------------------


def test_the_file_type_comes_from_the_bytes() -> None:
    assert attachments.sniff_media_type(pdf_bytes()) == "application/pdf"
    docx = attachments.sniff_media_type(docx_bytes())
    xlsx = attachments.sniff_media_type(xlsx_bytes())
    assert docx is not None and docx.endswith("wordprocessingml.document")
    assert xlsx is not None and xlsx.endswith("spreadsheetml.sheet")


@pytest.mark.parametrize(
    "content",
    [
        b"MZ\x90\x00executable",
        b"Just some text pretending to be a PDF",
        b"PK\x03\x04not-really-a-zip",
        b"",
    ],
)
def test_anything_else_is_not_a_document_whatever_it_is_called(content: bytes) -> None:
    assert attachments.sniff_media_type(content) is None


def test_a_macro_enabled_workbook_is_not_an_accepted_document() -> None:
    """A real ``.xlsm`` — the workbook part, the VBA project and the declaration.

    REWRITTEN (2026-09-20, security audit S-5). The archive this asserted on
    before carried ``xl/vbaProject.bin`` and NOTHING ELSE, so it passed against
    a sniffer that identified a workbook by the presence of ``xl/workbook.xml``:
    the fixture simply never contained the part that would have been matched. A
    macro-enabled workbook Excel actually writes carries both, and that file was
    admitted as ``.xlsx``. The assertion is unchanged; the evidence behind it is
    now the real thing.
    """
    assert attachments.sniff_media_type(xlsm_bytes()) is None


def test_a_macro_enabled_document_is_not_an_accepted_document() -> None:
    """The same for ``.docm`` — the upload path for a Board resolution."""
    assert attachments.sniff_media_type(docm_bytes()) is None


def test_a_vba_project_under_a_macro_free_declaration_is_refused() -> None:
    """Executable code is refused on what the container HOLDS, not on its claim."""
    smuggled = ooxml("word/vbaProject.bin", main=("word/document.xml", DOCX_MAIN))
    assert attachments.sniff_media_type(smuggled) is None


def test_a_container_that_never_says_what_it_is_is_refused() -> None:
    """A part without the declaration that points at it is not a document.

    This is the shape the fixture used to build for every OOXML case. No writer
    produces it, and accepting it would mean the media type came from a file
    name inside the archive rather than from what the document declares itself
    to be.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<x/>")
    assert attachments.sniff_media_type(buffer.getvalue()) is None


def test_a_declaration_with_no_part_behind_it_is_refused() -> None:
    manifest_only = ooxml(main=("xl/workbook.xml", XLSX_MAIN))
    stripped = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(manifest_only)) as source,
        zipfile.ZipFile(stripped, "w") as archive,
    ):
        archive.writestr("[Content_Types].xml", source.read("[Content_Types].xml"))
    assert attachments.sniff_media_type(stripped.getvalue()) is None


def test_the_documents_this_platform_writes_are_recognised() -> None:
    """The strict rule must admit real Office output, not just the fixtures."""
    from docx import Document  # noqa: PLC0415 - optional-looking, always installed
    from openpyxl import Workbook  # noqa: PLC0415

    document = io.BytesIO()
    Document().save(document)
    workbook = io.BytesIO()
    Workbook().save(workbook)
    assert attachments.sniff_media_type(document.getvalue()) == attachments.DOCX_MEDIA_TYPE
    assert attachments.sniff_media_type(workbook.getvalue()) == attachments.XLSX_MEDIA_TYPE


# --- uploading -------------------------------------------------------------


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


# --- withdrawal ------------------------------------------------------------


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


def test_a_download_is_recorded(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    uploaded = _upload(canonical_book, access, cycle, storage)
    row, slug = attachments.prepare_download(canonical_book, access, cycle.id, uploaded.id)
    assert row.id == uploaded.id
    assert slug
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.attachment.downloaded")
    ).all()
    assert events and events[-1].details["sha256"] == uploaded.sha256


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
