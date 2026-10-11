"""The embedded report family: what it can draw, and that it is what it claims.

Three separate promises are checked here, because each fails differently:

* **Provenance.** The README states a source, a cut and a SHA-256 per file. A
  font replaced without its README row is a licence and provenance claim that no
  longer matches the bytes we embed in a regulator's copy of a filing.
* **Coverage.** The reason the family exists is that Helvetica cannot draw ``₵``
  (deviation DV-004). A face that quietly lost it would take the filing PDF back
  to printing ``?`` for the currency the report is denominated in, and nothing
  else in the suite would notice.
* **Isolation.** D-022 makes the change conditional on existing returns being
  untouched, which is only true while nothing outside the ICAAP filing PDF
  imports this module.
"""

from __future__ import annotations

import re

import pytest
from fontTools.ttLib import TTFont

from app.icaap.service.render.pdf import render_pdf
from app.services.regulatory_reporting.exports import icaap_fonts
from tests.icaap.service.test_draft_pdf import build_document
from tests.icaap.service.test_icaap_filing_pdf import font_resources, render


def test_every_committed_face_and_the_licence_are_present() -> None:
    for file_name in icaap_fonts.FACE_FILES.values():
        assert icaap_fonts.font_path(file_name).is_file(), file_name
    licence = icaap_fonts.licence_text()
    # OFL §2 requires the licence to travel with the files; embedding a face we
    # could not lawfully embed would make the filed instrument itself defective.
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence
    assert "Noto Project Authors" in licence


def test_the_readme_checksums_match_the_committed_bytes() -> None:
    """The provenance table is the claim; the files are the fact."""
    readme = (icaap_fonts.FONT_DIR / "README.md").read_text(encoding="utf-8")
    row = re.compile(r"`([A-Za-z0-9.\-]+\.(?:ttf|txt))` \| \d+ \| `([0-9a-f]{64})`")
    stated = dict(row.findall(readme))
    assert stated, "the README's committed-file table could not be parsed"
    actual = icaap_fonts.file_digests()
    assert stated == actual, (
        "fonts/README.md and fonts/ disagree. Regenerating a face means updating "
        "its row in the same commit."
    )


@pytest.mark.parametrize("face_file", sorted(icaap_fonts.FACE_FILES.values()))
def test_every_face_draws_the_repertoire_the_report_promises(face_file: str) -> None:
    """Bold and italic are checked too: a gap in one face is a gap in the page."""
    with TTFont(icaap_fonts.font_path(face_file), lazy=True) as font:
        cmap = font.getBestCmap() or {}
        # pyHanko's OpenType path and the rest of the repo's embedded faces are
        # on a 1000-unit grid; a 2048-unit face would make the advances wrong.
        assert getattr(font["head"], "unitsPerEm") == 1000  # noqa: B009 - fontTools table
    missing = [ch for ch in icaap_fonts.PROMISED_CHARACTERS if ord(ch) not in cmap]
    assert not missing, f"{face_file} cannot draw {''.join(missing)!r}"


def test_the_cedi_sign_is_the_character_this_whole_module_exists_for() -> None:
    """DV-004 named it. Asserted on its own so the failure says why it matters."""
    with TTFont(icaap_fonts.font_path(icaap_fonts.FACE_FILES[icaap_fonts.FAMILY_NAME])) as font:
        assert 0x20B5 in (font.getBestCmap() or {})


def test_the_font_set_substitutes_only_what_it_cannot_draw() -> None:
    fonts = icaap_fonts.register_noto()
    assert fonts.printable("Total ₵100 ƐɛƆɔ") == "Total ₵100 ƐɛƆɔ"
    # Something genuinely outside the family still shows a visible substitute
    # rather than a black box or a silent drop.
    drawn = fonts.printable("\U0001f600")
    assert drawn == "?"


def test_registration_is_idempotent_and_registers_the_family() -> None:
    """reportlab resolves ``<b>`` through the FAMILY, not the face names."""
    from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415 - reportlab is heavy

    first = icaap_fonts.register_noto()
    assert icaap_fonts.register_noto() is first
    for name in icaap_fonts.FACE_FILES:
        assert pdfmetrics.getFont(name) is not None
    assert pdfmetrics.getFont(first.bold).fontName == first.bold


def test_only_the_icaap_filing_pdf_embeds_the_font_family() -> None:
    document = build_document()
    before = render_pdf(document)
    filing_fonts = font_resources(render())
    embedded_names = {name for name, embedded in filing_fonts if embedded}
    assert embedded_names
    assert all("NotoSans" in name for name in embedded_names)
    after = render_pdf(document)
    assert before == after
    draft_fonts = font_resources(after)
    assert draft_fonts
    assert {name for name, _ in draft_fonts} <= {
        "/Helvetica",
        "/Helvetica-Bold",
        "/Helvetica-Oblique",
        "/Helvetica-BoldOblique",
    }
    assert not any(embedded for _, embedded in draft_fonts)
