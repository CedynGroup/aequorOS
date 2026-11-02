"""An officer's name is text on the page, never markup reportlab interprets.

Why this exists (security audit, 2026-10-28 — issue #700)
---------------------------------------------------------
``User.display_name`` is free-form and every user may edit their own. A filing
stage decision snapshots it into the package's officer record, and the unsigned
attestation block printed it straight into a reportlab ``Paragraph``.
``Paragraph`` parses XML-like markup, and ``<img src=…>`` is fetched server-side
by ``ImageReader`` while the document is built — outside ``app.core.outbound``'s
egress guard. A preparer on an installation whose filing policy does not require
e-signature could therefore make the platform fetch an intranet or metadata URL
from inside the PDF it files, and could restyle the document of record.

The same rule covers the BoG form's cover: its Institution row prints
``Bank.name``, which staff provisioning sets, so that value reaches a
``Paragraph`` too.

The assertions are on the printed page, because that is where "escaped" and
"interpolated" differ: escaped markup comes out as the characters the officer
typed, and interpolated markup becomes an element reportlab acts on — which for
``<img>`` means a live HTTP request from the renderer.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime

import pdfplumber
import pytest

from app.reporting.service.bog_forms.engine import FormResult
from app.reporting.service.bog_forms.layout import FormLayout
from app.reporting.service.bog_forms.spec import FormSpec
from app.reporting.service.exports.pdf import (
    AttestedOfficer,
    render_pdf,
)
from app.reporting.service.templates import TEMPLATES, RenderedReturn

#: A name that makes reportlab fetch a URL and pull an image into the filed
#: document if it is interpolated rather than escaped.
IMAGE_FETCH = '<img src="http://169.254.169.254/latest/meta-data/" width="10"/>'

#: Each hostile name, and the fragments that must survive to the page. Fragments
#: rather than the whole string because a narrow cell wraps the name and the
#: extractor can interleave it with the column beside it — no wrap can drop a
#: character, and interpolated markup leaves none of these.
OFFICER_NAMES = [
    pytest.param(IMAGE_FETCH, ("<img", "169.254.169.254"), id="image-fetch"),
    pytest.param("<b>Prepared</b>", ("<b>Prepared</b>",), id="bold"),
    pytest.param("<a href='http://example.test'>click</a>", ("<a", "example.test"), id="link"),
    pytest.param("Ada & Lovelace <script>", ("Ada & Lovelace", "<script>"), id="ampersand-and-tag"),
]


def _officer(name: str, *, title: str | None = "Chief Financial Officer") -> AttestedOfficer:
    return AttestedOfficer(stage="Preparer", name=name, title=title, at="2025-12-31")


def _rendered_return(**pairs: str) -> RenderedReturn:
    """A return with no sections, so the whole page is the page furniture."""
    return RenderedReturn(
        template=TEMPLATES["bog-sdi-lmt-monthly-v1"],
        metadata_pairs=(("Return", "SDI-LMT-MONTHLY"), *pairs.items()),
        sections=(),
        provenance_runs=(),
        # render_pdf reads provenance_lines[0] for the page footer.
        provenance_lines=("Generated from the live snapshot.",),
    )


def _page_text(payload: bytes) -> str:
    with pdfplumber.open(io.BytesIO(payload)) as document:
        return " ".join(" ".join(page.extract_text() or "" for page in document.pages).split())


def _form_pdf(*, bank_name: str = "Sample Bank", officers: Sequence[AttestedOfficer] = ()) -> bytes:
    """The official BoG form as filed, through the public renderer.

    A spec with no sheets keeps the document to the pages this file is about —
    the cover and the attestation block.

    Imported here, not at module scope: ``bog_forms.render_pdf`` imports
    ``exports.pdf`` while ``exports/__init__`` imports ``render_form_pdf`` back
    from it, so whichever side loads FIRST is the one left partially
    initialized. The module-level import of ``exports.pdf`` above has already
    loaded the package in the order that works.
    """
    from app.reporting.service.bog_forms.render_pdf import (  # noqa: PLC0415
        render_form_pdf,
    )

    return render_form_pdf(
        FormResult(
            spec=FormSpec(
                code="BSD1",
                title="Daily Statement of Deposits",
                workbook="BSD1.xlsx",
                frequency="weekly",
                time_limit_days=2,
                basis="solo",
                sheets=(),
            ),
            layout=FormLayout(form="bsd1", workbook="BSD1.xlsx", sheets=()),
            lines=[],
            inputs={},
            formulas={},
            unmapped_cells=[],
            unresolved_external=[],
        ),
        bank_name=bank_name,
        period_label="Week ended 31 December 2025",
        reporting_date="31-Dec-25",
        generated_at=datetime(2026, 1, 2, tzinfo=UTC),
        signing_required=False,
        officers=officers,
    )


def _assert_printed_verbatim(printed: str, fragments: Sequence[str]) -> None:
    """The markup reached the page as characters, not as an element.

    ``<img …/>`` interpolated becomes a fetched image and leaves no text;
    ``<b>Prepared</b>`` becomes weight and loses its tags. Escaped, both print
    what the officer typed.
    """
    for fragment in fragments:
        assert fragment in printed, f"the name was not printed verbatim: {fragment!r}"


@pytest.mark.parametrize(("name", "fragments"), OFFICER_NAMES)
def test_the_unsigned_record_block_prints_the_name_literally(
    name: str, fragments: Sequence[str]
) -> None:
    """``signing_required=False`` is the block that used to interpolate the name."""
    payload = render_pdf(
        _rendered_return(),
        sandbox_watermark=False,
        signing_required=False,
        officers=(_officer(name),),
    )

    printed = _page_text(payload)
    _assert_printed_verbatim(printed, fragments)
    # The designation column is the officer's own title text too.
    assert "Chief Financial Officer" in printed


def test_the_bog_attestation_grid_prints_the_name_literally() -> None:
    """The BoG form's attestation grid escapes the same field the same way.

    Rendered through the public ``render_form_pdf``, so the assertion covers the
    page an institution actually files rather than one private helper's output.
    """
    printed = _page_text(_form_pdf(officers=(_officer(IMAGE_FETCH),)))

    # Interpolated, this line would instead be an ImageReader fetch of
    # 169.254.169.254 and none of these characters would be on the page.
    _assert_printed_verbatim(printed, ["<img", "169.254.169.254"])


def test_the_bog_cover_prints_the_bank_name_literally() -> None:
    """``Bank.name`` is operator-set text and is printed on the cover."""
    printed = _page_text(_form_pdf(bank_name=f"{IMAGE_FETCH} Bank"))

    _assert_printed_verbatim(printed, ["<img", "169.254.169.254", "Bank"])


def test_the_cover_does_not_interpret_snapshot_metadata() -> None:
    """The institution name on the cover is tenant-editable text, so escaped."""
    payload = render_pdf(
        _rendered_return(**{"Institution": "<b>Sample</b> Bank"}),
        sandbox_watermark=False,
        signing_required=False,
    )

    printed = _page_text(payload)
    assert "<b>Sample</b> Bank" in printed
