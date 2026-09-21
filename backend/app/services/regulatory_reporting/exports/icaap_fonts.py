"""The embedded Unicode family the ICAAP filing PDF is set in (D-022, DV-004).

Every other regulatory PDF is set in reportlab's standard Helvetica, whose
repertoire is Windows-1252. That is the whole of deviation DV-004: the Ghana
cedi sign ``₵`` (U+20B5) and the Ghanaian vowels ``Ɛ ɛ Ɔ ɔ Ŋ ŋ Ƒ ƒ Ʋ ʋ Ɖ ɖ``
cannot be drawn by it, so they printed as a visible ``?`` with a note saying the
exact text was held in the record. P0 made that honest; it did not make it
right. A filed document that cannot print the currency it is denominated in is
defective, and this module is the fix DV-004 deferred to exactly here.

**Scope is the point.** Nothing outside ``exports/icaap_pdf.py`` imports this
module, so no other return's PDF gains a font resource, changes a byte, or
acquires a dependency on a file in ``fonts/``. D-022 attaches that condition to
the change and the export goldens enforce it.

Two mechanical facts worth knowing:

* **Registration is global and idempotent.** reportlab's font registry is
  process-wide state, so registering twice in one process would be wasted work
  and — worse — a second registration racing the first. :func:`register_noto`
  is cached, which makes it safe to call per export.
* **``<b>``/``<i>`` resolve through the FAMILY, not the face names.** A
  ``Paragraph`` that contains ``<b>`` asks ``registerFontFamily`` for the bold
  member of the *current* font. Registering the four faces without the family
  would silently render bold text in the regular face, which is the kind of
  defect nobody notices until a regulator's copy is printed.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path
from typing import Final

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.services.icaap.render.pdf import FontSet
from app.services.regulatory_reporting.exports.text import xml_safe

#: The committed faces. Documented, with checksums, in ``fonts/README.md``.
FONT_DIR: Final = Path(__file__).parent / "fonts"

#: The reportlab registration name. Deliberately prefixed: reportlab's registry
#: is global, and a bare "Noto Sans" would collide with any other component that
#: registered the same family from a different file.
FAMILY_NAME: Final = "AeqNotoSans"

#: ``reportlab name -> file``. The regular face carries the bare family name
#: because ``registerFontFamily`` keys its lookup on the normal member's name.
FACE_FILES: Final[dict[str, str]] = {
    FAMILY_NAME: "NotoSans-Regular.ttf",
    f"{FAMILY_NAME}-Bold": "NotoSans-Bold.ttf",
    f"{FAMILY_NAME}-Italic": "NotoSans-Italic.ttf",
    f"{FAMILY_NAME}-BoldItalic": "NotoSans-BoldItalic.ttf",
}

#: The licence file that must travel with the fonts (SIL OFL 1.1 §2).
LICENCE_FILE: Final = "OFL.txt"

#: The repertoire this family is *promised* to draw, asserted by a test rather
#: than assumed. The cedi sign is the one DV-004 was written about; the vowels
#: and hooked consonants are the Ghanaian orthographies (Akan, Ewe, Dagbani,
#: Ga) a bank's own name or an officer's designation is written in; the
#: punctuation is what a word processor substitutes as an author types.
PROMISED_CHARACTERS: Final = "₵ƐɛƆɔŊŋƑƒƲʋƉɖ‘’“”–—…·•°"


class FontUnavailableError(RuntimeError):
    """A committed face is missing or unreadable.

    Raised rather than falling back to Helvetica. The fallback is available and
    would even look fine — right up to the first ``₵``, which would print as
    ``?`` in a document that says on its own provenance page that it is set in a
    Unicode face. A build that lost its fonts should fail loudly at the export,
    not quietly on page four of a filing.
    """


def font_path(face_file: str) -> Path:
    """The absolute path of one committed face file."""
    return FONT_DIR / face_file


def _printable(text: str) -> str:
    """``text`` restricted to what this family can actually draw.

    Same contract as ``pdf_parts.printable`` — control characters made safe,
    then one substitute character per undrawable character, never a silent drop
    — but the test is this font's own ``cmap`` instead of Windows-1252. That is
    what keeps the substitution count on the provenance page truthful for
    whichever font actually rendered the page.
    """
    from app.services.regulatory_reporting.exports.pdf_parts import (  # noqa: PLC0415
        UNPRINTABLE_SUBSTITUTE,
    )

    text = xml_safe(text)
    if text.isascii():
        return text
    drawable = _drawable_codepoints()
    return "".join(ch if ord(ch) in drawable else UNPRINTABLE_SUBSTITUTE for ch in text)


@cache
def _drawable_codepoints() -> frozenset[int]:
    """Every codepoint the REGULAR face maps, read from the file itself.

    The regular face is the measure because it is the one the body is set in;
    the coverage test asserts the other three agree on the promised repertoire,
    so a bold-only gap cannot hide behind this.
    """
    from fontTools.ttLib import TTFont as FontToolsFont  # noqa: PLC0415 - heavy, read once

    path = font_path(FACE_FILES[FAMILY_NAME])
    try:
        with FontToolsFont(path, lazy=True) as font:
            # A face with no usable unicode cmap draws nothing we can promise,
            # so it substitutes everything rather than claiming coverage.
            return frozenset(font.getBestCmap() or {})
    except OSError as exc:  # pragma: no cover - a broken deployment, not a branch
        raise FontUnavailableError(
            f"The ICAAP report font {path.name} could not be read from {path.parent}. "
            "The filing PDF embeds a Unicode family so it can print the reporting "
            "currency's symbol; it does not fall back to a font that cannot."
        ) from exc


@cache
def register_noto() -> FontSet:
    """Register the four faces and the family, and return the :class:`FontSet`.

    Cached: reportlab's registry is process-global, so this runs once per
    process however many ICAAP reports are exported.
    """
    for name, file_name in FACE_FILES.items():
        path = font_path(file_name)
        if not path.is_file():
            raise FontUnavailableError(
                f"The ICAAP report font {file_name} is missing from {FONT_DIR}. "
                "The filing PDF embeds a Unicode family (D-022) so it can print the "
                "cedi sign and non-Latin-1 names; it does not fall back to a font "
                "that cannot draw them."
            )
        pdfmetrics.registerFont(TTFont(name, str(path)))
    # Without this, "<b>" inside a Paragraph resolves to the regular face.
    pdfmetrics.registerFontFamily(
        FAMILY_NAME,
        normal=FAMILY_NAME,
        bold=f"{FAMILY_NAME}-Bold",
        italic=f"{FAMILY_NAME}-Italic",
        boldItalic=f"{FAMILY_NAME}-BoldItalic",
    )
    return FontSet(
        normal=FAMILY_NAME,
        bold=f"{FAMILY_NAME}-Bold",
        italic=f"{FAMILY_NAME}-Italic",
        bold_italic=f"{FAMILY_NAME}-BoldItalic",
        printable=_printable,
    )


def licence_text() -> str:
    """The SIL OFL 1.1 text committed beside the faces (OFL §2 travels with them)."""
    return (FONT_DIR / LICENCE_FILE).read_text(encoding="utf-8")


def file_digests() -> dict[str, str]:
    """``file name -> sha256`` for every committed file in ``fonts/``.

    The README states these; the test compares the two. A font replaced without
    its README row is a provenance claim that no longer matches the bytes.
    """
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(FONT_DIR.iterdir())
        if path.is_file() and path.suffix in {".ttf", ".txt"}
    }


__all__ = [
    "FACE_FILES",
    "FAMILY_NAME",
    "FONT_DIR",
    "LICENCE_FILE",
    "PROMISED_CHARACTERS",
    "FontUnavailableError",
    "file_digests",
    "font_path",
    "licence_text",
    "register_noto",
]
