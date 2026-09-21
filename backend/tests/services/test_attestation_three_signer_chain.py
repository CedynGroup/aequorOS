"""The three-signer chain: preparer → approver → Board, proven by execution.

This is the executable form of D-033. P3-DESIGN §12 classed the lock chain
BLOCKING-class "until spiked", because it was derived by reading pyHanko 0.35.2
rather than by running it, and the specific doubt was whether pyHanko would
accept a FieldMDP ``/Exclude`` lock on an **approval** signature (today only the
certifying preparer carries one). It does. This file keeps that answer executable
instead of leaving it in a report.

Three properties, and each would be a silent defect if it regressed — a document
with a broken lock chain still opens, still shows three signature stamps, and
only fails when an examiner's verifier reports ``ILLEGAL_MODIFICATIONS``:

1. :func:`test_the_three_signer_chain_validates` — the ceremony end to end, both
   PAdES profiles, every signature ``intact``/``valid``/``trusted``/``docmdp_ok``.
2. :func:`test_the_lock_chain_moves_the_all_lock_to_the_final_signer` — the
   ``/All`` lock is on ``Sig_Board`` and the approver carries ``/Exclude`` over
   the Board's fields. Leaving ``/All`` on the approver is the naive design, and
   it convicts BOTH earlier signatures the moment the Board fills a field.
3. :func:`test_signing_out_of_order_convicts_the_board_signature` — the reason
   ordering is a precondition of validity rather than a UX preference. The bytes
   layer refuses it first (:func:`test_the_board_cannot_sign_before_the_approver`);
   this test reaches past that refusal to show what it is protecting.

The PKI is a throwaway CA with a distinct certificate per officer, the document
is a real ``render_pdf`` output, and the timestamps are real RFC 3161 tokens from
a local dummy authority. Nothing cryptographic is mocked.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from pyhanko.sign.diff_analysis import DiffResult
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko.sign.validation import (
    SignatureCoverageLevel,
    read_certification_data,
    validate_pdf_signature,
)
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.services.attestation import layouts
from app.services.attestation.pdf_signing import (
    APPROVER_FIELD_NAME,
    ATTESTATION_PAGE_INDEX,
    BOARD_FIELD_NAME,
    DEFAULT_PLACEMENTS,
    PREPARER_FIELD_NAME,
    PadesProfile,
    PdfSigningError,
    SignatureAppearance,
    default_placements,
    label_for_role,
    prepare_signature_fields,
    sign_as_approver,
    sign_as_board,
    sign_as_preparer,
    signing_rule_y,
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

ICAAP_ORDER: tuple[str, ...] = ("preparer", "approver", "board")

#: One officer per role. Distinct certificates because the whole point of a third
#: signature is that it comes from a different person; a single key would let a
#: broken maker-checker guard pass this file.
OFFICERS: dict[str, tuple[str, str, str]] = {
    "preparer": ("Ama Mensah", "Chief Financial Officer", "SGN-7K4M9PQR2VWX3YZ8"),
    "approver": ("Kofi Boateng", "Chief Risk Officer", "SGN-8L5N0QRS3WXY4Z09"),
    "board": ("Efua Asante", "Board Chair", "SGN-9M6P1RST4XYZ5A10"),
}


# --- test PKI ---------------------------------------------------------------


@dataclass(frozen=True)
class _Pki:
    by_role: dict[str, signers.SimpleSigner]
    timestamper: DummyTimeStamper
    validation_context: ValidationContext

    def profile(self, *, lta: bool) -> PadesProfile:
        return PadesProfile(
            timestamper=self.timestamper,
            validation_context=self.validation_context if lta else None,
            use_pades_lta=lta,
            embed_validation_info=lta,
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
    ca_name = _name("AequorOS Three-Signer Test CA")
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

    def officer(common_name: str) -> signers.SimpleSigner:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = _issue(key.public_key(), common_name, ca_key, ca_cert)
        return signers.SimpleSigner(
            signing_cert=_asn1_cert(cert),
            signing_key=_asn1_key(key),
            cert_registry=SimpleCertificateStore.from_certs([ca_asn1]),
        )

    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa_cert = _issue(
        tsa_key.public_key(), "AequorOS Test TSA", ca_key, ca_cert, timestamping=True
    )
    return _Pki(
        by_role={role: officer(OFFICERS[role][0]) for role in ICAAP_ORDER},
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


# --- the document -----------------------------------------------------------


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
def icaap_placements() -> tuple:
    return default_placements(ICAAP_ORDER, layout="icaap")


@pytest.fixture(scope="module")
def prepared_pdf(unsigned_pdf: bytes, icaap_placements: tuple) -> bytes:
    return prepare_signature_fields(
        unsigned_pdf, placements=icaap_placements, signing_order=ICAAP_ORDER
    )


def _appearance(role: str) -> SignatureAppearance:
    name, title, signer_id = OFFICERS[role]
    return SignatureAppearance(
        role_label=label_for_role(role),
        signer_name=name,
        officer_title=title,
        signer_id=signer_id,
        signed_at=NOW,
    )


def _sign_all_three(pki: _Pki, prepared: bytes, *, lta: bool) -> bytes:
    profile = pki.profile(lta=lta)
    payload = sign_as_preparer(
        prepared,
        signer=pki.by_role["preparer"],
        appearance=_appearance("preparer"),
        profile=profile,
    )
    payload = sign_as_approver(
        payload,
        signer=pki.by_role["approver"],
        appearance=_appearance("approver"),
        profile=profile,
    )
    return sign_as_board(
        payload,
        signer=pki.by_role["board"],
        appearance=_appearance("board"),
        profile=profile,
        prior_fields=(PREPARER_FIELD_NAME, APPROVER_FIELD_NAME),
    )


def _as_dict(obj: object) -> generic.DictionaryObject:
    assert isinstance(obj, generic.DictionaryObject)
    return obj


def _locks(pdf_bytes: bytes) -> dict[str, dict[str, object] | None]:
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    out: dict[str, dict[str, object] | None] = {}
    for name, _value, field_ref in fields.enumerate_sig_fields(reader):
        raw = _as_dict(field_ref.get_object()).get("/Lock")
        if raw is None:
            out[name] = None
            continue
        lock = _as_dict(raw.get_object())
        out[name] = {
            "action": str(lock["/Action"]),
            "fields": [str(entry) for entry in lock.get("/Fields", [])],
        }
    return out


def _form_values(pdf_bytes: bytes) -> dict[str, str]:
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    return {
        str(field["/T"]): str(field.get("/V", ""))
        for field in (
            _as_dict(ref.get_object()) for ref in reader.root["/AcroForm"]["/Fields"]
        )
        if str(field["/FT"]) == "/Tx"
    }


# --- the chain --------------------------------------------------------------


def test_the_lock_chain_moves_the_all_lock_to_the_final_signer(
    prepared_pdf: bytes, icaap_placements: tuple
) -> None:
    """Each signer seals itself and everything before it; only the last seals all.

    The naive design keeps today's rule — approver ``/All`` — and adds a third
    field. That convicts the approver's own signature (the Board's fill is a
    change under a lock the approver installed) AND the preparer's (whose
    ``/Exclude`` never named the Board's fields). Both are asserted by
    construction here: the ``/All`` is on the Board, and each earlier signer's
    exclusion names exactly the fields of the roles that follow it, in placement
    order.
    """
    locks = _locks(prepared_pdf)
    assert locks[BOARD_FIELD_NAME] == {"action": "/All", "fields": []}

    def fields_of(*roles: str) -> list[str]:
        return [
            placement.field_name
            for placement in icaap_placements
            if placement.signing_role in roles
        ]

    assert locks[PREPARER_FIELD_NAME] == {
        "action": "/Exclude",
        "fields": fields_of("approver", "board"),
    }
    assert locks[APPROVER_FIELD_NAME] == {
        "action": "/Exclude",
        "fields": fields_of("board"),
    }


@pytest.mark.parametrize("lta", [True, False], ids=["pades_b_lta", "pades_b_t"])
def test_the_three_signer_chain_validates(pki: _Pki, prepared_pdf: bytes, lta: bool) -> None:
    """D-033, executed: pyHanko validates all three signatures.

    The specific claim under test is that an ``/Exclude`` FieldMDP lock on an
    APPROVAL signature behaves the way ``fields.FieldMDPSpec.is_locked`` reads:
    pyHanko has only ever been exercised here with ``/Exclude`` on the certifying
    signature and ``/All`` on the approval one.
    """
    signed = _sign_all_three(pki, prepared_pdf, lta=lta)
    reader = PdfFileReader(io.BytesIO(signed))

    regular = reader.embedded_regular_signatures
    assert [sig.field_name for sig in regular] == [
        PREPARER_FIELD_NAME,
        APPROVER_FIELD_NAME,
        BOARD_FIELD_NAME,
    ]

    # Still exactly one certification, still the preparer's, still level 2: a
    # third signature must not change what the document declares about itself.
    certification = read_certification_data(reader)
    assert certification is not None
    assert certification.permission == fields.MDPPerm.FILL_FORMS

    statuses = {
        sig.field_name: validate_pdf_signature(
            sig,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        for sig in regular
    }
    for field_name, status in statuses.items():
        assert status.intact, f"{field_name}: {status.summary()}"
        assert status.valid, f"{field_name}: {status.summary()}"
        assert status.trusted, f"{field_name}: {status.summary()}"
        assert status.docmdp_ok, f"{field_name}: {status.summary()}"
        assert status.bottom_line, f"{field_name}: {status.summary()}"

    # Each signer's revision covers everything up to itself, and the only thing
    # that happened after it was the NEXT signer filling their own fields.
    def changed(field_name: str) -> set[str]:
        result = statuses[field_name].diff_result
        assert isinstance(result, DiffResult), f"{field_name}: {result}"
        return set(result.changed_form_fields)

    assert changed(PREPARER_FIELD_NAME) == {
        "Sig_Approver",
        "Txt_Approver_Name_1",
        "Txt_Approver_Title_1",
        "Txt_Approver_Date_1",
        "Sig_Board",
        "Txt_Board_Name_1",
        "Txt_Board_Title_1",
        "Txt_Board_Date_1",
    }
    assert changed(APPROVER_FIELD_NAME) == {
        "Sig_Board",
        "Txt_Board_Name_1",
        "Txt_Board_Title_1",
        "Txt_Board_Date_1",
    }
    assert changed(BOARD_FIELD_NAME) == set()
    if not lta:
        # B-T: the Board's signature is the last revision in the file.
        assert statuses[BOARD_FIELD_NAME].coverage == SignatureCoverageLevel.ENTIRE_FILE

    # Every officer's own name, designation and date, filled in their own
    # revision from their own signature record — including the Board's.
    assert _form_values(signed) == {
        "Txt_Preparer_Name_1": "Ama Mensah",
        "Txt_Preparer_Title_1": "Chief Financial Officer",
        "Txt_Preparer_Date_1": "2026-07-31",
        "Txt_Approver_Name_1": "Kofi Boateng",
        "Txt_Approver_Title_1": "Chief Risk Officer",
        "Txt_Approver_Date_1": "2026-07-31",
        "Txt_Board_Name_1": "Efua Asante",
        "Txt_Board_Title_1": "Board Chair",
        "Txt_Board_Date_1": "2026-07-31",
    }
    for sig in regular:
        role = {v: k for k, v in {r: f"Sig_{r.title()}" for r in ICAAP_ORDER}.items()}[
            sig.field_name
        ]
        assert OFFICERS[role][2] in str(sig.sig_object["/Name"])


def test_the_board_cannot_sign_before_the_approver(pki: _Pki, prepared_pdf: bytes) -> None:
    """Refusal layer 3 (§1.5): the bytes layer, under every other guard.

    Workflow and preview refuse first. This one holds even for a caller that
    reached ``sign_as_board`` directly — which is what a future ICAAP service
    calling the signing module wrongly would look like.
    """
    with pytest.raises(PdfSigningError, match="The Board signs last"):
        sign_as_board(
            prepared_pdf,
            signer=pki.by_role["board"],
            appearance=_appearance("board"),
            profile=pki.profile(lta=False),
            prior_fields=(PREPARER_FIELD_NAME, APPROVER_FIELD_NAME),
        )


def test_signing_out_of_order_convicts_the_board_signature(
    pki: _Pki, prepared_pdf: bytes
) -> None:
    """Why the order is a validity precondition, not a preference.

    ``prior_fields=()`` bypasses the module's own guard deliberately, to reach
    the property the guard exists for: the Board's ``/All`` lock seals the
    approver's fields, so an approver signing afterwards produces a document
    whose BOARD signature reports illegal modifications. No product path can
    reach this state; the test exists so that a future change which loosens the
    ordering cannot look harmless.
    """
    profile = pki.profile(lta=False)
    certified = sign_as_preparer(
        prepared_pdf,
        signer=pki.by_role["preparer"],
        appearance=_appearance("preparer"),
        profile=profile,
    )
    board_first = sign_as_board(
        certified,
        signer=pki.by_role["board"],
        appearance=_appearance("board"),
        profile=profile,
        prior_fields=(),
    )
    out_of_order = sign_as_approver(
        board_first,
        signer=pki.by_role["approver"],
        appearance=_appearance("approver"),
        profile=profile,
    )
    reader = PdfFileReader(io.BytesIO(out_of_order))
    statuses = {
        sig.field_name: validate_pdf_signature(
            sig,
            signer_validation_context=pki.validation_context,
            ts_validation_context=pki.validation_context,
            diff_policy=attestation_diff_policy(),
        )
        for sig in reader.embedded_regular_signatures
    }
    assert not statuses[BOARD_FIELD_NAME].docmdp_ok
    assert not statuses[BOARD_FIELD_NAME].bottom_line
    # The preparer is untouched: its own EXCLUDE covered both later roles, so it
    # does not care which of them went first.
    assert statuses[PREPARER_FIELD_NAME].docmdp_ok


# --- the order itself -------------------------------------------------------


@pytest.mark.parametrize(
    ("order", "message"),
    [
        ((), "at least one signing role"),
        (("approver", "board"), "preparer must sign first"),
        (("preparer", "approver", "approver"), "names a role twice"),
        (("preparer", "witness"), "has no field on the return artifact"),
        (("preparer", "board", "approver"), "canonical order"),
    ],
    ids=["empty", "no_preparer_first", "duplicate", "unknown_role", "wrong_rank"],
)
def test_prepare_refuses_an_impossible_signing_order(
    unsigned_pdf: bytes, icaap_placements: tuple, order: tuple[str, ...], message: str
) -> None:
    """The order decides the locks, so an incoherent one can never reach a file."""
    with pytest.raises(PdfSigningError, match=message):
        prepare_signature_fields(
            unsigned_pdf, placements=icaap_placements, signing_order=order
        )


def test_prepare_refuses_a_placement_for_a_role_outside_the_signing_order(
    unsigned_pdf: bytes, icaap_placements: tuple
) -> None:
    """A Board box on a two-signer ceremony is the same refusal as ever.

    ``ROLE_FIELD_NAMES`` now knows the word ``board`` — it is the field-name
    vocabulary, not the required-role set — so the refusal has to come from the
    ORDER instead. Its wording is unchanged, because it is the wording a bank's
    operator has seen since placements shipped.
    """
    with pytest.raises(PdfSigningError, match="has no field on the return artifact"):
        prepare_signature_fields(unsigned_pdf, placements=icaap_placements)


def test_a_two_role_icaap_order_produces_todays_lock_shape(unsigned_pdf: bytes) -> None:
    """D-043: with the Board slot off, an ICAAP return signs like everything else.

    The default ICAAP policy is preparer + approver. The layout differs (three
    blocks are POSSIBLE) but the ceremony does not, so the lock chain collapses
    back to ``/Exclude`` then ``/All`` — no dormant ``Sig_Board`` sits on the
    document waiting for somebody to fill it.
    """
    order = ("preparer", "approver")
    prepared = prepare_signature_fields(
        unsigned_pdf,
        placements=default_placements(order, layout="icaap"),
        signing_order=order,
    )
    locks = _locks(prepared)
    assert set(locks) == {PREPARER_FIELD_NAME, APPROVER_FIELD_NAME}
    assert locks[APPROVER_FIELD_NAME] == {"action": "/All", "fields": []}
    preparer_lock = locks[PREPARER_FIELD_NAME]
    assert preparer_lock is not None
    assert preparer_lock["action"] == "/Exclude"


# --- geometry ---------------------------------------------------------------


def test_the_icaap_layout_carries_three_evenly_pitched_blocks() -> None:
    """Three blocks, same pitch as the standard two, all on the attestation page.

    The exact rule positions are owned jointly with the ICAAP renderer (P3-DESIGN
    §7.2): it draws its rules at :func:`signing_rule_y` so the two files cannot
    drift. What is asserted here is what this module is responsible for — that
    the blocks exist, do not overlap, and sit in a regular column.
    """
    standard = signing_rule_y("standard")
    icaap = signing_rule_y("icaap")
    assert list(standard) == ["preparer", "approver"]
    assert list(icaap) == ["preparer", "approver", "board"]

    standard_pitch = standard["preparer"] - standard["approver"]
    pitches = [
        icaap["preparer"] - icaap["approver"],
        icaap["approver"] - icaap["board"],
    ]
    assert pitches == [standard_pitch, standard_pitch]

    placed = default_placements(ICAAP_ORDER, layout="icaap")
    assert len(placed) == len(ICAAP_ORDER) * 4  # name, title, signature, date
    assert {placement.page_index for placement in placed} == {ATTESTATION_PAGE_INDEX}
    assert {placement.signing_role for placement in placed} == set(ICAAP_ORDER)
    # Every box is inside A4 (595 × 842) with a margin, and no two overlap.
    boxes = [placement.box for placement in placed]
    for box in boxes:
        assert 0 < box[0] < box[2] <= 545
        assert 0 < box[1] < box[3] <= 800
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            overlaps = (
                first[0] < second[2]
                and second[0] < first[2]
                and first[1] < second[3]
                and second[1] < first[3]
            )
            assert not overlaps, f"{first} overlaps {second}"


def test_the_standard_default_is_still_the_standard_default() -> None:
    """``default_placements()`` with no arguments IS ``DEFAULT_PLACEMENTS``."""
    assert default_placements() == DEFAULT_PLACEMENTS
    assert (
        default_placements(layouts.STANDARD_SIGNING_ORDER, layout="standard")
        == DEFAULT_PLACEMENTS
    )
