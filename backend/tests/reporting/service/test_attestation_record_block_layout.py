"""The unsigned attestation row holds the officer's text without spilling.

Why this file exists (review of the 2026-10-28 filing-escape fix)
-----------------------------------------------------------------
``_record_block`` used to pin the value row at 16 points — one line of body text
— and reportlab draws an over-tall cell straight over the row beneath it, so a
name that wrapped landed on top of the Name / Designation / Date labels. The row
is now sized to its content instead.

Sizing alone is not enough, because a display name may hold line breaks and 255
characters: spent on single characters and newlines, that content makes the row
taller than the page frame, and reportlab refuses a flowable it cannot fit. So
each field of this one-line-per-value block also collapses its line breaks, and
these tests pin both: the name never reaches the labels, and a maximal
line-broken name still produces a filed document.

The assertions are on the rendered page, because a row that is too short is
invisible in the renderer's inputs and obvious in the PDF: geometry says where
the text actually went.
"""

from __future__ import annotations

import io

import pdfplumber
import pytest

from app.reporting.service.exports.pdf import AttestedOfficer, render_pdf
from app.reporting.service.templates import TEMPLATES, RenderedReturn

#: Attestation page: the cover is page index 0.
_ATTESTATION_PAGE = 1

#: A name too long for the 207-point column at body size, so it wraps without
#: any line break in the stored value.
WRAPPED_NAME = "Abena Serwaa Mensah-Danquah Owusu-Ansah Boateng"


def _rendered_return() -> RenderedReturn:
    return RenderedReturn(
        template=TEMPLATES["bog-sdi-lmt-monthly-v1"],
        metadata_pairs=(("Return", "SDI-LMT-MONTHLY"),),
        sections=(),
        provenance_runs=(),
        # render_pdf reads provenance_lines[0] for the page footer.
        provenance_lines=("Generated from the live snapshot.",),
    )


def _page_words(payload: bytes) -> list[dict[str, float | str]]:
    with pdfplumber.open(io.BytesIO(payload)) as document:
        return document.pages[_ATTESTATION_PAGE].extract_words()


def _record_payload(name: str, *, title: str = "Chief Risk Officer") -> bytes:
    return render_pdf(
        _rendered_return(),
        sandbox_watermark=False,
        signing_required=False,
        officers=(AttestedOfficer(stage="Preparer", name=name, title=title, at="2025-12-31"),),
    )


def _assert_above_the_labels(words: list[dict[str, float | str]], name: str) -> None:
    """The printed name ends above the Name / Designation / Date label row."""
    labels = [word for word in words if word["text"] == "Name"]
    assert len(labels) == 1, f"expected one Name label row, found {len(labels)}"
    label_top = min(float(word["top"]) for word in labels)

    name_bottom = max(float(word["bottom"]) for word in words if str(word["text"]) in name.split())
    assert name_bottom <= label_top, (
        f"the name's last line ends at {name_bottom:.1f}pt, at or below the "
        f"field label's top at {label_top:.1f}pt"
    )


def test_a_wrapped_officer_name_stays_above_its_field_labels() -> None:
    """A name too long for the column wraps, and the row grows to hold it."""
    words = _page_words(_record_payload(WRAPPED_NAME))

    # Printed in full: a row that clipped its value would lose the last word,
    # and one that overflowed would draw it over the labels.
    assert any(word["text"] == "Boateng" for word in words), "the name was not printed in full"
    _assert_above_the_labels(words, WRAPPED_NAME)


def test_a_line_break_in_a_display_name_prints_as_one_line() -> None:
    """The field is one ruled line, so a stored break reads as the space it is."""
    words = _page_words(_record_payload("Ada\nLovelace"))

    assert "Ada Lovelace" in " ".join(str(word["text"]) for word in words)
    tops = {round(float(word["top"]), 1) for word in words if word["text"] in {"Ada", "Lovelace"}}
    assert len(tops) == 1, f"the name was drawn on more than one line: {sorted(tops)}"
    _assert_above_the_labels(words, "Ada Lovelace")


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param("\n", id="newline"),
        pytest.param("\x0b", id="vertical-tab"),
        pytest.param("\x0c", id="form-feed"),
    ],
)
def test_a_maximal_line_broken_name_still_exports(separator: str) -> None:
    """200 characters spent on line breaks must not block the filing export.

    ``decided_by_name`` holds at most 200 characters, and reportlab counts the
    vertical tab and form feed of a pasted document as line breaks alongside the
    newline. Sized purely to its content the row would then be taller than the
    page frame, and reportlab refuses a flowable it cannot fit (``LayoutError``):
    an officer could stop an unsigned return from being filed by editing their
    own name.
    """
    lines = [f"{index:02d}" for index in range(66)]
    name = separator.join(lines)
    assert len(name) == 197

    with pdfplumber.open(io.BytesIO(_record_payload(name))) as document:
        text = " ".join(" ".join(page.extract_text() or "" for page in document.pages).split())

    assert lines[0] in text
    assert lines[-1] in text
