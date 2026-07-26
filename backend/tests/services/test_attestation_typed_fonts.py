"""The bundled signature faces, as a shipping obligation rather than an asset dir.

Every assertion here is about a way the directory could go wrong silently: a
font added without its licence, a licence that is not the OFL, a catalogue entry
naming a file nobody committed, a face on a 2048-unit grid that stamps a
signature with its letters flung apart, or a browser preview offering a face the
backend cannot draw. None of those break a test that only checks "signing works"
— they break a filed document, or a licence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from app.models.attestation import TYPED_SIGNATURE_FONTS
from app.schemas.attestation import TypedSignatureFont
from app.services.attestation.typed_fonts import (
    FALLBACK_BASE_14_KEY,
    FONT_DIR,
    PREFERRED_SCRIPT_KEY,
    TYPED_FACES,
    available_face_keys,
    default_face_key,
)

#: pyHanko's OpenType path writes raw font-unit advances into the CIDFont ``/W``
#: array, which PDF reads as thousandths of an em. A face on any other grid
#: therefore stamps with the wrong advance — visibly, letters spread across and
#: out of the box. Sacramento and Parisienne (both otherwise fine OFL scripts)
#: are 2048-unit and were rejected for exactly this.
REQUIRED_UNITS_PER_EM = 1000

SCRIPT_KEYS = [key for key, face in TYPED_FACES.items() if face.is_script]


def test_the_catalogue_and_the_wire_vocabulary_are_the_same_list() -> None:
    """A key that exists in one and not the other is a 422 or a KeyError.

    Order matters too: it is the order the adoption panel offers, and script
    faces have to come first or the default is a typeset face again.
    """
    assert list(TYPED_FACES) == list(TYPED_SIGNATURE_FONTS)
    assert TypedSignatureFont.__value__.__args__ == TYPED_SIGNATURE_FONTS  # pyright: ignore[reportAttributeAccessIssue]
    assert list(TYPED_FACES)[: len(SCRIPT_KEYS)] == SCRIPT_KEYS


def test_every_face_resolves_to_exactly_one_way_of_reaching_the_page() -> None:
    for key, face in TYPED_FACES.items():
        assert key == face.key
        assert (face.file_name is None) != (face.base_14 is None), (
            f"{key} is neither a bundled file nor a standard-14 name, or is both"
        )


@pytest.mark.parametrize("key", SCRIPT_KEYS, ids=SCRIPT_KEYS)
def test_every_bundled_face_is_committed_with_its_licence(key: str) -> None:
    """The licence file is not documentation — it is the OFL's own condition.

    §2 of the SIL Open Font License requires the licence and copyright notice to
    travel with the font, so a font file without one is a font we are not
    entitled to redistribute, and the signed PDFs it produces inherit that.
    """
    face = TYPED_FACES[key]
    assert face.path is not None
    assert face.path.is_file(), f"{face.file_name} is missing from {FONT_DIR}"
    licence = FONT_DIR / f"{face.path.stem.split('-')[0]}-OFL.txt"
    assert licence.is_file(), f"{face.file_name} ships without {licence.name}"
    text = licence.read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "Copyright" in text


@pytest.mark.parametrize("key", SCRIPT_KEYS, ids=SCRIPT_KEYS)
def test_every_bundled_face_is_on_the_grid_pyhanko_assumes(key: str) -> None:
    face = TYPED_FACES[key]
    assert face.path is not None
    font = TTFont(face.path, lazy=True)
    try:
        assert font["head"].unitsPerEm == REQUIRED_UNITS_PER_EM, (  # pyright: ignore[reportAttributeAccessIssue]
            f"{face.file_name} is not on a {REQUIRED_UNITS_PER_EM}-unit em grid, so "
            f"pyHanko's /W array would misplace every glyph"
        )
    finally:
        font.close()


@pytest.mark.parametrize("key", SCRIPT_KEYS, ids=SCRIPT_KEYS)
def test_every_bundled_face_can_print_a_signature(key: str) -> None:
    """A face missing a letter would drop it out of an officer's name."""
    face = TYPED_FACES[key]
    metrics = face.metrics()
    assert metrics.ascent > 0 and metrics.descent > 0
    # Script faces reach far past body text; a fixed line ratio would clip them,
    # which is why the stamp measures instead of assuming.
    assert metrics.line_height > 1.0

    assert face.path is not None
    font = TTFont(face.path, lazy=True)
    try:
        cmap = font.getBestCmap() or {}
    finally:
        font.close()
    printable = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.-' "
    missing = [character for character in printable if ord(character) not in cmap]
    assert not missing, f"{face.file_name} cannot print {missing}"


def test_the_directory_holds_nothing_the_catalogue_does_not_name() -> None:
    """A stray font is a font nobody checked the licence of."""
    bundled = {face.file_name for face in TYPED_FACES.values() if face.file_name}
    assert {path.name for path in FONT_DIR.glob("*.ttf")} == bundled
    assert {path.name for path in FONT_DIR.glob("*.otf")} == set()


def test_the_fallback_face_needs_no_font_file() -> None:
    """The one that must survive a deployment stripped of ``fonts/``.

    Without it a slim image could not stamp a typed mark at all, and the module
    would be choosing between refusing every signature and substituting a face
    the officer did not adopt.
    """
    fallback = TYPED_FACES[FALLBACK_BASE_14_KEY]
    assert fallback.base_14 is not None
    assert fallback.file_name is None
    assert fallback.available()


def test_the_default_face_is_the_preferred_script_one_when_it_is_installed() -> None:
    assert TYPED_FACES[PREFERRED_SCRIPT_KEY].is_script
    assert default_face_key() == PREFERRED_SCRIPT_KEY
    assert available_face_keys() == list(TYPED_FACES)


def test_a_missing_font_file_drops_the_face_from_the_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Availability is read off the filesystem, not assumed from the catalogue.

    A signer must never be offered a face that would fail at signing time — on a
    filing deadline, after step-up, with the certification already half done.
    """
    monkeypatch.setattr(
        "app.services.attestation.typed_fonts.FONT_DIR", Path("/nonexistent-font-dir")
    )
    assert available_face_keys() == [
        key for key, face in TYPED_FACES.items() if face.base_14 is not None
    ]
    assert default_face_key() == FALLBACK_BASE_14_KEY


def test_the_dashboard_previews_every_face_the_backend_can_stamp() -> None:
    """A face the browser cannot preview is a mark adopted sight unseen.

    Read out of the dashboard's own module rather than duplicated here, so a key
    added on one side and forgotten on the other fails in CI instead of in front
    of an officer.
    """
    fonts_ts = (
        Path(__file__).resolve().parents[2]
        / "dashboard/components/attestation/signing/fonts.ts"
    )
    source = fonts_ts.read_text(encoding="utf-8")
    for key in TYPED_FACES:
        assert f"{key}:" in source, f"{key} has no preview style in fonts.ts"
    for key, face in TYPED_FACES.items():
        if face.file_name:
            assert face.file_name in source, (
                f"{key} previews in something other than the file the PDF embeds"
            )
    # The panel's first offer and the backend's own no-mark fallback are the
    # same face, so a signer who adopts nothing gets what the panel showed them.
    assert "DEFAULT_TYPED_FONT" in source
    assert f"= '{default_face_key()}';" in source
