"""Adopted signature marks — normalisation and the guarantee it replaces.

``pdf_signing`` used to promise "nothing user-supplied reaches the page". A drawn
signature is user-supplied by definition, so the promise now reads "normalised
raster only". These tests are the executable form of that narrower claim: each one
would be a DEFECT if it changed, because each is something the old guarantee used
to cover for free.

This module's other-suite cases live in ``test_attestation_appearance_integration.py``.
"""

from __future__ import annotations

__all__ = ["_png", "_strokes"]
import io
import zlib

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from app.attestation.service import appearance
from app.attestation.service.appearance import (
    MAX_UPLOAD_BYTES,
    NORMALISED_HEIGHT,
    NORMALISED_WIDTH,
    SignatureMarkRejected,
    normalise_drawn_signature,
)

#: PNG ancillary chunks that carry information an officer never meant to file.
_METADATA_CHUNKS = (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP")


def _strokes(size: tuple[int, int] = (400, 140)) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.line([(20, 110), (90, 30), (160, 110), (240, 40), (330, 90)], fill=(10, 20, 60), width=6)
    return image


def _png(image: Image.Image, *, info: PngInfo | None = None) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def _chunks(png: bytes) -> set[bytes]:
    """Every chunk type in a PNG, read the way a decoder would."""
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    found: set[bytes] = set()
    offset = 8
    while offset < len(png):
        length = int.from_bytes(png[offset : offset + 4], "big")
        kind = png[offset + 4 : offset + 8]
        found.add(kind)
        if kind == b"IEND":
            break
        offset += 12 + length
    return found


def test_a_drawn_image_with_embedded_metadata_is_normalised() -> None:
    """Metadata in, no metadata out — and fixed dimensions.

    THE test for the replaced guarantee. An officer's signature pad, or a phone
    photograph, can carry EXIF (including geolocation), an ICC profile and
    arbitrary text chunks. None of it belongs in a document filed with the
    regulator, and none of it is inspected anywhere downstream, so normalisation
    has to drop it rather than trust it.
    """
    info = PngInfo()
    info.add_text("Author", "someone else entirely")
    info.add_text("Comment", "<script>alert(1)</script>")
    info.add_itxt("Location", "5.6037,-0.1870")
    source = _strokes((640, 240))
    source.info["icc_profile"] = b"not-a-real-profile"
    raw = _png(source, info=info)
    assert _METADATA_CHUNKS[0] in raw, "the fixture must actually carry metadata"

    mark = normalise_drawn_signature(raw)

    assert mark.width == NORMALISED_WIDTH
    assert mark.height == NORMALISED_HEIGHT
    surviving = _chunks(mark.png) & set(_METADATA_CHUNKS)
    assert not surviving, f"metadata survived normalisation: {surviving}"
    # The declared size is the real size, and the strokes are still there.
    decoded = Image.open(io.BytesIO(mark.png))
    assert decoded.size == (NORMALISED_WIDTH, NORMALISED_HEIGHT)
    assert decoded.format == "PNG"
    assert decoded.mode == "RGBA"
    assert decoded.getbbox() is not None


def test_normalisation_is_a_re_raster_not_a_pass_through() -> None:
    """The stored bytes are ours, produced by our encoder.

    If the upload were forwarded unchanged, every guarantee above would rest on
    the uploader's encoder rather than on ours.
    """
    raw = _png(_strokes())
    assert normalise_drawn_signature(raw).png != raw


def test_a_non_raster_upload_is_refused() -> None:
    """A PDF is the interesting case: it is a document, not a mark.

    Embedding user-supplied PDF content in a certified document would put objects
    on the page that no part of this system has inspected.
    """
    with pytest.raises(SignatureMarkRejected, match="could not be decoded"):
        normalise_drawn_signature(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
    with pytest.raises(SignatureMarkRejected, match="could not be decoded"):
        normalise_drawn_signature(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>')


def test_a_raster_format_outside_the_allow_list_is_refused() -> None:
    buffer = io.BytesIO()
    _strokes().convert("P").save(buffer, format="GIF")
    with pytest.raises(SignatureMarkRejected, match="Only"):
        normalise_drawn_signature(buffer.getvalue())


def test_an_oversized_upload_is_refused_before_it_is_decoded() -> None:
    with pytest.raises(SignatureMarkRejected, match="over the"):
        normalise_drawn_signature(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_UPLOAD_BYTES + 1))


def test_an_empty_upload_is_refused() -> None:
    with pytest.raises(SignatureMarkRejected, match="empty"):
        normalise_drawn_signature(b"")


@pytest.mark.parametrize(
    ("width", "height"),
    [(3_000, 3_000), (40_000, 40_000)],
    ids=["over_our_limit", "over_pillows_limit"],
)
def test_a_decompression_bomb_is_refused_from_its_header(width: int, height: int) -> None:
    """Refused on declared dimensions, so no pixel is ever rasterised.

    A 40000×40000 PNG of one colour is a few hundred bytes on the wire and 6.4 GB
    in memory, so the byte-length cap alone would not catch it. Both ceilings are
    exercised because they are enforced by different code: the smaller by our own
    check, the larger by Pillow's — whose error does NOT derive from ``OSError``
    and therefore has to be named explicitly or it escapes as a 500.
    """
    bomb = _synthetic_png_header(width=width, height=height)
    with pytest.raises(SignatureMarkRejected, match="pixel limit"):
        normalise_drawn_signature(bomb)


def _synthetic_png_header(*, width: int, height: int) -> bytes:
    """A PNG whose header declares enormous dimensions. Never decoded by the code."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            len(payload).to_bytes(4, "big")
            + kind
            + payload
            + zlib.crc32(kind + payload).to_bytes(4, "big")
        )

    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )


def test_an_animated_upload_is_refused() -> None:
    """An APNG passes the format allow-list; only the frame check stops it.

    Accepting it would mean storing frames nothing inspected, of which exactly one
    was ever looked at.
    """
    first = _strokes((200, 80))
    second = Image.new("RGBA", (200, 80), (255, 0, 0, 128))
    buffer = io.BytesIO()
    first.save(buffer, format="PNG", save_all=True, append_images=[second], duration=100)
    # getattr, because ``n_frames`` only exists on multi-frame plugin classes —
    # the same reason the production check reads it defensively.
    assert getattr(Image.open(io.BytesIO(buffer.getvalue())), "n_frames", 1) == 2
    with pytest.raises(SignatureMarkRejected, match="more than one frame"):
        normalise_drawn_signature(buffer.getvalue())


def test_a_typed_mark_must_name_an_offered_font() -> None:
    name, font = appearance.validate_typed_mark(name="  Ama Mensah  ", font="times_italic")
    assert (name, font) == ("Ama Mensah", "times_italic")
    with pytest.raises(SignatureMarkRejected, match="Unknown signature font"):
        appearance.validate_typed_mark(name="Ama Mensah", font="Comic Sans")
    with pytest.raises(SignatureMarkRejected, match="needs a name"):
        appearance.validate_typed_mark(name="   ", font="times_italic")
