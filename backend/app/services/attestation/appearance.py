"""The mark an officer adopts — drawn or typed (docs/attestation_esignature.md §2.5).

§2.5 fixed four evidential lines and said the appearance is "generated
server-side from the signature record — never from client input". That held while
the appearance *was* the four lines. A drawn signature is client input by
definition, so this module is where that boundary is redrawn rather than
abandoned: the officer's mark passes through :func:`normalise_drawn_signature`
and nothing else reaches the page.

**What normalisation guarantees**, because "we sanitise it" is not a guarantee:

* the bytes are a raster image Pillow can decode, in one of
  :data:`ALLOWED_SOURCE_FORMATS` — anything Pillow cannot open, anything vector
  or document-shaped (PDF, EPS, SVG), and anything multi-frame is refused, not
  repaired;
* the stored bytes are a freshly encoded PNG of exactly
  :data:`NORMALISED_WIDTH` × :data:`NORMALISED_HEIGHT`, drawn onto a new
  transparent canvas. Because the canvas is a new image, no ancillary chunk of
  the upload survives — no EXIF, no ICC profile, no text chunks, no
  geolocation an officer never intended to file with a bank return;
* size is bounded at both ends of the pipe (:data:`MAX_UPLOAD_BYTES` before
  decoding, :data:`MAX_SOURCE_PIXELS` before rasterising, and
  :data:`MAX_STORED_BYTES` after encoding), so an upload can neither exhaust
  memory on the way in nor bloat every signed PDF on the way out.

The raw upload is deliberately never persisted. It has no evidential value —
the signature commits to the digest and the signed bytes, not to the officer's
brush strokes — and keeping it would only widen the personal-data footprint
Act 843 asks us to minimise.

Typed marks carry no user-supplied bytes at all: a name and a font KEY, resolved
by ``attestation.typed_fonts`` to a face this repository ships — a bundled
OFL script file or a PDF standard-14 name. A key naming a face the deployment
does not carry is refused here, at adoption, rather than at signing time.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import AdoptedSignatureAppearance
from app.models.attestation import APPEARANCE_KINDS, TYPED_SIGNATURE_FONTS
from app.services.attestation.typed_fonts import available_face_keys
from app.services.audit import record_event

#: Refused before Pillow sees the bytes. A hand-drawn signature is a few
#: kilobytes of PNG; anything approaching this is not one.
MAX_UPLOAD_BYTES: Final = 512 * 1024

#: Decompression-bomb ceiling, checked from the header before any pixel is
#: rasterised. Pillow's own MAX_IMAGE_PIXELS warning is not a refusal.
MAX_SOURCE_PIXELS: Final = 4_000_000

#: Every stored mark has these exact dimensions, so a placement box behaves
#: identically for every officer and the PDF stamp never has to reason about an
#: arbitrary aspect ratio.
NORMALISED_WIDTH: Final = 600
NORMALISED_HEIGHT: Final = 200

#: Raster formats a browser signature pad actually produces. An allow-list, not
#: a deny-list: the point is to know what we accepted, not to have guessed at
#: everything we should have refused.
ALLOWED_SOURCE_FORMATS: Final[frozenset[str]] = frozenset({"PNG", "JPEG", "WEBP"})

#: A 600×200 RGBA PNG of pen strokes encodes well under this. The cap exists so
#: an adversarially noisy upload cannot make every signed return large.
MAX_STORED_BYTES: Final = 128 * 1024

_STORED_FORMAT: Final = "PNG"

#: Matches ``signature_appearances.typed_name``; a longer name would be silently
#: truncated by the column, which is not something to do to a person's name.
_TYPED_NAME_LIMIT: Final = 120


class SignatureMarkRejected(ValueError):
    """An adopted mark cannot be accepted, and says exactly why.

    A ``ValueError`` rather than an ``HTTPException``: this module is
    bytes-in/bytes-out and the API layer owns the response shape (it maps this to
    422 — the upload is malformed, not in conflict with the return's state).
    """


@dataclass(frozen=True)
class NormalisedMark:
    """The only drawn bytes that are ever stored or drawn."""

    png: bytes
    width: int
    height: int


def normalise_drawn_signature(data: bytes) -> NormalisedMark:
    """Decode, re-raster, strip and re-encode an officer's drawn mark.

    Every refusal below is a refusal rather than a repair. A silently "fixed"
    signature mark would be a mark the officer did not draw, appearing on a
    document they are personally liable for under Act 930 s.93(3).
    """
    if not data:
        raise SignatureMarkRejected("The drawn signature is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SignatureMarkRejected(
            f"The drawn signature is {len(data)} bytes, over the "
            f"{MAX_UPLOAD_BYTES}-byte limit. A hand-drawn mark is a few kilobytes; "
            f"re-draw it rather than uploading a photograph."
        )
    try:
        source = Image.open(io.BytesIO(data))
    # DecompressionBombError derives from Exception, not OSError, and Image.open
    # raises it from the header — so it has to be named or it escapes this module
    # as a 500 for an input we mean to refuse politely.
    except Image.DecompressionBombError as exc:
        raise SignatureMarkRejected(
            f"The drawn signature declares more than the {MAX_SOURCE_PIXELS}-pixel limit."
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SignatureMarkRejected(
            "The drawn signature could not be decoded as an image. Only a plain "
            f"raster ({', '.join(sorted(ALLOWED_SOURCE_FORMATS))}) is accepted — not a "
            "PDF, not a vector drawing."
        ) from exc

    with source:
        if source.format not in ALLOWED_SOURCE_FORMATS:
            raise SignatureMarkRejected(
                f"The drawn signature is a {source.format or 'unrecognised'} image. Only "
                f"{', '.join(sorted(ALLOWED_SOURCE_FORMATS))} is accepted."
            )
        # Multi-frame input would mean only the first frame is inspected while
        # the rest travel unexamined; a signature is one mark, so refuse instead.
        if getattr(source, "n_frames", 1) > 1:
            raise SignatureMarkRejected(
                "The drawn signature has more than one frame. An animated image is "
                "not a signature."
            )
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise SignatureMarkRejected(
                f"The drawn signature is {source.width}×{source.height} pixels, over the "
                f"{MAX_SOURCE_PIXELS}-pixel limit."
            )
        try:
            ink = source.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise SignatureMarkRejected(
                "The drawn signature could not be rasterised."
            ) from exc

    scale = min(NORMALISED_WIDTH / ink.width, NORMALISED_HEIGHT / ink.height)
    scaled = ink.resize(
        (max(1, round(ink.width * scale)), max(1, round(ink.height * scale))),
        Image.Resampling.LANCZOS,
    )
    # A brand-new canvas is what strips the metadata: `convert` and `resize`
    # both carry `info` (EXIF, ICC profile, PNG text chunks) forward, and Pillow
    # writes some of those back out on save. Nothing from the upload's dictionary
    # reaches this image, so nothing from it can reach the filed document.
    canvas = Image.new("RGBA", (NORMALISED_WIDTH, NORMALISED_HEIGHT), (0, 0, 0, 0))
    canvas.paste(
        scaled,
        ((NORMALISED_WIDTH - scaled.width) // 2, (NORMALISED_HEIGHT - scaled.height) // 2),
    )

    buffer = io.BytesIO()
    canvas.save(buffer, format=_STORED_FORMAT, optimize=True)
    png = buffer.getvalue()
    if len(png) > MAX_STORED_BYTES:
        raise SignatureMarkRejected(
            f"The normalised signature is {len(png)} bytes, over the "
            f"{MAX_STORED_BYTES}-byte limit. Draw a simpler mark — a scan or photograph "
            "of a signature does not compress to a usable size."
        )
    return NormalisedMark(png=png, width=NORMALISED_WIDTH, height=NORMALISED_HEIGHT)


def validate_typed_mark(*, name: str, font: str) -> tuple[str, str]:
    """Check a typed mark's name and font key. Returns the cleaned pair."""
    cleaned = name.strip()
    if not cleaned:
        raise SignatureMarkRejected("A typed signature needs a name.")
    if len(cleaned) > _TYPED_NAME_LIMIT:
        raise SignatureMarkRejected(
            f"A typed signature name may be at most {_TYPED_NAME_LIMIT} characters."
        )
    if font not in TYPED_SIGNATURE_FONTS:
        raise SignatureMarkRejected(
            f"Unknown signature font '{font}'. Choose one of: "
            f"{', '.join(TYPED_SIGNATURE_FONTS)}."
        )
    # Availability, not just membership: the bundled script faces are files on
    # disk, and adopting one this deployment does not carry would leave the
    # signer with a mark that fails at signing time — on a filing deadline,
    # after step-up, with no way to tell what went wrong.
    if font not in available_face_keys():
        raise SignatureMarkRejected(
            f"Signature font '{font}' is not installed in this deployment. Choose one "
            f"of: {', '.join(available_face_keys())}."
        )
    return cleaned, font


def get_adopted(
    db: Session, ctx: TenantContext, signer_id: str
) -> AdoptedSignatureAppearance | None:
    """The signer's current adopted mark, or ``None`` if they never adopted one."""
    return db.scalar(
        select(AdoptedSignatureAppearance).where(
            AdoptedSignatureAppearance.organization_id == ctx.organization_id,
            AdoptedSignatureAppearance.signer_id == signer_id,
        )
    )


def adopt(  # noqa: PLR0913 - one act; the kinds carry disjoint payloads
    db: Session,
    ctx: TenantContext,
    *,
    signer_id: str,
    kind: str,
    drawn: bytes | None = None,
    typed_name: str | None = None,
    typed_font: str | None = None,
) -> AdoptedSignatureAppearance:
    """Adopt (or re-adopt) a signer's mark, in the caller's transaction.

    Re-adoption overwrites: the row is presentation, not evidence, and an
    officer who dislikes their own handwriting must not be forced to void a
    return to change it. The audit event is what keeps the *history* of the mark
    intact, and it lands on the immutable trail rather than here.
    """
    if kind not in APPEARANCE_KINDS:
        raise SignatureMarkRejected(
            f"Unknown signature kind '{kind}'. Choose one of: {', '.join(APPEARANCE_KINDS)}."
        )

    mark: NormalisedMark | None = None
    if kind == "drawn":
        if drawn is None:
            raise SignatureMarkRejected("A drawn signature needs image data.")
        mark = normalise_drawn_signature(drawn)
        typed_name = typed_font = None
    else:
        if typed_name is None or typed_font is None:
            raise SignatureMarkRejected("A typed signature needs a name and a font.")
        typed_name, typed_font = validate_typed_mark(name=typed_name, font=typed_font)

    now = datetime.now(UTC)
    row = get_adopted(db, ctx, signer_id)
    if row is None:
        row = AdoptedSignatureAppearance(
            organization_id=ctx.organization_id, signer_id=signer_id
        )
        db.add(row)
    row.kind = kind
    row.image_png = mark.png if mark else None
    row.image_width = mark.width if mark else None
    row.image_height = mark.height if mark else None
    row.typed_name = typed_name
    row.typed_font = typed_font
    row.adopted_at = now
    row.adopted_by = ctx.actor_user_id
    db.flush()

    record_event(
        db,
        ctx,
        event_type="attestation.signature_appearance_adopted",
        entity_type="signature_appearance",
        entity_id=row.id,
        details={
            "signer_id": signer_id,
            "kind": kind,
            "typed_font": typed_font,
            # The bytes never go in the audit detail — a length is enough to show
            # a mark was replaced, and the trail is not the place for an image.
            "image_bytes": len(mark.png) if mark else None,
        },
    )
    return row


__all__ = [
    "ALLOWED_SOURCE_FORMATS",
    "MAX_SOURCE_PIXELS",
    "MAX_STORED_BYTES",
    "MAX_UPLOAD_BYTES",
    "NORMALISED_HEIGHT",
    "NORMALISED_WIDTH",
    "NormalisedMark",
    "SignatureMarkRejected",
    "adopt",
    "get_adopted",
    "normalise_drawn_signature",
    "validate_typed_mark",
]
