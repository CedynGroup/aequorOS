"""The faces a typed signature mark can be adopted in (§2.5, §3.2).

Two families of face, and the difference between them is a licence, not a
preference:

* Four **bundled script faces** — real handwriting-style typefaces committed
  under the SIL Open Font License in ``fonts/`` (see that directory's README).
  They are embedded as subsets through pyHanko's OpenType path, so the glyphs a
  BoG examiner sees are the glyphs this repository shipped, on any reader.
* One **standard-14 fallback**, plus the three other base-14 faces this module
  has always offered. A base-14 face needs no font file, so a deployment that
  strips ``fonts/`` — a slim image, a vendored build — can still stamp a typed
  mark instead of failing every signature.

The script faces exist because the base-14 alternatives are slanted *body text*:
"Times Italic" set at 18pt does not read as a signature to anyone holding the
paper, which is the whole job of the mark. They are listed first because that is
the order the adoption panel offers them in, and :data:`PREFERRED_SCRIPT_KEY` is
what a signer who adopted nothing at all gets stamped in.

**Availability is checked, never assumed.** :meth:`TypedFace.available` reads the
filesystem. An officer who explicitly chose a face whose file is gone is
*refused*, not quietly re-set in Times — substituting a different mark than the
one a person adopted is exactly the class of silent change this module family
exists to prevent. The only place a substitution is legitimate is the fallback
for a signer who adopted nothing, because there no choice is being overridden.

The keys are the wire vocabulary (``models.attestation.TYPED_SIGNATURE_FONTS``,
which this catalogue must agree with exactly — asserted in the tests) and are
persisted on ``signature_appearances.typed_font``, so renaming one would orphan
every adopted mark.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

#: Where the bundled ``.ttf`` files and their licence texts live. A sibling
#: directory rather than a configurable path: the licence obligation travels
#: with the font file, and a font loaded from wherever an operator points us is
#: a font nobody reviewed the licence of.
FONT_DIR: Final = Path(__file__).with_name("fonts")

#: Vertical extent of one line in a standard-14 face, in ems. The base-14
#: metrics are close enough to each other that one pair covers all four, and
#: they are only used to centre a mark in its band — a point of slack there is
#: invisible, whereas a wrong number for a SCRIPT face (whose ascenders and
#: descenders are enormous) clips a signature, which is why those are measured.
_BASE_14_ASCENT: Final = 0.75
_BASE_14_DESCENT: Final = 0.25


@dataclass(frozen=True)
class FaceMetrics:
    """How far one line of a face reaches above and below its baseline, in ems."""

    ascent: float
    descent: float

    @property
    def line_height(self) -> float:
        return self.ascent + self.descent


@dataclass(frozen=True)
class TypedFace:
    """One adoptable face: how it is labelled, and how it reaches the page."""

    key: str
    label: str
    #: File name inside :data:`FONT_DIR` for a bundled script face; ``None`` for
    #: a standard-14 face, which the PDF names rather than carries.
    file_name: str | None = None
    #: PDF standard-14 ``/BaseFont`` name; ``None`` for a bundled face.
    base_14: str | None = None

    @property
    def is_script(self) -> bool:
        return self.file_name is not None

    @property
    def path(self) -> Path | None:
        return None if self.file_name is None else FONT_DIR / self.file_name

    def available(self) -> bool:
        """Whether this face can actually be drawn in this deployment."""
        path = self.path
        return path.is_file() if path is not None else self.base_14 is not None

    def metrics(self) -> FaceMetrics:
        """The face's own line extents, read from the file where there is one."""
        path = self.path
        if path is None:
            return FaceMetrics(_BASE_14_ASCENT, _BASE_14_DESCENT)
        return _measure(str(path))


@cache
def _measure(path: str) -> FaceMetrics:
    """``hhea`` extents in ems, cached per file.

    fontTools is imported here rather than at module scope: the catalogue is
    read on every adoption-panel request, and only a deployment that actually
    stamps a script face should pay for the font parser.
    """
    from fontTools.ttLib import TTFont  # noqa: PLC0415

    font = TTFont(path, lazy=True)
    try:
        units = font["head"].unitsPerEm  # pyright: ignore[reportAttributeAccessIssue]
        hhea = font["hhea"]
        return FaceMetrics(
            ascent=hhea.ascender / units,  # pyright: ignore[reportAttributeAccessIssue]
            descent=abs(hhea.descender) / units,  # pyright: ignore[reportAttributeAccessIssue]
        )
    finally:
        font.close()


#: Script faces first: they are what a signature is supposed to look like, and
#: the panel's first option is the one most officers will take.
TYPED_FACES: Final[dict[str, TypedFace]] = {
    face.key: face
    for face in (
        TypedFace("caveat", "Caveat", file_name="Caveat-Regular.ttf"),
        TypedFace("dancing_script", "Dancing Script", file_name="DancingScript-Regular.ttf"),
        TypedFace("great_vibes", "Great Vibes", file_name="GreatVibes-Regular.ttf"),
        TypedFace("allura", "Allura", file_name="Allura-Regular.ttf"),
        # The base-14 four. Kept — not pruned to the one the fallback needs —
        # because an officer who adopted one of them before the script faces
        # existed still has that key stored, and reading their own adopted mark
        # back must not 422.
        TypedFace("times_italic", "Times Italic (typeset)", base_14="Times-Italic"),
        TypedFace("times_bold_italic", "Times Bold Italic (typeset)", base_14="Times-BoldItalic"),
        TypedFace("helvetica_oblique", "Helvetica Oblique (typeset)", base_14="Helvetica-Oblique"),
        TypedFace("courier_oblique", "Courier Oblique (typeset)", base_14="Courier-Oblique"),
    )
}

#: What an officer who never adopted a mark is stamped in, and the panel's
#: default. Falls through to :data:`FALLBACK_BASE_14_KEY` when ``fonts/`` has
#: been stripped — this one substitution is legitimate because no officer chose
#: anything here for it to override.
PREFERRED_SCRIPT_KEY: Final = "caveat"
FALLBACK_BASE_14_KEY: Final = "times_italic"


def default_face_key() -> str:
    """The face a signer with no adopted mark gets, given what is installed."""
    preferred = TYPED_FACES[PREFERRED_SCRIPT_KEY]
    return preferred.key if preferred.available() else FALLBACK_BASE_14_KEY


def available_face_keys() -> list[str]:
    """The faces this deployment can actually stamp, in offer order.

    Returned to the adoption panel rather than the full catalogue, so a signer
    is never shown a choice that would fail at signing time.
    """
    return [key for key, face in TYPED_FACES.items() if face.available()]


__all__ = [
    "FALLBACK_BASE_14_KEY",
    "FONT_DIR",
    "PREFERRED_SCRIPT_KEY",
    "TYPED_FACES",
    "FaceMetrics",
    "TypedFace",
    "available_face_keys",
    "default_face_key",
]
