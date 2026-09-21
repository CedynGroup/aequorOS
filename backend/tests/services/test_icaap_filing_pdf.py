"""The ICAAP filing PDF — the document an officer signs and a regulator reads.

What is actually being protected:

* **the geometry pairing.** The rules this page strokes and the AcroForm fields
  ``pdf_signing`` places on them are arithmetic in two files. Nothing detects a
  drift between them except a printed filing with a signature stamp beside its
  line instead of on it, so the rendered page is MEASURED here — the same
  protection ``test_attestation_pdf_signing`` gives the standard layout, which
  P3-A explicitly handed over (P3-A §8 item 5);
* **both page shapes.** The Board slot ships disabled (D-043), so the default
  document has two blocks and the line that says where Board approval lives;
  a bank that enables the slot gets three. Both must be correct, and the
  two-block page must not simply be the three-block page with a gap;
* **the font.** DV-004's whole subject is that ``₵`` could not be drawn. The
  test reads the characters back out of the rendered page;
* **determinism.** A checksum over an export means nothing if the export is not
  reproducible;
* **isolation.** Existing returns' PDFs must be byte-identical, which is the
  condition D-022 attaches to embedding a font at all.
"""

from __future__ import annotations

import hashlib
import io
import re
from datetime import UTC, datetime

import pdfplumber
import pytest
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.reader import PdfFileReader

from app.services.attestation import pdf_signing
from app.services.attestation.pdf_signing import (
    ATTESTATION_PAGE_INDEX,
    MIN_BOX_SIZES,
    SIGNATURE_FIELD_TYPE,
)
from app.services.icaap.render.from_snapshot import (
    SnapshotError,
    filing_from_snapshot,
)
from app.services.regulatory_reporting.exports import icaap_pdf
from tests.services.icaap.filing_snapshot import (
    AKAN_VOWELS,
    CEDI,
    FakePackage,
    snapshot,
)

TWO_SIGNERS = ("preparer", "approver")
THREE_SIGNERS = ("preparer", "approver", "board")


# --------------------------------------------------------------------------
# Reading the rendered page back
# --------------------------------------------------------------------------
def page_text(payload: bytes) -> list[str]:
    """Every character each page draws, in drawing order.

    Deliberately the raw character stream and not ``extract_text``: it keeps
    rotated watermark glyphs and canvas-drawn labels in the same string as the
    body, which is what the glyph-coverage and page-shape assertions read.
    """
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        return ["".join(char["text"] for char in page.chars) for page in pdf.pages]


def all_text(payload: bytes) -> str:
    return "\n".join(page_text(payload))


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value)


def shows(payload: bytes, phrase: str) -> bool:
    """Whether the document draws this phrase, wherever its lines happen to break.

    Reading a laid-out table back as a string is lossy in both directions:
    concatenating characters swallows the break inside a wrapped cell
    ("12 March2027"), while ``extract_text`` and ``extract_words`` interleave
    columns whose cells wrap to different depths — and ``extract_text``
    additionally drops table cells outright on a dense page, which would make a
    MISSING table read as a passing test. Comparing with whitespace removed is
    immune to all of it, and the phrases asserted here are long enough that a
    false match across two cells is not a real risk.
    """
    return _squash(phrase) in _squash(all_text(payload))


def drawn_rules(payload: bytes, page_index: int) -> set[tuple[float, float, float]]:
    """Every stroked horizontal rule on a page as ``(y, x_start, x_end)``.

    A minimal content-stream walk. The signing blocks are drawn straight onto
    the canvas at absolute coordinates, so — unlike a flowed table — no
    translation stack has to be tracked; the ``q``/``Q`` pairs are handled
    anyway so a future flowable on this page cannot make the reading wrong.
    """
    reader = PdfFileReader(io.BytesIO(payload))
    page = reader.root["/Pages"]["/Kids"][page_index].get_object()
    contents = page.raw_get("/Contents").get_object()
    assert isinstance(contents, generic.StreamObject)
    data = contents.data
    token = re.compile(
        rb"\bq\b|\bQ\b|1 0 0 1 (-?[\d.]+) (-?[\d.]+) cm"
        rb"|(-?[\d.]+) (-?[\d.]+) m (-?[\d.]+) (-?[\d.]+) l"
    )
    stack: list[tuple[float, float]] = [(0.0, 0.0)]
    rules: set[tuple[float, float, float]] = set()
    for match in token.finditer(data):
        operator = match.group(0)
        if operator == b"q":
            stack.append(stack[-1])
            continue
        if operator == b"Q":
            if len(stack) > 1:
                stack.pop()
            continue
        origin_x, origin_y = stack[-1]
        if match.group(1) is not None:
            stack[-1] = (origin_x + float(match.group(1)), origin_y + float(match.group(2)))
            continue
        x1, y1, x2, y2 = (float(match.group(index)) for index in (3, 4, 5, 6))
        if abs(y1 - y2) < 0.01:
            left = round(origin_x + min(x1, x2), 2)
            right = round(origin_x + max(x1, x2), 2)
            rules.add((round(origin_y + y1, 2), left, right))
    return rules


def render(order: tuple[str, ...] = TWO_SIGNERS, **kwargs: object) -> bytes:
    filing = filing_from_snapshot(
        snapshot(**kwargs),  # type: ignore[arg-type]
        package=FakePackage(),
        signing_order=order,
    )
    return icaap_pdf.render_filing_pdf(filing)


# --------------------------------------------------------------------------
# Geometry: the page and the fields are one layout
# --------------------------------------------------------------------------
@pytest.mark.parametrize("order", [TWO_SIGNERS, THREE_SIGNERS])
def test_every_placed_field_lands_on_a_rule_this_page_actually_drew(
    order: tuple[str, ...],
) -> None:
    """The pairing P3-A §8 item 5 handed over, for both ceremonies.

    Reads the rules the page stroked and asserts every default placement sits ON
    one of them and INSIDE its horizontal span. Without this, the constants in
    ``pdf_signing`` and the ink on the page are two independent opinions.
    """
    payload = render(order)
    rules = drawn_rules(payload, ATTESTATION_PAGE_INDEX)
    # The navy header rule runs the full width near the top of every page.
    blocks = {rule for rule in rules if rule[0] < 700}
    placements = pdf_signing.default_placements(order, layout=icaap_pdf.LAYOUT)
    assert len(blocks) == len(placements), (
        f"expected one rule per placed cell, found {sorted(blocks)}"
    )
    for placement in placements:
        landed = [
            rule
            for rule in blocks
            if abs(rule[0] - placement.box[1]) <= 1.0
            and rule[1] <= placement.box[0] + 1.0
            and placement.box[2] <= rule[2] + 1.0
        ]
        assert landed, (
            f"the {placement.signing_role} {placement.field_type} box {placement.box} "
            f"sits on no ruled cell; the page drew {sorted(blocks)}"
        )


def test_the_rules_sit_at_the_signing_authority_s_own_coordinates() -> None:
    """``signing_rule_y('icaap')`` is the one source; the page must not restate it."""
    payload = render(THREE_SIGNERS)
    expected = set(pdf_signing.signing_rule_y(icaap_pdf.LAYOUT).values())
    drawn = {rule[0] for rule in drawn_rules(payload, ATTESTATION_PAGE_INDEX) if rule[0] < 700}
    assert expected <= {round(value) for value in drawn}


def test_the_signature_cell_clears_the_floor_the_stamp_is_refused_below() -> None:
    minimum_width, minimum_height = MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE]
    for placement in pdf_signing.default_placements(THREE_SIGNERS, layout=icaap_pdf.LAYOUT):
        if placement.field_type != SIGNATURE_FIELD_TYPE:
            continue
        left, bottom, right, top = placement.box
        assert right - left >= minimum_width
        assert top - bottom >= minimum_height


def test_no_body_text_runs_through_a_signature_box() -> None:
    """A statement flowing over a signature stamp is invisible until it is printed."""
    payload = render(THREE_SIGNERS)
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        page = pdf.pages[ATTESTATION_PAGE_INDEX]
        height = page.height
        # pdfplumber's y grows downward; convert to PDF user space.
        baselines = {round(height - char["bottom"], 2) for char in page.chars}
    for placement in pdf_signing.default_placements(THREE_SIGNERS, layout=icaap_pdf.LAYOUT):
        if placement.field_type != SIGNATURE_FIELD_TYPE:
            continue
        left, bottom, right, top = placement.box
        inside = {y for y in baselines if bottom < y < top}
        assert not inside, f"text sits inside the {placement.signing_role} stamp {placement.box}"


# --------------------------------------------------------------------------
# The two page shapes
# --------------------------------------------------------------------------
def test_the_default_document_has_two_blocks_and_says_where_board_approval_lives() -> None:
    """D-043: no dormant ``Sig_Board``, and no silence about the Board either."""
    payload = render(TWO_SIGNERS)
    attestation = page_text(payload)[ATTESTATION_PAGE_INDEX]
    assert attestation.count("Signature") == 2
    assert shows(payload, icaap_pdf.BOARD_RESOLUTION_NOTICE)
    assert "Approved by the Board of Directors" not in attestation


def test_enabling_the_board_slot_adds_a_third_block_and_drops_the_notice() -> None:
    payload = render(THREE_SIGNERS)
    attestation = page_text(payload)[ATTESTATION_PAGE_INDEX]
    assert attestation.count("Signature") == 3
    assert "Approved by the Board of Directors" in attestation
    assert not shows(payload, icaap_pdf.BOARD_RESOLUTION_NOTICE)


def test_both_shapes_print_each_officer_s_frozen_statement() -> None:
    """The statement is what the signature is attached to, so it is on the page."""
    two = page_text(render(TWO_SIGNERS))[ATTESTATION_PAGE_INDEX]
    three = page_text(render(THREE_SIGNERS))[ATTESTATION_PAGE_INDEX]
    assert "I prepared this ICAAP report" in two
    assert "Senior Management has reviewed" in two
    assert "The Board has reviewed and challenged" not in two
    assert "The Board has reviewed and challenged" in three


def test_the_attestation_is_page_index_one() -> None:
    """``ATTESTATION_PAGE_INDEX`` is where every placement says its page is."""
    pages = page_text(render(THREE_SIGNERS))
    assert "Attestation" in pages[ATTESTATION_PAGE_INDEX]
    placements = pdf_signing.default_placements(THREE_SIGNERS, layout=icaap_pdf.LAYOUT)
    assert {placement.page_index for placement in placements} == {ATTESTATION_PAGE_INDEX}


# --------------------------------------------------------------------------
# Font coverage (DV-004 / D-022)
# --------------------------------------------------------------------------
def test_the_cedi_sign_and_ghanaian_vowels_are_drawn_not_substituted() -> None:
    filing = filing_from_snapshot(
        snapshot(), package=FakePackage(), signing_order=TWO_SIGNERS
    )
    payload, substituted = icaap_pdf.render_filing_pdf_with_report(filing)
    text = all_text(payload)
    assert CEDI in text, "the reporting currency's own symbol did not reach the page"
    for vowel in AKAN_VOWELS:
        assert vowel in text
    assert substituted == 0
    assert "every character in it" in text.lower()


def test_user_prose_that_looks_like_markup_prints_as_typed() -> None:
    """reportlab's ``Paragraph`` parses markup; author text is never markup."""
    assert shows(render(), "<b>not markup</b>")


# --------------------------------------------------------------------------
# Content and provenance
# --------------------------------------------------------------------------
def test_the_provenance_page_states_the_filing_its_digests_and_its_inputs() -> None:
    payload = render()
    for expected in (
        "Return: ICAAP-REPORT",
        f"Package: {FakePackage().id} (version 1)",
        "c" * 64,  # content digest
        "9" * 64,  # review digest
        "capital-2026.3",  # engine version of the bound run
        "car_min",
        "Pending confirmation",
        "2 paragraph(s)",  # AI-assisted count
        "Efua Asante",  # the approver named on the review record
    ):
        assert shows(payload, expected), expected


def test_a_parameter_with_no_governed_row_is_named_not_omitted() -> None:
    """D-024 fails closed; the filed page says which value was not resolved."""
    payload = render()
    assert shows(payload, "icaap_stress_horizon_years_min")
    assert shows(payload, "Not resolved")


def test_the_exposure_draft_line_is_on_the_page() -> None:
    assert shows(render(), "EXPOSURE DRAFT")


def test_a_final_framework_drops_the_exposure_draft_line() -> None:
    assert not shows(render(TWO_SIGNERS, framework_status="final"), "EXPOSURE DRAFT")


def test_the_annex_and_its_source_package_are_printed() -> None:
    payload = render()
    assert shows(payload, "Annex A")
    assert shows(payload, "ICAAP-STRESS-APPENDIX2")
    assert shows(payload, "CAR under the severe scenario")


def test_the_filed_documents_are_listed_with_their_checksums() -> None:
    payload = render()
    assert shows(payload, "Board resolution of 12 March 2027")
    assert shows(payload, "1" * 64)
    # No fabricated upload date beside a checksum the manifest does not carry.
    assert not shows(payload, "Uploaded")


def test_a_data_block_prints_as_a_table_and_a_fact_ref_as_its_frozen_value() -> None:
    payload = render()
    assert shows(payload, "Capital components")
    assert shows(payload, "CET1 capital")
    # The factRef in the prose resolves to the figure the block was bound to.
    assert shows(payload, "Total regulatory capital of")
    assert shows(payload, "1,500,000")
    assert not shows(payload, "no longer part of this cycle")


def test_the_checklist_prints_the_requirement_and_why_one_was_set_aside() -> None:
    payload = render()
    assert shows(payload, "State the internal capital the Board considers adequate.")
    # REG-ICAAP-008: the reason travels with the document.
    assert shows(payload, "The institution has no subsidiaries.")


# --------------------------------------------------------------------------
# Determinism, watermarks and refusals
# --------------------------------------------------------------------------
def test_two_renders_of_one_sealed_package_are_byte_identical() -> None:
    assert render(THREE_SIGNERS) == render(THREE_SIGNERS)


def test_the_filing_carries_no_draft_watermark() -> None:
    assert "DRAFT" not in page_text(render())[0].upper().replace("EXPOSURE DRAFT", "")


def test_a_rehearsal_snapshot_is_watermarked_even_though_it_should_never_exist() -> None:
    """Rehearsals never mint a package. If one ever did, the page must say so."""
    payload = render(TWO_SIGNERS, cycle_kind="rehearsal")
    assert "REHEARSAL" in all_text(payload)


def test_an_explicit_draft_export_is_watermarked() -> None:
    from app.domain.icaap.document import DRAFT_WATERMARK  # noqa: PLC0415

    filing = filing_from_snapshot(
        snapshot(), package=FakePackage(), signing_order=TWO_SIGNERS, watermark=DRAFT_WATERMARK
    )
    assert "DRAFT" in all_text(icaap_pdf.render_filing_pdf(filing))


def test_an_unsigned_reading_copy_draws_no_blocks_and_says_so() -> None:
    payload = render(())
    assert not {rule for rule in drawn_rules(payload, ATTESTATION_PAGE_INDEX) if rule[0] < 700}
    assert shows(payload, "carries no signing blocks")


def test_a_snapshot_of_another_family_is_refused_rather_than_rendered() -> None:
    """Rendering something plausible from the wrong figures is the worse failure."""
    with pytest.raises(SnapshotError):
        filing_from_snapshot(
            {"return_code": "BSD1", "metadata": {"generated_at": "2026-01-01T00:00:00+00:00"}},
            package=FakePackage(),
        )


def test_the_generated_at_on_the_page_is_the_package_s_not_the_clock_s() -> None:
    package = FakePackage(generated_at=datetime(2027, 3, 1, 8, 30, tzinfo=UTC))
    filing = filing_from_snapshot(
        snapshot(), package=package, signing_order=TWO_SIGNERS
    )
    assert shows(icaap_pdf.render_filing_pdf(filing), "2027-03-01T08:30:00")


# --------------------------------------------------------------------------
# Isolation: no existing return's PDF moved (D-022's condition)
# --------------------------------------------------------------------------
def test_the_draft_renderer_escapes_identically_under_the_default_font() -> None:
    """D-022 permits the embedded font only if existing PDFs are byte-identical.

    Making the ``FontSet`` actually drive the escaping — rather than only the
    substitution counter, which is how ``₵`` used to print as ``?`` while the
    count said nothing had been substituted — touches the shared renderer. It is
    a no-op for every caller that keeps the default: ``HELVETICA.printable`` IS
    ``pdf_parts.printable``, so the escaped output is the same string it was.
    Asserted over the awkward cases rather than assumed.
    """
    from app.domain.icaap.document import IcaapDocument  # noqa: PLC0415
    from app.services.icaap.render.pdf import HELVETICA, IcaapPdfRenderer  # noqa: PLC0415
    from app.services.regulatory_reporting.exports import pdf_parts  # noqa: PLC0415

    assert HELVETICA.printable is pdf_parts.printable
    blank = IcaapDocument(
        institution_name="Sample Bank",
        institution_short_name="Sample",
        cycle_title="ICAAP",
        fiscal_year=2026,
        as_of=datetime(2026, 12, 31, tzinfo=UTC).date(),
        basis="solo",
        basis_label="Solo",
        cycle_kind="annual",
        cycle_kind_label="Annual",
        framework_code="F",
        framework_title="F",
        framework_version="1",
        framework_status="final",
        framework_digest="d",
        regulator_short="R",
        currency="GHS",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    renderer = IcaapPdfRenderer(blank, fonts=HELVETICA)
    for text in (
        "plain ascii",
        f"amount {CEDI}1,234",
        AKAN_VOWELS,
        "<b>markup</b> & entities <>&",
        "line\nbreak\r\nand\rcr",
        "emoji \U0001f600 and CJK 資本",
        "quotes “curly” ‘single’ – — …",
        "vertical\x0btab and form\x0cfeed",
        "",
    ):
        assert renderer._escape(text) == pdf_parts.escape_text(text), text  # noqa: SLF001


def test_the_generic_tabular_renderer_never_sees_the_embedded_font() -> None:
    """Every non-ICAAP return still renders through ``exports/pdf.py`` alone."""
    import inspect  # noqa: PLC0415

    from app.services.regulatory_reporting.exports import pdf as tabular  # noqa: PLC0415

    source = inspect.getsource(tabular)
    assert "icaap_fonts" not in source
    assert "AeqNotoSans" not in source


# --------------------------------------------------------------------------
# The golden
# --------------------------------------------------------------------------
#: SHA-256 over the DECODED page content streams and the font resources each
#: page names, for the two-signer and three-signer documents. Not a hash of the
#: file, deliberately: the file is Flate-compressed, so a raw digest also pins
#: the zlib build and would fail on a machine whose zlib emits different bytes
#: for identical content — a false alarm that teaches people to regenerate
#: goldens. This digest changes when, and only when, what is DRAWN changes.
#:
#: Regenerate by printing ``render_digest(render(order))`` and reading why the
#: page moved before pasting the new value in.
#:
#: Changed 2026-09-20, for two reasons both read before regenerating:
#:  * regulatory audit R-2 — the governed-parameter table headed the parameter
#:    CODE column "Value" and the value column "Applied", so a signed filing
#:    read "Value = car_min". Extracting the drawn text confirms the header is
#:    now "Parameter" and that the only remaining "applied" is the section
#:    heading "Regulatory values applied", which is intended.
#:  * architecture audit M3 — ICAAP packages now carry `snapshot["provenance"]`
#:    (it was skipped at the freeze mint), so the provenance block draws content
#:    where it previously had none.
#: Both are corrections to what the document SAYS, which is exactly what this
#: golden exists to surface.
RENDER_GOLDENS = {
    TWO_SIGNERS: "5f636536aa44275dba8e7e7e7f11bf204dd481a5f9a29c8be54697f20a143d10",
    THREE_SIGNERS: "22249020638b469cb350250e3e59e6061fd1b4d7b00e982e10febd6bf01a66dd",
}


def render_digest(payload: bytes) -> str:
    """Everything the document draws, independent of how the file is packed."""
    reader = PdfFileReader(io.BytesIO(payload))
    pages = reader.root["/Pages"]["/Kids"]
    digest = hashlib.sha256()
    digest.update(str(len(pages)).encode())
    for reference in pages:
        page = reference.get_object()
        contents = page.raw_get("/Contents").get_object()
        assert isinstance(contents, generic.StreamObject)
        digest.update(contents.data)
        resources = page.raw_get("/Resources").get_object()
        fonts = resources.raw_get("/Font").get_object() if "/Font" in resources else {}
        digest.update(",".join(sorted(str(name) for name in fonts)).encode())
    return digest.hexdigest()


@pytest.mark.parametrize("order", [TWO_SIGNERS, THREE_SIGNERS])
def test_the_rendered_page_matches_its_golden(order: tuple[str, ...]) -> None:
    assert render_digest(render(order)) == RENDER_GOLDENS[order]


def test_the_two_page_shapes_are_genuinely_different_documents() -> None:
    """Guards the golden above: two identical values would pass it vacuously."""
    assert render_digest(render(TWO_SIGNERS)) != render_digest(render(THREE_SIGNERS))


# --------------------------------------------------------------------------
# The editor grammar, end to end
# --------------------------------------------------------------------------
def test_every_block_node_the_editor_admits_reaches_the_page() -> None:
    """The walker is shared with the draft renderer; the filing must use it all."""
    payload = render()
    assert shows(payload, "Quoted board minute.")  # blockquote
    # The bullet glyph itself extracts as an unmapped cid: reportlab draws
    # ``ListFlowable``'s bullet from a symbol font with no ToUnicode entry, and
    # P1's Helvetica draft of the same document does exactly the same, so it is
    # pre-existing and not the embedded family's doing. The ITEM is what must be
    # on the page.
    assert shows(payload, "Credit risk")  # bulletList item
    assert shows(payload, "1.Market risk")  # orderedList marker + item
    assert shows(payload, f"Capital in {CEDI}")  # heading, in the embedded font
    assert shows(payload, "Bold&italic")  # marks, and the hardBreak between runs


def test_long_statements_do_not_rule_a_second_set_of_signing_blocks() -> None:
    """Only page 1 carries signing blocks, whatever the frozen wording's length.

    The attestation statements come from the framework, so their length is not
    ours to bound. When they overflowed, the page template leaked onto the
    overflow pages and drew a second and third set of empty rules — three
    signature lines on a filed document, one of which had a field behind it.
    """
    payload_snapshot = snapshot()
    statements = payload_snapshot["metadata"]["icaap"]["filing"]["statements"]
    for role in statements:
        statements[role] = "This is a very long attestation statement. " * 60
    filing = filing_from_snapshot(
        payload_snapshot, package=FakePackage(), signing_order=THREE_SIGNERS
    )
    payload = icaap_pdf.render_filing_pdf(filing)
    placements = pdf_signing.default_placements(THREE_SIGNERS, layout=icaap_pdf.LAYOUT)
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        page_count = len(pdf.pages)
    assert len(
        {rule for rule in drawn_rules(payload, ATTESTATION_PAGE_INDEX) if rule[0] < 700}
    ) == len(placements)
    # Other pages carry table grids, which are horizontal strokes too; what must
    # not appear anywhere else is a rule AT a signing cell's own coordinates.
    for index in range(page_count):
        if index == ATTESTATION_PAGE_INDEX:
            continue
        stray = [
            rule
            for rule in drawn_rules(payload, index)
            for placement in placements
            if abs(rule[0] - placement.box[1]) <= 1.0
            and abs(rule[1] - placement.box[0]) <= 1.0
            and abs(rule[2] - placement.box[2]) <= 1.0
        ]
        assert not stray, f"page {index} rules signing blocks that no field is placed on"
