"""The ICAAP draft PDF.

What is actually being protected here:

* a draft can reach a supervisor's inbox, so every page has to say it is one;
* the prose is typed by users and reportlab's ``Paragraph`` parses markup, so
  a section containing ``<b>`` or ``<script>`` must print those characters and
  never be interpreted or crash the build;
* the standard font cannot draw the cedi sign or Ghanaian vowels, so the
  document must substitute visibly, count what it substituted, and say where
  the exact text lives — without ever claiming it reproduced the text (DV-004);
* re-exporting the same document must give the same bytes, because a checksum
  over an export is only meaningful if the export is reproducible.

Text is read back by concatenating the drawn characters, which keeps the
rotated watermark and the page furniture in the same stream as the body.
"""

from __future__ import annotations

import io
from datetime import date, datetime

import pdfplumber
import pytest

from app.domain.icaap.document import (
    DRAFT_WATERMARK,
    NOT_AVAILABLE,
    REHEARSAL_WATERMARK,
    AttachmentLine,
    ChecklistLine,
    DataBlockRef,
    FactRef,
    IcaapDocument,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    Run,
    SectionRender,
)
from app.services.icaap.render import format as fmt
from app.services.icaap.render.pdf import render_pdf, render_pdf_with_report

GENERATED_AT = datetime(2026, 9, 19, 12, 0, 0)


def text_of(payload: bytes) -> str:
    """Every character the PDF draws, page by page, in drawing order."""
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        return "\n".join("".join(char["text"] for char in page.chars) for page in pdf.pages)


def page_count(payload: bytes) -> int:
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        return len(pdf.pages)


def paragraph(text: str, style: str = "body") -> ParagraphBlock:
    return ParagraphBlock(runs=(Run(text=text),), style=style)  # type: ignore[arg-type]


def capital_block() -> object:
    payload = {
        "schema": fmt.PAYLOAD_SCHEMA,
        "title": "Capital position",
        "as_of": "2025-12-31",
        "source_label": "Official capital run (baseline)",
        "unit": {"currency": "GHS", "scale": 1},
        "tables": [
            {
                "key": "capital_components",
                "title": "Capital components",
                "columns": [
                    {"key": "label", "label": "Component", "kind": "text"},
                    {"key": "amount", "label": "Amount", "kind": "amount"},
                ],
                "rows": [
                    {"cells": {"label": "CET1 capital", "amount": "1234567"}},
                    {"cells": {"label": "Total capital", "amount": "-25"}, "emphasis": "total"},
                ],
            }
        ],
        "notes": ["Sourced from the sealed run."],
    }
    return fmt.block_render(
        block_id="b1",
        payload=payload,
        status="stale",
        fallback_title="Capital position",
        provenance=(("Run", "run-abc"), ("Input hash", "hash-def")),
    )


def build_document(  # noqa: PLR0913 - each keyword is a document property under test
    *,
    sections: tuple[SectionRender, ...] | None = None,
    is_rehearsal: bool = False,
    watermark: str | None = DRAFT_WATERMARK,
    blocks: dict[str, object] | None = None,
    fact_text: dict[tuple[str, str], str] | None = None,
    framework_status: str = "exposure_draft",
    attachments: tuple[AttachmentLine, ...] = (),
) -> IcaapDocument:
    if sections is None:
        sections = (
            SectionRender(
                key="executive_summary",
                letter="a",
                title="Executive Summary",
                citation_label="¶49(a)",
                source_status="sourced",
                content_label="Version 2 (committed)",
                blocks=(paragraph("The Board reviewed the capital position."),),
                checklist=(
                    ChecklistLine(
                        item_id="a1",
                        text="State the purpose of the ICAAP",
                        citation_label="¶49(a)",
                        status="not_applicable",
                        reason="The group has no subsidiaries",
                    ),
                ),
            ),
        )
    return IcaapDocument(
        institution_name="Sample Bank Ltd",
        institution_short_name="Sample Bank",
        cycle_title="ICAAP FY2025",
        fiscal_year=2025,
        as_of=date(2025, 12, 31),
        basis="solo",
        basis_label="Solo",
        cycle_kind="rehearsal" if is_rehearsal else "annual",
        cycle_kind_label="Rehearsal" if is_rehearsal else "Annual ICAAP",
        framework_code="BOG-ICAAP",
        framework_title="Guideline on ICAAP",
        framework_version="2026.02-ed",
        framework_status=framework_status,
        framework_digest="sha256:7f3c",
        regulator_short="BoG",
        currency="GHS",
        generated_at=GENERATED_AT,
        sections=sections,
        blocks=blocks or {},  # type: ignore[arg-type]
        fact_text=fact_text or {},
        attachments=attachments,
        readiness_summary=("2 items block freeze", "1 warning"),
        watermark=watermark,
        is_rehearsal=is_rehearsal,
    )


class TestDeterminism:
    def test_two_renders_are_byte_identical(self) -> None:
        document = build_document()
        assert render_pdf(document) == render_pdf(document)

    def test_a_re_render_draws_the_same_text(self) -> None:
        document = build_document(blocks={"b1": capital_block()})
        assert text_of(render_pdf(document)) == text_of(render_pdf(document))

    def test_the_generation_time_is_the_callers_not_the_clocks(self) -> None:
        # Two documents differing only in generated_at must differ; nothing
        # else in the render may read a clock.
        first = build_document()
        second = build_document()
        object.__setattr__(second, "generated_at", datetime(2026, 9, 20, 9, 0, 0))
        assert render_pdf(first) != render_pdf(second)


class TestWatermarks:
    def test_a_draft_says_so_on_every_page(self) -> None:
        payload = render_pdf(build_document())
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            for page in pdf.pages:
                assert DRAFT_WATERMARK in "".join(char["text"] for char in page.chars)

    def test_a_rehearsal_carries_the_rehearsal_wording_as_well(self) -> None:
        # D-029: a rehearsal walks the whole lifecycle, so the watermark is the
        # only thing distinguishing its output from a real filing.
        body = text_of(render_pdf(build_document(is_rehearsal=True)))
        assert DRAFT_WATERMARK in body
        assert REHEARSAL_WATERMARK in body

    def test_a_normal_cycle_does_not_claim_to_be_a_rehearsal(self) -> None:
        body = text_of(render_pdf(build_document(is_rehearsal=False)))
        assert REHEARSAL_WATERMARK not in body

    def test_a_document_with_no_watermark_carries_none(self) -> None:
        # The seam P3's filing PDF uses: same renderer, no draft marking.
        body = text_of(render_pdf(build_document(watermark=None)))
        assert DRAFT_WATERMARK not in body


class TestHostileText:
    @pytest.mark.parametrize(
        "hostile",
        [
            "<b>bold</b> & <script>alert(1)</script>",
            "a < b > c & d",
            "<font color='red'>red</font>",
            "<a href='http://example.invalid'>link</a>",
            "</p></td>",
        ],
    )
    def test_markup_prints_as_characters(self, hostile: str) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(paragraph(hostile),),
        )
        body = text_of(render_pdf(build_document(sections=(section,))))
        assert hostile in body

    def test_control_characters_do_not_break_the_export(self) -> None:
        # Word's soft line break (\x0b) reaches narrative text through paste;
        # it used to make an export impossible.
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(paragraph("before\x0bafter\x07bell\x00nul"),),
        )
        payload = render_pdf(build_document(sections=(section,)))
        body = text_of(payload)
        assert "before" in body
        assert "after" in body

    def test_a_very_long_word_still_produces_a_document(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(paragraph("A" * 4000),),
        )
        assert page_count(render_pdf(build_document(sections=(section,)))) >= 1

    def test_a_right_to_left_script_does_not_break_the_export(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(paragraph("التقييم الداخلي لكفاية رأس المال"),),
        )
        payload, substituted = render_pdf_with_report(build_document(sections=(section,)))
        assert page_count(payload) >= 1
        # Arabic is outside the standard font, so it is disclosed rather than
        # silently dropped.
        assert substituted > 0


class TestGlyphCoverage:
    def _cedi_document(self) -> IcaapDocument:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(paragraph("Capital of ₵1m, reviewed by Ɛ. Ɔsei."),),
        )
        return build_document(sections=(section,))

    def test_unprintable_characters_are_counted(self) -> None:
        _payload, substituted = render_pdf_with_report(self._cedi_document())
        assert substituted == 3  # ₵, Ɛ, Ɔ

    def test_the_count_reaches_the_provenance_page(self) -> None:
        body = text_of(render_pdf(self._cedi_document()))
        assert "3 character(s)" in body

    def test_the_note_never_claims_the_text_was_reproduced(self) -> None:
        # DV-004: the false "reproduced verbatim" wording is what this replaced.
        body = text_of(render_pdf(self._cedi_document())).lower()
        assert "verbatim" not in body
        assert "workspace record" in body

    def test_a_clean_document_still_explains_the_limitation(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "needed substituting" in body


class TestContent:
    def test_a_factref_prints_the_bound_figure(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(
                ParagraphBlock(
                    runs=(
                        Run(text="CAR is "),
                        Run(text="", fact=FactRef(block_id="b1", fact_key="car_pct")),
                        Run(text=" at year end."),
                    ),
                    style="body",
                ),
            ),
        )
        body = text_of(
            render_pdf(build_document(sections=(section,), fact_text={("b1", "car_pct"): "15.42%"}))
        )
        assert "CAR is 15.42% at year end." in body

    def test_a_fact_with_no_value_prints_not_available_not_zero(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(
                ParagraphBlock(
                    runs=(
                        Run(text="Buffer: "),
                        Run(text="", fact=FactRef(block_id="b1", fact_key="absent")),
                    ),
                    style="body",
                ),
            ),
        )
        body = text_of(render_pdf(build_document(sections=(section,))))
        assert f"Buffer: {NOT_AVAILABLE}" in body

    def test_a_bound_block_prints_its_table_and_staleness_note(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(DataBlockRef(block_id="b1"),),
        )
        body = text_of(
            render_pdf(build_document(sections=(section,), blocks={"b1": capital_block()}))
        )
        assert "CET1 capital" in body
        assert "1,234,567.00" in body
        assert "Newer figures are available" in body

    def test_a_referenced_block_the_cycle_no_longer_has_is_named(self) -> None:
        # Never invented, never silently dropped: the sentence around it would
        # otherwise point at nothing.
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(DataBlockRef(block_id="gone"),),
        )
        body = text_of(render_pdf(build_document(sections=(section,))))
        assert "no longer part of this cycle" in body

    def test_an_empty_section_prints_only_its_own_note(self) -> None:
        section = SectionRender(
            key="s",
            letter="q",
            title="Disclosure",
            citation_label="¶82",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(),
            empty_note="No text has been written for this section yet.",
        )
        body = text_of(render_pdf(build_document(sections=(section,))))
        assert "No text has been written for this section yet." in body

    def test_a_cycle_with_no_sections_still_renders(self) -> None:
        body = text_of(render_pdf(build_document(sections=())))
        assert "This cycle has no sections." in body
        assert "No requirements are recorded" in body

    def test_lists_and_quotes_survive(self) -> None:
        section = SectionRender(
            key="s",
            letter="a",
            title="Section",
            citation_label="",
            source_status="sourced",
            content_label="Working draft — not committed",
            blocks=(
                ListBlock(
                    ordered=True,
                    start=3,
                    items=((paragraph("third item"),), (paragraph("fourth item"),)),
                ),
                ListBlock(ordered=False, start=1, items=((paragraph("a bullet"),),)),
                QuoteBlock(children=(paragraph("a quoted line"),)),
            ),
        )
        body = text_of(render_pdf(build_document(sections=(section,))))
        assert "a bullet" in body
        assert "a quoted line" in body
        # The list starts where the author left it, not at 1 — and the marker
        # is drawn, so the DOCX and the PDF number the same list the same way.
        assert "3.third item" in body
        assert "4.fourth item" in body

    def test_the_reason_a_requirement_is_not_applicable_reaches_the_pdf(self) -> None:
        # REG-ICAAP-008: an unevidenced "not applicable" is a claim without a
        # basis, so the reason travels with the document.
        body = text_of(render_pdf(build_document()))
        assert "The group has no subsidiaries" in body

    def test_attachments_are_indexed_with_their_checksum(self) -> None:
        attachments = (
            AttachmentLine(
                kind_title="Board resolution",
                title="Resolution 12/2026",
                sha256_prefix="9f2a1c04",
                uploaded_on=date(2026, 1, 5),
                withdrawn=False,
            ),
            AttachmentLine(
                kind_title="Evidence",
                title="Superseded workbook",
                sha256_prefix="11bb22cc",
                uploaded_on=date(2026, 1, 6),
                withdrawn=True,
            ),
        )
        body = text_of(render_pdf(build_document(attachments=attachments)))
        assert "9f2a1c04" in body
        assert "Withdrawn" in body

    def test_no_attachments_is_stated_rather_than_left_blank(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "No documents have been attached" in body


class TestProvenance:
    def test_the_framework_and_digest_are_named(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "sha256:7f3c" in body
        assert "BOG-ICAAP" in body

    def test_an_exposure_draft_says_the_regulator_may_change_the_text(self) -> None:
        body = text_of(render_pdf(build_document(framework_status="exposure_draft")))
        assert "Exposure draft" in body

    def test_a_final_framework_makes_no_exposure_draft_claim(self) -> None:
        body = text_of(render_pdf(build_document(framework_status="final")))
        assert "Exposure draft" not in body

    def test_a_framework_that_moved_under_the_cycle_is_declared(self) -> None:
        # Same code and version, different text: the requirements printed here
        # are not the ones the cycle was built against, and the reader is told.
        document = build_document()
        object.__setattr__(
            document,
            "framework_digest_note",
            "This cycle recorded framework digest sha256:old; this export renders sha256:7f3c.",
        )
        body = text_of(render_pdf(document))
        assert "recorded framework digest sha256:old" in body

    def test_a_matching_framework_makes_no_such_claim(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "recorded framework digest" not in body

    def test_the_document_states_it_is_not_a_filing(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "not a regulatory filing" in body

    def test_figure_sources_carry_their_run_identity(self) -> None:
        body = text_of(render_pdf(build_document(blocks={"b1": capital_block()})))
        assert "run-abc" in body
        assert "hash-def" in body

    def test_the_footer_names_the_institution_and_framework(self) -> None:
        body = text_of(render_pdf(build_document()))
        assert "Sample Bank Ltd · ICAAP FY2025 (Solo)" in body
        assert "framework BOG-ICAAP 2026.02-ed" in body


class TestJurisdictionNeutrality:
    def test_nothing_ghanaian_is_written_by_the_renderer(self) -> None:
        document = build_document()
        object.__setattr__(document, "currency", "NGN")
        object.__setattr__(document, "regulator_short", "CBN")
        object.__setattr__(document, "institution_name", "Lagos Bank Plc")
        object.__setattr__(document, "framework_code", "CBN-ICAAP")
        object.__setattr__(document, "framework_title", "Guidance on ICAAP")
        body = text_of(render_pdf(document))
        assert "GHS" not in body
        assert "BoG" not in body
        assert "NGN" in body
