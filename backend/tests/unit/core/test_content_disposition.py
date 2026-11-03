"""A download header cannot be steered by the filename an uploader chose.

Why this exists (security audit, 2026-10-28 — issue #711)
---------------------------------------------------------
An attachment keeps the filename the client sent. The download route used to
interpolate it straight into a header::

    f'attachment; filename="{row.original_filename}"'

so a file stored as ``x.pdf"; filename="evil.html`` downloaded as
``evil.html`` for every colleague who opened it. The bytes were still the
evidence file; only the name it was saved under belonged to the uploader.

These tests pin the helper at the boundary where it matters: the header string
a browser actually parses, and the guarantee that no stored name can break out
of the single quoted parameter it is meant to fill.
"""

from __future__ import annotations

import pytest

from app.core.content_disposition import (
    DEFAULT_FILENAME,
    attachment_headers,
    attachment_name_parts,
)

#: Names that would end the quoted ``filename`` parameter and append their own,
#: or reach outside the header entirely.
HOSTILE_NAMES = [
    pytest.param('x.pdf"; filename="evil.html', id="quote-closes-parameter"),
    pytest.param("x.pdf\"; filename*=UTF-8''evil%2Ehtml", id="quote-injects-rfc5987"),
    pytest.param("x.pdf; filename=evil.html", id="bare-semicolon"),
    pytest.param('x.pdf"', id="trailing-quote"),
    pytest.param('x"y.pdf', id="embedded-quote"),
    pytest.param("x;y.pdf", id="embedded-semicolon"),
    pytest.param("x\r\ny.pdf", id="header-split"),
    pytest.param("x\ny.pdf", id="bare-newline"),
    pytest.param("x\x00y.pdf", id="nul-byte"),
    pytest.param("../../etc/passwd", id="posix-traversal"),
    pytest.param("..\\..\\windows\\system32\\evil.pdf", id="windows-traversal"),
    pytest.param("C:\\Users\\bank\\evidence.pdf", id="windows-drive"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="blank"),
    pytest.param(".", id="dot"),
    pytest.param("..", id="dot-dot"),
    pytest.param("报告.pdf", id="non-ascii"),
    pytest.param('quote"and;semicolon&.pdf', id="mixed"),
]


def _parameters(header: str) -> dict[str, str]:
    """The header split into ``name=value`` pairs, quotes stripped."""
    _, _, tail = header.partition("; ")
    pairs: dict[str, str] = {}
    for part in tail.split("; "):
        name, _, value = part.partition("=")
        pairs[name.strip()] = value.strip().strip('"')
    return pairs


def test_the_header_is_an_attachment_with_both_name_forms() -> None:
    header = attachment_headers("BSD1-2025-12.xlsx")["Content-Disposition"]

    assert header.startswith("attachment; ")
    assert _parameters(header)["filename"] == "BSD1-2025-12.xlsx"
    # The RFC 5987 form carries the same name, encoded in full.
    assert _parameters(header)["filename*"] == "UTF-8''BSD1-2025-12.xlsx"


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_no_stored_name_can_add_a_header_parameter(name: str) -> None:
    """The whole point: the name contributes ONE value, and nothing else.

    Asserted on the string a browser receives rather than on an intermediate,
    because a helper that is safe in isolation can still be assembled unsafely.
    """
    header = attachment_headers(name)["Content-Disposition"]

    # A carriage return, a newline or a raw backslash anywhere in the header
    # would let the name escape its parameter or end the header.
    assert "\r" not in header
    assert "\n" not in header
    assert "\\" not in header

    parameters = _parameters(header)
    assert set(parameters) == {"filename", "filename*"}
    # The ASCII form is restricted to characters no filesystem reinterprets.
    assert all(character.isalnum() or character in "._-" for character in parameters["filename"])
    # The encoded form is percent-encoded in full, so no separator survives.
    for separator in ('"', ";", "/", "\\"):
        assert separator not in parameters["filename*"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param('x.pdf"; filename="evil.html', "x.pdf_filename_evil.html", id="quote-run"),
        pytest.param("../../etc/passwd", "passwd", id="posix-traversal"),
        pytest.param("C:\\Users\\bank\\evidence.pdf", "evidence.pdf", id="windows-path"),
        pytest.param("   ", DEFAULT_FILENAME, id="blank"),
        pytest.param("..", DEFAULT_FILENAME, id="dot-dot"),
        pytest.param("报告.pdf", f"{DEFAULT_FILENAME}.pdf", id="non-ascii"),
    ],
)
def test_the_ascii_form_is_inert_and_keeps_the_extension(name: str, expected: str) -> None:
    ascii_name, _ = attachment_name_parts(name)
    assert ascii_name == expected


def test_the_utf8_form_keeps_the_banks_own_spelling() -> None:
    """A client that honours RFC 5987 saves the name the bank uploaded."""
    _, utf8_name = attachment_name_parts("../../报告 2025.pdf")
    assert utf8_name == "报告 2025.pdf"

    header = attachment_headers("../../报告 2025.pdf")["Content-Disposition"]
    assert _parameters(header)["filename*"] == "UTF-8''%E6%8A%A5%E5%91%8A%202025.pdf"


@pytest.mark.parametrize("name", [pytest.param(".", id="dot"), pytest.param("..", id="dot-dot")])
def test_a_directory_name_falls_back_in_both_name_forms(name: str) -> None:
    """RFC 6266 §4.3: ``.`` and ``..`` are ignored or substituted by recipients.

    They are not filenames, so neither parameter may carry one: the ASCII form
    and the UTF-8 form a browser chooses between must name the same fallback.
    """
    assert attachment_name_parts(name) == (DEFAULT_FILENAME, DEFAULT_FILENAME)

    parameters = _parameters(attachment_headers(name)["Content-Disposition"])
    assert parameters["filename"] == DEFAULT_FILENAME
    assert parameters["filename*"] == f"UTF-8''{DEFAULT_FILENAME}"


def test_a_long_name_does_not_grow_the_header_without_bound() -> None:
    """The schema caps a stored name at 255 characters; the ASCII stem is capped."""
    ascii_name, _ = attachment_name_parts("a" * 255 + ".pdf")
    assert len(ascii_name) < 140
