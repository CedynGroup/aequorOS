"""The ICAAP Word working copy.

The danger a working copy carries is that it looks like the filing. Four things
keep it from being mistaken for one, and each is checked here because each fails
independently:

* the ``WORKING COPY — not the filed document`` notice is the Word SECTION
  header, so it is on every page of every printout — not one line on a cover
  that a reader scrolls past;
* the kind is in ``UNFILABLE_WORKING_ARTIFACT_KINDS``, so the filing set excludes
  it and a submission event can never list it as filed. Membership of
  ``WORKING_ARTIFACT_KINDS`` alone stopped proving that on 2026-09-20, when the
  BSD formula workbook opted IN to filing: the Word draft is excluded because it
  has not opted in, and that is what this suite now pins;
* nothing signs it — ``artifact_signing`` signs the ``pdf`` kind, and a Word
  file has no signature field to sign into;
* it is stored under its own object stem, so it cannot overwrite the filing.

Determinism is checked because python-docx stamps the wall clock into every zip
entry at save, which would make two exports of one sealed package differ and
turn the stored checksum into noise.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime

import pytest

from app.domain.icaap.document import WORKING_COPY_NOTICE
from app.services.icaap.render.from_snapshot import filing_from_snapshot
from app.services.regulatory_reporting.exports import icaap_docx, icaap_pdf
from app.services.regulatory_reporting.workflow import (
    FILABLE_WORKING_ARTIFACT_KINDS,
    UNFILABLE_WORKING_ARTIFACT_KINDS,
    WORKING_ARTIFACT_KINDS,
)
from tests.services.icaap.filing_snapshot import CEDI, FakePackage, snapshot

TWO_SIGNERS = ("preparer", "approver")


def build(**kwargs: object) -> bytes:
    filing = filing_from_snapshot(
        snapshot(**kwargs),  # type: ignore[arg-type]
        package=FakePackage(),
        signing_order=TWO_SIGNERS,
    )
    return icaap_docx.render_working_docx(filing)


def part(payload: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read(name).decode("utf-8")


def document_xml(payload: bytes) -> str:
    return part(payload, "word/document.xml")


# --------------------------------------------------------------------------
# It says what it is
# --------------------------------------------------------------------------
def test_the_working_copy_notice_is_the_page_header_of_every_word_section() -> None:
    payload = build()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        headers = [name for name in archive.namelist() if name.startswith("word/header")]
        assert headers, "the document declares no header part"
        for name in headers:
            assert WORKING_COPY_NOTICE in archive.read(name).decode("utf-8")


def test_the_notice_is_repeated_on_the_cover_and_the_provenance_page() -> None:
    xml = document_xml(build())
    # Twice in the body: the reader who opens it and the reader who prints the
    # last page both see it without hunting.
    assert xml.count(WORKING_COPY_NOTICE) >= 2


def test_the_core_properties_say_it_too() -> None:
    assert WORKING_COPY_NOTICE in part(build(), "docProps/core.xml")


def test_the_attestation_copy_states_that_it_carries_no_signature() -> None:
    xml = document_xml(build())
    assert "carries no signature and cannot be signed" in xml
    assert "The filed instrument is the signed PDF" in xml


# --------------------------------------------------------------------------
# It is never the filing
# --------------------------------------------------------------------------
def test_the_kind_is_excluded_from_the_filing_set() -> None:
    """Recalculable AND not opted in to filing — both halves, or this proves
    nothing: since 2026-09-20 a working kind can be filed (``xlsx_working`` is),
    so the exclusion now rests on the Word draft's ABSENCE from the opt-in set."""
    assert icaap_docx.ARTIFACT_KIND in WORKING_ARTIFACT_KINDS
    assert icaap_docx.ARTIFACT_KIND not in FILABLE_WORKING_ARTIFACT_KINDS
    assert icaap_docx.ARTIFACT_KIND in UNFILABLE_WORKING_ARTIFACT_KINDS


def test_the_object_stem_cannot_collide_with_the_filing_pdf() -> None:
    assert icaap_docx.file_stem("ICAAP-REPORT") == "ICAAP-REPORT.working"
    assert icaap_docx.file_stem("ICAAP-REPORT") != "ICAAP-REPORT"


def test_the_signing_layer_only_ever_signs_the_pdf_kind() -> None:
    from app.services.attestation import artifact_signing  # noqa: PLC0415

    assert artifact_signing.ARTIFACT_KIND == "pdf"
    assert artifact_signing.ARTIFACT_KIND != icaap_docx.ARTIFACT_KIND


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------
def test_two_renders_of_one_sealed_package_are_byte_identical() -> None:
    assert build() == build()


def test_no_zip_entry_carries_a_wall_clock_stamp() -> None:
    """python-docx stamps every entry at save; the normaliser replaces them all.

    One fixed epoch rather than the package's own time: the entry stamps are the
    thing that must not vary, and the package's time is pinned where a reader
    can see it — ``dcterms:modified`` in the core properties.
    """
    package = FakePackage(generated_at=datetime(2027, 3, 1, 8, 30, tzinfo=UTC))
    filing = filing_from_snapshot(snapshot(), package=package, signing_order=TWO_SIGNERS)
    payload = icaap_docx.render_working_docx(filing)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        stamps = {info.date_time for info in archive.infolist()}
    assert len(stamps) == 1
    assert stamps.pop()[0] < 2020
    assert "2027-03-01T08:30:00" in part(payload, "docProps/core.xml")


def test_the_entries_are_written_in_a_stable_order() -> None:
    with zipfile.ZipFile(io.BytesIO(build())) as archive:
        names = archive.namelist()
    assert names == sorted(names)


def test_the_recorded_modification_time_is_pinned_not_now() -> None:
    assert "2026-09-19T12:00:00" in part(build(), "docProps/core.xml")


# --------------------------------------------------------------------------
# It carries the same figures as the PDF
# --------------------------------------------------------------------------
def test_the_prose_figures_and_provenance_all_reach_the_word_file() -> None:
    xml = document_xml(build())
    for expected in (
        "Total regulatory capital of",
        "1,500,000",  # the factRef's frozen value, as the PDF prints it
        "CET1 capital",  # the data block, as a table
        "State the internal capital the Board considers adequate.",
        "The institution has no subsidiaries.",  # REG-ICAAP-008
        "Board resolution of 12 March 2027",
        "car_min",
        "icaap_stress_horizon_years_min",
        "capital-2026.3",
        "Efua Asante",
        "Annex A",
        "ICAAP-STRESS-APPENDIX2",
    ):
        assert expected in xml, expected


def test_ooxml_carries_the_cedi_sign_without_substitution() -> None:
    """Unlike a PDF, OOXML is UTF-8 — there was never a coverage problem here."""
    assert CEDI in document_xml(build())


def test_markup_typed_by_an_author_is_escaped_not_interpreted() -> None:
    assert "&lt;b&gt;not markup&lt;/b&gt;" in document_xml(build())


def test_the_word_copy_and_the_filing_pdf_agree_on_the_attachment_manifest() -> None:
    """One frozen snapshot, two renderers: they cannot show different documents."""
    filing = filing_from_snapshot(snapshot(), package=FakePackage(), signing_order=TWO_SIGNERS)
    xml = document_xml(icaap_docx.render_working_docx(filing))
    for entry in filing.provenance.attachments:
        assert entry.sha256 in xml
        assert entry.title in xml
    # And the PDF renders from the same object without a second data gather.
    assert icaap_pdf.render_filing_pdf(filing)


# --------------------------------------------------------------------------
# The coupling to P1's renderer, made loud
# --------------------------------------------------------------------------
def test_the_private_p1_renderer_this_module_extends_still_exists() -> None:
    """``icaap_docx`` subclasses ``render/docx._DocxRenderer`` deliberately.

    Re-implementing the prose, tables and list markers here is how a draft and a
    working copy of one cycle would come to differ. The cost is a dependency on
    an underscored name; this test is what turns a rename in P1 into a failing
    test rather than a failing export.
    """
    from app.services.icaap.render import docx as p1_docx  # noqa: PLC0415

    assert hasattr(p1_docx, "_DocxRenderer")
    for method in ("_cover", "_contents", "_section", "_checklist", "_table", "_page_header"):
        assert hasattr(p1_docx._DocxRenderer, method), method  # noqa: SLF001


def test_a_rehearsal_snapshot_is_banner_ed_as_well_as_working() -> None:
    xml = document_xml(build(cycle_kind="rehearsal"))
    assert "REHEARSAL" in xml
    assert WORKING_COPY_NOTICE in xml


@pytest.mark.parametrize("status", ["exposure_draft", "final"])
def test_the_exposure_draft_line_follows_the_frozen_framework_status(status: str) -> None:
    xml = document_xml(build(framework_status=status))
    assert ("EXPOSURE DRAFT" in xml) is (status == "exposure_draft")


# --------------------------------------------------------------------------
# The golden
# --------------------------------------------------------------------------
#: SHA-256 over the sorted (entry name, UNCOMPRESSED bytes) manifest of the
#: package. Not a hash of the .docx file: the container is Deflate-compressed,
#: so a raw digest also pins the zlib build and would fail on a machine whose
#: zlib packs identical content differently — a false alarm that teaches people
#: to regenerate goldens instead of reading them. Literal byte stability is
#: covered by ``test_two_renders_of_one_sealed_package_are_byte_identical``;
#: this pins what is IN the document.
#:
#: Regenerate by printing ``content_digest(build())`` and reading why the
#: document changed before pasting the new value in.
#:
#: Changed 2026-09-20 (regulatory audit R-2). The governed-parameter table
#: headed the parameter-CODE column "Value" and the value column "Applied",
#: so a signed filing read "Value = car_min | Applied = 13". The headers are
#: now ("Parameter", "Value", "Unit", "Status", "Citation"). Verified before
#: regenerating: the rendered table reads Parameter | Value | Unit | Status |
#: Citation over car_min | 13 | pct, and "Applied" no longer appears anywhere
#: in the document. Nothing else moved.
CONTENT_GOLDEN = "3778a41c1a835427d482dd8ba47cbbedc793465065a8f7e4436351e3d3d4afa5"


def content_digest(payload: bytes) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in sorted(archive.namelist()):
            digest.update(name.encode())
            digest.update(archive.read(name))
    return digest.hexdigest()


def test_the_working_copy_matches_its_golden() -> None:
    assert content_digest(build()) == CONTENT_GOLDEN
