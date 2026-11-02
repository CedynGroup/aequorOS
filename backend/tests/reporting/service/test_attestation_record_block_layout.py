"""The unsigned attestation row holds the officer's text, however tall it is.

Why this file exists (review of the 2026-10-28 filing-escape fix)
-----------------------------------------------------------------
``escape_text`` turns a line break in a display name into ``<br/>``, so an
officer whose name carries one prints over two lines. ``_record_block`` used to
pin the value row at 16 points — one line of body text — and reportlab draws an
over-tall cell straight over the row beneath it, so the second line of the name
landed on top of the Name / Designation / Date labels.

The assertion is on the rendered page, because a row that is too short is
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


@pytest.mark.parametrize(
    ("name", "last_token"),
    [
        pytest.param("Ada\nLovelace", "Lovelace", id="line-break"),
        pytest.param(WRAPPED_NAME, "Boateng", id="wrapped"),
    ],
)
def test_a_multi_line_officer_name_stays_above_its_field_labels(name: str, last_token: str) -> None:
    payload = render_pdf(
        _rendered_return(),
        sandbox_watermark=False,
        signing_required=False,
        officers=(
            AttestedOfficer(
                stage="Preparer", name=name, title="Chief Risk Officer", at="2025-12-31"
            ),
        ),
    )

    words = _page_words(payload)
    labels = [word for word in words if word["text"] == "Name"]
    assert len(labels) == 1, f"expected one Name label row, found {len(labels)}"
    label_top = min(float(word["top"]) for word in labels)

    # The name is printed in full — a clipped or overrun row would lose this.
    assert any(word["text"] == last_token for word in words), "the name was not printed in full"
    name_bottom = max(float(word["bottom"]) for word in words if str(word["text"]) in name.split())
    assert name_bottom <= label_top, (
        f"the name's last line ends at {name_bottom:.1f}pt, at or below the "
        f"field label's top at {label_top:.1f}pt"
    )
