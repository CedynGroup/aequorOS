"""Evidence: what the bytes actually are, and what withdrawal does and does not do.

This module's other-suite cases live in ``test_attachments_integration.py``.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.icaap.service import attachments
from tests.icaap.service.test_attachments_integration import (
    _OOXML,
    XLSX_MAIN,
    ooxml,
    pdf_bytes,
    xlsx_bytes,
)

DOCX_MAIN = f"{_OOXML}.wordprocessingml.document.main+xml"
#: What a macro-enabled member of the family declares instead.
DOCM_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


def docx_bytes() -> bytes:
    return ooxml(main=("word/document.xml", DOCX_MAIN))


def xlsm_bytes() -> bytes:
    """A macro-enabled workbook as Excel actually writes one."""
    return ooxml("xl/vbaProject.bin", main=("xl/workbook.xml", XLSM_MAIN))


def docm_bytes() -> bytes:
    """A macro-enabled document as Word actually writes one."""
    return ooxml("word/vbaProject.bin", main=("word/document.xml", DOCM_MAIN))


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
