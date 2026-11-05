"""The OpenBao signing backend: Transit signs, PKI certifies
(app/attestation/service/openbao.py — docs §3.3, §3.4).

``Pkcs11RawSigner`` is the cautionary tale this suite exists to avoid repeating:
carefully written, never executed, correctness therefore unknown. So everything
below the ``OPENBAO_TEST_ADDR`` gate runs against a REAL OpenBao server — see
``backend/README.md#run-tests`` for CI service setup. Nothing is mocked: the
signatures are produced by the server, the certificates are issued by a PKI
mount this suite bootstraps with the shipped operational script, and the PDFs
are validated by pyHanko afterwards.

The gate follows ``TEST_DATABASE_URL``'s precedent — the default hermetic suite
stays runnable with no server — but CI sets the variable, so the coverage is
real rather than theatre. The tests above the gate need no server and always run.

The assertions are the properties that would be DEFECTS:

* a Transit signature verifies against the public key OpenBao itself reports;
* the ``vault:v<n>:`` envelope is stripped exactly, and an envelope we do not
  recognise is refused instead of becoming signature bytes;
* the recorded ``signature_method`` matches the key — an ECDSA key never claims
  PSS;
* a CSR assembled without the private key really is signed by that key, and one
  that is not is refused rather than submitted;
* the issued certificate chains to the PKI root and carries the ``SGN-`` id
  where a machine can read it;
* a signed PDF validates under pyHanko with the OpenBao-backed signer, through
  the real ``signing.certify`` ceremony, anchored on the INSTITUTIONAL root
  rather than on the chain the signature carries;
* revoking a key puts its certificate on the CA's CRL, and a revocation that
  could not be published leaves nothing recorded;
* an unreachable server, a wrong AppRole, a denied path and a missing PKI role
  all raise, and none of them ever falls back to another backend or to
  self-signing;
* the AppRole secret id never reaches a log record, an exception string, or an
  audit event.

This module's other-suite cases live in ``test_attestation_openbao_integration.py``.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from asn1crypto import csr as asn1_csr
from asn1crypto import keys as asn1_keys
from asn1crypto import pem as asn1_pem
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

from app.attestation.service import signers
from app.attestation.service.openbao import (
    KEY_NAME_PREFIX,
    OpenBaoTransitRawSigner,
    _decode_signature,
    _require_well_formed_csr,
    new_transit_key_ref,
    transit_key_stem,
)
from app.attestation.service.signers import (
    SignerBackendError,
    SignerBackendUnavailable,
    SoftwareRawSigner,
    get_raw_signer,
    signer_subject,
)
from app.core.config import get_settings
from tests.attestation.service.test_attestation_openbao_integration import (
    DIGEST,
    SIGNER_ID,
    asn1_x509,
)
from tests.support.helpers import ORG_1, ORG_2

#: A closed port on loopback. Nothing listens here, so a connection attempt is
#: refused immediately — the "OpenBao is down" case without a timeout wait.
DEAD_ADDRESS = "http://127.0.0.1:1"


def test_the_vault_version_prefix_is_stripped_and_the_body_decoded() -> None:
    """``vault:v1:<base64>`` is the whole wire format; the rest is DER."""
    der = bytes.fromhex("3045022012340220abcd")
    envelope = f"vault:v1:{base64.b64encode(der).decode()}"

    assert _decode_signature(envelope, key_ref="k") == der
    # The version moves with key rotation and must not confuse the parser.
    assert _decode_signature(f"vault:v27:{base64.b64encode(der).decode()}", key_ref="k") == der


@pytest.mark.parametrize(
    "envelope",
    [
        "MEUCIQC",  # bare base64 with no envelope
        "vault:v:MEUCIQC=",  # no version number
        "bao:v1:MEUCIQC=",  # a prefix we have never seen
        "vault:v1:",  # an empty body
        "vault:v1:not base64!",
        None,
        123,
    ],
)
def test_an_unrecognised_signature_envelope_is_refused(envelope: object) -> None:
    """Loose parsing here would turn a protocol change into unverifiable bytes.

    Splitting on the last colon would happily "decode" every one of these into
    something, and the failure would surface as a signature that no verifier can
    check — months later, on a filed return.
    """
    with pytest.raises(SignerBackendError):
        _decode_signature(envelope, key_ref="aequoros-OR-DEM00001-SGN-1")


def test_key_names_are_tenant_prefixed_and_never_reused() -> None:
    """The prefix is what an ACL policy globs on; the nonce is what rotation needs."""
    stem = transit_key_stem(organization_id=ORG_1, signer_id=SIGNER_ID)
    assert stem == f"{KEY_NAME_PREFIX}-{ORG_1}-{SIGNER_ID}"

    first = new_transit_key_ref(organization_id=ORG_1, signer_id=SIGNER_ID)
    second = new_transit_key_ref(organization_id=ORG_1, signer_id=SIGNER_ID)
    assert first.startswith(f"{stem}-") and second.startswith(f"{stem}-")
    assert first != second

    # One tenant's glob must not reach another's keys.
    other = new_transit_key_ref(organization_id=ORG_2, signer_id=SIGNER_ID)
    assert not other.startswith(f"{KEY_NAME_PREFIX}-{ORG_1}-")


def test_a_transit_key_name_is_a_valid_platform_key_ref() -> None:
    """``key_ref`` is one grammar across every backend; Transit must fit it.

    Transit's own route refuses slashes, which is why the design note's
    ``aequoros/<org>/<signer>`` became hyphen-separated — and a name this
    platform would reject would fail at ``sign_digest`` instead of at creation.
    """
    key_ref = new_transit_key_ref(organization_id=ORG_1, signer_id=SIGNER_ID)

    assert signers._KEY_REF_PATTERN.match(key_ref)
    assert "/" not in key_ref


def test_the_factory_selects_openbao_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example.test:8200")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "secret-1")
    get_settings.cache_clear()

    assert isinstance(get_raw_signer(), OpenBaoTransitRawSigner)


def test_openbao_selected_without_credentials_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that asked for OpenBao and got a soft key would lie about custody."""
    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    get_settings.cache_clear()

    with pytest.raises(SignerBackendUnavailable, match="OPENBAO_ADDR"):
        get_raw_signer()

    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example.test:8200")
    get_settings.cache_clear()
    with pytest.raises(SignerBackendUnavailable, match="OPENBAO_ROLE_ID"):
        get_raw_signer()


def test_openbao_is_permitted_in_production_where_software_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entire point of the backend: production can sign.

    The soft backend is refused because this process can read the key. OpenBao's
    is created non-exportable inside the server, so the sole-control argument
    survives and the same APP_ENV that locks out ``software`` must not lock out
    this one (§3.3).
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example.test:8200")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "secret-1")

    monkeypatch.setenv("SIGNING_BACKEND", "software")
    get_settings.cache_clear()
    with pytest.raises(signers.SignerBackendForbidden):
        get_raw_signer()

    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    get_settings.cache_clear()
    assert isinstance(get_raw_signer(), OpenBaoTransitRawSigner)


def test_readiness_gaps_name_the_missing_openbao_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/health/ready`` and ``ensure_signing_configured`` both read this list.

    An address or AppRole that is missing is exactly as fatal to filing as an
    unset SIGNER_ID_PEPPER, and must be reported at deploy time rather than at
    the moment an officer presses sign.
    """
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-pepper")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    get_settings.cache_clear()

    assert get_settings().attestation.signing_readiness_gaps() == [
        "OPENBAO_ADDR",
        "OPENBAO_ROLE_ID",
        "OPENBAO_SECRET_ID",
    ]

    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example.test:8200")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "secret-1")
    get_settings.cache_clear()
    assert get_settings().attestation.signing_readiness_gaps() == []

    # A pkcs11 deployment's unset OpenBao settings are unused, not a gap.
    monkeypatch.setenv("SIGNING_BACKEND", "pkcs11")
    monkeypatch.setenv("OPENBAO_ADDR", "")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "")
    get_settings.cache_clear()
    assert get_settings().attestation.signing_readiness_gaps() == []


def test_an_unreachable_server_raises_unavailable_and_produces_nothing() -> None:
    """Down is down. There is no local key to quietly sign with instead."""
    signer = OpenBaoTransitRawSigner(
        address=DEAD_ADDRESS, role_id="role-1", secret_id="secret-1", timeout_seconds=2.0
    )

    with pytest.raises(SignerBackendUnavailable, match="could not be reached"):
        signer.sign_digest(DIGEST, key_ref="aequoros-OR-DEM00001-SGN-1-aaaaaaaa")
    with pytest.raises(SignerBackendUnavailable):
        signer.certificate(key_ref="aequoros-OR-DEM00001-SGN-1-aaaaaaaa")
    # And nothing anywhere returned a SoftwareRawSigner as consolation.
    assert not isinstance(signer, SoftwareRawSigner)


def test_something_that_is_not_openbao_answering_is_reported_as_unavailable() -> None:
    """A proxy or captive portal answering 200 with HTML is a real deployment state.

    It must surface as "OpenBao is unavailable", not as a bare ValueError from
    inside the signing transaction, where nothing names the cause.
    """
    signer = OpenBaoTransitRawSigner(
        address="https://bao.example.test:8200",
        role_id="role-1",
        secret_id="secret-1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, html="<html>Sign in to the network</html>")
        ),
    )

    with pytest.raises(SignerBackendUnavailable, match="non-JSON"):
        signer.sign_digest(DIGEST, key_ref="aequoros-OR-DEM00001-SGN-1-aaaaaaaa")


def test_the_repr_carries_the_endpoint_and_not_the_credential() -> None:
    """A repr lands in tracebacks and debuggers; the secret id must not."""
    signer = OpenBaoTransitRawSigner(
        address="https://bao.example.test:8200",
        role_id="role-1",
        secret_id="s3cr3t-id-value",
        transit_mount="transit",
    )

    rendered = repr(signer)
    assert "bao.example.test" in rendered
    assert "s3cr3t-id-value" not in rendered


def _spki(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _hand_built_csr(
    private_key: ec.EllipticCurvePrivateKey, *, subject: x509.Name, sound: bool = True
) -> str:
    """A CSR assembled exactly as ``build_csr`` does.

    ``sound=False`` signs a DIFFERENT digest, which is what a subtle assembly
    bug produces: every byte parses, the signature is a real ECDSA signature by
    the real key, and it attests to nothing.
    """
    info = asn1_csr.CertificationRequestInfo(
        {
            "version": "v1",
            "subject": asn1_x509.Name.load(subject.public_bytes()),
            "subject_pk_info": asn1_keys.PublicKeyInfo.load(_spki(private_key.public_key())),
            "attributes": [],
        }
    )
    digest = hashlib.sha256(info.dump()).digest() if sound else DIGEST
    request = asn1_csr.CertificationRequest(
        {
            "certification_request_info": info,
            "signature_algorithm": {"algorithm": "sha256_ecdsa"},
            "signature": private_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))),
        }
    )
    return asn1_pem.armor("CERTIFICATE REQUEST", request.dump()).decode("ascii")


def test_a_csr_whose_signature_covers_other_bytes_is_refused() -> None:
    """The proof of possession has to prove possession OF THIS REQUEST.

    A CA may well accept it — nothing obliges OpenBao's CSR parsing to check the
    self-signature — and the result would be a certificate whose
    proof-of-possession is a fiction, discovered years later by whoever finally
    audits a filed return.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = _spki(private_key.public_key())
    subject = signer_subject(
        signer_id=SIGNER_ID, display_name="Ama Mensah", organization_name="Sample Bank"
    )

    _require_well_formed_csr(
        _hand_built_csr(private_key, subject=subject), public_der=public_der, key_ref="k"
    )

    with pytest.raises(SignerBackendError, match="does not verify against its own public key"):
        _require_well_formed_csr(
            _hand_built_csr(private_key, subject=subject, sound=False),
            public_der=public_der,
            key_ref="k",
        )


def test_a_csr_for_another_key_is_refused() -> None:
    """The request must certify the key the custody backend actually holds."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    stranger = ec.generate_private_key(ec.SECP256R1())
    subject = signer_subject(signer_id=SIGNER_ID, display_name=None, organization_name=None)

    with pytest.raises(SignerBackendError, match="different public key"):
        _require_well_formed_csr(
            _hand_built_csr(private_key, subject=subject),
            public_der=_spki(stranger.public_key()),
            key_ref="k",
        )


def test_there_is_no_self_signing_path_on_the_openbao_backend() -> None:
    """Phase 1's scaffolding is GONE, not merely unused.

    A dormant ``self_sign_certificate`` would be one plausible-looking fallback
    away from a deployment that asked for CA-issued certificates and quietly got
    certificates chaining to nothing.
    """
    assert not hasattr(OpenBaoTransitRawSigner, "self_sign_certificate")
    # …and it is still the dev backend's documented behaviour, which is where a
    # certificate chaining to nothing is honest.
    assert hasattr(SoftwareRawSigner, "self_sign_certificate")


def test_a_certificate_that_lost_the_signer_id_is_refused() -> None:
    """A certificate nobody can attribute must never reach ``signer_keys``.

    The CA composes the subject, so this is the platform's own check on what
    came back: OpenBao rejects the request outright when the role forbids the
    serial number (the test below), but a role configured to DROP it — or a
    different CA behind the same interface — would return a perfectly valid
    certificate that cannot be tied to a signer record, and the failure would
    surface only when somebody finally tried to tie it.
    """
    from app.attestation.service.openbao import (  # noqa: PLC0415
        _require_subject_identity,
    )

    key = ec.generate_private_key(ec.SECP256R1())
    anonymous = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ama Mensah")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(anonymous)
        .issuer_name(anonymous)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )

    with pytest.raises(SignerBackendError, match="subject serialNumber"):
        _require_subject_identity(certificate, signer_id=SIGNER_ID)
