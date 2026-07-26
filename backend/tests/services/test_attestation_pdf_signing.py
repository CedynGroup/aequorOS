"""End-to-end PAdES signing of a real return PDF (docs/attestation_esignature.md §3.2).

Nothing cryptographic is mocked. Each test mints a throwaway CA, a P-256 signer
certificate and an RSA timestamping certificate with ``cryptography``, signs an
actual ``render_pdf`` output, and validates the result with
``pyhanko.sign.validation.validate_pdf_signature``. The one thing that cannot be
real is the timestamping *authority*: there is no TSA to reach from a test, so
pyHanko's ``DummyTimeStamper`` issues genuine RFC 3161 tokens from the local
throwaway TSA key. The token structure, its embedding, and its validation are
therefore exercised for real; only the authority's independence is simulated.

``test_tamper_between_signatures_is_detected`` is the load-bearing test: it is
the executable form of the claim that we would know if the figures changed
between the preparer's and the approver's signatures.

The last section covers the **bridge** — ``signers.get_pdf_signer``, the piece
that turns an enrolled custody key into the pyHanko ``Signer`` every function
here takes. There the key material is real too: a sealed soft key, opened
through the software backend exactly as production opens a token.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.diff_analysis import (
    DiffResult,
    ModificationLevel,
    SuspiciousModification,
)
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko.sign.validation import (
    SignatureCoverageLevel,
    read_certification_data,
    validate_pdf_signature,
    validate_pdf_timestamp,
)
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.models import AttestationSignature
from app.services.attestation.appearance import normalise_drawn_signature
from app.services.attestation.pdf_signing import (
    APPROVER_BOX,
    APPROVER_FIELD_NAME,
    ATTESTATION_PAGE_INDEX,
    DEFAULT_PLACEMENTS,
    DETAIL_MIN_HEIGHT,
    DETAIL_MIN_WIDTH,
    MIN_BOX_SIZES,
    MIN_LEGIBLE_FONT_SIZE,
    PREPARER_BOX,
    PREPARER_FIELD_NAME,
    SIGNATURE_FIELD_TYPE,
    SIGNER_ID_CHARS,
    AdoptedMark,
    FieldPlacement,
    PadesProfile,
    PdfSigningError,
    SignatureAppearance,
    SignatureRecordLike,
    label_for_role,
    prepare_signature_fields,
    sign_as_approver,
    sign_as_preparer,
)
from app.services.attestation.signers import (
    RSA_2048_PSS_SHA256,
    KmsRawSigner,
    PdfSignerUnavailable,
    SoftwareRawSigner,
    default_validity,
    get_pdf_signer,
    signer_subject,
)
from app.services.attestation.typed_fonts import TYPED_FACES
from app.services.attestation.verify import attestation_diff_policy
from app.services.regulatory_reporting.exports.pdf import render_pdf
from app.services.regulatory_reporting.templates import (
    STANDARD_ATTESTATION_LINES,
    ColumnSpec,
    RenderedCell,
    RenderedReturn,
    RenderedRow,
    RenderedSection,
    ReturnTemplate,
    SectionLayout,
)

#: The §2.5 worked example. Text on a page only — it never has to agree with
#: the clock the test PKI runs on.
NOW = datetime(2026, 7, 31, 14, 2, 11, tzinfo=UTC)

#: Certificate and timestamp validity are anchored to the real clock, because
#: pyHanko validates the signer path at the actual moment of validation: a
#: certificate minted around a hardcoded date would start failing the day the
#: suite ran outside that window.
CLOCK_NOW = datetime.now(UTC)
CERT_NOT_BEFORE = CLOCK_NOW - timedelta(days=365)
CERT_NOT_AFTER = CLOCK_NOW + timedelta(days=365)

#: A figure that appears verbatim in the rendered figures page's (Flate-encoded)
#: content stream, so a test can edit it the way a tamperer would.
FIGURE_TEXT = b"1,234"
TAMPERED_FIGURE_TEXT = b"9,999"
FIGURES_PAGE_INDEX = 2


# --- test PKI ---------------------------------------------------------------


@dataclass(frozen=True)
class SigningPki:
    """A throwaway CA plus the signer and TSA credentials issued under it."""

    signer: signers.SimpleSigner
    timestamper: DummyTimeStamper
    validation_context: ValidationContext

    def profile(self, *, lta: bool) -> PadesProfile:
        """B-LTA when ``lta``, otherwise B-T (timestamp, no embedded LTV data)."""
        return PadesProfile(
            timestamper=self.timestamper,
            validation_context=self.validation_context if lta else None,
            use_pades_lta=lta,
            embed_validation_info=lta,
        )


def _subject(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        # Non-repudiation: pyHanko's default key-usage constraint for a signer.
        content_commitment=not ca,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def _self_signed_ca(key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    name = _subject("AequorOS Attestation Test CA")
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(CERT_NOT_BEFORE)
        .not_valid_after(CERT_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .sign(key, hashes.SHA256())
    )


def _issue(
    public_key: ec.EllipticCurvePublicKey | rsa.RSAPublicKey,
    common_name: str,
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    *,
    timestamping: bool = False,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(_subject(common_name))
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(CERT_NOT_BEFORE)
        .not_valid_after(CERT_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(ca=False), critical=True)
    )
    if timestamping:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
    return builder.sign(ca_key, hashes.SHA256())


def _asn1_cert(cert: x509.Certificate) -> asn1_x509.Certificate:
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def _asn1_key(
    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey,
) -> asn1_keys.PrivateKeyInfo:
    return asn1_keys.PrivateKeyInfo.load(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


@pytest.fixture(scope="module")
def pki() -> SigningPki:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _self_signed_ca(ca_key)
    ca_asn1 = _asn1_cert(ca_cert)

    # ECDSA P-256 with SHA-256 — the algorithm §3.3 names for signer keys.
    signer_key = ec.generate_private_key(ec.SECP256R1())
    signer_cert = _issue(signer_key.public_key(), "Ama Mensah", ca_key, ca_cert)

    # pyHanko's DummyTimeStamper signs TSTInfo with RSA only.
    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa_cert = _issue(tsa_key.public_key(), "AequorOS Test TSA", ca_key, ca_cert, timestamping=True)

    return SigningPki(
        signer=signers.SimpleSigner(
            signing_cert=_asn1_cert(signer_cert),
            signing_key=_asn1_key(signer_key),
            cert_registry=SimpleCertificateStore.from_certs([ca_asn1]),
        ),
        timestamper=DummyTimeStamper(
            tsa_cert=_asn1_cert(tsa_cert),
            tsa_key=_asn1_key(tsa_key),
            certs_to_embed=SimpleCertificateStore.from_certs([ca_asn1]),
            fixed_dt=CLOCK_NOW,
        ),
        # No fetching: a test must never reach a CA, and production pins the
        # same way (validation material comes from the embedded LTV data).
        validation_context=ValidationContext(
            trust_roots=[ca_asn1], allow_fetching=False, revocation_mode="soft-fail"
        ),
    )


# --- the document under signature -------------------------------------------


def _rendered_return() -> RenderedReturn:
    layout = SectionLayout(
        section_code="summary",
        layout_id="test.summary",
        sheet_title="Summary",
        columns=(
            ColumnSpec("code", "Row"),
            ColumnSpec("description", "Item"),
            ColumnSpec("value", "Amount (GHS '000)", "ghs"),
        ),
        fidelity="CONFIRMED",
        source_citation="BoG BSD/2026/test",
    )
    template = ReturnTemplate(
        template_id="test.v1",
        return_code="BSD3",
        title="Test Return",
        fidelity="CONFIRMED",
        source_citation="BoG BSD/2026/test",
        sections=(layout,),
    )
    rows = (
        RenderedRow(
            cells=(
                RenderedCell("text", "1.1"),
                RenderedCell("text", "Total assets"),
                # Rendered in GHS '000, so this prints as FIGURE_TEXT.
                RenderedCell("ghs", Decimal("1234000")),
            )
        ),
    )
    return RenderedReturn(
        template=template,
        metadata_pairs=(
            ("Return", "Test Return"),
            ("Institution", "Sample Bank"),
            ("Reporting date", "2026-06-30"),
        ),
        sections=(RenderedSection(layout=layout, title="Summary", rows=rows, total_row=None),),
        provenance_runs=(("liquidity", "run-1", "hash-1", "engine-1"),),
        provenance_lines=("AequorOS · Test Return · 2026-06-30",),
        attestation_lines=STANDARD_ATTESTATION_LINES,
    )


def _as_dict(obj: object) -> generic.DictionaryObject:
    """Narrow a resolved PDF object to a dictionary (pyHanko is untyped)."""
    assert isinstance(obj, generic.DictionaryObject)
    return obj


def _page(pdf_or_reader: bytes | PdfFileReader, page_index: int) -> generic.DictionaryObject:
    reader = (
        pdf_or_reader
        if isinstance(pdf_or_reader, PdfFileReader)
        else PdfFileReader(io.BytesIO(pdf_or_reader))
    )
    return _as_dict(reader.root["/Pages"]["/Kids"][page_index].get_object())


def _page_content_stream(pdf: bytes, page_index: int) -> generic.StreamObject:
    contents = _page(pdf, page_index).raw_get("/Contents").get_object()
    assert isinstance(contents, generic.StreamObject)
    return contents


@pytest.fixture(scope="module")
def unsigned_pdf() -> bytes:
    """The archived artifact. Rendered ONCE — signing never re-renders (§3.2)."""
    pdf = render_pdf(_rendered_return(), sandbox_watermark=False)
    # reportlab Flate-compresses page content, so the figure lives in the
    # decoded stream rather than in the raw file bytes.
    assert FIGURE_TEXT in _page_content_stream(pdf, FIGURES_PAGE_INDEX).data
    return pdf


@pytest.fixture(scope="module")
def prepared_pdf(unsigned_pdf: bytes) -> bytes:
    return prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)


def _default_signature_placements() -> tuple[FieldPlacement, ...]:
    """Only the two signature boxes out of the default eight."""
    return tuple(
        placement
        for placement in DEFAULT_PLACEMENTS
        if placement.field_type == SIGNATURE_FIELD_TYPE
    )


#: Two signature boxes past :data:`DETAIL_MIN_WIDTH`/:data:`DETAIL_MIN_HEIGHT`,
#: which is the layout an institution that saved a template before the stamp was
#: redesigned still has. Everything asserting the name/designation and timestamp
#: rows uses this rather than the default cells — those are deliberately sized
#: for the anatomy alone, because the form rules its own cells for the rest.
ROOMY_BOXES: tuple[tuple[int, int, int, int], tuple[int, int, int, int]] = (
    (51, 300, 291, 380),
    (304, 300, 544, 380),
)


@pytest.fixture(scope="module")
def roomy_prepared_pdf(unsigned_pdf: bytes) -> bytes:
    return prepare_signature_fields(
        unsigned_pdf,
        placements=(
            FieldPlacement("preparer", ATTESTATION_PAGE_INDEX, ROOMY_BOXES[0]),
            FieldPlacement("approver", ATTESTATION_PAGE_INDEX, ROOMY_BOXES[1]),
        ),
    )


def _appearance(role: str) -> SignatureAppearance:
    return SignatureAppearance(
        role_label=label_for_role(role),
        signer_name="Ama Mensah",
        officer_title="Chief Financial Officer",
        signer_id="SGN-7K4M9PQR2VWX3YZ8",
        signed_at=NOW,
    )


def _sign_both(pki: SigningPki, prepared: bytes, *, lta: bool) -> bytes:
    certified = sign_as_preparer(
        prepared,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=lta),
    )
    return sign_as_approver(
        certified,
        signer=pki.signer,
        appearance=_appearance("approver"),
        profile=pki.profile(lta=lta),
    )


# --- field preparation ------------------------------------------------------


def test_prepare_adds_two_empty_named_fields_on_the_attestation_page(
    unsigned_pdf: bytes, prepared_pdf: bytes
) -> None:
    reader = PdfFileReader(io.BytesIO(prepared_pdf))
    found = {name: value for name, value, _ in fields.enumerate_sig_fields(reader)}
    assert set(found) == {PREPARER_FIELD_NAME, APPROVER_FIELD_NAME}
    assert all(value is None for value in found.values())  # empty, not signed

    widget_rects = {
        tuple(int(coord) for coord in _as_dict(annot.get_object())["/Rect"])
        for annot in _page(reader, ATTESTATION_PAGE_INDEX)["/Annots"]
    }
    # All eight of the default set: four labelled cells per signing role.
    assert widget_rects == {placement.box for placement in DEFAULT_PLACEMENTS}
    assert PREPARER_BOX in widget_rects
    assert APPROVER_BOX in widget_rects

    # Incremental update: the archived bytes survive verbatim as a prefix, which
    # is what lets the signature cover exactly the artifact that was archived.
    assert prepared_pdf.startswith(unsigned_pdf)


def _ink_baselines(pdf: bytes, page_index: int) -> set[float]:
    """The y coordinate of every text baseline and stroked line on a page.

    A minimal content-stream walk over reportlab's own output: it emits each
    flowable inside ``q 1 0 0 1 x y cm`` and positions text with
    ``1 0 0 1 tx ty Tm``, so ``cm_y + tm_y`` is the absolute baseline.
    """
    data = _page_content_stream(pdf, page_index).data
    token = re.compile(rb"1 0 0 1 (-?[\d.]+) (-?[\d.]+) (cm|Tm)|(Tj)|\bS\b")
    cm_y = tm_y = 0.0
    ink: set[float] = set()
    for match in token.finditer(data):
        if match.group(3) == b"cm":
            cm_y, tm_y = float(match.group(2)), 0.0
        elif match.group(3) == b"Tm":
            tm_y = float(match.group(2))
        else:
            ink.add(round(cm_y + tm_y, 2))
    return ink


def _drawn_rules(pdf: bytes, page_index: int) -> set[tuple[float, float, float]]:
    """Every stroked horizontal rule on a page as ``(y, x_start, x_end)``.

    reportlab nests each flowable and each table cell in its own ``q … Q`` with
    a translation, so the translations have to be stacked rather than tracked as
    a single running value — a cell's local offset must not survive the ``Q``
    that closes it, or every rule after the first lands somewhere it was not
    drawn.
    """
    data = _page_content_stream(pdf, page_index).data
    token = re.compile(
        rb"\bq\b"
        rb"|\bQ\b"
        rb"|1 0 0 1 (-?[\d.]+) (-?[\d.]+) cm"
        rb"|(-?[\d.]+) (-?[\d.]+) m (-?[\d.]+) (-?[\d.]+) l"
    )
    stack: list[tuple[float, float]] = [(0.0, 0.0)]
    rules: set[tuple[float, float, float]] = set()
    for match in token.finditer(data):
        operator = match.group(0)
        if operator == b"q":
            stack.append(stack[-1])
            continue
        if operator == b"Q":
            if len(stack) > 1:
                stack.pop()
            continue
        origin_x, origin_y = stack[-1]
        if match.group(1) is not None:
            stack[-1] = (origin_x + float(match.group(1)), origin_y + float(match.group(2)))
            continue
        x1, y1, x2, y2 = (float(match.group(index)) for index in (3, 4, 5, 6))
        if y1 == y2:
            rules.add(
                (round(origin_y + y1, 2), round(origin_x + x1, 2), round(origin_x + x2, 2))
            )
    return rules


def test_the_default_placements_land_on_the_templates_own_ruled_cells(
    unsigned_pdf: bytes,
) -> None:
    """The founder should not have to nudge a box on a return we designed.

    ``DEFAULT_PLACEMENTS`` and ``exports/pdf.py._signing_block`` are two files'
    worth of arithmetic about one layout. Rather than trust them to agree, this
    reads the rules the template actually stroked and asserts every default box
    sits ON one of them and INSIDE its horizontal span. It was the absence of
    this pairing that put the two signature boxes in the empty band below the
    attestation block, where a preparer had to drag them onto wording the
    template never ruled for them.
    """
    rules = {
        (y, x_start, x_end)
        for y, x_start, x_end in _drawn_rules(unsigned_pdf, ATTESTATION_PAGE_INDEX)
        # The navy header rule runs the full width near the top of every page.
        if y < 800
    }
    assert len(rules) == 2, f"expected one rule per signing block, found {sorted(rules)}"
    # A placement box is whole points by contract; the template's margins are
    # millimetres (18 mm = 51.02 pt), so the two agree to within a point and the
    # assertion has to allow exactly that much and no more.
    tolerance = 1.0
    for placement in DEFAULT_PLACEMENTS:
        landed = [
            rule
            for rule in rules
            if abs(rule[0] - placement.box[1]) <= tolerance
            and rule[1] <= placement.box[0] + tolerance
            and placement.box[2] <= rule[2] + tolerance
        ]
        assert landed, (
            f"the {placement.signing_role} {placement.field_type} box {placement.box} "
            f"sits on no ruled cell; the template drew {sorted(rules)}"
        )


def test_the_signature_cell_has_room_for_the_whole_stamp(unsigned_pdf: bytes) -> None:
    """The template's own cell must clear the floor the stamp is refused below.

    Asserted against the RENDERED page rather than the constants, because the
    failure this guards is a template edit narrowing the Signature column until
    the return we ship cannot be signed on its own form.
    """
    min_width, min_height = MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE]
    for box in (PREPARER_BOX, APPROVER_BOX):
        assert box[2] - box[0] >= min_width
        assert box[3] - box[1] >= min_height
    # And nothing the template printed runs through the stamp: the only ink at
    # the box's own height is the rule it sits on.
    baselines = _ink_baselines(unsigned_pdf, ATTESTATION_PAGE_INDEX)
    for box in (PREPARER_BOX, APPROVER_BOX):
        collisions = {y for y in baselines if box[1] < y < box[3]}
        assert not collisions, f"rendered content sits inside {box}: {sorted(collisions)}"


def test_both_signature_fields_carry_a_field_lock(prepared_pdf: bytes) -> None:
    """The approver seals everything; the preparer seals everything but them.

    The preparer's lock is what stops "fill a form field" — the one thing their
    own DocMDP level permits — from being enough to rewrite the name, designation
    and date printed under their signature.
    """
    reader = PdfFileReader(io.BytesIO(prepared_pdf))
    locks = {
        name: _as_dict(field_ref.get_object()).get("/Lock")
        for name, _, field_ref in fields.enumerate_sig_fields(reader)
    }
    approver_lock = locks[APPROVER_FIELD_NAME]
    assert approver_lock is not None
    lock = _as_dict(approver_lock.get_object())
    assert lock["/Type"] == "/SigFieldLock"
    assert lock["/Action"] == "/All"  # every field seals once the approver signs

    preparer_lock = locks[PREPARER_FIELD_NAME]
    assert preparer_lock is not None
    exclusion = _as_dict(preparer_lock.get_object())
    assert exclusion["/Type"] == "/SigFieldLock"
    assert exclusion["/Action"] == "/Exclude"
    # Every field the approver still has to fill, and nothing of the preparer's.
    assert {str(name) for name in exclusion["/Fields"]} == {
        placement.field_name
        for placement in DEFAULT_PLACEMENTS
        if placement.signing_role == "approver"
    }


def test_prepare_refuses_to_run_twice(prepared_pdf: bytes) -> None:
    with pytest.raises(PdfSigningError, match="already exist"):
        prepare_signature_fields(prepared_pdf, placements=DEFAULT_PLACEMENTS)


def test_prepare_refuses_a_signed_document(pki: SigningPki, prepared_pdf: bytes) -> None:
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    with pytest.raises(PdfSigningError, match="already carries a signature"):
        prepare_signature_fields(certified, placements=DEFAULT_PLACEMENTS)


# --- placement validation ---------------------------------------------------
#
# These replace the old "is this the attestation page?" guard. That guard could
# only ever say "not the page the constants assumed"; with placement explicit the
# refusals below are the ones an operator cannot make for themselves.


def _placed(
    role: str,
    page_index: int,
    box: tuple[int, int, int, int],
    field_type: str = "signature",
) -> FieldPlacement:
    return FieldPlacement(
        signing_role=role, page_index=page_index, box=box, field_type=field_type
    )


def _pair(
    *, preparer: FieldPlacement | None = None, approver: FieldPlacement | None = None
) -> tuple[FieldPlacement, ...]:
    """The minimum complete set: one signature box per role, and nothing else."""
    by_role = {p.signing_role: p for p in _default_signature_placements()}
    return (preparer or by_role["preparer"], approver or by_role["approver"])


def test_prepare_accepts_a_placement_on_any_real_page(unsigned_pdf: bytes) -> None:
    """A figures page is a legitimate destination now, and lands where asked.

    The whole point of removing the attestation-page guard: an institution whose
    return format puts the signature block elsewhere can place it there.
    """
    boxes = ((51, 300, 291, 380), (304, 300, 544, 380))
    prepared = prepare_signature_fields(
        unsigned_pdf,
        placements=(
            _placed("preparer", FIGURES_PAGE_INDEX, boxes[0]),
            _placed("approver", FIGURES_PAGE_INDEX, boxes[1]),
        ),
    )
    reader = PdfFileReader(io.BytesIO(prepared))
    assert {name for name, _, _ in fields.enumerate_sig_fields(reader)} == {
        PREPARER_FIELD_NAME,
        APPROVER_FIELD_NAME,
    }
    assert {
        tuple(int(coord) for coord in _as_dict(annot.get_object())["/Rect"])
        for annot in _page(reader, FIGURES_PAGE_INDEX)["/Annots"]
    } == set(boxes)
    # Nothing was stamped on the page the constants used to insist on.
    assert "/Annots" not in _page(reader, ATTESTATION_PAGE_INDEX)


def test_prepare_refuses_a_page_the_return_does_not_have(unsigned_pdf: bytes) -> None:
    with pytest.raises(PdfSigningError, match="page 99, but this return has"):
        prepare_signature_fields(
            unsigned_pdf, placements=_pair(preparer=_placed("preparer", 99, PREPARER_BOX))
        )


@pytest.mark.parametrize(
    ("box", "detail"),
    [
        ((-40, 470, 240, 550), "falls outside"),  # off the left edge
        ((400, 470, 700, 550), "falls outside"),  # off the right edge
        ((51, 780, 291, 900), "falls outside"),  # off the top
    ],
    ids=["left", "right", "top"],
)
def test_prepare_refuses_a_box_outside_the_page(
    unsigned_pdf: bytes, box: tuple[int, int, int, int], detail: str
) -> None:
    """Refused, never clamped.

    A clamped box would silently move a signature somewhere the operator did not
    put it, and a signature half off the page is worse than a refusal they can act
    on. Asserted per edge because a one-sided comparison would pass the others.
    """
    with pytest.raises(PdfSigningError, match=detail):
        prepare_signature_fields(
            unsigned_pdf,
            placements=_pair(preparer=_placed("preparer", ATTESTATION_PAGE_INDEX, box)),
        )
    # …and nothing partial was produced: the original bytes are untouched.
    assert not PdfFileReader(io.BytesIO(unsigned_pdf)).embedded_signatures


@pytest.mark.parametrize("field_type", list(MIN_BOX_SIZES), ids=list(MIN_BOX_SIZES))
@pytest.mark.parametrize("shrink", ["width", "height"])
def test_prepare_refuses_a_box_below_its_own_kind_of_minimum(
    unsigned_pdf: bytes, field_type: str, shrink: str
) -> None:
    """Every kind answers for its own content, and only for its own.

    One point under either dimension is enough — the boundary is the assertion —
    and every kind is covered because a single shared floor is exactly the defect
    this replaced: the date box would have inherited the signature block's size.
    """
    width, height = MIN_BOX_SIZES[field_type]
    box = (
        (51, 300, 51 + width - 1, 300 + height)
        if shrink == "width"
        else (51, 300, 51 + width, 300 + height - 1)
    )
    placements = (
        *_pair(),
        _placed("preparer", ATTESTATION_PAGE_INDEX, box, field_type),
    )
    if field_type == SIGNATURE_FIELD_TYPE:
        # The signature kind has no spare slot: the preparer's own box IS the
        # one under test.
        placements = _pair(preparer=_placed("preparer", ATTESTATION_PAGE_INDEX, box))
    with pytest.raises(PdfSigningError, match="below the"):
        prepare_signature_fields(unsigned_pdf, placements=placements)


def test_a_signature_box_below_the_floor_is_refused_with_the_numbers(
    unsigned_pdf: bytes,
) -> None:
    """A refusal that only prints two numbers is a refusal nobody can act on.

    The founder's original complaint was the inverse — a box small enough to
    print a bare squiggle was ACCEPTED — so the floor now covers all three
    stamp elements, and it has to say so: an operator staring at a rejected
    140×22 box cannot otherwise guess that the missing 10 points are the signer
    ID's line box.
    """
    min_width, min_height = MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE]
    with pytest.raises(PdfSigningError) as refusal:
        prepare_signature_fields(
            unsigned_pdf,
            placements=_pair(
                preparer=_placed(
                    "preparer",
                    ATTESTATION_PAGE_INDEX,
                    (51, 300, 51 + min_width, 300 + min_height - 1),
                )
            ),
        )
    message = str(refusal.value)
    assert f"{min_width}×{min_height}" in message
    assert f"{min_width}×{min_height - 1} points" in message  # what was placed
    assert f"{SIGNER_ID_CHARS}-character permanent signer ID" in message
    assert f"at {MIN_LEGIBLE_FONT_SIZE} pt" in message
    assert "role label" in message
    assert "adopted mark" in message


def _stamp_font_size(pdf: bytes, field_name: str, *, resource: str) -> float:
    """The size a stamp element is actually set at, in points.

    Read out of the appearance's own content stream rather than assumed: the
    layout is what decides how far each element shrinks, and the only honest way
    to assert legibility is to measure what it did.
    """
    normal = _as_dict(_widget(pdf, field_name)["/AP"])["/N"].get_object()
    assert isinstance(normal, generic.StreamObject)
    sizes = re.findall(
        rb"%s ([\d.]+) Tf" % resource.encode("ascii"), _stamp_stream(normal)
    )
    assert sizes, f"no {resource} text found in {field_name}'s appearance"
    return min(float(size) for size in sizes)


def _stamp_stream(normal: generic.StreamObject) -> bytes:
    """The stamp's own drawing operators, including its nested form XObject."""
    chunks = [normal.data]
    for ref in normal.get("/Resources", {}).get("/XObject", {}).values():
        child = ref.get_object()
        if isinstance(child, generic.StreamObject):
            chunks.append(child.data)
    return b"\n".join(chunks)


def _sign_into_box(
    pki: SigningPki,
    unsigned_pdf: bytes,
    box: tuple[int, int, int, int],
    *,
    mark: AdoptedMark | None = None,
) -> bytes:
    prepared = prepare_signature_fields(
        unsigned_pdf,
        placements=_pair(preparer=_placed("preparer", ATTESTATION_PAGE_INDEX, box)),
    )
    appearance = (
        _appearance("preparer") if mark is None else _mark_appearance("preparer", mark)
    )
    return sign_as_preparer(
        prepared, signer=pki.signer, appearance=appearance, profile=pki.profile(lta=False)
    )


@pytest.mark.parametrize(
    "size",
    [
        MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE],
        (144, 35),
        (PREPARER_BOX[2] - PREPARER_BOX[0], PREPARER_BOX[3] - PREPARER_BOX[1]),
        (240, 80),
    ],
    ids=["at_the_floor", "a_bog_ruled_line", "the_default_cell", "roomy"],
)
def test_the_signer_id_renders_at_every_accepted_signature_size(
    pki: SigningPki, unsigned_pdf: bytes, size: tuple[int, int]
) -> None:
    """Defect 1, as an executable statement.

    The permanent signer ID used to render only above a 185×61 threshold, so
    every smaller placed box — the common case, a field on a form's ruled line —
    filed a bare mark with nothing on the page saying whose it was or that it was
    a digital signature at all. It must now appear at EVERY size the placement
    check accepts, and at a size an examiner can read, so the smallest accepted
    box is the first case here and the assertion is on the drawn string and the
    font size it was drawn at.
    """
    width, height = size
    signed = _sign_into_box(
        pki,
        unsigned_pdf,
        (51, 300, 51 + width, 300 + height),
        mark=AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font="caveat"),
    )
    lines = _appearance_lines(signed, PREPARER_FIELD_NAME)
    assert "SGN-7K4M9PQR2VWX3YZ8" in lines, f"no signer ID drawn in a {width}×{height} box"
    assert "Prepared by" in lines, f"no role label drawn in a {width}×{height} box"
    assert _stamp_font_size(signed, PREPARER_FIELD_NAME, resource="/AeqMono") >= (
        MIN_LEGIBLE_FONT_SIZE
    )
    # …and it is still in the signature dictionary, for the machine reader.
    embedded = PdfFileReader(io.BytesIO(signed)).embedded_regular_signatures[0]
    assert "SGN-7K4M9PQR2VWX3YZ8" in str(embedded.sig_object["/Name"])


def test_the_smallest_signature_box_still_renders_a_legible_mark(
    pki: SigningPki, unsigned_pdf: bytes
) -> None:
    """The founder's 144×35 case, and the floor beneath it.

    A BoG attestation block prints a ruled line roughly 144×35 pt. The floor must
    stay under that — a stamp anatomy nobody can fit on the regulator's own form
    is not an improvement — AND the mark inside it must stay readable, so the size
    the layout actually chose is measured rather than reasoned about.
    """
    min_width, min_height = MIN_BOX_SIZES[SIGNATURE_FIELD_TYPE]
    assert (min_width, min_height) <= (144, 35)
    for box in ((51, 300, 51 + min_width, 300 + min_height), (51, 400, 195, 435)):
        signed = _sign_into_box(
            pki,
            unsigned_pdf,
            box,
            mark=AdoptedMark(kind="typed", typed_name="A. Mensah", typed_font="caveat"),
        )
        rendered = _stamp_font_size(signed, PREPARER_FIELD_NAME, resource="/AeqMark")
        assert rendered >= MIN_LEGIBLE_FONT_SIZE, (
            f"the mark renders at {rendered:.1f} pt in a {box} box"
        )


def test_a_form_sized_box_carries_the_stamp_but_not_the_detail_rows(
    pki: SigningPki, unsigned_pdf: bytes
) -> None:
    """What a box on a form's ruled line prints, exactly.

    The three anatomy elements, and NOT the name/designation and timestamp rows —
    there is no room for them at a legible size, and the form asks for those in
    its own labelled cells, which is where the placed ``name``/``title``/
    ``date_signed`` fields put them.
    """
    signed = _sign_into_box(
        pki, unsigned_pdf, (51, 400, 195, 435), mark=_drawn_mark()
    )
    assert _appearance_lines(signed, PREPARER_FIELD_NAME) == [
        "Prepared by",
        "SGN-7K4M9PQR2VWX3YZ8",
    ]


def test_a_roomy_box_adds_the_designation_and_timestamp_rows(
    pki: SigningPki, unsigned_pdf: bytes
) -> None:
    """The detail rows are additive, never a substitute for the anatomy.

    A box past the threshold prints all four §2.5 facts — and still prints them
    in the same order and the same places, so a template an institution saved
    when the boxes were 240×80 keeps the fuller block.
    """
    assert (DETAIL_MIN_WIDTH, DETAIL_MIN_HEIGHT) <= (240, 80)
    signed = _sign_into_box(pki, unsigned_pdf, (51, 300, 291, 380))
    assert _appearance_lines(signed, PREPARER_FIELD_NAME) == [
        "Prepared by",
        "SGN-7K4M9PQR2VWX3YZ8",
        "Ama Mensah — Chief Financial Officer",
        "2026-07-31 14:02:11 GMT   (RFC 3161 timestamped)",
    ]


def test_prepare_refuses_a_set_that_leaves_the_approver_unplaced(
    unsigned_pdf: bytes,
) -> None:
    """The DocMDP consequence, asserted.

    The preparer's certification permits only form filling, so a field left
    unplaced can never be added — the approver would be locked out of their own
    signature. Refused up front instead.
    """
    with pytest.raises(
        PdfSigningError, match=r"No signature placement was given for \['approver'\]"
    ):
        prepare_signature_fields(unsigned_pdf, placements=_pair()[:1])


def test_prepare_refuses_two_placements_for_one_role(unsigned_pdf: bytes) -> None:
    preparer = _pair()[0]
    with pytest.raises(PdfSigningError, match="same signing role"):
        prepare_signature_fields(unsigned_pdf, placements=(preparer, preparer))


def test_prepare_refuses_a_role_with_no_field_on_the_document(
    unsigned_pdf: bytes,
) -> None:
    with pytest.raises(PdfSigningError, match="has no field on the return artifact"):
        prepare_signature_fields(
            unsigned_pdf,
            placements=(
                *DEFAULT_PLACEMENTS,
                _placed("board", ATTESTATION_PAGE_INDEX, (51, 300, 291, 380)),
            ),
        )


def test_a_field_cannot_be_added_after_certification(
    pki: SigningPki, unsigned_pdf: bytes
) -> None:
    """The constraint the whole placement design is shaped by.

    Once the preparer has certified at ``MDPPerm.FILL_FORMS``, adding a field is a
    structural change. This asserts the refusal is unconditional — not just for
    the two known names — so nothing can grow a "place it later" path.
    """
    prepared = prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)
    certified = sign_as_preparer(
        prepared,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    with pytest.raises(PdfSigningError, match="already carries a signature"):
        prepare_signature_fields(
            certified,
            placements=(
                _placed("preparer", ATTESTATION_PAGE_INDEX, (51, 200, 291, 280)),
                _placed("approver", ATTESTATION_PAGE_INDEX, (304, 200, 544, 280)),
            ),
        )


# --- the visible appearance (§2.5) ------------------------------------------


def test_appearance_renders_exactly_the_four_specified_lines() -> None:
    assert _appearance("approver").lines() == (
        "Approved by",
        "Ama Mensah — Chief Financial Officer",
        "Signer ID: SGN-7K4M9PQR2VWX3YZ8",
        "2026-07-31 14:02:11 GMT   (RFC 3161 timestamped)",
    )
    assert _appearance("preparer").lines()[0] == "Prepared by"


def test_appearance_from_record_prefers_the_trusted_timestamp() -> None:
    @dataclass(frozen=True)
    class Record:
        signing_role: str = "approver"
        signer_id: str = "SGN-7K4M9PQR2VWX3YZ8"
        signer_display_name: str | None = "Ama Mensah"
        officer_title: str | None = "Chief Financial Officer"
        tsa_time: datetime | None = NOW
        declared_at: datetime = NOW - timedelta(hours=9)

    assert SignatureAppearance.from_record(Record()).lines() == _appearance("approver").lines()
    # Server clock only as a fallback, and never silently: the record's own
    # declared_at is what shows when no token asserted a time.
    fallback = SignatureAppearance.from_record(Record(tsa_time=None), timestamped=False)
    assert fallback.lines()[3] == "2026-07-31 05:02:11 GMT   (server time)"


def test_the_orm_signature_record_satisfies_the_appearance_protocol() -> None:
    """Production builds the appearance from the ORM row, so a column rename in
    ``AttestationSignature`` must fail here rather than in the field.

    The cast is not laziness: SQLAlchemy declares columns as ``Mapped[str]`` and
    pyright does not resolve that descriptor when matching a Protocol, even
    though instance access yields a plain ``str``. Conformance is therefore
    asserted by actually reading every attribute the appearance needs.
    """
    row = AttestationSignature(
        signing_role="approver",
        signer_id="SGN-7K4M9PQR2VWX3YZ8",
        signer_display_name="Ama Mensah",
        officer_title="Chief Financial Officer",
        tsa_time=NOW,
        declared_at=NOW - timedelta(hours=9),
    )
    record = cast(SignatureRecordLike, row)
    assert SignatureAppearance.from_record(record).lines() == _appearance("approver").lines()


def test_appearance_may_not_claim_a_timestamp_the_document_lacks(
    pki: SigningPki, prepared_pdf: bytes
) -> None:
    with pytest.raises(PdfSigningError, match="claims an RFC 3161 timestamp"):
        sign_as_preparer(
            prepared_pdf,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=PadesProfile(use_pades_lta=False, embed_validation_info=False),
        )


def test_pades_lta_requires_trust_roots(pki: SigningPki, prepared_pdf: bytes) -> None:
    with pytest.raises(PdfSigningError, match="ValidationContext"):
        sign_as_preparer(
            prepared_pdf,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=PadesProfile(timestamper=pki.timestamper),
        )


def _appearance_streams(pdf: bytes) -> dict[str, bytes]:
    """The rendered appearance content stream per signature field.

    Nested form XObjects included: pyHanko draws the stamp text into a child
    XObject of the widget's /AP /N stream.
    """
    page = _page(pdf, ATTESTATION_PAGE_INDEX)
    collected: dict[str, bytes] = {}

    def walk(stream: generic.StreamObject, depth: int = 0) -> list[bytes]:
        chunks = [stream.data]
        if depth > 4:
            return chunks
        xobjects = stream.get("/Resources", {}).get("/XObject", {})
        for child in xobjects.values():
            chunks.extend(walk(child.get_object(), depth + 1))
        return chunks

    for annot in page["/Annots"]:
        widget = _as_dict(annot.get_object())
        appearance = widget.get("/AP")
        if appearance is None:
            continue
        normal = _as_dict(appearance)["/N"].get_object()
        assert isinstance(normal, generic.StreamObject)
        collected[str(widget["/T"])] = b"\n".join(walk(normal))
    return collected


def _appearance_lines(pdf: bytes, field_name: str) -> list[str]:
    """The text actually drawn in a field's appearance, one entry per line.

    Decoded from the hex strings the appearance font engine emits, using the
    WinAnsi codec the emitted font resource declares — i.e. read back exactly
    the way a conforming PDF reader (or a BoG examiner's viewer) would.
    """
    stream = _appearance_streams(pdf)[field_name]
    return [
        bytes.fromhex(run.decode("ascii")).decode("cp1252")
        for run in re.findall(rb"<([0-9a-fA-F]+)>\s*Tj", stream)
    ]


def test_the_em_dash_is_encoded_for_the_font_the_appearance_declares(
    pki: SigningPki, roomy_prepared_pdf: bytes
) -> None:
    """Regression guard for the PDFDocEncoding/WinAnsi mismatch.

    pyHanko's simple font engine declares /WinAnsiEncoding but serialises text
    as PDFDocEncoding, where the em dash is 0x84 rather than WinAnsi's 0x97 — a
    signature block that misprints an officer's designation. The appearance must
    carry 0x97. Asserted on a roomy box, because that is where the designation
    is printed inside the stamp rather than in the form's own cell.
    """
    signed = _sign_both(pki, roomy_prepared_pdf, lta=False)
    stream = _appearance_streams(signed)[APPROVER_FIELD_NAME]
    officer_runs = [
        bytes.fromhex(run.decode("ascii"))
        for run in re.findall(rb"<([0-9a-fA-F]+)>\s*Tj", stream)
        if b"Chief Financial Officer" in bytes.fromhex(run.decode("ascii"))
    ]
    assert officer_runs, "no officer-title line found in the appearance stream"
    for decoded in officer_runs:
        assert b"\x97" in decoded  # WinAnsi em dash
        assert b"\x84" not in decoded  # the PDFDocEncoding byte pyHanko would emit


def test_appearance_refuses_characters_the_standard_font_cannot_render(
    pki: SigningPki, prepared_pdf: bytes
) -> None:
    unrenderable = SignatureAppearance(
        role_label="Approved by",
        signer_name="李 明",  # outside cp1252 — would silently lose glyphs
        officer_title="Chief Financial Officer",
        signer_id="SGN-7K4M9PQR2VWX3YZ8",
        signed_at=NOW,
    )
    with pytest.raises(PdfSigningError, match="cannot render"):
        sign_as_approver(
            prepared_pdf,
            signer=pki.signer,
            appearance=unrenderable,
            profile=pki.profile(lta=False),
        )


# --- the adopted mark (drawn or typed) --------------------------------------


def _drawn_mark() -> AdoptedMark:
    """A real normalised mark: PIL draws strokes, the normaliser encodes them."""
    from PIL import Image, ImageDraw  # noqa: PLC0415 - fixture-local

    source = Image.new("RGBA", (400, 140), (0, 0, 0, 0))
    pen = ImageDraw.Draw(source)
    pen.line([(20, 110), (90, 30), (160, 110), (240, 40), (330, 90)], fill=(10, 20, 60), width=6)
    raw = io.BytesIO()
    source.save(raw, format="PNG")
    normalised = normalise_drawn_signature(raw.getvalue())
    return AdoptedMark(kind="drawn", image_png=normalised.png)


def _mark_appearance(role: str, mark: AdoptedMark) -> SignatureAppearance:
    base = _appearance(role)
    return SignatureAppearance(
        role_label=base.role_label,
        signer_name=base.signer_name,
        officer_title=base.officer_title,
        signer_id=base.signer_id,
        signed_at=base.signed_at,
        mark=mark,
    )


def _widget(pdf: bytes, field_name: str) -> generic.DictionaryObject:
    for annot in _page(pdf, ATTESTATION_PAGE_INDEX)["/Annots"]:
        widget = _as_dict(annot.get_object())
        if str(widget["/T"]) == field_name:
            return widget
    raise AssertionError(f"no widget for {field_name}")


def _appearance_xobjects(pdf: bytes, field_name: str) -> dict[str, generic.StreamObject]:
    """Every XObject reachable from a field's normal appearance, by resource name."""
    normal = _as_dict(_widget(pdf, field_name)["/AP"])["/N"].get_object()
    assert isinstance(normal, generic.StreamObject)
    collected: dict[str, generic.StreamObject] = {}

    def walk(stream: generic.StreamObject, depth: int = 0) -> None:
        if depth > 4:
            return
        for name, ref in stream.get("/Resources", {}).get("/XObject", {}).items():
            child = ref.get_object()
            if isinstance(child, generic.StreamObject):
                collected[str(name)] = child
                walk(child, depth + 1)

    walk(normal)
    return collected


@pytest.mark.parametrize(
    "mark",
    [
        None,
        AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font="caveat"),
        AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font="times_italic"),
    ],
    ids=["no_mark", "typed_script", "typed_base_14"],
)
def test_the_evidential_lines_survive_every_mark_variant(
    pki: SigningPki, roomy_prepared_pdf: bytes, mark: AdoptedMark | None
) -> None:
    """Whatever the officer adopted, the four §2.5 facts are still on the page.

    This is the property the drawn/typed feature must never cost: the mark is
    additive. An examiner holding a printout reads the signer ID and the
    timestamp, and neither depends on which face was adopted — or on whether one
    was adopted at all.
    """
    appearance = _appearance("approver") if mark is None else _mark_appearance("approver", mark)
    signed = sign_as_approver(
        roomy_prepared_pdf,
        signer=pki.signer,
        appearance=appearance,
        profile=pki.profile(lta=False),
    )
    role, who, signer_id_line, when = _appearance("approver").lines()
    lines = _appearance_lines(signed, APPROVER_FIELD_NAME)
    # Anchored on position rather than equality: a base-14 mark is drawn as its
    # own ``Tj`` run and shows up between the label and the identifier, while a
    # script mark is a glyph array and does not. The evidence is what must hold
    # its place either way.
    assert lines[0] == role
    assert signer_id_line.removeprefix("Signer ID: ") in lines
    assert lines[-2:] == [who, when]


def test_a_drawn_mark_is_embedded_as_an_image_beside_the_evidential_lines(
    pki: SigningPki, roomy_prepared_pdf: bytes
) -> None:
    """The drawn signature reaches the page as an image XObject, and only that.

    Asserting the object's own dictionary rather than "it rendered" is what pins
    the honest half of the module's guarantee: a raster of the normaliser's exact
    dimensions, nothing vector, nothing text-templated.
    """
    mark = _drawn_mark()
    signed = sign_as_approver(
        roomy_prepared_pdf,
        signer=pki.signer,
        appearance=_mark_appearance("approver", mark),
        profile=pki.profile(lta=False),
    )
    images = [
        stream
        for stream in _appearance_xobjects(signed, APPROVER_FIELD_NAME).values()
        if stream.get("/Subtype") == "/Image"
    ]
    assert len(images) == 1
    mark_image = images[0]
    assert int(mark_image["/Width"]) == 600
    assert int(mark_image["/Height"]) == 200
    assert mark_image["/ColorSpace"] == "/DeviceRGB"
    # The pen strokes were drawn on transparency, so the alpha channel must have
    # travelled with them — without the /SMask the mark would sit in a white box.
    assert "/SMask" in mark_image
    # And the evidence is still there beside it.
    assert "SGN-7K4M9PQR2VWX3YZ8" in _appearance_lines(signed, APPROVER_FIELD_NAME)


def _appearance_faces(pdf: bytes, field_name: str) -> dict[str, generic.DictionaryObject]:
    """Every font resource the field's appearance declares, by resource name."""
    collected: dict[str, generic.DictionaryObject] = {}
    for stream in [
        _as_dict(_widget(pdf, field_name)["/AP"])["/N"].get_object(),
        *_appearance_xobjects(pdf, field_name).values(),
    ]:
        for name, ref in stream.get("/Resources", {}).get("/Font", {}).items():
            collected[str(name)] = _as_dict(ref.get_object())
    return collected


def test_a_base_14_typed_mark_renders_in_the_chosen_face_not_the_evidential_courier(
    pki: SigningPki, prepared_pdf: bytes
) -> None:
    """The chosen font must actually be used, and must not replace Courier.

    A typed mark drawn in the evidential font would make the adoption choice
    cosmetic; Courier disappearing would take the monospace signer ID with it.
    """
    signed = sign_as_approver(
        prepared_pdf,
        signer=pki.signer,
        appearance=_mark_appearance(
            "approver",
            AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font="times_italic"),
        ),
        profile=pki.profile(lta=False),
    )
    faces = {
        str(font["/BaseFont"]) for font in _appearance_faces(signed, APPROVER_FIELD_NAME).values()
    }
    assert "/Times-Italic" in faces
    assert "/Courier" in faces


@pytest.mark.parametrize(
    "key", [key for key, face in TYPED_FACES.items() if face.is_script], ids=lambda key: key
)
def test_a_script_mark_embeds_the_bundled_font_into_the_document(
    pki: SigningPki, prepared_pdf: bytes, key: str
) -> None:
    """Defect 2, as an executable statement.

    The four typed choices used to be PDF standard-14 faces — slanted body text,
    which nobody reads as a signature. Each script face must now reach the filed
    document as an EMBEDDED subset of the ``.ttf`` this repository ships: the
    resource has to be a composite font with a descendant carrying a
    ``/FontFile2``, because a face merely *named* in the resource dictionary
    would render as whatever the examiner's reader happened to substitute.
    """
    face = TYPED_FACES[key]
    signed = sign_as_approver(
        prepared_pdf,
        signer=pki.signer,
        appearance=_mark_appearance(
            "approver", AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font=key)
        ),
        profile=pki.profile(lta=False),
    )
    mark_font = _appearance_faces(signed, APPROVER_FIELD_NAME)["/AeqMark"]
    assert mark_font["/Subtype"] == "/Type0"
    descendant = _as_dict(mark_font["/DescendantFonts"][0].get_object())
    descriptor = _as_dict(descendant["/FontDescriptor"])
    embedded = descriptor["/FontFile2"].get_object()
    assert isinstance(embedded, generic.StreamObject)
    assert embedded.data[:4] == b"\x00\x01\x00\x00"  # a real TrueType sfnt
    # The subset is named after the face this repository actually ships, so a
    # swapped file shows up here rather than as a differently-shaped signature.
    assert face.path is not None
    assert face.path.stem.split("-")[0].lower() in str(mark_font["/BaseFont"]).lower()
    # …and the evidential Courier is untouched beside it.
    assert "/Courier" in {
        str(font["/BaseFont"])
        for name, font in _appearance_faces(signed, APPROVER_FIELD_NAME).items()
        if name != "/AeqMark"
    }


@pytest.mark.parametrize(
    ("mark", "detail"),
    [
        (AdoptedMark(kind="drawn"), "carries no image bytes"),
        (AdoptedMark(kind="typed", typed_font="times_italic"), "carries no name"),
        (
            AdoptedMark(kind="typed", typed_name="Ama Mensah", typed_font="Comic Sans"),
            "is not one of",
        ),
        (AdoptedMark(kind="stamped"), "Unknown signature mark kind"),
    ],
    ids=["drawn_without_bytes", "typed_without_name", "unknown_font", "unknown_kind"],
)
def test_an_unrenderable_mark_is_refused_not_silently_dropped(
    pki: SigningPki, prepared_pdf: bytes, mark: AdoptedMark, detail: str
) -> None:
    """Falling back to a mark-less block would file a signature the officer did
    not choose, under a block that looks complete."""
    with pytest.raises(PdfSigningError, match=detail):
        sign_as_approver(
            prepared_pdf,
            signer=pki.signer,
            appearance=_mark_appearance("approver", mark),
            profile=pki.profile(lta=False),
        )


def test_a_typed_mark_outside_the_standard_font_is_refused(
    pki: SigningPki, prepared_pdf: bytes
) -> None:
    """Same rule as the name line: refuse rather than substitute glyphs."""
    with pytest.raises(PdfSigningError, match="cannot render"):
        sign_as_approver(
            prepared_pdf,
            signer=pki.signer,
            appearance=_mark_appearance(
                "approver",
                AdoptedMark(kind="typed", typed_name="李 明", typed_font="times_italic"),
            ),
            profile=pki.profile(lta=False),
        )


# --- the two-signature document ---------------------------------------------


@pytest.mark.parametrize("lta", [True, False], ids=["pades_b_lta", "pades_b_t"])
def test_two_signature_document_validates_both_signatures(
    pki: SigningPki, prepared_pdf: bytes, lta: bool
) -> None:
    signed = _sign_both(pki, prepared_pdf, lta=lta)
    reader = PdfFileReader(io.BytesIO(signed))

    # (a) two signatures, in their own named fields, in that order.
    regular = reader.embedded_regular_signatures
    assert [sig.field_name for sig in regular] == [PREPARER_FIELD_NAME, APPROVER_FIELD_NAME]

    statuses = [
        validate_pdf_signature(
            sig,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        for sig in regular
    ]
    preparer, approver = statuses

    # (b) both intact and valid, each on its own.
    for status in statuses:
        assert status.intact
        assert status.valid
        assert status.trusted
        assert status.timestamp_validity is not None
        assert status.timestamp_validity.valid  # a real RFC 3161 token, validated
        assert status.docmdp_ok
        assert status.bottom_line

    # (c) the preparer covers its whole revision, and the only thing that
    # happened afterwards was the approver filling their form field.
    assert preparer.coverage == SignatureCoverageLevel.ENTIRE_REVISION
    assert preparer.modification_level == ModificationLevel.FORM_FILLING
    assert isinstance(preparer.diff_result, DiffResult)
    assert preparer.diff_result.changed_form_fields == {
        placement.field_name
        for placement in DEFAULT_PLACEMENTS
        if placement.signing_role == "approver"
    }
    if lta:
        # B-LTA appends the document timestamp and the DSS validation material
        # as further revisions, so no *signature* is the last thing in the file.
        # The equivalent guarantee is that everything after the approval is
        # classified as LTA material and nothing else: no form change, no content
        # change, no added page.
        assert approver.coverage == SignatureCoverageLevel.ENTIRE_REVISION
        assert approver.modification_level == ModificationLevel.LTA_UPDATES
        assert isinstance(approver.diff_result, DiffResult)
        assert approver.diff_result.changed_form_fields == set()
        document_timestamps = reader.embedded_timestamp_signatures
        assert len(document_timestamps) == 2  # one per signature
        for embedded_ts in document_timestamps:
            ts_status = validate_pdf_timestamp(
                embedded_ts, validation_context=pki.validation_context
            )
            assert ts_status.intact
            assert ts_status.valid
    else:
        # B-T: the approval signature is the last revision, so it covers the
        # whole file outright.
        assert approver.coverage == SignatureCoverageLevel.ENTIRE_FILE

    # (d) each stamp carries its role label and the permanent signer ID, so the
    # identity travels with the filed document and is legible from a printout
    # alone — in the DEFAULT cells, which are the size a form's ruled line is.
    assert _appearance_lines(signed, PREPARER_FIELD_NAME) == [
        "Prepared by",
        "SGN-7K4M9PQR2VWX3YZ8",
    ]
    assert _appearance_lines(signed, APPROVER_FIELD_NAME) == [
        "Approved by",
        "SGN-7K4M9PQR2VWX3YZ8",
    ]
    # …and the officer's name, designation and date are on the form's own ruled
    # cells, filled from the same record.
    assert _form_values(signed) == {
        "Txt_Preparer_Name_1": "Ama Mensah",
        "Txt_Preparer_Title_1": "Chief Financial Officer",
        "Txt_Preparer_Date_1": "2026-07-31",
        "Txt_Approver_Name_1": "Ama Mensah",
        "Txt_Approver_Title_1": "Chief Financial Officer",
        "Txt_Approver_Date_1": "2026-07-31",
    }

    # ...and into the machine-readable signature dictionary, so a verifier never
    # has to read the drawn page. (A raw byte search would be the wrong assertion:
    # pyHanko serialises PDF strings with octal escapes, e.g. "-" as \055.)
    for sig in regular:
        assert "SGN-7K4M9PQR2VWX3YZ8" in str(sig.sig_object["/Name"])


def test_preparer_signature_is_a_docmdp_certification(pki: SigningPki, prepared_pdf: bytes) -> None:
    signed = _sign_both(pki, prepared_pdf, lta=False)
    reader = PdfFileReader(io.BytesIO(signed))

    certification = read_certification_data(reader)
    assert certification is not None
    assert certification.permission == fields.MDPPerm.FILL_FORMS  # level 2
    # The certifying signature is the preparer's, identified by the signature
    # bytes the field holds (author_sig is the raw signature dictionary).
    certifying_fields = {
        name
        for name, value, _ in fields.enumerate_sig_fields(reader)
        if value is not None
        and _as_dict(value.get_object())["/Contents"]
        == _as_dict(certification.author_sig)["/Contents"]
    }
    assert certifying_fields == {PREPARER_FIELD_NAME}


def test_certification_must_be_the_first_signature(pki: SigningPki, prepared_pdf: bytes) -> None:
    approved_first = sign_as_approver(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("approver"),
        profile=pki.profile(lta=False),
    )
    with pytest.raises(PdfSigningError, match="must be the first signature"):
        sign_as_preparer(
            approved_first,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=pki.profile(lta=False),
        )


def test_signing_requires_a_prepared_field(pki: SigningPki, unsigned_pdf: bytes) -> None:
    with pytest.raises(PdfSigningError, match="no signature field"):
        sign_as_preparer(
            unsigned_pdf,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=pki.profile(lta=False),
        )


def test_a_field_cannot_be_signed_twice(pki: SigningPki, prepared_pdf: bytes) -> None:
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    with pytest.raises(PdfSigningError, match="already signed"):
        sign_as_preparer(
            certified,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=pki.profile(lta=False),
        )


# --- typed fields: name / designation / date --------------------------------
#
# The load-bearing question for this family is not "does it draw" but "does
# filling the approver's boxes after the preparer certified still leave the
# certification intact". Everything below is the executable answer.


def _typed_field_placements() -> tuple[FieldPlacement, ...]:
    """Both roles' four boxes, laid out the way a BSD3 attestation block asks."""
    return (
        _placed("preparer", ATTESTATION_PAGE_INDEX, (51, 400, 195, 435)),
        _placed("preparer", ATTESTATION_PAGE_INDEX, (51, 380, 195, 396), "name"),
        _placed("preparer", ATTESTATION_PAGE_INDEX, (51, 360, 195, 376), "title"),
        _placed("preparer", ATTESTATION_PAGE_INDEX, (51, 340, 130, 356), "date_signed"),
        _placed("preparer", ATTESTATION_PAGE_INDEX, (140, 340, 195, 356), "initials"),
        _placed("approver", ATTESTATION_PAGE_INDEX, (304, 400, 448, 435)),
        _placed("approver", ATTESTATION_PAGE_INDEX, (304, 380, 448, 396), "name"),
        _placed("approver", ATTESTATION_PAGE_INDEX, (304, 360, 448, 376), "title"),
        _placed("approver", ATTESTATION_PAGE_INDEX, (304, 340, 383, 356), "date_signed"),
        _placed("approver", ATTESTATION_PAGE_INDEX, (393, 340, 448, 356), "initials"),
    )


def _form_values(pdf: bytes) -> dict[str, str]:
    """Every text field's value, by field name."""
    reader = PdfFileReader(io.BytesIO(pdf))
    listed = _as_dict(reader.root["/AcroForm"])["/Fields"]
    return {
        str(_as_dict(ref.get_object())["/T"]): str(_as_dict(ref.get_object()).get("/V", ""))
        for ref in listed
        if _as_dict(ref.get_object()).get("/FT") == "/Tx"
    }


@pytest.fixture(scope="module")
def typed_field_pdf(unsigned_pdf: bytes) -> bytes:
    return prepare_signature_fields(unsigned_pdf, placements=_typed_field_placements())


def test_typed_fields_are_created_empty_on_the_placed_boxes(typed_field_pdf: bytes) -> None:
    values = _form_values(typed_field_pdf)
    assert set(values) == {
        f"Txt_{role}_{kind}_1"
        for role in ("Preparer", "Approver")
        for kind in ("Name", "Title", "Date", "Initials")
    }
    assert set(values.values()) == {""}  # created, never pre-filled


def _text_field_widgets(pdf: bytes) -> dict[str, generic.DictionaryObject]:
    reader = PdfFileReader(io.BytesIO(pdf))
    listed = _as_dict(reader.root["/AcroForm"])["/Fields"]
    return {
        str(_as_dict(ref.get_object())["/T"]): _as_dict(ref.get_object())
        for ref in listed
        if _as_dict(ref.get_object()).get("/FT") == "/Tx"
    }


def test_a_filled_text_field_paints_no_background_and_no_border(
    pki: SigningPki, typed_field_pdf: bytes
) -> None:
    """Defect 4, as an executable statement.

    A filed regulatory return that shows grey boxes behind the officer's name
    reads as a half-finished web form rather than as a document. Three things
    have to hold together, so all three are asserted: the widget declares an
    EMPTY ``/BG`` and ``/BC`` (the explicit "paint neither", as opposed to an
    absent ``/MK`` that leaves it to the reader), it declares a zero-width
    border, and it is ReadOnly — which is what stops a viewer painting its
    field-highlight tint over a value nobody may edit anyway.
    """
    signed = sign_as_approver(
        sign_as_preparer(
            typed_field_pdf,
            signer=pki.signer,
            appearance=_appearance("preparer"),
            profile=pki.profile(lta=False),
        ),
        signer=pki.signer,
        appearance=_approver_appearance(),
        profile=pki.profile(lta=False),
    )
    widgets = _text_field_widgets(signed)
    assert widgets, "no text fields on the signed document"
    for name, widget in widgets.items():
        appearance_characteristics = _as_dict(widget["/MK"])
        assert list(appearance_characteristics["/BG"]) == [], f"{name} declares a background"
        assert list(appearance_characteristics["/BC"]) == [], f"{name} declares a border colour"
        assert int(_as_dict(widget["/BS"])["/W"]) == 0, f"{name} declares a border width"
        assert int(widget["/Ff"]) & 1, f"{name} is not ReadOnly"
    # …and the drawn appearance is the value alone: no fill operator, no rect.
    for name in widgets:
        stream = _filled_appearance(signed, name)
        assert b" re" not in stream and b" f" not in stream.replace(b"/Tf", b""), (
            f"{name}'s appearance paints a shape: {stream!r}"
        )


def _filled_appearance(pdf: bytes, field_name: str) -> bytes:
    normal = _as_dict(_text_field_widgets(pdf)[field_name]["/AP"])["/N"].get_object()
    assert isinstance(normal, generic.StreamObject)
    return normal.data


def _approver_appearance() -> SignatureAppearance:
    return SignatureAppearance(
        role_label=label_for_role("approver"),
        signer_name="Kofi Owusu",
        officer_title="Managing Director",
        signer_id="SGN-2QR8T5VW9XYZ4B6C",
        signed_at=NOW + timedelta(days=1),
    )


def test_filling_the_approver_fields_after_certification_keeps_docmdp_intact(
    pki: SigningPki, typed_field_pdf: bytes
) -> None:
    """THE constraint this feature had to be built around.

    The preparer certifies at ``MDPPerm.FILL_FORMS``. The approver's name,
    designation and date are only known afterwards, so they are FILLED into
    fields that already existed rather than drawn onto the page — and this
    asserts, against pyHanko's own diff analysis rather than by reasoning, that
    doing so leaves the certification valid and the modification classified no
    higher than form filling.
    """
    certified = sign_as_preparer(
        typed_field_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    signed = sign_as_approver(
        certified,
        signer=pki.signer,
        appearance=_approver_appearance(),
        profile=pki.profile(lta=False),
    )

    reader = PdfFileReader(io.BytesIO(signed))
    preparer, approver = (
        validate_pdf_signature(
            sig,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        for sig in reader.embedded_regular_signatures
    )
    assert preparer.intact
    assert preparer.docmdp_ok
    assert preparer.modification_level == ModificationLevel.FORM_FILLING
    assert preparer.modification_level <= ModificationLevel.FORM_FILLING
    assert preparer.coverage == SignatureCoverageLevel.ENTIRE_REVISION
    assert preparer.bottom_line
    assert approver.docmdp_ok
    assert approver.bottom_line

    # …and only the approver's own fields moved. The preparer's are inside the
    # revision their signature covers, so they are not in this set at all.
    assert isinstance(preparer.diff_result, DiffResult)
    assert preparer.diff_result.changed_form_fields == {
        APPROVER_FIELD_NAME,
        "Txt_Approver_Name_1",
        "Txt_Approver_Title_1",
        "Txt_Approver_Date_1",
        "Txt_Approver_Initials_1",
    }


def test_every_typed_value_is_derived_from_the_signature_record(
    pki: SigningPki, typed_field_pdf: bytes
) -> None:
    """What the form prints is what the record says — nothing else can reach it."""
    certified = sign_as_preparer(
        typed_field_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    signed = sign_as_approver(
        certified,
        signer=pki.signer,
        appearance=_approver_appearance(),
        profile=pki.profile(lta=False),
    )
    assert _form_values(signed) == {
        "Txt_Preparer_Name_1": "Ama Mensah",
        "Txt_Preparer_Title_1": "Chief Financial Officer",
        "Txt_Preparer_Date_1": "2026-07-31",
        "Txt_Preparer_Initials_1": "A.M.",
        "Txt_Approver_Name_1": "Kofi Owusu",
        "Txt_Approver_Title_1": "Managing Director",
        "Txt_Approver_Date_1": "2026-08-01",
        "Txt_Approver_Initials_1": "K.O.",
    }


def test_the_preparers_printed_identity_cannot_be_rewritten_afterwards(
    pki: SigningPki, typed_field_pdf: bytes
) -> None:
    """The residual hole the preparer's ``/Lock`` closes.

    Filling a form field is exactly what the DocMDP level permits, so without a
    FieldMDP exclusion an appended revision could swap the name printed under the
    preparer's signature and still report ``docmdp_ok``. Forging that revision
    here is the only way to know the lock is doing its job.
    """
    certified = sign_as_preparer(
        typed_field_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    writer = IncrementalPdfFileWriter(io.BytesIO(certified))
    listed = _as_dict(writer.prev.root["/AcroForm"])["/Fields"]
    for ref in listed:
        field = _as_dict(ref.get_object())
        if str(field.get("/T", "")) != "Txt_Preparer_Name_1":
            continue
        field[generic.pdf_name("/V")] = generic.TextStringObject("Someone Else")
        writer.mark_update(ref)
    out = io.BytesIO()
    writer.write(out)

    status = validate_pdf_signature(
        PdfFileReader(out).embedded_regular_signatures[0],
        signer_validation_context=pki.validation_context,
        ts_validation_context=pki.validation_context,
        diff_policy=attestation_diff_policy(),
    )
    assert status.intact  # the bytes the preparer signed are untouched…
    assert status.docmdp_ok is False  # …and the document is still convicted
    assert isinstance(status.diff_result, SuspiciousModification)
    assert "Txt_Preparer_Name_1" in str(status.diff_result)


def test_a_document_prepared_without_typed_fields_still_signs(
    pki: SigningPki, unsigned_pdf: bytes
) -> None:
    """Filling is a no-op where nothing was placed.

    Returns prepared before typed fields existed — and any layout an institution
    saved with signature boxes only — carry no text fields at all, and the fill
    step must not go looking for boxes that are not there.
    """
    signature_only = prepare_signature_fields(unsigned_pdf, placements=_pair())
    signed = _sign_both(pki, signature_only, lta=False)
    assert _form_values(signed) == {}


# --- tamper detection -------------------------------------------------------


def _edit_a_figure(pdf: bytes, *, page_index: int) -> tuple[bytes, int]:
    """Forge an incremental update that changes a printed figure.

    The realistic attack, not a crude byte flip: the preparer's signed bytes are
    left alone (so that signature stays *intact*) and the page is repointed at an
    edited copy of its content stream in a new revision. Detection therefore has
    to come from the DocMDP policy and diff analysis, which is exactly the claim
    §3.2 makes. Returns the forged file and the object number of the page whose
    content was replaced.
    """
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf))
    page_ref = writer.find_page_for_modification(page_index)[0]
    page = _as_dict(page_ref.get_object())
    contents = page.raw_get("/Contents").get_object()
    assert isinstance(contents, generic.StreamObject)
    original = contents.data
    edited = original.replace(FIGURE_TEXT, TAMPERED_FIGURE_TEXT)
    assert edited != original, "the figures page did not contain the expected figure"
    page[generic.pdf_name("/Contents")] = writer.add_object(
        generic.StreamObject(stream_data=edited)
    )
    writer.mark_update(page_ref)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), page_ref.idnum


def test_tamper_between_signatures_is_detected(pki: SigningPki, prepared_pdf: bytes) -> None:
    """THE test: a figure edited between the two signatures cannot pass.

    Everything about the design rests on this — the DocMDP level, the field
    lock, the incremental updates — so the assertions are deliberately exact:
    the preparer's signature is still cryptographically intact and still covers
    its whole revision, yet the modification introduced afterwards is classified
    ABOVE ``FORM_FILLING``, the DocMDP check fails, and the offending object is
    named.
    """
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    forged, tampered_page_idnum = _edit_a_figure(certified, page_index=FIGURES_PAGE_INDEX)
    signed = sign_as_approver(
        forged,
        signer=pki.signer,
        appearance=_appearance("approver"),
        profile=pki.profile(lta=False),
    )
    # The forgery really is in the filed bytes — detection is not luck.
    assert TAMPERED_FIGURE_TEXT in signed

    reader = PdfFileReader(io.BytesIO(signed))
    preparer, approver = (
        validate_pdf_signature(
            sig,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        for sig in reader.embedded_regular_signatures
    )

    # The signature itself is untouched: this is not "the bytes broke".
    assert preparer.intact
    assert preparer.valid
    assert preparer.coverage == SignatureCoverageLevel.ENTIRE_REVISION

    # ...and yet the document is convicted.
    assert preparer.modification_level == ModificationLevel.OTHER
    assert preparer.modification_level > ModificationLevel.FORM_FILLING
    assert preparer.docmdp_ok is False
    assert preparer.bottom_line is False

    # The specific offending object is reported, not just "something changed".
    assert isinstance(preparer.diff_result, SuspiciousModification)
    assert f"idnum={tampered_page_idnum}" in str(preparer.diff_result)

    # The approver's own signature is still intact over its own revision: the
    # tamper is attributable to what happened between the two, which is the
    # point of signing incrementally rather than re-signing a fresh render.
    assert approver.intact
    assert approver.coverage == SignatureCoverageLevel.ENTIRE_FILE


def test_editing_the_certified_bytes_breaks_the_signature(
    pki: SigningPki, prepared_pdf: bytes
) -> None:
    """The cruder attack, for completeness: an in-place byte edit inside the
    signed range fails the integrity check outright."""
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile(lta=False),
    )
    # Overwrite one byte of the figures page's encoded content stream in place,
    # so the file layout and every byte range stay exactly as signed.
    encoded = _page_content_stream(certified, FIGURES_PAGE_INDEX).encoded_data
    assert encoded in certified
    mutated = bytes([encoded[0] ^ 0xFF]) + encoded[1:]
    forged = certified.replace(encoded, mutated, 1)
    assert len(forged) == len(certified)  # byte ranges unchanged, content is not

    reader = PdfFileReader(io.BytesIO(forged))
    status = validate_pdf_signature(
        reader.embedded_regular_signatures[0],
        signer_validation_context=pki.validation_context,
        ts_validation_context=pki.validation_context,
    )
    assert status.intact is False


# --- the bridge from a custody key to a pyHanko Signer ----------------------

#: A sealed key needs a vault key; the suite must never touch a real one.
_BRIDGE_VAULT_KEY = "test-vault-master-key-not-for-production-0003"
_BRIDGE_SIGNER_ID = "SGN-7K4M9PQR2VWX3YZ8"


@dataclass(frozen=True)
class EnrolledKey:
    """What ``signer_keys`` stores for one officer, minus the database row."""

    backend: SoftwareRawSigner
    key_ref: str
    certificate: x509.Certificate

    @property
    def pem(self) -> str:
        return self.certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _enrol_soft_key(tmp_path: Path, *, algorithm: str | None = None) -> EnrolledKey:
    backend = SoftwareRawSigner(
        key_dir=tmp_path / "signing_keys", master_key_material=_BRIDGE_VAULT_KEY
    )
    key_ref = "sgn_bridge_0001"
    kwargs = {"algorithm": algorithm} if algorithm else {}
    backend.generate_key(key_ref=key_ref, **kwargs)
    valid_from, valid_to = default_validity()
    certificate = backend.self_sign_certificate(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=_BRIDGE_SIGNER_ID,
            display_name="Ama Mensah",
            organization_name="Sample Bank",
        ),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return EnrolledKey(backend=backend, key_ref=key_ref, certificate=certificate)


def _untimestamped_appearance() -> SignatureAppearance:
    return SignatureAppearance(
        role_label=label_for_role("preparer"),
        signer_name="Ama Mensah",
        officer_title="Chief Financial Officer",
        signer_id=_BRIDGE_SIGNER_ID,
        signed_at=NOW,
        timestamped=False,
    )


def _self_anchored(certificate: x509.Certificate) -> ValidationContext:
    return ValidationContext(
        trust_roots=[_asn1_cert(certificate)], allow_fetching=False, revocation_mode="soft-fail"
    )


@pytest.mark.parametrize("algorithm", [None, RSA_2048_PSS_SHA256], ids=["ecdsa_p256", "rsa_pss"])
def test_the_bridge_signs_the_return_under_the_enrolled_certificate(
    tmp_path: Path, prepared_pdf: bytes, algorithm: str | None
) -> None:
    """A custody key, through the bridge, produces a valid PAdES signature.

    Both enrolled algorithms are covered because the bridge is the only place
    that has to translate the recorded ``signer_keys.algorithm`` into pyHanko's
    signature mechanism — an RSA key silently signed as PKCS#1 v1.5 where the
    record says PSS would make the recorded method a lie.
    """
    enrolled = _enrol_soft_key(tmp_path, algorithm=algorithm)
    with get_pdf_signer(
        key_ref=enrolled.key_ref,
        certificate_pem=enrolled.pem,
        algorithm=algorithm or "ecdsa_p256_sha256",
        signer=enrolled.backend,
    ) as bridged:
        signed = sign_as_preparer(
            prepared_pdf,
            signer=bridged,
            # No TSA in this test, so the page must not claim one — the same
            # agreement the orchestrator maintains where no authority is
            # configured.
            appearance=_untimestamped_appearance(),
            profile=PadesProfile(
                timestamper=None,
                validation_context=None,
                use_pades_lta=False,
                embed_validation_info=False,
            ),
        )

    embedded = PdfFileReader(io.BytesIO(signed)).embedded_regular_signatures
    assert [sig.field_name for sig in embedded] == [PREPARER_FIELD_NAME]
    status = validate_pdf_signature(
        embedded[0], signer_validation_context=_self_anchored(enrolled.certificate)
    )
    assert status.intact
    assert status.valid
    assert status.trusted

    # The document is signed under the certificate the key register enrolled —
    # not some other copy — so the PDF and the detached attestation are two
    # artefacts of one act by one identifiable person.
    assert status.signing_cert.dump() == _asn1_cert(enrolled.certificate).dump()
    assert _BRIDGE_SIGNER_ID in status.signing_cert.subject.human_friendly

    # The original archived bytes survive as a prefix: the bridge changes who
    # signs, never how (incremental updates only).
    assert signed.startswith(prepared_pdf)


def test_the_bridge_never_hands_back_key_material(tmp_path: Path) -> None:
    """The Signer is opaque: the caller gets a signing capability, not a key.

    This is the property that lets one call site serve a soft key and an HSM.
    ``SimpleSigner`` does hold the key by construction — the software backend
    is dev-only for exactly that reason — but nothing the ceremony touches
    exposes it, so no caller can grow a dependency on readable key material.
    """
    enrolled = _enrol_soft_key(tmp_path)
    with get_pdf_signer(
        key_ref=enrolled.key_ref, certificate_pem=enrolled.pem, signer=enrolled.backend
    ) as bridged:
        assert isinstance(bridged, signers.Signer)
        assert not hasattr(bridged, "private_key")
        assert not hasattr(bridged, "key_material")


def test_a_backend_that_cannot_bridge_fails_loudly(tmp_path: Path) -> None:
    """No ``None``, no unsigned fallback — the KMS backend is a documented stub.

    The alternative would be a certification that quietly files an unsigned
    return, so the refusal is typed and names the way out.
    """
    enrolled = _enrol_soft_key(tmp_path)
    kms = KmsRawSigner(provider="aws", key_id="arn:test")
    with (
        pytest.raises(PdfSignerUnavailable, match="not implemented") as raised,
        get_pdf_signer(key_ref=enrolled.key_ref, certificate_pem=enrolled.pem, signer=kms),
    ):
        pytest.fail("the KMS stub must not yield a Signer")
    assert "SIGNING_BACKEND" in str(raised.value)


def test_the_bridge_refuses_a_certificate_it_cannot_parse(tmp_path: Path) -> None:
    enrolled = _enrol_soft_key(tmp_path)
    with (
        pytest.raises(PdfSignerUnavailable, match="could not be parsed"),
        get_pdf_signer(
            key_ref=enrolled.key_ref,
            certificate_pem="-----BEGIN CERTIFICATE-----\nnot a certificate\n",
            signer=enrolled.backend,
        ),
    ):
        pytest.fail("an unparseable certificate must not yield a Signer")


def test_a_text_field_placed_before_a_signature_still_prepares(
    unsigned_pdf: bytes,
) -> None:
    """Field creation order must not depend on the order fields were dragged.

    The return PDF is rendered by reportlab and carries no AcroForm; only
    ``append_signature_field`` creates one, and a text field needs it to exist.
    Preparation used to walk the placements in the order they arrived, so a
    signer who dropped a Name onto the form before a Signature — an ordinary
    thing to do — had the whole certification refused with "This PDF has no
    AcroForm". Reported from the live workspace on 2026-07-25.
    """
    placements = (
        # Deliberately text-first for both roles, which is what broke it.
        _placed("preparer", 1, (60, 380, 200, 400), field_type="name"),
        _placed("preparer", 1, (60, 330, 200, 372)),
        _placed("approver", 1, (300, 380, 440, 400), field_type="date_signed"),
        _placed("approver", 1, (300, 330, 440, 372)),
    )

    prepared = prepare_signature_fields(unsigned_pdf, placements=placements)

    reader = PdfFileReader(io.BytesIO(prepared))
    names = {name for name, _, _ in fields.enumerate_sig_fields(reader)}
    assert {PREPARER_FIELD_NAME, APPROVER_FIELD_NAME} <= names
    # The original bytes must still be a literal prefix — archival relies on it.
    assert prepared.startswith(unsigned_pdf)
