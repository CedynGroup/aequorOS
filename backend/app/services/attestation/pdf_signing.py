"""PAdES signing of the return artifact (docs/attestation_esignature.md §3.2).

Two signatures, one document, two revisions:

1. :func:`prepare_signature_fields` adds the empty named fields
   ``Sig_Preparer`` and ``Sig_Approver`` to the pages and boxes the caller
   places them at, on the already-rendered return PDF — plus a text form field
   per placed ``name``/``title``/``initials``/``date_signed`` box (see "Typed
   fields" below).
2. :func:`sign_as_preparer` fills ``Sig_Preparer`` with a **certification
   (DocMDP) signature** at permission level 2 (``MDPPerm.FILL_FORMS``) — "form
   filling and signing permitted, nothing else". Every later change to the file
   is therefore measurable against a declared policy instead of being merely
   invisible.
3. :func:`sign_as_approver` fills ``Sig_Approver`` as a standard approval
   signature in an **incremental update**, so the preparer's byte range is
   untouched and both signatures verify independently. The field carries a
   ``/Lock`` (FieldMDP, ``/Action /All``) so all form fields seal once the
   approver has signed.

**Typed fields (BSD3's "name / designation / signature / date").** A regulator's
attestation block asks for four things per officer, not one, so a signing role
gets MANY placements: exactly one ``signature`` — the real PDF signature field —
and any number of ``name``, ``title``, ``initials`` and ``date_signed`` boxes,
each created here as an AcroForm **text** field. Text fields, not drawn content,
because the approver's values are only known when the approver signs, and by then
the preparer's certification permits nothing except *filling form fields*: drawing
onto the page would be a structural change and would convict the document. Both
roles' fields are therefore created in step 1, before any signature exists, and
each role's own values are filled in the same incremental update as that role's
signature — which also keeps the filled values inside the revision that signature
covers. ``Sig_Preparer`` carries a FieldMDP ``/Exclude`` lock naming exactly the
approver's fields, so after certification the preparer's own printed name, title
and date can no longer be rewritten while the approver's can still be filled.

That layering is the whole mechanism behind the question "how would we know if
the figures changed between the two signatures". A tamper cannot hide: editing
the document after the preparer certified it either breaks the byte range (the
signature is not intact) or arrives as an incremental update that pyHanko's diff
analysis classifies above ``FORM_FILLING`` and reports against the DocMDP
policy (``docmdp_ok = False``). Verification lives in ``verify`` (§3.5 checks 1
and 2); this module's job is to produce a document those checks can convict.

**Determinism (§3.2, gap G12).** Signing NEVER re-renders the document. The
unsigned PDF is rendered once by
``app/services/regulatory_reporting/exports/pdf.py``, archived as a
``regulatory_artifact_versions`` row, and every function here takes those exact
bytes and appends an incremental update to them. ``reportlab`` is pinned in
``pyproject.toml`` for the same reason: a re-render that shifted one byte would
produce a different document from the one the signature covers. The prepared
and signed outputs all keep the original bytes as a literal prefix — asserted
in the tests, because it is the property archival relies on.

**Injection, not import.** The cryptographic backends stay out of this module:
callers pass a pyHanko ``Signer`` (from ``attestation.signers``, whose HSM/KMS
backends never expose key material) and a ``TimeStamper`` (from
``attestation.tsa.build_pdf_timestamper``). This module therefore holds no key
policy and no network dependency, and is testable with a software key.

**What is guaranteed about the drawn page, precisely.** This paragraph used to
read "nothing user-supplied reaches the page", which was true only while the
appearance was four server-derived lines. Officers now adopt their own
signature, so a drawn mark IS user-supplied by definition and the old claim
could not survive the feature — it is replaced rather than deleted, because a
guarantee that quietly stopped holding would be worse than none. What holds now:

* Every printed word — role label, name, designation, initials, permanent signer
  ID, signing date and time — is built by :class:`SignatureAppearance` from the
  signature record alone. No value is read from a request body, and none is
  refused-then-substituted: a string the font cannot print raises rather than
  losing a glyph out of an officer's name.
* That is true of the text fields too. A placement says WHERE a value goes and
  WHICH KIND of value it is; the value itself is derived here, in
  :meth:`SignatureAppearance.derived_values`, from the same record the stamp
  reads. A client can move a date box; it can never say what the date is.
* The only user-supplied content that can reach the page is a **normalised
  raster**: fixed-dimension PNG pixels produced by
  ``attestation.appearance.normalise_drawn_signature``. No user-supplied PDF,
  vector drawing, font, colour space, or text template is ever embedded, and no
  path here decodes bytes the normaliser did not encode.
* A typed mark carries no bytes from the caller either — a name plus a font KEY,
  resolved here through ``attestation.typed_fonts`` to a face this repository
  ships. Since script faces landed that includes an **embedded font subset**, so
  the "no font is ever embedded" half of the clause above is no longer true and
  is not left standing: the fonts embedded are ours, they are committed in
  ``fonts/`` under the SIL Open Font License, and no path here opens a font file
  a caller named.
* :func:`prepare_signature_fields` refuses a box too small to print its own kind
  of content legibly (:data:`MIN_BOX_SIZES`), so no placement can produce a
  filed document an examiner cannot read.

**What the signature box carries, and why the identifier is never optional.**
The stamp has DocuSign's anatomy — a role label straddling the top rule, the
officer's adopted mark in the middle, the permanent signer ID printed beneath —
and all three are drawn at **every accepted box size**. There was a period when
the evidential text only appeared above a threshold and a smaller box printed the
mark alone, on the reasoning that the identity travelled in the placed
``name``/``title``/``date_signed`` fields and in the signature dictionary's
``/Name``. Both of those are true and neither is enough: an examiner holding the
printout of a return signed into a form-sized box saw a squiggle with nothing
saying it was a digital signature or whose it was. The floor
(:func:`_signature_minimum`) is therefore derived from fitting all three
elements, and a box below it is refused with the numbers spelled out. A box
roomy enough (:data:`DETAIL_MIN_WIDTH`, :data:`DETAIL_MIN_HEIGHT`) adds the
name/designation and timestamp rows underneath.
"""

from __future__ import annotations

import binascii
import io
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Final, Protocol

from pyhanko import stamp
from pyhanko.pdf_utils import content, generic, layout
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import BoxConstraints
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.timestamps import TimeStamper
from pyhanko_certvalidator import ValidationContext

from app.services.attestation.typed_fonts import (
    TYPED_FACES,
    TypedFace,
    default_face_key,
)
from app.services.public_ids import SIGNER_ID_LENGTH, SIGNER_PUBLIC_ID_PREFIX

#: The two field names are part of the filed artifact's structure: a verifier
#: (and a BoG examiner's PDF reader) resolves a signature by field name, so
#: renaming either one would orphan every historical signature.
PREPARER_FIELD_NAME: Final = "Sig_Preparer"
APPROVER_FIELD_NAME: Final = "Sig_Approver"

#: Page 1 (0-based) of every rendered return: ``render_pdf`` builds the cover
#: page and then opens the attestation block with a ``PageBreak``
#: (``exports/pdf.py`` ``_attestation``).
ATTESTATION_PAGE_INDEX: Final = 1

#: First appearance line per signing role (``models.attestation.SIGNING_ROLES``).
#: §2.5 fixes the two that matter; the other two are spelled out rather than
#: derived so no role can produce ungrammatical text on a filed document.
ROLE_LABELS: Final[dict[str, str]] = {
    "preparer": "Prepared by",
    "approver": "Approved by",
    "board": "Approved by (Board)",
    "witness": "Witnessed by",
}
_FALLBACK_ROLE_LABEL: Final = "Signed by"

_APPEARANCE_FONT_SIZE: Final = 8

#: Standard-14 Courier, referenced with /WinAnsiEncoding by pyHanko's simple
#: font engine (see :class:`_WinAnsiFontEngine`). Monospace mirrors how the
#: signer ID is presented in-app (§2.5), so the two renderings of the same
#: string look like the same string.
_APPEARANCE_FONT_NAME: Final = "Courier"
_APPEARANCE_FONT_WIDTH: Final = 0.6

#: The role label's face. Proportional and upright, so the label reads as
#: furniture rather than competing with the monospaced evidence beneath it.
_LABEL_FONT_NAME: Final = "Helvetica"
_LABEL_FONT_WIDTH: Final = 0.55

#: Below this the permanent signer ID stops being reliably readable on a printed
#: A4 return, which is the one thing the stamp exists to carry (§2.5, §2.6). It
#: is the floor every minimum box size is derived from — not a rendering size.
MIN_LEGIBLE_FONT_SIZE: Final = 6

#: ``SGN-`` plus 16 Crockford characters. Derived rather than counted, because
#: the signature box's minimum WIDTH is this string's width: lengthening the
#: identifier without widening the box would print it off the edge of a filed
#: return.
SIGNER_ID_CHARS: Final = len(SIGNER_PUBLIC_ID_PREFIX) + 1 + SIGNER_ID_LENGTH

#: The stamp's anatomy — DocuSign's, because that is the shape an examiner has
#: been trained by every other e-signed document to read:
#:
#: .. code-block:: text
#:
#:     ┌ Prepared by ──────────────┐
#:     │      «adopted mark»       │
#:     │   SGN-CHETJ0Q4MC23W0E1    │
#:     └───────────────────────────┘
#:
#: The role label straddles the top rule, the officer's mark takes the middle
#: band, and the permanent signer ID is printed underneath — at EVERY accepted
#: box size, which is the point. The identifier used to appear only in a box
#: above a threshold, so the ordinary case (a field dropped on the form's ruled
#: line) filed a bare squiggle: nothing on the paper said it was a digital
#: signature and nothing tied it to a determinate person. It travelled in the
#: signature dictionary, which serves a machine and not the examiner holding the
#: printout.
_STAMP_PADDING: Final = 3
_STAMP_ROW_GAP: Final = 1.5
_STAMP_BORDER_WIDTH: Final = 0.5

#: Where the label sits along the top rule, and the clear space the rule is
#: broken by on either side of it.
_STAMP_LABEL_INSET: Final = 4
_STAMP_LABEL_GAP: Final = 2

#: The label straddles the top rule, so the rule cannot sit on the box's own top
#: edge — the label's ascenders would be clipped off by the appearance stream's
#: bounding box. These are its share of the height, above the rule (the cap
#: height) and below it (the descender, so "Prepared by" does not touch the
#: mark).
_LABEL_ABOVE_RULE: Final = 0.4
_LABEL_BELOW_RULE: Final = 0.6

#: Label and identifier both grow with the box and are clamped: below the floor
#: they stop being readable, above the ceiling they stop being furniture and
#: start shouting over the mark.
_LABEL_MIN_SIZE: Final = 5.0
_LABEL_MAX_SIZE: Final = 7.0
_ID_MAX_SIZE: Final = 8.0
_DETAIL_MAX_SIZE: Final = 7.5

#: The mark's band. A fraction so it scales with a generous field, floored so a
#: box the size of a form's ruled line still shows a recognisable stroke.
_MARK_BAND_FRACTION: Final = 0.42
_MARK_BAND_MIN_HEIGHT: Final = 13

#: The longest FIXED-FORMAT evidential line, in characters:
#: ``2026-07-31 14:02:11 GMT   (RFC 3161 timestamped)``. Its length does not
#: depend on who signs, so the detail threshold can be checked at placement time
#: — before any signer is known, which is when fields have to be created. (The
#: name/designation line is variable and legitimately shrinks; the
#: machine-readable line is the one that must survive.)
_EVIDENTIAL_LINE_CHARS: Final = 48

#: How much vertical room one line of text really needs, as a multiple of its
#: font size: the cap height plus the descender, which is what has to fit between
#: a field's two edges.
_LINE_BOX_RATIO: Final = 1.25

#: ``/Parent`` chains are shallow in practice; the bound is there so a malformed
#: (or maliciously cyclic) page tree fails loudly instead of hanging.
_PAGE_TREE_DEPTH_LIMIT: Final = 32

#: The same, for the AcroForm ``/Kids`` tree. Every field this module creates is
#: terminal, so the walk is flat in practice.
_FIELD_TREE_DEPTH_LIMIT: Final = 8

#: A conservative average glyph advance in ems for a typed mark whose real
#: metrics are not consulted — only the standard-14 faces, and only for deriving
#: the minimum box size. Over-estimating shrinks the mark slightly;
#: under-estimating would let it overflow its box. A bundled script face is
#: measured exactly, by HarfBuzz, at the moment it is drawn.
_TYPED_FONT_WIDTH: Final = 0.6


class PdfSigningError(RuntimeError):
    """A signing precondition failed, or the appearance cannot be rendered.

    Deliberately not an ``HTTPException``: this module is bytes-in/bytes-out and
    the caller (``attestation.signing``) owns the API-facing error shape.
    """


#: Which document field each signing role fills. Only these two roles have a
#: field on the artifact; ``artifact_signing.FIELD_SIGNING_ROLES`` refuses a
#: policy that asks for any other signature ON the PDF rather than recording one
#: that the document does not contain.
ROLE_FIELD_NAMES: Final[dict[str, str]] = {
    "preparer": PREPARER_FIELD_NAME,
    "approver": APPROVER_FIELD_NAME,
}

#: The kinds of box an operator can place. ``signature`` is the PDF signature
#: field; the other four are text form fields whose value is derived from the
#: signature record. The names are the wire vocabulary too
#: (``schemas.attestation.PlacementFieldType``) — a rename would orphan every
#: stored placement, so they are as fixed as the field names above.
SIGNATURE_FIELD_TYPE: Final = "signature"
FIELD_TYPES: Final[tuple[str, ...]] = (
    SIGNATURE_FIELD_TYPE,
    "initials",
    "name",
    "title",
    "date_signed",
)

#: Text-field names are ``Txt_<Role>_<Kind>_<n>``. The role token is taken from
#: the signature field name rather than restated, so the two families of field
#: on one document can never disagree about who a field belongs to.
_ROLE_TOKENS: Final[dict[str, str]] = {
    role: name.removeprefix("Sig_") for role, name in ROLE_FIELD_NAMES.items()
}
_TEXT_FIELD_TOKENS: Final[dict[str, str]] = {
    "initials": "Initials",
    "name": "Name",
    "title": "Title",
    "date_signed": "Date",
}

#: The signing date, as it prints. ISO rather than a locale format: the value is
#: read by an examiner in one jurisdiction and by a verifier in none, and
#: ``31/07/2026`` means two different days depending on who is holding it.
_DATE_FORMAT: Final = "%Y-%m-%d"

#: Padding inside a derived text field, every side. Two points keeps a glyph off
#: the ruled line it is printed on without eating a box that is only twelve
#: points tall to begin with.
_FIELD_PADDING: Final = 2

#: A filled form value is not a headline: past this the text stops growing with
#: its box and simply sits in it.
_DERIVED_FONT_MAX_SIZE: Final = 12

#: The resource name every Courier-set string in this module declares its font
#: under — the stamp's signer ID and detail rows, and the derived text fields'
#: filled values. Namespaced so it cannot collide with a font the export engine
#: put in the page's own resources.
_MONO_FONT_RESOURCE: Final = "/AeqMono"

#: The string each kind of TEXT box must be able to print at
#: :data:`MIN_LEGIBLE_FONT_SIZE`. ``date_signed`` is a FIXED format, so its
#: minimum is a total guarantee. The other three are variable and shrink to fit —
#: no box size can promise a legible forty-character designation, and refusing
#: one would only stop an officer whose title is long from signing — so their
#: reference is the shortest realistic value. What the minimum buys there is that
#: a SHORT value is never illegible, which is the failure an operator dragging a
#: box would otherwise cause without noticing.
#:
#: The signature box has no reference string: its floor is the stamp's own
#: geometry (:func:`_signature_minimum`), not one value's width.
_REFERENCE_CONTENT: Final[dict[str, str]] = {
    "initials": "A.M.",
    "name": "A. Mensah",
    "title": "Director",
    "date_signed": "2026-07-31",  # the exact width of _DATE_FORMAT's output
}


def _text_field_minimum(reference: str) -> tuple[int, int]:
    """The smallest box that prints ``reference`` legibly as a filled value.

    Courier's exactly-0.6 em advance is why the width is a calculation rather
    than an estimate: a proportional face would need per-glyph metrics to say
    this honestly, and a wrong estimate here means either a refused placement
    that would have been fine or a filed document with text off its edge.
    """
    return (
        ceil(len(reference) * _APPEARANCE_FONT_WIDTH * MIN_LEGIBLE_FONT_SIZE)
        + 2 * _FIELD_PADDING,
        ceil(MIN_LEGIBLE_FONT_SIZE * _LINE_BOX_RATIO) + 2 * _FIELD_PADDING,
    )


def _signature_minimum() -> tuple[int, int]:
    """The smallest box that fits the whole stamp — label, mark, identifier.

    Not "the smallest box a mark is legible in", which is what this used to
    answer and why a 37×12 field could be placed: a box that holds a squiggle
    and nothing else produces a filed return on which no printed character says
    the mark is a signature or whose it is. All three elements are the unit, so
    all three are what the floor is derived from.

    Width is the identifier's, because it is the widest thing that cannot shrink
    — a name shortens, ``SGN-`` plus sixteen characters does not. Height is the
    three rows stacked at their own floors, with the label straddling the top
    rule and therefore costing only half its height inside the box.
    """
    return (
        ceil(SIGNER_ID_CHARS * _APPEARANCE_FONT_WIDTH * MIN_LEGIBLE_FONT_SIZE)
        + 2 * _STAMP_PADDING,
        ceil(
            _LABEL_MIN_SIZE * (_LABEL_ABOVE_RULE + _LABEL_BELOW_RULE)
            + _STAMP_ROW_GAP
            + _MARK_BAND_MIN_HEIGHT
            + _STAMP_ROW_GAP
            + MIN_LEGIBLE_FONT_SIZE * _LINE_BOX_RATIO
            + _STAMP_PADDING
        ),
    )


#: The per-kind floor. Each kind answers for its own content — a date box and a
#: signature stamp do not need the same room, and the single 185×61 that once
#: applied to all of them is what stopped a signature field fitting a form's
#: ruled line at all.
MIN_BOX_SIZES: Final[dict[str, tuple[int, int]]] = {
    SIGNATURE_FIELD_TYPE: _signature_minimum(),
    **{
        field_type: _text_field_minimum(reference)
        for field_type, reference in _REFERENCE_CONTENT.items()
    },
}

#: Above this the stamp also prints the officer's name and designation and the
#: signing timestamp, under the identifier. Width is the fixed-format timestamp
#: line at the legibility floor; height is the stamp minimum plus two more rows.
#: A threshold and not a floor: below it those three facts are printed by the
#: ``name``/``title``/``date_signed`` fields the operator placed on the form's
#: own ruled lines, which is where the regulator asked for them.
DETAIL_MIN_WIDTH: Final = (
    ceil(_EVIDENTIAL_LINE_CHARS * _APPEARANCE_FONT_WIDTH * MIN_LEGIBLE_FONT_SIZE)
    + 2 * _STAMP_PADDING
)
DETAIL_MIN_HEIGHT: Final = MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE][1] + 2 * ceil(
    MIN_LEGIBLE_FONT_SIZE * _LINE_BOX_RATIO + _STAMP_ROW_GAP
)


def min_box_size(field_type: str) -> tuple[int, int]:
    """The ``(width, height)`` floor for one kind of box, in points."""
    try:
        return MIN_BOX_SIZES[field_type]
    except KeyError as exc:
        raise PdfSigningError(
            f"Unknown signature field type {field_type!r}; the placeable kinds are "
            f"{list(FIELD_TYPES)}."
        ) from exc


@dataclass(frozen=True)
class FieldPlacement:
    """One placed box: whose it is, what it prints, which page, and where.

    ``box`` is ``(x1, y1, x2, y2)`` in PDF user space, origin bottom-left, in
    points — the same convention the PDF itself uses, so a placement read out of
    the database means the same thing as a placement read out of the file.

    ``field_index`` distinguishes two boxes of the same kind for the same role: a
    return whose attestation block repeats on a continuation page needs the date
    twice, and both are the same derived value.
    """

    signing_role: str
    page_index: int
    box: tuple[int, int, int, int]
    field_type: str = SIGNATURE_FIELD_TYPE
    field_index: int = 1

    @property
    def field_name(self) -> str:
        try:
            role_token = _ROLE_TOKENS[self.signing_role]
        except KeyError as exc:
            raise PdfSigningError(
                f"Signing role {self.signing_role!r} has no field on the return artifact; "
                f"only {sorted(ROLE_FIELD_NAMES)} can be placed on the document."
            ) from exc
        if self.field_type == SIGNATURE_FIELD_TYPE:
            return ROLE_FIELD_NAMES[self.signing_role]
        try:
            kind_token = _TEXT_FIELD_TOKENS[self.field_type]
        except KeyError as exc:
            raise PdfSigningError(
                f"Unknown signature field type {self.field_type!r}; the placeable kinds "
                f"are {list(FIELD_TYPES)}."
            ) from exc
        return f"Txt_{role_token}_{kind_token}_{self.field_index}"

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]


#: The attestation block's own ruled cells, in PDF user space (origin
#: bottom-left) on A4 — the numbers ``exports/pdf.py._signing_block`` draws, read
#: back. Each role gets four boxes sitting ON the rules the template prints, one
#: per labelled cell.
#:
#: The pairing is asserted against the RENDERED page in
#: ``tests/services/test_attestation_pdf_signing.py``, not trusted: this is two
#: files' worth of arithmetic about one layout, and the failure mode when they
#: drift is a filed return whose signature stamp sits beside its line rather than
#: on it.
_SIGNING_RULE_Y: Final[dict[str, int]] = {"preparer": 672, "approver": 580}
#: field type → (left edge, width, height above the rule).
_SIGNING_CELLS: Final[dict[str, tuple[int, int, int]]] = {
    "name": (51, 120, 16),
    "title": (179, 120, 16),
    SIGNATURE_FIELD_TYPE: (307, 150, 46),
    "date_signed": (465, 71, 16),
}


def _signing_cell(signing_role: str, field_type: str) -> tuple[int, int, int, int]:
    """The box for one labelled cell of one role's signing block."""
    left, width, height = _SIGNING_CELLS[field_type]
    bottom = _SIGNING_RULE_Y[signing_role]
    return (left, bottom, left + width, bottom + height)


#: The two signature stamps on their own, exported because they are the boxes a
#: caller most often needs to reason about.
PREPARER_BOX: Final[tuple[int, int, int, int]] = _signing_cell("preparer", SIGNATURE_FIELD_TYPE)
APPROVER_BOX: Final[tuple[int, int, int, int]] = _signing_cell("approver", SIGNATURE_FIELD_TYPE)

#: The default for a return nobody has placed fields on: an API-driven
#: certification, or an institution that never opens the workspace.
#:
#: It used to be two lone signature boxes floating in the clear band BELOW the
#: attestation block — a guess, because the block printed one undivided rule
#: under wording that asked for four things, so there was nothing to land on.
#: The template now rules the four cells it asks for
#: (``exports/pdf.py._signing_block``) and the default fills all four per role.
#: The founder should not have to nudge a box on a return we designed.
DEFAULT_PLACEMENTS: Final[tuple[FieldPlacement, ...]] = tuple(
    FieldPlacement(
        signing_role=role,
        page_index=ATTESTATION_PAGE_INDEX,
        box=_signing_cell(role, field_type),
        field_type=field_type,
    )
    for role in _SIGNING_RULE_Y
    for field_type in _SIGNING_CELLS
)


@dataclass(frozen=True)
class AdoptedMark:
    """A signer's adopted mark, as the page draws it.

    ``image_png`` is only ever bytes that came back out of
    ``attestation.appearance.normalise_drawn_signature`` — see this module's
    docstring for what that does and does not guarantee. ``typed_font`` is a KEY
    into ``typed_fonts.TYPED_FACES``, never a font name or a font file path from
    a caller.
    """

    kind: str
    image_png: bytes | None = None
    typed_name: str | None = None
    typed_font: str | None = None

    def require_renderable(self) -> None:
        if self.kind == "drawn":
            if not self.image_png:
                raise PdfSigningError(
                    "A drawn signature mark carries no image bytes, so the appearance "
                    "cannot be rendered."
                )
            return
        if self.kind != "typed":
            raise PdfSigningError(f"Unknown signature mark kind {self.kind!r}.")
        if not self.typed_name:
            raise PdfSigningError("A typed signature mark carries no name.")
        _ = self.face

    @property
    def face(self) -> TypedFace:
        """The catalogue entry this mark's font key resolves to.

        A face whose bundled file is missing is refused rather than replaced by a
        standard-14 lookalike: the officer adopted a particular mark, and quietly
        stamping a different one onto a filed return is the same class of defect
        as substituting a glyph in their name.
        """
        face = TYPED_FACES.get(self.typed_font or "")
        if face is None:
            raise PdfSigningError(
                f"Typed signature font {self.typed_font!r} is not one of "
                f"{sorted(TYPED_FACES)}."
            )
        if not face.available():
            raise PdfSigningError(
                f"Typed signature font {self.typed_font!r} resolves to {face.file_name}, "
                f"which is not installed in this deployment. Restore "
                f"app/services/attestation/fonts/, or have the signer adopt a face this "
                f"deployment carries."
            )
        return face


class SignatureRecordLike(Protocol):
    """The fields of ``AttestationSignature`` the appearance is derived from.

    A structural type rather than the ORM class: keeping the model out of this
    module is what lets it be exercised on raw bytes with no database, and it
    documents exactly which persisted columns the printed page can depend on.

    Note for callers holding an ``AttestationSignature`` row: pyright does not
    resolve SQLAlchemy's ``Mapped[...]`` descriptors when matching a Protocol,
    so a row needs ``cast(SignatureRecordLike, row)`` (or construct
    :class:`SignatureAppearance` directly from the values you already have).
    """

    @property
    def signing_role(self) -> str: ...
    @property
    def signer_id(self) -> str: ...
    @property
    def signer_display_name(self) -> str | None: ...
    @property
    def officer_title(self) -> str | None: ...
    @property
    def tsa_time(self) -> datetime | None: ...
    @property
    def declared_at(self) -> datetime: ...


@dataclass(frozen=True)
class SignatureAppearance:
    """The four lines of §2.5, server-derived and self-contained.

    ``signer_id`` is on the page on purpose: it must travel with the filed
    document so a signature stays attributable to a determinate person from a
    printout alone, even after the signer is deprovisioned and even if the
    display name is later minimised under Act 843 (§2.5, §2.6).

    ``timestamped`` is not decoration. It is only ever true when an RFC 3161
    token is actually embedded — :meth:`PadesProfile.require_deliverable`
    refuses to sign a page that claims a timestamp the document does not carry.
    """

    role_label: str
    signer_name: str
    officer_title: str | None
    signer_id: str
    signed_at: datetime
    timestamped: bool = True
    #: The officer's adopted mark, drawn above the four lines. ``None`` renders
    #: exactly what this module rendered before adoption existed — an officer who
    #: never adopted a mark still gets a complete, evidential signature block.
    mark: AdoptedMark | None = None

    @classmethod
    def from_record(
        cls, record: SignatureRecordLike, *, timestamped: bool = True
    ) -> SignatureAppearance:
        """Build the appearance from a signature record — never from client input.

        The trusted RFC 3161 time is authoritative when present; ``declared_at``
        (the server clock) is only a fallback for record-keeping, matching the
        precedence documented on the model.
        """
        return cls(
            role_label=label_for_role(record.signing_role),
            signer_name=record.signer_display_name or "(name withheld)",
            officer_title=record.officer_title,
            signer_id=record.signer_id,
            signed_at=record.tsa_time or record.declared_at,
            timestamped=timestamped,
        )

    def lines(self) -> tuple[str, str, str, str]:
        """Exactly the four facts of §2.5, in order.

        Still four and still these four. What changed is where the stamp puts
        them: the role is the frame's label, the signer ID is a row of its own
        under the mark **at every accepted box size**, and the remaining two are
        the detail rows a box past :data:`DETAIL_MIN_HEIGHT` adds — or, in a box
        the size of a form's ruled line, the values the placed ``name``,
        ``title`` and ``date_signed`` fields print on the form's own lines.
        """
        who = self.signer_name
        if self.officer_title:
            who = f"{self.signer_name} — {self.officer_title}"
        moment = self.signed_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        suffix = "   (RFC 3161 timestamped)" if self.timestamped else "   (server time)"
        return (self.role_label, who, f"Signer ID: {self.signer_id}", f"{moment} GMT{suffix}")

    def detail_lines(self) -> tuple[str, str]:
        """The two facts a roomy stamp prints beneath the signer ID."""
        _, who, _, when = self.lines()
        return (who, when)

    def derived_values(self) -> dict[str, str]:
        """What each kind of placed text field prints, by field type.

        Every value comes off this record, which came off the signature row —
        the same provenance as the stamp's four lines. There is no path by which
        a client says what its own name or signing date is, which is the property
        that lets a placement be freely draggable: it decides where a value goes,
        never what it says.

        An officer with no recorded designation gets an EMPTY title, not a dash
        or a placeholder: the form asked for a designation, the record does not
        have one, and printing something that looks like an answer would be a
        small lie on a filed document.
        """
        return {
            "initials": _initials(self.signer_name),
            "name": self.signer_name,
            "title": self.officer_title or "",
            "date_signed": self.signed_at.astimezone(UTC).strftime(_DATE_FORMAT),
        }


def _initials(name: str) -> str:
    """``"Ama Mensah"`` → ``"A.M."`` — first letter of each word, dotted.

    Non-alphabetic leading characters are skipped rather than printed, so a
    hyphenated or parenthesised name still yields letters.
    """
    letters = [part[0].upper() for part in name.split() if part[:1].isalpha()]
    return "".join(f"{letter}." for letter in letters)


def label_for_role(role: str) -> str:
    """The first appearance line for a signing role."""
    return ROLE_LABELS.get(role, _FALLBACK_ROLE_LABEL)


# --- appearance rendering ---------------------------------------------------

#: The other two resource names the stamp declares its faces under; the
#: evidential Courier reuses :data:`_MONO_FONT_RESOURCE`, since the stamp's
#: identifier row and a derived field's value are the same face for the same
#: reason. All three are namespaced so none can collide with a font the export
#: engine left in the page's own resources.
_STAMP_LABEL_RESOURCE: Final = "/AeqLabel"
_STAMP_MARK_RESOURCE: Final = "/AeqMark"

#: How the label and the identifier grow with the box, before clamping. Both are
#: fractions of the box height rather than of each other, so a wide-and-short
#: field and a tall one both stay in proportion.
_LABEL_HEIGHT_FRACTION: Final = 0.16
_ID_HEIGHT_FRACTION: Final = 0.18

#: The stamp's ink. Near-black rather than black for the frame and the label, so
#: the furniture recedes behind the mark and the identifier the way a printed
#: form's rules do; the evidence itself is drawn in plain black.
_STAMP_FRAME_GREY: Final = 0.45
_STAMP_LABEL_GREY: Final = 0.35


def _win_ansi(text: str, *, face: str, subject: str) -> bytes:
    """``text`` as a hex string for a WinAnsi-encoded font, or a refusal.

    pyHanko's simple font engine serialises through ``TextStringObject``, which
    prefers PDFDocEncoding, while the font resource it emits declares
    ``/WinAnsiEncoding``. The two agree on ASCII and on Latin-1's upper half but
    NOT on 0x80–0x9F: an em dash is 0x84 in PDFDocEncoding and 0x97 in WinAnsi,
    so §2.5's "Ama Mensah — Chief Financial Officer" would print with a low
    double quote where the dash belongs. Every string this module draws in a
    standard-14 face therefore goes through cp1252 (WinAnsi's codec-level
    equivalent) and is emitted as hex.

    Characters cp1252 cannot represent are refused rather than substituted: a
    silently mangled name on a filed return is worse than a failed signing run,
    which an operator can fix by having the signer adopt a bundled script face
    (those are embedded subsets and carry their own glyphs).
    """
    try:
        encoded = text.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise PdfSigningError(
            f"The {subject} contains characters the standard {face} font cannot "
            f"render: {text!r}. Rendering it would silently substitute glyphs in a "
            f"signer's name or designation."
        ) from exc
    return binascii.hexlify(encoded)


def _base_14_font(base_font: str) -> generic.DictionaryObject:
    """A fresh standard-14 font dictionary, WinAnsi-encoded.

    Fresh per call, never a shared module-level object: pyHanko records the
    reference it assigned on the object itself, so handing the same instance to
    two writers would leave one of them describing the other's file.
    """
    return generic.DictionaryObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/Font"),
            generic.pdf_name("/Subtype"): generic.pdf_name("/Type1"),
            generic.pdf_name("/BaseFont"): generic.pdf_name(f"/{base_font}"),
            generic.pdf_name("/Encoding"): generic.pdf_name("/WinAnsiEncoding"),
        }
    )


@dataclass(frozen=True)
class _StampLayout:
    """Where every element of the stamp sits inside its box, in points.

    Computed once from the field's own ``/Rect`` and then only read, so the
    frame, the mark band and the text rows cannot be derived from different
    assumptions and overlap — a mark drawn over the signer ID would destroy the
    one thing the stamp exists to carry.
    """

    width: float
    height: float
    label_size: float
    label_width: float
    mark_bottom: float
    mark_height: float
    id_size: float
    id_baseline: float
    detail_size: float
    #: The optional rows, each already paired with the baseline it prints on, so
    #: the drawing code cannot pair a line with the wrong row — or draw a line
    #: the layout never made room for.
    detail_rows: tuple[tuple[str, float], ...]

    @property
    def top_rule(self) -> float:
        """Where the frame's top edge runs — the label is centred on it."""
        return self.height - self.label_size * _LABEL_ABOVE_RULE

    @property
    def label_baseline(self) -> float:
        return self.top_rule - self.label_size * (_LABEL_ABOVE_RULE - 0.05)


def _stamp_layout(appearance: SignatureAppearance, *, width: int, height: int) -> _StampLayout:
    """Fit label, mark band, signer ID and optional detail rows into one box.

    Laid out from the bottom edge up, because the rows that must survive are the
    ones nearest it: whatever room is left over after the identifier (and the
    detail rows, when the box is big enough for them) becomes the mark's band.
    The mark is the element that can shrink without losing evidence — it is a
    stroke, not a string — so it is the one that absorbs the slack.
    """
    label_size = min(_LABEL_MAX_SIZE, max(_LABEL_MIN_SIZE, height * _LABEL_HEIGHT_FRACTION))
    inner_width = width - 2 * _STAMP_PADDING
    id_size = min(
        _ID_MAX_SIZE,
        inner_width / (SIGNER_ID_CHARS * _APPEARANCE_FONT_WIDTH),
        max(MIN_LEGIBLE_FONT_SIZE, height * _ID_HEIGHT_FRACTION),
    )
    detail_size = min(
        _DETAIL_MAX_SIZE,
        inner_width / (_EVIDENTIAL_LINE_CHARS * _APPEARANCE_FONT_WIDTH),
        max(MIN_LEGIBLE_FONT_SIZE, id_size * 0.9),
    )

    cursor = float(_STAMP_PADDING)
    detail_rows: list[tuple[str, float]] = []
    if _details_fit(width, height):
        for text in reversed(appearance.detail_lines()):
            detail_rows.insert(0, (text, cursor + detail_size * (_LINE_BOX_RATIO - 1)))
            cursor += detail_size * _LINE_BOX_RATIO + _STAMP_ROW_GAP
    id_baseline = cursor + id_size * (_LINE_BOX_RATIO - 1)
    cursor += id_size * _LINE_BOX_RATIO + _STAMP_ROW_GAP

    label_width = len(appearance.role_label) * _LABEL_FONT_WIDTH * label_size
    return _StampLayout(
        width=width,
        height=height,
        label_size=label_size,
        label_width=label_width,
        mark_bottom=cursor,
        # The band starts below the label's descender, which hangs under the top
        # rule the label is centred on.
        mark_height=max(
            height
            - label_size * (_LABEL_ABOVE_RULE + _LABEL_BELOW_RULE)
            - _STAMP_ROW_GAP
            - cursor,
            0.0,
        ),
        id_size=id_size,
        id_baseline=id_baseline,
        detail_size=detail_size,
        detail_rows=tuple(detail_rows),
    )


def _details_fit(box_width: int, box_height: int) -> bool:
    """Whether this box can carry the name/designation and timestamp rows too."""
    return box_width >= DETAIL_MIN_WIDTH and box_height >= DETAIL_MIN_HEIGHT


class _SignatureStampContent(content.PdfContent):
    """The whole visible signature, drawn as one content stream.

    Hand-rolled rather than assembled from pyHanko's ``TextStampStyle`` because
    the stamp needs three faces at once — the label's Helvetica, the mark's
    adopted face, the identifier's Courier — and a stamp style carries exactly
    one. The alternative, nesting content fragments, was what produced the old
    two-layout split where a small box silently dropped everything except the
    mark.

    The content's box is the field's box and it is drawn 1:1 (see
    :func:`_stamp_style`), so every coordinate here is in the field's own user
    space with the origin at its bottom-left corner.
    """

    def __init__(self, appearance: SignatureAppearance, *, width: int, height: int) -> None:
        self._appearance = appearance
        self._layout = _stamp_layout(appearance, width=width, height=height)
        super().__init__(box=layout.BoxConstraints(width=width, height=height))

    def render(self) -> bytes:
        ops = [
            self._frame(),
            self._label(),
            self._mark(),
            self._id_row(),
            *self._detail_rows(),
        ]
        return b" ".join(op for op in ops if op)

    # -- the frame and its label --------------------------------------------

    def _frame(self) -> bytes:
        """A rule around the stamp, broken where the label crosses the top edge.

        One open polyline — up the label's left gap, round the box, back to the
        label's right gap — rather than a closed rectangle with an opaque patch
        behind the label: the stamp usually sits ON the form's printed signature
        line, and a white patch would erase part of the regulator's template.
        """
        box = self._layout
        inset = _STAMP_BORDER_WIDTH / 2
        left, right = inset, box.width - inset
        bottom, top = inset, box.top_rule
        gap_start = max(_STAMP_LABEL_INSET - _STAMP_LABEL_GAP, left)
        gap_end = min(_STAMP_LABEL_INSET + box.label_width + _STAMP_LABEL_GAP, right)
        corners = (
            (gap_start, top),
            (left, top),
            (left, bottom),
            (right, bottom),
            (right, top),
            (gap_end, top),
        )
        path = b" ".join(
            b"%g %g %s" % (x, y, b"m" if index == 0 else b"l")
            for index, (x, y) in enumerate(corners)
        )
        return b"q %g G %g w %s S Q" % (_STAMP_FRAME_GREY, _STAMP_BORDER_WIDTH, path)

    def _label(self) -> bytes:
        box = self._layout
        return b"q %g g BT %s %g Tf %g %g Td <%s> Tj ET Q" % (
            _STAMP_LABEL_GREY,
            _STAMP_LABEL_RESOURCE.encode("ascii"),
            box.label_size,
            _STAMP_LABEL_INSET,
            box.label_baseline,
            self._declare(
                _STAMP_LABEL_RESOURCE,
                _LABEL_FONT_NAME,
                self._appearance.role_label,
                subject="signature role label",
            ),
        )

    # -- the evidential rows -------------------------------------------------

    def _id_row(self) -> bytes:
        """The permanent signer ID, at every accepted box size.

        Printed bare, without a "Signer ID:" prefix: at the smallest accepted
        box the prefix would take a third of the width and force the identifier
        itself below the legibility floor, and ``SGN-`` already says what the
        string is.
        """
        box = self._layout
        return self._text_row(
            self._appearance.signer_id,
            size=box.id_size,
            baseline=box.id_baseline,
            subject="permanent signer ID",
        )

    def _detail_rows(self) -> list[bytes]:
        box = self._layout
        return [
            self._text_row(
                text, size=box.detail_size, baseline=baseline, subject="signature block"
            )
            for text, baseline in box.detail_rows
        ]

    def _text_row(self, text: str, *, size: float, baseline: float, subject: str) -> bytes:
        return b"q 0 g BT %s %g Tf %g %g Td <%s> Tj ET Q" % (
            _MONO_FONT_RESOURCE.encode("ascii"),
            size,
            _STAMP_PADDING,
            baseline,
            self._declare(
                _MONO_FONT_RESOURCE, _APPEARANCE_FONT_NAME, text, subject=subject
            ),
        )

    def _declare(self, resource: str, base_font: str, text: str, *, subject: str) -> bytes:
        """Register a standard-14 face on this content and encode ``text`` for it."""
        self.set_resource(
            category=content.ResourceType.FONT,
            name=generic.pdf_name(resource),
            value=_base_14_font(base_font),
        )
        return _win_ansi(text, face=base_font, subject=subject)

    # -- the adopted mark ----------------------------------------------------

    def _mark(self) -> bytes:
        """The officer's mark, centred in the band the layout left for it."""
        box = self._layout
        if box.mark_height <= 0:
            return b""
        mark = self._appearance.mark or _fallback_mark(self._appearance)
        mark.require_renderable()
        if mark.kind == "drawn":
            return self._drawn_mark(mark)
        return self._typed_mark(mark)

    def _drawn_mark(self, mark: AdoptedMark) -> bytes:
        assert mark.image_png is not None  # require_renderable
        # Imported here: Pillow is only needed to draw an adopted mark, and a
        # deployment where nobody has adopted one must not pay for the import.
        from PIL import Image  # noqa: PLC0415
        from pyhanko.pdf_utils import images  # noqa: PLC0415

        box = self._layout
        source = Image.open(io.BytesIO(mark.image_png))
        available_width = box.width - 2 * _STAMP_PADDING
        scale = min(available_width / source.width, box.mark_height / source.height)
        drawn = images.PdfImage(
            source,
            writer=self.writer,
            box=layout.BoxConstraints(
                width=round(source.width * scale), height=round(source.height * scale)
            ),
        )
        ops = drawn.render()
        self.import_resources(drawn.resources)
        return b"q 1 0 0 1 %g %g cm %s Q" % (
            (box.width - source.width * scale) / 2,
            box.mark_bottom,
            ops,
        )

    def _typed_mark(self, mark: AdoptedMark) -> bytes:
        """The signer's name in the face they adopted.

        A bundled script face is shaped and embedded as a subset through
        pyHanko's OpenType path, so the glyphs on the filed page are the ones
        this repository ships under the OFL and do not depend on the reader
        having the font. A standard-14 face is named rather than carried, the
        way the rest of this module's text is.
        """
        assert mark.typed_name is not None  # require_renderable
        face = mark.face
        if face.base_14 is not None:
            return self._typed_base_14(mark.typed_name, face)
        return self._typed_script(mark.typed_name, face)

    def _typed_base_14(self, name: str, face: TypedFace) -> bytes:
        assert face.base_14 is not None
        hex_text = self._declare(
            _STAMP_MARK_RESOURCE, face.base_14, name, subject="typed signature"
        )
        advance_em = len(name) * _TYPED_FONT_WIDTH
        return self._draw_mark_text(b"<%s> Tj" % hex_text, advance_em=advance_em, face=face)

    def _typed_script(self, name: str, face: TypedFace) -> bytes:
        # Imported here for the same reason Pillow is: fontTools and HarfBuzz are
        # only needed to embed a bundled face, and the import is not cheap.
        from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory  # noqa: PLC0415

        path = face.path
        assert path is not None  # face.base_14 is None, so this is a bundled face
        engine = GlyphAccumulatorFactory(font_file=str(path)).create_font_engine(
            self._ensure_writer
        )
        shaped = engine.shape(name)
        self.set_resource(
            category=content.ResourceType.FONT,
            name=generic.pdf_name(_STAMP_MARK_RESOURCE),
            value=engine.as_resource(),
        )
        return self._draw_mark_text(
            shaped.graphics_ops, advance_em=shaped.x_advance, face=face
        )

    def _draw_mark_text(self, show_ops: bytes, *, advance_em: float, face: TypedFace) -> bytes:
        """Size a shaped mark to its band and place it on the baseline.

        The size comes from the face's own extents rather than a fixed ratio: a
        script face's ascenders and descenders reach far past a text face's, and
        a mark sized as if it were body text would have its loops clipped by the
        band it sits in.
        """
        box = self._layout
        metrics = face.metrics()
        available_width = box.width - 2 * _STAMP_PADDING
        size = min(
            available_width / advance_em if advance_em > 0 else _ID_MAX_SIZE,
            box.mark_height / metrics.line_height,
        )
        return b"q 0 g BT %s %g Tf 1 0 0 1 %g %g Tm %s ET Q" % (
            _STAMP_MARK_RESOURCE.encode("ascii"),
            size,
            (box.width - advance_em * size) / 2,
            box.mark_bottom + metrics.descent * size,
            show_ops,
        )


def _fallback_mark(appearance: SignatureAppearance) -> AdoptedMark:
    """The mark for a signer who never adopted one.

    Their name, in whichever face this deployment has: the preferred script one,
    or the standard-14 fallback if ``fonts/`` was stripped. This is the only
    place a face may be substituted, because no officer chose anything here for
    the substitution to override — without it the band would be empty, which
    reads to an examiner like a failed render rather than an un-personalised
    signature.
    """
    return AdoptedMark(
        kind="typed", typed_name=appearance.signer_name, typed_font=default_face_key()
    )


def _stamp_style(
    appearance: SignatureAppearance, *, box_width: int, box_height: int
) -> stamp.BaseStampStyle:
    """The visible appearance for one signature box.

    One layout at every size, which is the change: label, mark and permanent
    signer ID always, plus the name/designation and timestamp rows when the box
    is past :data:`DETAIL_MIN_HEIGHT`. There used to be two layouts with a
    threshold between them, and below the threshold the box printed the mark
    alone — so the ordinary case, a field dropped on a form's ruled line, filed a
    signature with nothing on the page identifying it or its signer.

    ``StaticStampStyle`` with ``NO_SCALING`` and no margins hands the content the
    field's box unscaled and un-inset, so :class:`_SignatureStampContent` draws
    in the field's own coordinates. pyHanko's own border is off because the stamp
    draws its own — the frame has to be broken where the label crosses it.
    """
    return stamp.StaticStampStyle(
        border_width=0,
        background=_SignatureStampContent(
            appearance, width=box_width, height=box_height
        ),
        background_layout=layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MIN,
            y_align=layout.AxisAlignment.ALIGN_MIN,
            margins=layout.Margins(left=0, right=0, top=0, bottom=0),
            inner_content_scaling=layout.InnerScaling.NO_SCALING,
        ),
        # The stamp is ink, not a watermark: pyHanko's 0.6 default would print a
        # signature that looks like a draft.
        background_opacity=1.0,
    )


# --- field preparation ------------------------------------------------------


def prepare_signature_fields(
    pdf_bytes: bytes, *, placements: Sequence[FieldPlacement]
) -> bytes:
    """Add every empty field — signature and text — where ``placements`` puts it.

    Every field is created up front, before ANY signature exists, because the
    preparer's DocMDP policy only permits *filling* form fields: a field added
    after certification would itself be a structural change and would show up as
    tampering. That is why the preparer places the approver's boxes too, and why
    the approver cannot move them afterwards — an inherent consequence of
    certifying at ``MDPPerm.FILL_FORMS``, not a product decision. The two
    ``/Lock`` policies are installed here for the same reason:

    * ``Sig_Approver`` locks ``/All`` — once the approver has signed, no form
      field in the document may change again (§3.2 "Field locking").
    * ``Sig_Preparer`` locks everything EXCEPT the approver's own fields, so the
      preparer's printed name, designation and date are sealed by their own
      certification while the approver's are still fillable. Without it, filling
      form fields — which the DocMDP level permits by construction — would be
      enough to rewrite what the preparer's block says about them.

    Returns the original bytes plus one incremental update; the input is left
    intact as a prefix.
    """
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    if reader.embedded_signatures:
        raise PdfSigningError(
            "This PDF already carries a signature, so signature fields can no longer "
            "be added: adding a field is a structural change and would invalidate the "
            "existing certification. Prepare fields on the unsigned archived artifact."
        )
    resolved = _validate_placements(reader, placements)
    existing = {name for name, _, _ in fields.enumerate_sig_fields(reader)}
    clashes = existing & {placement.field_name for placement in resolved}
    if clashes:
        raise PdfSigningError(
            f"Signature field(s) {sorted(clashes)} already exist in this PDF. "
            f"Field preparation runs exactly once, on the archived unsigned artifact."
        )

    approver_fields = [
        placement.field_name
        for placement in resolved
        if placement.signing_role == "approver"
    ]
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
    # Signature fields FIRST, in two passes, and not merely by convention: the
    # return PDF is rendered by reportlab and carries no AcroForm at all, and
    # `fields.append_signature_field` is what creates one. `_append_text_field`
    # requires it to exist. Iterating `resolved` in placement order made that
    # depend on the order the user happened to drop fields in — dragging a Name
    # onto the page before a Signature refused the whole certification.
    for placement in resolved:
        if placement.field_type != SIGNATURE_FIELD_TYPE:
            continue
        is_approver = placement.field_name == APPROVER_FIELD_NAME
        fields.append_signature_field(
            writer,
            fields.SigFieldSpec(
                sig_field_name=placement.field_name,
                on_page=placement.page_index,
                box=placement.box,
                readable_field_name=label_for_role(placement.signing_role),
                field_mdp_spec=(
                    fields.FieldMDPSpec(fields.FieldMDPAction.ALL)
                    if is_approver
                    else fields.FieldMDPSpec(
                        fields.FieldMDPAction.EXCLUDE, fields=approver_fields
                    )
                ),
            ),
        )
    for placement in resolved:
        if placement.field_type != SIGNATURE_FIELD_TYPE:
            _append_text_field(writer, placement)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _validate_placements(
    reader: PdfFileReader, placements: Sequence[FieldPlacement]
) -> tuple[FieldPlacement, ...]:
    """Refuse a placement set that cannot produce a usable signed document.

    This replaced ``_require_page_is_attestation``, which asserted the fields
    landed on a page whose content stream contained the ``(Attestation)``
    heading. That guard existed because the boxes were module constants nobody
    could see or move, so the only defence against stamping a signer's name over
    a figures table was to pin the page. With placement explicit — an operator
    positions each field on a rendered page and the position is stored, audited
    and shown back — where a signature goes is the institution's call, including
    onto a figures page if their return format puts the attestation block there.
    What is NOT their call is a field that cannot be drawn, so the checks that
    remain are the ones an operator cannot see for themselves:

    * every named role must have a signature field on the artifact, and both of
      them must be placed — an approver whose field was never created can never
      sign, because the certification forbids adding one later;
    * no two placements may resolve to the same field name, since one AcroForm
      field cannot be in two places;
    * the page must exist;
    * the box must lie inside that page's MediaBox. Refused, never clamped: a
      clamped box silently moves a signature somewhere the operator did not put
      it, and half a signature off the page is worse than a refusal an operator
      can act on;
    * the box must be large enough to print its own kind of content legibly
      (:data:`MIN_BOX_SIZES`). Per kind, because a date and a signature mark do
      not need the same room and a single floor derived from the largest of them
      is what stopped a signature field fitting a form's ruled line.
    """
    resolved = tuple(placements)
    signature_roles = [
        placement.signing_role
        for placement in resolved
        if placement.field_type == SIGNATURE_FIELD_TYPE
    ]
    if len(set(signature_roles)) != len(signature_roles):
        raise PdfSigningError(
            "Two signature placements name the same signing role; each role has exactly "
            "one signature field on the document."
        )
    missing = set(ROLE_FIELD_NAMES) - set(signature_roles)
    if missing:
        raise PdfSigningError(
            f"No signature placement was given for {sorted(missing)}. Every signature "
            f"field must exist before the preparer certifies — the certification permits "
            f"only form filling afterwards, so a field left unplaced can never be added."
        )
    names = [placement.field_name for placement in resolved]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise PdfSigningError(
            f"Field(s) {duplicated} were placed more than once. Two boxes of the same "
            f"kind for the same signer need different field indexes — one AcroForm field "
            f"cannot be in two places."
        )

    page_count = _page_count(reader)
    for placement in resolved:
        _ = placement.field_name  # refuses an unknown role or kind, with its own message
        if not 0 <= placement.page_index < page_count:
            raise PdfSigningError(
                f"The {placement.signing_role} {placement.field_type} field is placed on "
                f"page {placement.page_index}, but this return has {page_count} page(s)."
            )
        min_width, min_height = min_box_size(placement.field_type)
        if placement.width < min_width or placement.height < min_height:
            raise PdfSigningError(
                f"The {placement.signing_role} {placement.field_type} field is "
                f"{placement.width}×{placement.height} points, below the "
                f"{min_width}×{min_height} it needs to print "
                f"{too_small_detail(placement.field_type)}. Enlarge the field rather "
                f"than filing a return an examiner cannot read."
            )
        _require_inside_page(reader, placement)
    return resolved


def too_small_detail(field_type: str) -> str:
    """What a box of this kind must fit, spelled out for the refusal message.

    A refusal that only prints two numbers tells an operator their box is wrong
    but not what it is wrong for; the signature stamp in particular is refused
    for a reason nobody can guess from the outside — three stacked elements, not
    one squiggle.
    """
    if field_type == SIGNATURE_FIELD_TYPE:
        return (
            f"the whole signature stamp — the role label at {_LABEL_MIN_SIZE:g} pt, the "
            f"adopted mark in a band at least {_MARK_BAND_MIN_HEIGHT} pt tall, and the "
            f"{SIGNER_ID_CHARS}-character permanent signer ID at "
            f"{MIN_LEGIBLE_FONT_SIZE} pt"
        )
    return f"'{_REFERENCE_CONTENT[field_type]}' legibly"


def _page_count(reader: PdfFileReader) -> int:
    try:
        return int(reader.root["/Pages"]["/Count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfSigningError(
            "This PDF's page tree could not be read, so signature fields cannot be placed."
        ) from exc


def _require_inside_page(reader: PdfFileReader, placement: FieldPlacement) -> None:
    x0, y0, x1, y1 = _media_box(reader, placement.page_index)
    bx0, by0, bx1, by1 = placement.box
    if bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1:
        raise PdfSigningError(
            f"The {placement.signing_role} signature box {placement.box} falls outside "
            f"page {placement.page_index}, whose media box is "
            f"({x0:g}, {y0:g}, {x1:g}, {y1:g}). Move the field onto the page — a box is "
            f"never trimmed to fit, because a signature the operator cannot see is not "
            f"the signature they placed."
        )


def _media_box(reader: PdfFileReader, page_index: int) -> tuple[float, float, float, float]:
    """The page's media box, following ``/Parent`` for the inheritable attribute."""
    try:
        resolved = reader.find_page_for_modification(page_index)[0].get_object()
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise PdfSigningError(
            f"Page {page_index} of this PDF could not be read, so a signature field "
            f"cannot be placed on it."
        ) from exc
    if not isinstance(resolved, generic.DictionaryObject):
        raise PdfSigningError(f"Page {page_index} of this PDF is not a page dictionary.")
    node: generic.DictionaryObject = resolved
    for _ in range(_PAGE_TREE_DEPTH_LIMIT):
        raw = node.get("/MediaBox")
        if raw is not None:
            try:
                x0, y0, x1, y1 = (float(value) for value in raw)
            except (TypeError, ValueError) as exc:
                raise PdfSigningError(
                    f"Page {page_index} declares an unreadable /MediaBox."
                ) from exc
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        parent = node.get("/Parent")
        if parent is None:
            break
        walked = parent.get_object()
        if not isinstance(walked, generic.DictionaryObject):
            break
        node = walked
    raise PdfSigningError(
        f"Page {page_index} declares no /MediaBox, so a signature box cannot be checked "
        f"against the page it would be drawn on."
    )


# --- derived text fields ----------------------------------------------------


def _mono_font_resource() -> generic.DictionaryObject:
    """A fresh standard-14 Courier dictionary for a derived field's appearance."""
    return _base_14_font(_APPEARANCE_FONT_NAME)


def _append_text_field(writer: IncrementalPdfFileWriter, placement: FieldPlacement) -> None:
    """Create one empty AcroForm text field for a placed derived value.

    Written here rather than taken from pyHanko because pyHanko's form helpers
    are signature-field-specific. The dictionary is a merged field/widget — the
    ordinary single-widget shape — so the annotation the page carries and the
    field the AcroForm lists are one object, which is also what pyHanko's diff
    analysis expects to see filled later.

    ``/AcroForm`` is guaranteed to exist by the time this runs: the caller
    creates the signature fields first, and ``append_signature_field`` builds the
    form if the export did not.
    """
    x1, y1, x2, y2 = placement.box
    form = _acro_form(writer)
    page_ref = writer.find_page_for_modification(placement.page_index)[0]
    field = generic.DictionaryObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/Annot"),
            generic.pdf_name("/Subtype"): generic.pdf_name("/Widget"),
            generic.pdf_name("/FT"): generic.pdf_name("/Tx"),
            generic.pdf_name("/T"): generic.TextStringObject(placement.field_name),
            generic.pdf_name("/TU"): generic.TextStringObject(
                f"{label_for_role(placement.signing_role)} — {placement.field_type}"
            ),
            generic.pdf_name("/Rect"): generic.ArrayObject(
                [generic.NumberObject(value) for value in (x1, y1, x2, y2)]
            ),
            # Print, and do not scroll or zoom with the page (bits 3 and 4 are
            # meaningless for a form value that is already sized to its box).
            generic.pdf_name("/F"): generic.NumberObject(4),
            # ReadOnly. A derived value is not something anyone may retype: it
            # comes off the signature record, and the flag is what stops a reader
            # painting the field-highlight tint over it — a filed return that
            # shows grey boxes behind the officer's name reads as an unfinished
            # web form rather than as a document. Set at CREATION, in the
            # revision before any signature exists, so it is never a change the
            # preparer's DocMDP policy has to account for.
            generic.pdf_name("/Ff"): generic.NumberObject(1),
            # No background fill and no border: an empty /BG and /BC is the
            # explicit "paint neither", as opposed to leaving /MK absent and
            # letting each viewer decide. Only the value shows, sitting on the
            # rule the template already printed.
            generic.pdf_name("/MK"): generic.DictionaryObject(
                {
                    generic.pdf_name("/BG"): generic.ArrayObject(),
                    generic.pdf_name("/BC"): generic.ArrayObject(),
                }
            ),
            generic.pdf_name("/BS"): generic.DictionaryObject(
                {
                    generic.pdf_name("/W"): generic.NumberObject(0),
                    generic.pdf_name("/S"): generic.pdf_name("/S"),
                }
            ),
            generic.pdf_name("/DA"): generic.TextStringObject(
                f"{_MONO_FONT_RESOURCE} {MIN_LEGIBLE_FONT_SIZE} Tf 0 g"
            ),
            generic.pdf_name("/P"): page_ref,
        }
    )
    field_ref = writer.add_object(field)

    listed = form["/Fields"]
    listed.append(field_ref)
    writer.update_container(listed)

    # /DR is what a reader falls back on if it ever regenerates the appearance
    # itself; the appearance streams written at fill time carry their own copy.
    resources = _sub_dictionary(form, "/DR")
    fonts = _sub_dictionary(resources, "/Font")
    if _MONO_FONT_RESOURCE not in fonts:
        fonts[generic.pdf_name(_MONO_FONT_RESOURCE)] = writer.add_object(
            _mono_font_resource()
        )
    writer.update_container(form)

    page = page_ref.get_object()
    if not isinstance(page, generic.DictionaryObject):
        raise PdfSigningError(f"Page {placement.page_index} of this PDF is not a page.")
    annotations = page.get("/Annots")
    if annotations is None:
        annotations = generic.ArrayObject()
        page[generic.pdf_name("/Annots")] = annotations
        writer.update_container(page)
    annotations.append(field_ref)
    writer.update_container(annotations)


def _sub_dictionary(
    parent: generic.DictionaryObject, key: str
) -> generic.DictionaryObject:
    """``parent[key]`` as a mutable dictionary, created if absent.

    Resolved rather than taken raw: an AcroForm that arrived with an INDIRECT
    ``/DR`` would otherwise hand back a reference, and mutating that would either
    raise or write the font resource into nothing.
    """
    existing = parent.get(key)
    resolved = existing.get_object() if existing is not None else None
    if isinstance(resolved, generic.DictionaryObject):
        return resolved
    created = generic.DictionaryObject()
    parent[generic.pdf_name(key)] = created
    return created


def _acro_form(writer: IncrementalPdfFileWriter) -> generic.DictionaryObject:
    form = writer.root.get("/AcroForm")
    resolved = form.get_object() if form is not None else None
    if not isinstance(resolved, generic.DictionaryObject) or "/Fields" not in resolved:
        raise PdfSigningError(
            "This PDF has no AcroForm to add a text field to. Signature fields are "
            "created first precisely so the form exists by the time this runs."
        )
    return resolved


def _fill_derived_fields(
    writer: IncrementalPdfFileWriter, *, signing_role: str, values: Mapping[str, str]
) -> None:
    """Fill this role's text fields with their derived values, in ``writer``.

    Called from inside :func:`_sign`, so the fill and the signature land in ONE
    incremental update. That is not tidiness: pyHanko's diff analysis treats an
    appearance stream older than the signed revision as an in-place-update
    candidate and reports it as not-valid-when-locked, so a fill in its own
    earlier revision would make the preparer's own ``/Lock`` convict a field
    nobody had touched. Filling alongside the signature keeps every value inside
    the revision that signature covers, which is where it belongs anyway.

    A document prepared before typed fields existed simply has none of these
    fields, and this is a no-op on it.
    """
    prefix = f"Txt_{_ROLE_TOKENS.get(signing_role, signing_role)}_"
    for name, field_ref in _text_fields(writer.prev):
        if not name.startswith(prefix):
            continue
        field_type = _field_type_of(name)
        if field_type is None:
            continue
        _fill_one(writer, name=name, field_ref=field_ref, value=values.get(field_type, ""))


def _field_type_of(field_name: str) -> str | None:
    """``Txt_Approver_Date_1`` → ``date_signed``; ``None`` for a stranger."""
    parts = field_name.split("_")
    if len(parts) != 4:  # noqa: PLR2004 - Txt / role / kind / index
        return None
    for field_type, token in _TEXT_FIELD_TOKENS.items():
        if parts[2] == token:
            return field_type
    return None


def _text_fields(
    reader: PdfFileReader,
) -> Iterator[tuple[str, generic.IndirectObject]]:
    """Every ``/Tx`` field in the AcroForm, by name.

    Walked here rather than through pyHanko's ``enumerate_fields_in``, which is
    not part of its public surface. The tree is flat in practice — this module is
    the only thing that ever adds a text field, and it adds terminal ones — but
    the walk follows ``/Kids`` anyway, bounded and cycle-guarded, so a form built
    by something else cannot make it loop or silently miss a field.
    """
    form = reader.root.get("/AcroForm")
    resolved = form.get_object() if form is not None else None
    if not isinstance(resolved, generic.DictionaryObject):
        return
    listed = resolved.get("/Fields")
    if not isinstance(listed, generic.ArrayObject):
        return
    seen: set[tuple[int, int]] = set()
    pending: list[tuple[generic.IndirectObject, int]] = [
        (ref, 0) for ref in listed if isinstance(ref, generic.IndirectObject)
    ]
    while pending:
        field_ref, depth = pending.pop()
        key = (field_ref.idnum, field_ref.generation)
        if key in seen or depth > _FIELD_TREE_DEPTH_LIMIT:
            continue
        seen.add(key)
        field = field_ref.get_object()
        if not isinstance(field, generic.DictionaryObject):
            continue
        kids = field.get("/Kids")
        if isinstance(kids, generic.ArrayObject):
            pending.extend(
                (kid, depth + 1) for kid in kids if isinstance(kid, generic.IndirectObject)
            )
        name = field.get("/T")
        if field.get("/FT") == "/Tx" and name is not None:
            yield str(name), field_ref


def _fill_one(
    writer: IncrementalPdfFileWriter,
    *,
    name: str,
    field_ref: generic.IndirectObject,
    value: str,
) -> None:
    """Set one field's ``/V`` and draw the matching appearance stream.

    The appearance is generated rather than left to ``/NeedAppearances``: a
    reader that regenerates appearances is rewriting a certified document to
    display it, and the one thing this module will not do is let what an examiner
    sees depend on which viewer they opened it in.
    """
    field = field_ref.get_object()
    if not isinstance(field, generic.DictionaryObject):
        raise PdfSigningError(f"Form field {name!r} is not a field dictionary.")
    x1, y1, x2, y2 = _field_rect(field, name)
    width, height = x2 - x1, y2 - y1
    size = _fitted_font_size(value, width=width, height=height)
    try:
        encoded = value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise PdfSigningError(
            f"The value for form field {name!r} contains characters the standard "
            f"{_APPEARANCE_FONT_NAME} font cannot render: {value!r}. Rendering it would "
            f"silently substitute glyphs in a signer's name or designation."
        ) from exc
    # /Tx BMC … EMC is the marked-content wrapper a text field's appearance is
    # required to carry; readers that re-flow form appearances look for it.
    stream = content.RawContent(
        b"/Tx BMC q BT %s %g Tf 0 g %g %g Td <%s> Tj ET Q EMC"
        % (
            _MONO_FONT_RESOURCE.encode("ascii"),
            size,
            _FIELD_PADDING,
            _baseline(size, height),
            binascii.hexlify(encoded),
        ),
        box=BoxConstraints(width=width, height=height),
    ).as_form_xobject()
    stream[generic.pdf_name("/Resources")] = generic.DictionaryObject(
        {
            generic.pdf_name("/Font"): generic.DictionaryObject(
                {
                    generic.pdf_name(_MONO_FONT_RESOURCE): writer.add_object(
                        _mono_font_resource()
                    )
                }
            )
        }
    )
    appearance = generic.DictionaryObject()
    appearance[generic.pdf_name("/N")] = writer.add_object(stream)
    field[generic.pdf_name("/AP")] = appearance
    field[generic.pdf_name("/V")] = generic.TextStringObject(value)
    writer.mark_update(field_ref)


def _field_rect(field: generic.DictionaryObject, name: str) -> tuple[float, float, float, float]:
    try:
        raw = field["/Rect"]
        x1, y1, x2, y2 = (float(coord) for coord in raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfSigningError(
            f"Form field {name!r} declares no readable /Rect, so its value cannot be "
            f"drawn."
        ) from exc
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _fitted_font_size(value: str, *, width: float, height: float) -> float:
    """The largest size at which ``value`` fits its box, capped.

    An over-long designation legitimately shrinks below the legibility floor
    rather than being truncated or refused: the placement was already checked
    against the floor for a representative value, and neither cutting a name off
    nor failing a certification on filing day is a better answer than small
    print.
    """
    by_height = max(height - 2 * _FIELD_PADDING, 1) / _LINE_BOX_RATIO
    if not value:
        return min(by_height, _DERIVED_FONT_MAX_SIZE)
    by_width = max(width - 2 * _FIELD_PADDING, 1) / (len(value) * _APPEARANCE_FONT_WIDTH)
    return min(by_height, by_width, _DERIVED_FONT_MAX_SIZE)


def _baseline(size: float, height: float) -> float:
    """Where the text sits so its line box is centred in the field."""
    return (height - size * _LINE_BOX_RATIO) / 2 + size * (_LINE_BOX_RATIO - 1)


# --- signing ----------------------------------------------------------------


@dataclass(frozen=True)
class PadesProfile:
    """The long-term-validation profile for one signature (§3.2).

    The four values always travel together and are only meaningful together, so
    they are validated as one thing: PAdES B-LTA is a timestamper *plus* the
    embedded validation material *plus* a document timestamp over it. The
    defaults are B-LTA because that is what keeps a filed return verifiable for
    the retention period without re-fetching anything from a CA that may no
    longer exist; a caller that cannot supply trusted time must say so out loud
    by turning the flags off, which also forces the appearance to stop claiming
    a timestamp.

    ``timestamper`` comes from ``attestation.tsa.build_pdf_timestamper()`` and
    ``validation_context`` pins the configured trust roots.
    """

    timestamper: TimeStamper | None = None
    validation_context: ValidationContext | None = None
    use_pades_lta: bool = True
    embed_validation_info: bool = True

    def require_deliverable(self, appearance: SignatureAppearance) -> None:
        """Refuse a profile the inputs cannot actually deliver.

        Every check exists so the document never over-claims: B-LTA without a
        timestamper is not B-LTA; embedded validation material with no trust
        roots is not verifiable; and an appearance printing
        "(RFC 3161 timestamped)" beside a signature that carries no token would
        be a printed falsehood on a document filed with the regulator.
        """
        if appearance.timestamped and self.timestamper is None:
            raise PdfSigningError(
                "The appearance claims an RFC 3161 timestamp but no TimeStamper was "
                "supplied. Pass attestation.tsa.build_pdf_timestamper(), or build the "
                "appearance with timestamped=False."
            )
        if self.use_pades_lta and self.timestamper is None:
            raise PdfSigningError(
                "PAdES B-LTA requires an RFC 3161 TimeStamper for the document timestamp."
            )
        if (self.use_pades_lta or self.embed_validation_info) and self.validation_context is None:
            raise PdfSigningError(
                "PAdES B-LT/B-LTA embeds validation material, which needs a "
                "ValidationContext pinned to the configured trust roots."
            )


@dataclass(frozen=True)
class _FieldRole:
    """Which field is being filled, and whether filling it certifies the document."""

    signing_role: str
    field_name: str
    certify: bool


_PREPARER_ROLE: Final = _FieldRole("preparer", PREPARER_FIELD_NAME, certify=True)
_APPROVER_ROLE: Final = _FieldRole("approver", APPROVER_FIELD_NAME, certify=False)


def sign_as_preparer(
    pdf_bytes: bytes,
    *,
    signer: signers.Signer,
    appearance: SignatureAppearance,
    profile: PadesProfile,
) -> bytes:
    """Certify the return into ``Sig_Preparer`` (DocMDP, level 2).

    ``certify=True`` with ``docmdp_permissions=MDPPerm.FILL_FORMS`` is what
    makes the approver's later signature legitimate and everything else
    detectable. Only one certification signature may exist per document, so this
    must be the first signature applied.
    """
    return _sign(pdf_bytes, _PREPARER_ROLE, signer=signer, appearance=appearance, profile=profile)


def sign_as_approver(
    pdf_bytes: bytes,
    *,
    signer: signers.Signer,
    appearance: SignatureAppearance,
    profile: PadesProfile,
) -> bytes:
    """Approve into ``Sig_Approver`` as an incremental update.

    ``pdf_bytes`` must be the preparer-certified document: the update is
    appended, so the preparer's byte range and its coverage of revision 1 are
    unchanged and both signatures verify on their own. The field's ``/Lock``
    (installed by :func:`prepare_signature_fields`) takes effect here and seals
    every form field.
    """
    return _sign(pdf_bytes, _APPROVER_ROLE, signer=signer, appearance=appearance, profile=profile)


def _sign(
    pdf_bytes: bytes,
    role: _FieldRole,
    *,
    signer: signers.Signer,
    appearance: SignatureAppearance,
    profile: PadesProfile,
) -> bytes:
    profile.require_deliverable(appearance)
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    box_width, box_height = _require_empty_field(reader, role.field_name)
    if role.certify and reader.embedded_signatures:
        raise PdfSigningError(
            "A certification (DocMDP) signature must be the first signature in the "
            "document; this PDF already carries one or more signatures."
        )

    metadata = signers.PdfSignatureMetadata(
        field_name=role.field_name,
        certify=role.certify,
        # Level 2. Only meaningful together with certify=True; the approver's
        # restrictions come from the field's /Lock, not from here.
        docmdp_permissions=fields.MDPPerm.FILL_FORMS,
        subfilter=fields.SigSeedSubFilter.PADES,
        use_pades_lta=profile.use_pades_lta,
        embed_validation_info=profile.embed_validation_info,
        validation_context=profile.validation_context,
        reason=f"{appearance.role_label} (AequorOS attestation)",
        # /Name carries the permanent signer ID into the signature dictionary as
        # well as the appearance, so a machine reader never has to OCR the stamp.
        name=f"{appearance.signer_name} ({appearance.signer_id})",
    )
    pdf_signer = signers.PdfSigner(
        metadata,
        signer=signer,
        timestamper=profile.timestamper,
        # The field's own /Rect is the authority on how big the appearance is:
        # the placement that created it may have come from any of the three
        # resolution sources, and the layout must match the box actually drawn.
        stamp_style=_stamp_style(appearance, box_width=box_width, box_height=box_height),
    )
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
    # Same update as the signature: see _fill_derived_fields on why the two
    # cannot be separate revisions.
    _fill_derived_fields(
        writer, signing_role=role.signing_role, values=appearance.derived_values()
    )
    out = io.BytesIO()
    try:
        pdf_signer.sign_pdf(
            writer,
            # The field must already exist: pyHanko may not invent one, because a
            # new field would be a structural change under the DocMDP policy.
            existing_fields_only=True,
            # No text params: the stamp is not a pyHanko text template any more,
            # it is a content stream this module draws itself (_stamp_style), so
            # every printed word is already bound into the style.
            output=out,
        )
    except PdfSigningError:
        raise
    except Exception as exc:  # pyHanko raises SigningError/PdfError/OSError families
        raise PdfSigningError(f"Signing field {role.field_name!r} failed: {exc}") from exc
    return out.getvalue()


def _require_empty_field(reader: PdfFileReader, field_name: str) -> tuple[int, int]:
    """Assert the field exists and is unsigned; return its size in points."""
    for name, value, field_ref in fields.enumerate_sig_fields(reader):
        if name != field_name:
            continue
        if value is not None:
            raise PdfSigningError(
                f"Signature field {field_name!r} is already signed. A second signature "
                f"in the same field would replace evidence rather than add to it."
            )
        return _field_size(field_ref.get_object(), field_name)
    raise PdfSigningError(
        f"This PDF has no signature field {field_name!r}. Run "
        f"prepare_signature_fields() on the archived unsigned artifact first."
    )


def _field_size(field: object, field_name: str) -> tuple[int, int]:
    try:
        rect = field["/Rect"]  # pyright: ignore[reportIndexIssue] - pyHanko is untyped
        return (
            abs(round(float(rect[2]) - float(rect[0]))),
            abs(round(float(rect[3]) - float(rect[1]))),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PdfSigningError(
            f"Signature field {field_name!r} declares no readable /Rect, so its "
            f"appearance cannot be laid out."
        ) from exc


__all__ = [
    "APPROVER_BOX",
    "APPROVER_FIELD_NAME",
    "ATTESTATION_PAGE_INDEX",
    "DEFAULT_PLACEMENTS",
    "DETAIL_MIN_HEIGHT",
    "DETAIL_MIN_WIDTH",
    "FIELD_TYPES",
    "MIN_BOX_SIZES",
    "MIN_LEGIBLE_FONT_SIZE",
    "PREPARER_BOX",
    "PREPARER_FIELD_NAME",
    "ROLE_FIELD_NAMES",
    "ROLE_LABELS",
    "SIGNATURE_FIELD_TYPE",
    "SIGNER_ID_CHARS",
    "AdoptedMark",
    "FieldPlacement",
    "PadesProfile",
    "PdfSigningError",
    "SignatureAppearance",
    "SignatureRecordLike",
    "label_for_role",
    "min_box_size",
    "too_small_detail",
    "prepare_signature_fields",
    "sign_as_approver",
    "sign_as_preparer",
]
