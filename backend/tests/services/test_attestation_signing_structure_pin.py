"""The structural pin: what a two-signer return's signing structure IS, today.

This file was written and run against **unmodified** signing code, before the
Board-signature redesign (P3-DESIGN §1, D-007, D-043) touched a line of it. That
order is the whole point. The redesign's claim is that "every existing two-signer
return keeps byte-identical signing behaviour"; a test written afterwards could
only ever pin whatever the refactor happened to produce. Written first, the
literals below are evidence rather than a restatement.

What is pinned, and why each part:

* :data:`EXPECTED_DEFAULT_PLACEMENTS` — the eight default boxes, element for
  element. ``DEFAULT_PLACEMENTS`` becomes a call to a parameterised
  ``default_placements()`` in the redesign, and the one thing that may not
  change is what it returns for a standard return.
* :data:`EXPECTED_PREPARED_STRUCTURE` — every AcroForm field in the prepared
  document, in creation order, with its name, type, rectangle, flags, page and
  ``/Lock``. The lock chain is the part the redesign rewrites (``is_approver``
  becomes a rank comparison over a signing order), so the ``/Exclude`` list's
  exact **contents and order** are recorded, not merely its set.
* :data:`EXPECTED_SIGNED_STRUCTURE` — per signature: which field, whether it
  certifies, the DocMDP permission, the FieldMDP transform pyHanko reads back
  out of the signature dictionary, and the coverage and modification level the
  validator assigns. That is the layer a wrong lock breaks, and it breaks
  silently: a document with a bad chain still opens.

Bytes are deliberately NOT hashed. ``IncrementalPdfFileWriter`` writes a random
second ``/ID`` per update and ECDSA signatures are randomised, so a whole-file
digest would be a flaky test rather than a strict one (P3-DESIGN §1.2.5). The
prepared revision's bytes ARE compared for equality under a patched
``os.urandom``, which is the strongest byte-level statement this layer supports;
:func:`test_prepared_bytes_are_identical_when_the_signing_order_is_passed_explicitly`
extends it to the redesign's new argument.

The PKI, the rendered return and the appearances are built the same way as
``test_attestation_pdf_signing.py`` — a throwaway CA, a real ``render_pdf``
output, real RFC 3161 tokens from a local dummy authority. Nothing is mocked.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.services.attestation.pdf_signing import (
    APPROVER_FIELD_NAME,
    ATTESTATION_PAGE_INDEX,
    DEFAULT_PLACEMENTS,
    PREPARER_FIELD_NAME,
    PadesProfile,
    SignatureAppearance,
    label_for_role,
    prepare_signature_fields,
    sign_as_approver,
    sign_as_preparer,
)
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

NOW = datetime(2026, 7, 31, 14, 2, 11, tzinfo=UTC)
CLOCK_NOW = datetime.now(UTC)
CERT_NOT_BEFORE = CLOCK_NOW - timedelta(days=365)
CERT_NOT_AFTER = CLOCK_NOW + timedelta(days=365)


# --- what is pinned ---------------------------------------------------------

#: ``DEFAULT_PLACEMENTS``, spelled out. Four labelled cells per role, the
#: preparer's block above the approver's, on the attestation page.
EXPECTED_DEFAULT_PLACEMENTS: tuple[tuple[str, int, tuple[int, int, int, int], str, int], ...] = (
    ("preparer", 1, (51, 672, 171, 688), "name", 1),
    ("preparer", 1, (179, 672, 299, 688), "title", 1),
    ("preparer", 1, (307, 672, 457, 718), "signature", 1),
    ("preparer", 1, (465, 672, 536, 688), "date_signed", 1),
    ("approver", 1, (51, 580, 171, 596), "name", 1),
    ("approver", 1, (179, 580, 299, 596), "title", 1),
    ("approver", 1, (307, 580, 457, 626), "signature", 1),
    ("approver", 1, (465, 580, 536, 596), "date_signed", 1),
)

#: Every AcroForm field of the prepared document, in ``/Fields`` order.
#:
#: Signature fields come first because ``prepare_signature_fields`` creates them
#: in a first pass (the reportlab output carries no AcroForm at all, and
#: ``append_signature_field`` is what builds one). The ``/Lock`` entries are the
#: load-bearing part: ``Sig_Approver`` seals everything once it is signed, and
#: ``Sig_Preparer`` seals everything EXCEPT the four fields the approver has yet
#: to fill — listed in placement order, which is the order the redesign's
#: rank-based ``later[...]`` comprehension must reproduce exactly.
EXPECTED_PREPARED_STRUCTURE: tuple[dict[str, Any], ...] = (
    {
        "name": "Sig_Preparer",
        "type": "/Sig",
        "rect": [307, 672, 457, 718],
        # Print | Locked: ``append_signature_field`` sets the Locked annotation
        # flag on a signature widget; the derived text fields carry Print alone.
        "flags": 132,
        "page_index": 1,
        "lock": {
            "action": "/Exclude",
            "fields": [
                "Txt_Approver_Name_1",
                "Txt_Approver_Title_1",
                "Sig_Approver",
                "Txt_Approver_Date_1",
            ],
        },
    },
    {
        "name": "Sig_Approver",
        "type": "/Sig",
        "rect": [307, 580, 457, 626],
        "flags": 132,
        "page_index": 1,
        "lock": {"action": "/All", "fields": []},
    },
    {
        "name": "Txt_Preparer_Name_1",
        "type": "/Tx",
        "rect": [51, 672, 171, 688],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
    {
        "name": "Txt_Preparer_Title_1",
        "type": "/Tx",
        "rect": [179, 672, 299, 688],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
    {
        "name": "Txt_Preparer_Date_1",
        "type": "/Tx",
        "rect": [465, 672, 536, 688],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
    {
        "name": "Txt_Approver_Name_1",
        "type": "/Tx",
        "rect": [51, 580, 171, 596],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
    {
        "name": "Txt_Approver_Title_1",
        "type": "/Tx",
        "rect": [179, 580, 299, 596],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
    {
        "name": "Txt_Approver_Date_1",
        "type": "/Tx",
        "rect": [465, 580, 536, 596],
        "flags": 4,
        "page_index": 1,
        "lock": None,
    },
)

#: The signature layer of a finished two-signer return (PAdES B-T, so the last
#: signature is the last revision and its coverage statement is unambiguous).
#:
#: ``field_mdp`` is read back out of the signature dictionary's ``/Reference``
#: array by pyHanko — i.e. it is what a VERIFIER sees, not what we installed.
#: That is the assertion worth making: the ``/Lock`` on the field and the
#: FieldMDP transform in the signature are two different objects, and only the
#: second one constrains later revisions.
EXPECTED_SIGNED_STRUCTURE: tuple[dict[str, Any], ...] = (
    {
        "field": "Sig_Preparer",
        "certifies": True,
        "docmdp": "MDPPerm.FILL_FORMS",
        "field_mdp": {
            "action": "FieldMDPAction.EXCLUDE",
            "fields": [
                "Txt_Approver_Name_1",
                "Txt_Approver_Title_1",
                "Sig_Approver",
                "Txt_Approver_Date_1",
            ],
        },
        "coverage": "SignatureCoverageLevel.ENTIRE_REVISION",
        "modification_level": "ModificationLevel.FORM_FILLING",
        "changed_form_fields": [
            "Sig_Approver",
            "Txt_Approver_Date_1",
            "Txt_Approver_Name_1",
            "Txt_Approver_Title_1",
        ],
    },
    {
        "field": "Sig_Approver",
        "certifies": False,
        "docmdp": None,
        "field_mdp": {"action": "FieldMDPAction.ALL", "fields": []},
        "coverage": "SignatureCoverageLevel.ENTIRE_FILE",
        "modification_level": "ModificationLevel.NONE",
        "changed_form_fields": [],
    },
)


# --- test PKI ---------------------------------------------------------------


@dataclass(frozen=True)
class _Pki:
    signer: signers.SimpleSigner
    timestamper: DummyTimeStamper
    validation_context: ValidationContext

    @property
    def profile(self) -> PadesProfile:
        """B-T: a real timestamp token, no appended LTV revisions."""
        return PadesProfile(
            timestamper=self.timestamper,
            validation_context=None,
            use_pades_lta=False,
            embed_validation_info=False,
        )


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=not ca,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
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
        .subject_name(_name(common_name))
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
def pki() -> _Pki:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = _name("AequorOS Structure Pin CA")
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(CERT_NOT_BEFORE)
        .not_valid_after(CERT_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_asn1 = _asn1_cert(ca_cert)

    signer_key = ec.generate_private_key(ec.SECP256R1())
    signer_cert = _issue(signer_key.public_key(), "Ama Mensah", ca_key, ca_cert)
    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa_cert = _issue(
        tsa_key.public_key(), "AequorOS Pin TSA", ca_key, ca_cert, timestamping=True
    )
    return _Pki(
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
        return_code="LCR-NSFR",
        title="Test Return",
        fidelity="CONFIRMED",
        source_citation="BoG BSD/2026/test",
        sections=(layout,),
    )
    return RenderedReturn(
        template=template,
        metadata_pairs=(
            ("Return", "Test Return"),
            ("Institution", "Sample Bank"),
            ("Reporting date", "2026-06-30"),
        ),
        sections=(
            RenderedSection(
                layout=layout,
                title="Summary",
                rows=(
                    RenderedRow(
                        cells=(
                            RenderedCell("text", "1.1"),
                            RenderedCell("text", "Total assets"),
                            RenderedCell("ghs", Decimal("1234000")),
                        )
                    ),
                ),
                total_row=None,
            ),
        ),
        provenance_runs=(("liquidity", "run-1", "hash-1", "engine-1"),),
        provenance_lines=("AequorOS · Test Return · 2026-06-30",),
        attestation_lines=STANDARD_ATTESTATION_LINES,
    )


@pytest.fixture(scope="module")
def unsigned_pdf() -> bytes:
    return render_pdf(_rendered_return(), sandbox_watermark=False)


@pytest.fixture(scope="module")
def prepared_pdf(unsigned_pdf: bytes) -> bytes:
    return prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)


def _appearance(role: str) -> SignatureAppearance:
    return SignatureAppearance(
        role_label=label_for_role(role),
        signer_name="Ama Mensah",
        officer_title="Chief Financial Officer",
        signer_id="SGN-7K4M9PQR2VWX3YZ8",
        signed_at=NOW,
    )


@pytest.fixture(scope="module")
def signed_pdf(pki: _Pki, prepared_pdf: bytes) -> bytes:
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.signer,
        appearance=_appearance("preparer"),
        profile=pki.profile,
    )
    return sign_as_approver(
        certified,
        signer=pki.signer,
        appearance=_appearance("approver"),
        profile=pki.profile,
    )


# --- reading the structure back ---------------------------------------------


def _as_dict(obj: object) -> generic.DictionaryObject:
    assert isinstance(obj, generic.DictionaryObject)
    return obj


def _lock_of(field: generic.DictionaryObject) -> dict[str, Any] | None:
    raw = field.get("/Lock")
    if raw is None:
        return None
    lock = _as_dict(raw.get_object())
    assert str(lock["/Type"]) == "/SigFieldLock"
    return {
        "action": str(lock["/Action"]),
        "fields": [str(name) for name in lock.get("/Fields", [])],
    }


def _page_index_of(reader: PdfFileReader, field: generic.DictionaryObject) -> int | None:
    """Which page the field's widget is annotated on."""
    page_ref = field.get("/P")
    if page_ref is None:
        return None
    target = page_ref.get_object()
    kids = reader.root["/Pages"]["/Kids"]
    for index, kid in enumerate(kids):
        if kid.get_object() is target:
            return index
    return None


def _acroform_structure(pdf_bytes: bytes) -> tuple[dict[str, Any], ...]:
    """Every AcroForm field, in ``/Fields`` order, as plain JSON-able data."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    listed = reader.root["/AcroForm"]["/Fields"]
    structure: list[dict[str, Any]] = []
    for ref in listed:
        field = _as_dict(ref.get_object())
        structure.append(
            {
                "name": str(field["/T"]),
                "type": str(field["/FT"]),
                "rect": [int(coord) for coord in field["/Rect"]],
                "flags": int(field.get("/F", 0)),
                "page_index": _page_index_of(reader, field),
                "lock": _lock_of(field),
            }
        )
    return tuple(structure)


def _signature_structure(pdf_bytes: bytes, pki: _Pki) -> tuple[dict[str, Any], ...]:
    """Per signature: what it certifies, what it locks, and how it validates."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    certifying = reader.embedded_regular_signatures[0].sig_object.get("/Contents")
    out: list[dict[str, Any]] = []
    for embedded in reader.embedded_regular_signatures:
        status = validate_pdf_signature(
            embedded,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        # A DocMDP transform only exists on a certification signature; pyHanko
        # returns None for an approval one.
        docmdp = embedded.docmdp_level
        field_mdp = embedded.fieldmdp
        out.append(
            {
                "field": embedded.field_name,
                "certifies": embedded.sig_object.get("/Contents") is certifying
                and docmdp is not None,
                "docmdp": None if docmdp is None else str(docmdp),
                "field_mdp": (
                    None
                    if field_mdp is None
                    else {
                        "action": str(field_mdp.action),
                        "fields": list(field_mdp.fields or []),
                    }
                ),
                "coverage": str(status.coverage),
                "modification_level": str(status.modification_level),
                "changed_form_fields": sorted(
                    getattr(status.diff_result, "changed_form_fields", set()) or set()
                ),
            }
        )
    return tuple(out)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=False, separators=(",", ":"))


# --- the pins ---------------------------------------------------------------


def test_default_placements_are_exactly_the_eight_standard_cells() -> None:
    """``DEFAULT_PLACEMENTS``, element for element.

    The redesign turns this constant into ``default_placements()`` with a
    signing-order argument. What it returns for a standard return is the one
    thing that may not move — a shifted default would re-place the signature on
    every return nobody has laid out by hand.
    """
    assert (
        tuple(
            (
                placement.signing_role,
                placement.page_index,
                placement.box,
                placement.field_type,
                placement.field_index,
            )
            for placement in DEFAULT_PLACEMENTS
        )
        == EXPECTED_DEFAULT_PLACEMENTS
    )
    assert {placement.signing_role for placement in DEFAULT_PLACEMENTS} == {
        "preparer",
        "approver",
    }
    assert all(
        placement.page_index == ATTESTATION_PAGE_INDEX for placement in DEFAULT_PLACEMENTS
    )


def test_prepared_acroform_structure_is_unchanged(prepared_pdf: bytes) -> None:
    """Field creation order, geometry, flags and the whole lock chain."""
    structure = _acroform_structure(prepared_pdf)
    assert _canonical(structure) == _canonical(EXPECTED_PREPARED_STRUCTURE)

    # Spelled out again as prose, because the digest above says "equal to a
    # literal" and this says what the literal MEANS. The redesign replaces the
    # `is_approver` test with a rank comparison over a signing order; for the
    # standard order these two statements are what it has to keep producing.
    by_name = {entry["name"]: entry for entry in structure}
    assert by_name[APPROVER_FIELD_NAME]["lock"] == {"action": "/All", "fields": []}
    assert by_name[PREPARER_FIELD_NAME]["lock"] == {
        "action": "/Exclude",
        "fields": [
            placement.field_name
            for placement in DEFAULT_PLACEMENTS
            if placement.signing_role == "approver"
        ],
    }


def test_signature_layer_structure_is_unchanged(pki: _Pki, signed_pdf: bytes) -> None:
    """DocMDP, FieldMDP, coverage and modification level, as a verifier reads them."""
    assert _canonical(_signature_structure(signed_pdf, pki)) == _canonical(
        EXPECTED_SIGNED_STRUCTURE
    )


def test_prepared_bytes_are_reproducible_under_a_fixed_random_source(
    monkeypatch: pytest.MonkeyPatch, unsigned_pdf: bytes
) -> None:
    """Two preparations of one artifact differ only in the document ``/ID``.

    ``IncrementalPdfFileWriter`` mints a random second ``/ID`` element per
    update, which is why no test in this repository hashes a prepared or signed
    file. Pinning the random source removes that one source of variation and
    leaves a genuine byte comparison — the strongest form the "unchanged"
    claim can take at this layer.
    """
    # Patched by dotted path: ``os`` is an implementation detail of that module
    # rather than part of its public surface, and importing it as one would be a
    # claim this test has no business making.
    monkeypatch.setattr(
        "pyhanko.pdf_utils.incremental_writer.os.urandom", lambda size: b"\0" * size
    )
    first = prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)
    second = prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)
    assert first == second
    assert first.startswith(unsigned_pdf)


def test_prepared_bytes_are_identical_when_the_signing_order_is_passed_explicitly(
    monkeypatch: pytest.MonkeyPatch, unsigned_pdf: bytes
) -> None:
    """The redesign's new argument, at its default, changes nothing at all.

    ``prepare_signature_fields`` gained a ``signing_order`` parameter defaulting
    to ``("preparer", "approver")``. Passing that value explicitly must produce
    the same file as omitting it, byte for byte — which is the mechanical form
    of "the standard layout is untouched".

    This was the one assertion in the file that could not run at step 0, since
    the parameter did not exist yet; it was carried as a skip guarded on the
    function signature until the redesign landed, and the guard was deleted the
    moment it went green rather than left behind as a permanent escape hatch.
    """
    # Patched by dotted path: ``os`` is an implementation detail of that module
    # rather than part of its public surface, and importing it as one would be a
    # claim this test has no business making.
    monkeypatch.setattr(
        "pyhanko.pdf_utils.incremental_writer.os.urandom", lambda size: b"\0" * size
    )
    implicit = prepare_signature_fields(unsigned_pdf, placements=DEFAULT_PLACEMENTS)
    explicit = prepare_signature_fields(
        unsigned_pdf,
        placements=DEFAULT_PLACEMENTS,
        signing_order=("preparer", "approver"),
    )
    assert implicit == explicit


def test_only_the_two_standard_signature_fields_exist(prepared_pdf: bytes) -> None:
    """No third field appears on a standard return, whatever the vocabulary knows.

    ``ROLE_FIELD_NAMES`` gains a ``board`` entry in the redesign — it becomes a
    vocabulary rather than the required-role set. This is the assertion that the
    widened vocabulary does not leak a field onto a return whose policy never
    asked for one.
    """
    reader = PdfFileReader(io.BytesIO(prepared_pdf))
    assert {name for name, _, _ in fields.enumerate_sig_fields(reader)} == {
        PREPARER_FIELD_NAME,
        APPROVER_FIELD_NAME,
    }
