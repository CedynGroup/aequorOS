"""What an uploaded document actually IS, decided from its bytes.

Shared by every surface that accepts a regulator-facing document: the ICAAP
cycle's evidence locker (``services/icaap/attachments.py``, which re-exports
these names so its own callers are unchanged) and the package attachment plane
(``services/regulatory_reporting/attachments.py``).

The client's ``Content-Type`` header is not evidence of anything. A file called
``board-resolution.pdf`` that is actually a macro-enabled workbook is the attack
this closes, and it has to be closed the same way on both surfaces — one
implementation, so a format admitted on one is admitted on the other and a
format refused on one cannot be smuggled in through the other.

An OOXML container is read the way the format itself defines identity: its own
manifest, ``[Content_Types].xml``, names the content type of the part that
decides what the document is. Identifying the container by the mere PRESENCE of
``word/document.xml`` or ``xl/workbook.xml`` is not the same test — a
macro-enabled ``.docm`` / ``.xlsm`` carries those parts too, so that reading
admitted exactly the format this module says it refuses.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The document formats a regulator-facing attachment may be, and the file
#: extension each is stored under. Deliberately small: a Board resolution, a
#: senior-management report and a set of minutes are documents, not archives,
#: images or executables.
ALLOWED_MEDIA: dict[str, str] = {
    PDF_MEDIA_TYPE: "pdf",
    DOCX_MEDIA_TYPE: "docx",
    XLSX_MEDIA_TYPE: "xlsx",
}

_ZIP_MAGIC = b"PK\x03\x04"
_MANIFEST = "[Content_Types].xml"

#: For each allowed OOXML format: the main-part content type the manifest
#: declares, and the part that declaration must actually point at. BOTH are
#: required — a manifest that claims to be a workbook without carrying one is
#: not a workbook, and a workbook part with no declaration is a container that
#: never said what it is.
_OOXML_FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"wordprocessingml.document.main+xml", "word/document.xml", DOCX_MEDIA_TYPE),
    (b"spreadsheetml.sheet.main+xml", "xl/workbook.xml", XLSX_MEDIA_TYPE),
)

#: How a macro-enabled member of the OOXML family announces itself in its own
#: manifest (``vnd.ms-word.document.macroEnabled.main+xml``,
#: ``vnd.ms-excel.sheet.macroEnabled.main+xml``, and the template variants).
#: Matched case-insensitively, because the declaration is a bare string in a
#: file the uploader wrote.
_MACRO_DECLARATION = b"macroenabled"

#: The VBA project itself. Checked as well as the declaration, so a container
#: that carries executable code while declaring a macro-free type is refused on
#: what it CONTAINS rather than on what it says about itself.
_MACRO_PART = "vbaproject.bin"

#: A manifest is a few hundred bytes of XML. Anything claiming to be larger is
#: not one, and refusing to decompress it keeps a crafted archive from costing
#: memory on a route whose whole job is to be strict about bytes.
_MANIFEST_MAX_BYTES = 1_000_000

_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    KeyError,
    EOFError,
    ValueError,
    NotImplementedError,
    RuntimeError,
)


def _manifest_bytes(archive: zipfile.ZipFile) -> bytes | None:
    """``[Content_Types].xml``, bounded, or ``None`` if there isn't an honest one."""
    info = archive.getinfo(_MANIFEST)
    if info.file_size > _MANIFEST_MAX_BYTES:
        return None
    with archive.open(info) as part:
        manifest = part.read(_MANIFEST_MAX_BYTES + 1)
    return None if len(manifest) > _MANIFEST_MAX_BYTES else manifest


def _macro_enabled(names: set[str], manifest: bytes) -> bool:
    if _MACRO_DECLARATION in manifest.lower():
        return True
    return any(name.rsplit("/", 1)[-1].lower() == _MACRO_PART for name in names)


def sniff_media_type(content: bytes) -> str | None:  # noqa: PLR0911 - one exit per refusal
    """What the bytes actually are, or ``None`` if this is not an allowed document.

    A macro-enabled workbook or document, a ``.doc``, an executable and a text
    file renamed ``.pdf`` all return ``None``. Only the three office formats in
    :data:`ALLOWED_MEDIA` are recognised, and an OOXML container is identified
    by the content type its own manifest declares for its main part — never by
    its extension, and never by the presence of a part alone.
    """
    if content.startswith(b"%PDF-"):
        return PDF_MEDIA_TYPE
    if not content.startswith(_ZIP_MAGIC):
        return None
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if _MANIFEST not in names:
                return None
            manifest = _manifest_bytes(archive)
    except _ARCHIVE_ERRORS:
        return None
    if manifest is None:
        return None
    if _macro_enabled(names, manifest):
        return None
    declared = manifest.lower()
    for declaration, main_part, media_type in _OOXML_FORMATS:
        if declaration in declared and main_part in names:
            return media_type
    return None


__all__ = [
    "ALLOWED_MEDIA",
    "DOCX_MEDIA_TYPE",
    "PDF_MEDIA_TYPE",
    "XLSX_MEDIA_TYPE",
    "sniff_media_type",
]
