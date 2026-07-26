"""The OpenBao signing backend: Transit signs, PKI certifies
(app/services/attestation/openbao.py — docs §3.3, §3.4).

``Pkcs11RawSigner`` is the cautionary tale this suite exists to avoid repeating:
carefully written, never executed, correctness therefore unknown. So everything
below the ``OPENBAO_TEST_ADDR`` gate runs against a REAL OpenBao server — dev
mode in CI (``.github/workflows/risk-service.yml`` runs one as a job service),
``docker run openbao/openbao server -dev`` locally. Nothing is mocked: the
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
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from asn1crypto import csr as asn1_csr
from asn1crypto import keys as asn1_keys
from asn1crypto import pem as asn1_pem
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509.oid import NameOID
from loguru import logger
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import SignatureCoverageLevel, validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.models import AttestationSignature, AuditEvent, SignerKey
from app.services.attestation import artifact_signing, pdf_signing, signers, verify
from app.services.attestation.keys import SignerKeyError, SignerKeyService
from app.services.attestation.openbao import (
    KEY_NAME_PREFIX,
    OpenBaoPkiIssuer,
    OpenBaoTransitRawSigner,
    _decode_signature,
    _require_well_formed_csr,
    _serial_hex,
    _Token,
    new_transit_key_ref,
    transit_key_stem,
)
from app.services.attestation.signers import (
    ECDSA_P256_SHA256,
    RSA_2048_PSS_SHA256,
    SignerBackendError,
    SignerBackendForbidden,
    SignerBackendUnavailable,
    SignerKeyMaterialMissing,
    SoftwareRawSigner,
    get_raw_signer,
    signer_subject,
)
from scripts import bootstrap_openbao_pki
from tests.api.helpers import ORG_1, ORG_2
from tests.services.test_attestation_artifact_signing import (
    CHECKER,
    MAKER,
    InMemoryStorageClient,
    _bytes,
    _certify,
    _seed,
    _trust,
    _version,
)

DIGEST = hashlib.sha256(b"aequoros attestation payload").digest()
SIGNER_ID = "SGN-7K4M9PQR2VWX3YZ8"

#: A closed port on loopback. Nothing listens here, so a connection attempt is
#: refused immediately — the "OpenBao is down" case without a timeout wait.
DEAD_ADDRESS = "http://127.0.0.1:1"


# --- the gate ---------------------------------------------------------------


#: The mounts and role the deployment defaults name, and which
#: ``scripts/bootstrap_openbao_pki.py`` creates. Reused verbatim rather than
#: given test-only names, so the suite exercises the documented configuration.
PKI_MOUNT = bootstrap_openbao_pki.DEFAULT_ISSUING_MOUNT
PKI_ROLE = bootstrap_openbao_pki.DEFAULT_ROLE


@dataclass(frozen=True)
class BaoServer:
    """A live OpenBao and the AppRole credentials provisioned against it."""

    address: str
    root_token: str
    #: Full-tenant role: may create and sign every ``aequoros-*`` key.
    role_id: str
    secret_id: str
    #: Scoped role: ORG_1 only, to prove a policy denial is a denial.
    scoped_role_id: str
    scoped_secret_id: str
    #: Where the bootstrap wrote the PKI root, for ATTESTATION_TRUST_ROOTS.
    trust_roots: Path = Path()

    def admin(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        return httpx.request(
            method,
            f"{self.address}/v1/{path}",
            json=body,
            headers={"X-Vault-Token": self.root_token},
            timeout=10.0,
        )

    @property
    def root_certificate(self) -> x509.Certificate:
        return x509.load_pem_x509_certificate(self.trust_roots.read_bytes())

    def crl(self, mount: str = PKI_MOUNT) -> x509.CertificateRevocationList:
        return x509.load_der_x509_crl(
            httpx.get(f"{self.address}/v1/{mount}/crl", timeout=10.0).content
        )


def _provision(address: str, token: str, trust_roots: Path) -> BaoServer:
    """Enable the engines and mint the AppRoles, the way a bank's IT would.

    The PKI half is provisioned by running the shipped bootstrap script, not by
    a test-local imitation of it: the script IS the operational contract, and a
    fixture that hand-rolled the mounts would let it rot while the suite stayed
    green.
    """
    server = BaoServer(
        address=address,
        root_token=token,
        role_id="",
        secret_id="",
        scoped_role_id="",
        scoped_secret_id="",
        trust_roots=trust_roots,
    )
    # Both are idempotent enough for a re-run: an existing mount answers 400
    # "path is already in use", which is not a failure here.
    server.admin("POST", "sys/mounts/transit", {"type": "transit"})
    server.admin("POST", "sys/auth/approle", {"type": "approle"})
    _bootstrap_pki(address, token, trust_roots)

    roles: dict[str, str] = {}
    for name, prefix in (("aequoros-test", "*"), ("aequoros-test-scoped", f"{ORG_1}-*")):
        # The SHIPPED policy generator, at both scopes: whole-server and one
        # tenant. A policy that forgot `revoke`, or a tenant glob that leaked,
        # therefore fails this suite rather than a bank.
        policy = bootstrap_openbao_pki.policy_document(
            name=name,
            transit_mount="transit",
            mount=PKI_MOUNT,
            role=PKI_ROLE,
            key_prefix=prefix,
        )
        assert server.admin(
            "PUT", f"sys/policies/acl/{name}", {"policy": policy}
        ).status_code < 400
        assert server.admin(
            "POST",
            f"auth/approle/role/{name}",
            {
                "token_policies": name,
                # A short token TTL on purpose: the renewal path is then
                # exercised by any suite that runs longer than the lease.
                "token_ttl": "10m",
                "token_max_ttl": "1h",
            },
        ).status_code < 400
        role_id = server.admin("GET", f"auth/approle/role/{name}/role-id").json()["data"][
            "role_id"
        ]
        secret_id = server.admin(
            "POST", f"auth/approle/role/{name}/secret-id"
        ).json()["data"]["secret_id"]
        roles[f"{name}_role"] = role_id
        roles[f"{name}_secret"] = secret_id

    return replace(
        server,
        role_id=roles["aequoros-test_role"],
        secret_id=roles["aequoros-test_secret"],
        scoped_role_id=roles["aequoros-test-scoped_role"],
        scoped_secret_id=roles["aequoros-test-scoped_secret"],
    )


def _bootstrap_pki(address: str, token: str, trust_roots: Path) -> None:
    """Run the operational script in-process, against the test server."""
    args = bootstrap_openbao_pki.parse_args(
        [
            "--addr",
            address,
            "--organization",
            "Sample Bank",
            "--country",
            "GH",
            "--trust-roots",
            str(trust_roots),
        ]
    )
    bootstrap_openbao_pki.bootstrap(bootstrap_openbao_pki.Admin(address, token), args)


@pytest.fixture(scope="session")
def bao(tmp_path_factory: pytest.TempPathFactory) -> BaoServer:
    """A reachable, unsealed OpenBao — or a skip that says why."""
    address = os.getenv("OPENBAO_TEST_ADDR")
    if not address:
        pytest.skip(
            "OPENBAO_TEST_ADDR is not set. These tests run against a real OpenBao "
            "(`docker run -p 8200:8200 -e BAO_DEV_ROOT_TOKEN_ID=... openbao/openbao "
            "server -dev`); CI runs one as a job service."
        )
    token = os.getenv("OPENBAO_TEST_TOKEN", "root")
    try:
        health = httpx.get(f"{address.rstrip('/')}/v1/sys/health", timeout=10.0)
    except httpx.HTTPError as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"OPENBAO_TEST_ADDR={address} is not reachable: {exc}")
    if health.json().get("sealed"):  # pragma: no cover - environment, not logic
        pytest.skip(f"OpenBao at {address} is sealed; these tests need it unsealed.")
    anchor = tmp_path_factory.mktemp("bao-trust") / "aequoros-attestation-root.pem"
    return _provision(address.rstrip("/"), token, anchor)


@pytest.fixture
def bao_signer(bao: BaoServer) -> OpenBaoTransitRawSigner:
    return OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id=bao.secret_id
    )


@pytest.fixture
def bao_issuer(bao_signer: OpenBaoTransitRawSigner) -> OpenBaoPkiIssuer:
    return OpenBaoPkiIssuer(signer=bao_signer, pki_mount=PKI_MOUNT, role=PKI_ROLE)


def _fresh_key_ref(organization_id: str = ORG_1) -> str:
    """A key name no earlier run can have used, so the dev server never clashes."""
    return new_transit_key_ref(
        organization_id=organization_id, signer_id=f"SGN-{secrets.token_hex(6).upper()}"
    )


def _settings_for(bao: BaoServer, **overrides: Any) -> Settings:
    base = get_settings()
    attestation = base.attestation.model_copy(
        update={
            "signing_backend": "openbao",
            "openbao_addr": bao.address,
            "openbao_role_id": bao.role_id,
            "openbao_secret_id": bao.secret_id,
            **overrides,
        }
    )
    return base.model_copy(update={"attestation": attestation})


# --- envelope parsing: no server needed -------------------------------------


def test_the_vault_version_prefix_is_stripped_and_the_body_decoded() -> None:
    """``vault:v1:<base64>`` is the whole wire format; the rest is DER."""
    der = bytes.fromhex("3045022012340220abcd")
    envelope = f"vault:v1:{base64.b64encode(der).decode()}"

    assert _decode_signature(envelope, key_ref="k") == der
    # The version moves with key rotation and must not confuse the parser.
    assert _decode_signature(
        f"vault:v27:{base64.b64encode(der).decode()}", key_ref="k"
    ) == der


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


# --- key naming: no server needed -------------------------------------------


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


# --- the factory: no server needed ------------------------------------------


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


# --- against a real server: signing -----------------------------------------


def test_a_transit_signature_verifies_against_the_public_key_openbao_reports(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """The load-bearing round trip: the server signs, we verify independently."""
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)

    signature = bao_signer.sign_digest(DIGEST, key_ref=key_ref)

    public_key = bao_signer.public_key(key_ref=key_ref)
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    # As a signature over the DIGEST …
    public_key.verify(signature, DIGEST, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    # … and equivalently over the original message, which is what a third party
    # holding only the filed document would compute.
    public_key.verify(
        signature, b"aequoros attestation payload", ec.ECDSA(hashes.SHA256())
    )


def test_a_transit_signature_does_not_verify_over_a_different_digest(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)
    signature = bao_signer.sign_digest(DIGEST, key_ref=key_ref)
    public_key = bao_signer.public_key(key_ref=key_ref)
    assert isinstance(public_key, ec.EllipticCurvePublicKey)

    with pytest.raises(InvalidSignature):
        public_key.verify(
            signature,
            hashlib.sha256(b"tampered figures").digest(),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )


def test_rsa_pss_signing_through_transit_verifies(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """RSA-2048/PSS for a bank whose CA requires it (§3.3).

    ``salt_length='hash'`` is asserted rather than assumed: the verifier accepts
    ``PSS.AUTO``, but a salt length that drifted from the digest length would
    stop matching the software backend's output and make the two custody
    backends produce differently-shaped evidence for the same act.
    """
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref, algorithm=RSA_2048_PSS_SHA256)

    signature = bao_signer.sign_digest(DIGEST, key_ref=key_ref)

    public_key = bao_signer.public_key(key_ref=key_ref)
    assert isinstance(public_key, rsa.RSAPublicKey)
    public_key.verify(
        signature,
        DIGEST,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        utils.Prehashed(hashes.SHA256()),
    )


def test_a_hex_digest_is_refused_before_it_reaches_the_server(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """64 ASCII bytes would sign cleanly and bind nothing."""
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)

    with pytest.raises(ValueError, match="32-byte SHA-256 digest"):
        bao_signer.sign_digest(DIGEST.hex().encode("ascii"), key_ref=key_ref)


def test_a_missing_key_raises_key_material_missing(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """OpenBao answers 400 'signing key not found'; the caller must see the typed error."""
    absent = _fresh_key_ref()

    with pytest.raises(SignerKeyMaterialMissing):
        bao_signer.sign_digest(DIGEST, key_ref=absent)
    with pytest.raises(SignerKeyMaterialMissing):
        bao_signer.certificate(key_ref=absent)
    with pytest.raises(SignerKeyMaterialMissing):
        bao_signer.public_key(key_ref=absent)


# --- against a real server: authentication ----------------------------------


def test_a_wrong_approle_secret_raises_rather_than_returning_a_signature(
    bao: BaoServer,
) -> None:
    signer = OpenBaoTransitRawSigner(
        address=bao.address,
        role_id=bao.role_id,
        secret_id="00000000-0000-0000-0000-000000000000",
    )

    with pytest.raises(SignerBackendForbidden, match="rejected the AppRole login"):
        signer.sign_digest(DIGEST, key_ref=_fresh_key_ref())


def test_a_destroyed_secret_id_stops_working_immediately(bao: BaoServer) -> None:
    """Revocation is the operator's kill switch; it must actually kill."""
    disposable = bao.admin(
        "POST", "auth/approle/role/aequoros-test/secret-id"
    ).json()["data"]
    signer = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id=disposable["secret_id"]
    )
    key_ref = _fresh_key_ref()
    signer.create_key(key_ref=key_ref)

    bao.admin(
        "POST",
        "auth/approle/role/aequoros-test/secret-id-accessor/destroy",
        {"secret_id_accessor": disposable["secret_id_accessor"]},
    )

    # The cached token outlives the secret id by design (it has its own lease),
    # so force the credential to be used again.
    fresh = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id=disposable["secret_id"]
    )
    with pytest.raises(SignerBackendForbidden):
        fresh.sign_digest(DIGEST, key_ref=key_ref)


def test_a_policy_denied_key_raises_forbidden_not_a_bogus_signature(
    bao: BaoServer,
) -> None:
    """The tenant prefix is only worth something if the server enforces it."""
    scoped = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.scoped_role_id, secret_id=bao.scoped_secret_id
    )
    own = _fresh_key_ref(ORG_1)
    scoped.create_key(key_ref=own)
    assert scoped.sign_digest(DIGEST, key_ref=own)

    others = _fresh_key_ref(ORG_2)
    with pytest.raises(SignerBackendForbidden):
        scoped.create_key(key_ref=others)
    with pytest.raises(SignerBackendForbidden):
        scoped.sign_digest(DIGEST, key_ref=others)


def test_an_expired_token_is_re_established_rather_than_failing_the_signature(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """A 403 from a dead token must cost a re-login, not a filing.

    Tokens have their own lease and a long-running process WILL meet an expired
    one. Simulating it by poisoning the cache proves the retry path exists —
    without it, the first signature after a token expiry would fail an officer
    mid-ceremony.
    """
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)
    bao_signer.sign_digest(DIGEST, key_ref=key_ref)
    public_key = bao_signer.public_key(key_ref=key_ref)
    assert isinstance(public_key, ec.EllipticCurvePublicKey)

    # A token the server has never issued, presented as a live one. Every call
    # answers 403 until the backend notices and logs in again.
    bao_signer._token = _Token(
        value="hvs.thisTokenWasNeverIssued", lease_seconds=3600, renewable=False
    )

    recovered = bao_signer.sign_digest(DIGEST, key_ref=key_ref)

    public_key.verify(recovered, DIGEST, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


def test_a_denial_that_survives_re_authentication_is_reported_as_forbidden(
    bao: BaoServer,
) -> None:
    """The 403 retry must not become an infinite loop or a swallowed denial.

    A dead token and a policy denial look identical on the wire, so the retry
    exists to tell them apart. This is the other half: when the fresh token is
    refused too, the AppRole genuinely lacks the capability and the caller has to
    hear so.
    """
    scoped = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.scoped_role_id, secret_id=bao.scoped_secret_id
    )
    scoped._token = _Token(
        value="hvs.thisTokenWasNeverIssued", lease_seconds=3600, renewable=False
    )

    with pytest.raises(SignerBackendForbidden):
        scoped.sign_digest(DIGEST, key_ref=_fresh_key_ref(ORG_2))


# --- the CSR: no server needed ----------------------------------------------


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
            "signature": private_key.sign(
                digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
            ),
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


# --- against a real server: certificates ------------------------------------


def test_the_csr_is_signed_through_transit_and_proves_possession(
    bao_signer: OpenBaoTransitRawSigner, bao_issuer: OpenBaoPkiIssuer
) -> None:
    """The awkward part: a CSR signed by a key nothing can read.

    ``cryptography`` cannot build this — its CSR builder needs a key object —
    so the request is assembled with asn1crypto and signed through the same
    ``sign_digest`` the officer's attestation goes through. If that were wrong,
    the certificate would still be issued and every later proof-of-possession
    argument would be false.
    """
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)
    subject = signer_subject(
        signer_id=SIGNER_ID, display_name="Ama Mensah", organization_name="Sample Bank"
    )

    pem = bao_issuer.build_csr(key_ref=key_ref, subject=subject)

    parsed = x509.load_pem_x509_csr(pem.encode("ascii"))
    assert parsed.is_signature_valid
    assert parsed.subject == subject
    from_server = bao_signer.public_key(key_ref=key_ref)
    assert isinstance(from_server, ec.EllipticCurvePublicKey)
    parsed_key = parsed.public_key()
    assert isinstance(parsed_key, ec.EllipticCurvePublicKey)
    assert parsed_key.public_numbers() == from_server.public_numbers()


def test_an_rsa_pss_csr_carries_its_parameters_and_verifies(
    bao_signer: OpenBaoTransitRawSigner, bao_issuer: OpenBaoPkiIssuer
) -> None:
    """RSA-PSS names its digest in the algorithm PARAMETERS, not the OID.

    A bare ``rsassa_pss`` identifier means SHA-1 with a 20-byte salt (RFC 4055),
    so a request that omitted the parameters would be verified under the wrong
    scheme — rejected by a strict CA, or worse accepted by a lenient one.
    """
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref, algorithm=RSA_2048_PSS_SHA256)
    subject = signer_subject(
        signer_id=SIGNER_ID, display_name="Kwesi Owusu", organization_name="Sample Bank"
    )

    parsed = x509.load_pem_x509_csr(
        bao_issuer.build_csr(key_ref=key_ref, subject=subject).encode("ascii")
    )

    assert parsed.is_signature_valid
    assert parsed.signature_algorithm_oid.dotted_string == "1.2.840.113549.1.1.10"
    assert isinstance(parsed.public_key(), rsa.RSAPublicKey)


def test_a_csr_that_does_not_prove_possession_is_never_submitted(
    bao_signer: OpenBaoTransitRawSigner,
    bao_issuer: OpenBaoPkiIssuer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is on the ISSUANCE path, not a decorative helper.

    ``sign_digest`` is redirected to sign a different value — a real signature
    by the real key over the wrong bytes, which is what a subtle assembly bug
    would produce — and the enrolment must stop before the CA is asked.
    """
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)
    real = bao_signer.sign_digest
    monkeypatch.setattr(
        bao_signer, "sign_digest", lambda _digest, *, key_ref: real(DIGEST, key_ref=key_ref)
    )

    with pytest.raises(SignerBackendError, match="does not verify"):
        bao_issuer.issue(
            key_ref=key_ref,
            subject=signer_subject(
                signer_id=SIGNER_ID, display_name=None, organization_name=None
            ),
            signer_id=SIGNER_ID,
            ttl_seconds=3600,
        )
    # Nothing was filed against the key, so the mount did not acquire a
    # certificate for a request that was never sound.
    with pytest.raises(SignerKeyMaterialMissing):
        bao_signer.certificate(key_ref=key_ref)


def test_the_issued_certificate_verifies_against_the_pki_root(
    bao: BaoServer, bao_signer: OpenBaoTransitRawSigner, bao_issuer: OpenBaoPkiIssuer
) -> None:
    """The whole point of the phase: the chain terminates at an institution.

    Every link is verified rather than trusted from the response, and the anchor
    is compared to the file the bootstrap wrote — the same file an operator
    points ``ATTESTATION_TRUST_ROOTS`` at, so "the root" cannot mean two things.
    """
    signer_id = f"SGN-{secrets.token_hex(8).upper()}"
    key_ref = new_transit_key_ref(organization_id=ORG_1, signer_id=signer_id)
    bao_signer.create_key(key_ref=key_ref)

    issued = bao_issuer.issue(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=signer_id, display_name="Ama Mensah", organization_name="Sample Bank"
        ),
        signer_id=signer_id,
        ttl_seconds=365 * 86_400,
    )

    assert issued.certificate.subject != issued.certificate.issuer  # NOT self-signed
    current = issued.certificate
    for parent in issued.chain:
        current.verify_directly_issued_by(parent)
        current = parent
    assert current.subject == current.issuer  # the root signs itself
    assert current.fingerprint(hashes.SHA256()) == bao.root_certificate.fingerprint(
        hashes.SHA256()
    )
    assert bao_issuer.trust_anchor().fingerprint(hashes.SHA256()) == current.fingerprint(
        hashes.SHA256()
    )
    # The role's profile, which is what keeps the certificate a SIGNING
    # certificate and nothing else.
    usage = issued.certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and usage.content_commitment
    assert not usage.key_cert_sign
    # Revocation has somewhere to be published to and somewhere to be read from.
    assert issued.certificate.extensions.get_extension_for_class(x509.CRLDistributionPoints)
    assert issued.certificate.extensions.get_extension_for_class(
        x509.AuthorityInformationAccess
    )


def test_the_signer_id_is_recoverable_from_the_certificate_alone(
    bao_signer: OpenBaoTransitRawSigner, bao_issuer: OpenBaoPkiIssuer
) -> None:
    """An examiner holding the certificate can tie it to the signer record.

    The subject ``serialNumber`` (X.520 2.5.4.5), not the PDF signature
    dictionary: a certificate detached from its document must still be
    attributable, which is the property that survives a name change or an Act
    843 redaction (§3.4, legal register L10).
    """
    signer_id = f"SGN-{secrets.token_hex(8).upper()}"
    key_ref = new_transit_key_ref(organization_id=ORG_1, signer_id=signer_id)
    bao_signer.create_key(key_ref=key_ref)

    issued = bao_issuer.issue(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=signer_id, display_name="Ama Mensah", organization_name="Sample Bank"
        ),
        signer_id=signer_id,
        ttl_seconds=3600,
    )

    subject = issued.certificate.subject
    assert [
        attribute.value for attribute in subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    ] == [signer_id]
    # Re-parsed from PEM, because that is all a third party ever has.
    reloaded = x509.load_pem_x509_certificate(
        issued.certificate.public_bytes(serialization.Encoding.PEM)
    )
    assert signer_id in reloaded.subject.rfc4514_string()


def test_a_certificate_that_lost_the_signer_id_is_refused() -> None:
    """A certificate nobody can attribute must never reach ``signer_keys``.

    The CA composes the subject, so this is the platform's own check on what
    came back: OpenBao rejects the request outright when the role forbids the
    serial number (the test below), but a role configured to DROP it — or a
    different CA behind the same interface — would return a perfectly valid
    certificate that cannot be tied to a signer record, and the failure would
    surface only when somebody finally tried to tie it.
    """
    from app.services.attestation.openbao import (  # noqa: PLC0415
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


def test_a_role_that_forbids_the_signer_id_fails_the_enrolment_loudly(
    bao_signer: OpenBaoTransitRawSigner, bao: BaoServer
) -> None:
    """The CA is the first line, and it refuses rather than quietly complying.

    ``allowed_serial_numbers`` is what makes the subject identifier a CA-enforced
    property rather than a client convention — the reason the id lives in the
    subject ``serialNumber`` and not in a SAN ``otherName``, which no role can
    constrain. A role that has not been granted it must stop the enrolment, not
    issue a certificate with the identifier stripped.
    """
    lax_role = "aequoros-test-no-serial"
    assert bao.admin(
        "POST",
        f"{PKI_MOUNT}/roles/{lax_role}",
        {
            "allow_any_name": True,
            "enforce_hostnames": False,
            "cn_validations": ["disabled"],
            "allowed_serial_numbers": [],
            "key_type": "any",
            "ttl": "1h",
            "max_ttl": "1h",
        },
    ).status_code < 400
    assert bao.admin(
        "PUT",
        f"sys/policies/acl/{lax_role}",
        {"policy": f'path "{PKI_MOUNT}/sign/{lax_role}" {{ capabilities = ["update"] }}\n'},
    ).status_code < 400
    scoped = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id=bao.secret_id
    )
    assert bao.admin(
        "POST",
        "auth/approle/role/aequoros-test",
        {
            "token_policies": f"aequoros-test,{lax_role}",
            "token_ttl": "10m",
            "token_max_ttl": "1h",
        },
    ).status_code < 400
    signer_id = f"SGN-{secrets.token_hex(8).upper()}"
    key_ref = new_transit_key_ref(organization_id=ORG_1, signer_id=signer_id)
    scoped.create_key(key_ref=key_ref)
    issuer = OpenBaoPkiIssuer(signer=scoped, pki_mount=PKI_MOUNT, role=lax_role)
    try:
        with pytest.raises(SignerBackendError, match="not allowed by this role"):
            issuer.issue(
                key_ref=key_ref,
                subject=signer_subject(
                    signer_id=signer_id, display_name="Ama Mensah", organization_name=None
                ),
                signer_id=signer_id,
                ttl_seconds=3600,
            )
        with pytest.raises(SignerKeyMaterialMissing):
            scoped.certificate(key_ref=key_ref)
    finally:
        # Put the shared AppRole back, so a later test in this session is not
        # handed a token that still carries the deliberately-broken policy.
        bao.admin(
            "POST",
            "auth/approle/role/aequoros-test",
            {"token_policies": "aequoros-test", "token_ttl": "10m", "token_max_ttl": "1h"},
        )


def test_a_missing_pki_role_names_the_bootstrap_rather_than_self_signing(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    """"Cannot certify" must read as an operational gap, not as a mystery."""
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)
    issuer = OpenBaoPkiIssuer(
        signer=bao_signer, pki_mount="pki-does-not-exist", role=PKI_ROLE
    )

    with pytest.raises(SignerBackendError) as failure:
        issuer.issue(
            key_ref=key_ref,
            subject=signer_subject(
                signer_id=SIGNER_ID, display_name=None, organization_name=None
            ),
            signer_id=SIGNER_ID,
            ttl_seconds=3600,
        )
    with pytest.raises(SignerKeyMaterialMissing):
        bao_signer.certificate(key_ref=key_ref)
    assert "bootstrap_openbao_pki" in str(failure.value) or "may not issue" in str(
        failure.value
    )


def test_a_key_with_no_certificate_reports_that_rather_than_guessing(
    bao_signer: OpenBaoTransitRawSigner,
) -> None:
    key_ref = _fresh_key_ref()
    bao_signer.create_key(key_ref=key_ref)

    with pytest.raises(SignerKeyMaterialMissing, match="no certificate chain"):
        bao_signer.certificate(key_ref=key_ref)


# --- against a real server: the enrolment path ------------------------------


@pytest.fixture
def openbao_env(bao: BaoServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment configured to sign with OpenBao, end to end.

    ``ATTESTATION_TRUST_ROOTS`` points at the anchor the bootstrap wrote, which
    is the configuration a provisioned deployment actually runs: without it the
    verifier would fall back to the chain each signature carries and report
    ``trust_anchor: embedded_chain``.
    """
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    monkeypatch.setenv("OPENBAO_ADDR", bao.address)
    monkeypatch.setenv("OPENBAO_ROLE_ID", bao.role_id)
    monkeypatch.setenv("OPENBAO_SECRET_ID", bao.secret_id)
    monkeypatch.setenv("OPENBAO_PKI_MOUNT", PKI_MOUNT)
    monkeypatch.setenv("OPENBAO_PKI_ROLE", PKI_ROLE)
    monkeypatch.setenv("ATTESTATION_TRUST_ROOTS", str(bao.trust_roots))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    """One object store behind both writers — the exporter and the signer."""
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    monkeypatch.setattr(
        "app.services.attestation.artifact_signing.get_storage_client", lambda: client
    )
    return client


@pytest.mark.usefixtures("openbao_env")
def test_enrolment_records_openbao_custody_and_a_ca_issued_certificate(
    db_session: Session, bao: BaoServer
) -> None:
    """``signer_keys`` must state the custody AND the authority behind the key."""
    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    service = SignerKeyService(db_session, MAKER)

    issued = service.issue(
        signer_id=identity.signer_id,
        display_name="Kwesi Owusu",
        organization_name="Sample Bank",
    )

    record = issued.record
    assert record.backend == "openbao"
    assert record.algorithm == ECDSA_P256_SHA256
    assert record.key_ref.startswith(f"{KEY_NAME_PREFIX}-{ORG_1}-{identity.signer_id}-")
    # The stored certificate and the stored key_ref must agree: the ceremony
    # signs with one and files the other as the verification material.
    signature = service.signer.sign_digest(DIGEST, key_ref=record.key_ref)
    public_key = x509.load_pem_x509_certificate(
        record.certificate_pem.encode("ascii")
    ).public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    public_key.verify(signature, DIGEST, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    # The row carries the whole path, so a verifier reading only the database
    # can build leaf → intermediate → root.
    assert issued.certificate.subject != issued.certificate.issuer
    assert record.certificate_chain_pem is not None
    chain = x509.load_pem_x509_certificates(record.certificate_chain_pem.encode("ascii"))
    assert len(chain) >= 1
    issued.certificate.verify_directly_issued_by(chain[0])
    assert chain[-1].fingerprint(hashes.SHA256()) == bao.root_certificate.fingerprint(
        hashes.SHA256()
    )
    # A year, from the CA, not from a local clock we asked it to honour.
    assert (record.not_after - record.not_before).days >= 364
    # Nothing about the AppRole reached the row.
    assert bao.secret_id not in record.certificate_pem


@pytest.mark.usefixtures("openbao_env")
def test_the_enrolment_audit_event_names_the_certificate_authority(
    db_session: Session,
) -> None:
    """"Who vouched for this officer?" must be answerable from the register."""
    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    SignerKeyService(db_session, MAKER).issue(signer_id=identity.signer_id)
    db_session.flush()

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "signer_key.issued")
    ).one()
    assert event.details["certificate_source"] == "openbao_pki"
    assert event.details["self_signed"] == "False"


@pytest.mark.usefixtures("openbao_env")
def test_revoking_a_key_puts_its_certificate_on_the_ca_crl(
    db_session: Session, bao: BaoServer
) -> None:
    """Revocation that only a database knows about is theatre.

    The auditor's question about a departed officer is whether the certificate
    still checks out, and only the CA can answer no — so the serial must appear
    on the CRL a third party fetches, not merely in ``signer_keys.status``.
    """
    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    service = SignerKeyService(db_session, MAKER)
    issued = service.issue(signer_id=identity.signer_id, display_name="Kwesi Owusu")
    serial = issued.certificate.serial_number
    assert serial not in {entry.serial_number for entry in bao.crl()}

    revoked = service.revoke(key_id=issued.record.id, reason="Officer left the bank.")

    assert revoked.status == "revoked"
    assert serial in {entry.serial_number for entry in bao.crl()}
    # The CRL is signed by the issuing CA, so a third party can trust what it
    # says without asking us.
    crl = bao.crl()
    chain = x509.load_pem_x509_certificates(
        str(issued.record.certificate_chain_pem).encode("ascii")
    )
    issuing_key = chain[0].public_key()
    assert isinstance(issuing_key, ec.EllipticCurvePublicKey)
    assert crl.is_signature_valid(issuing_key)
    # …and the register records what the CA actually did.
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "signer_key.revoked")
    ).one()
    assert event.details["ca_revocation"] == "revoked"
    # The signer can be enrolled again; the revoked key is not "active" anywhere.
    assert service.active_key(identity.signer_id) is None


@pytest.mark.usefixtures("openbao_env")
def test_a_revocation_the_ca_refuses_records_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row that says revoked while the certificate verifies is worse than a retry.

    The CA call happens before the row is touched, so an unreachable OpenBao
    leaves the key exactly as it was — visibly still active, and revocable again
    once the CA is back.
    """
    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    service = SignerKeyService(db_session, MAKER)
    issued = service.issue(signer_id=identity.signer_id, display_name="Kwesi Owusu")

    def refuse(*, certificate: x509.Certificate) -> str:
        raise SignerBackendUnavailable("OpenBao could not be reached.")

    monkeypatch.setattr(service.pki_issuer, "revoke", refuse)
    with pytest.raises(SignerKeyError, match="keep verifying"):
        service.revoke(key_id=issued.record.id, reason="Officer left the bank.")

    db_session.refresh(issued.record)
    assert issued.record.status == "active"
    assert issued.record.revoked_at is None


def test_the_serial_matches_the_format_openbao_keys_its_store_by(
    bao: BaoServer, bao_signer: OpenBaoTransitRawSigner, bao_issuer: OpenBaoPkiIssuer
) -> None:
    """The serial is derived from the certificate, never remembered.

    A key enrolled before this code existed — or restored from a backup — must
    still be revocable, so the colon-hex form is recomputed rather than stored,
    and it has to be byte-for-byte what the CA filed it under.
    """
    signer_id = f"SGN-{secrets.token_hex(8).upper()}"
    key_ref = new_transit_key_ref(organization_id=ORG_1, signer_id=signer_id)
    bao_signer.create_key(key_ref=key_ref)
    issued = bao_issuer.issue(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=signer_id, display_name=None, organization_name=None
        ),
        signer_id=signer_id,
        ttl_seconds=3600,
    )

    serial = _serial_hex(issued.certificate)

    stored = bao.admin("GET", f"{PKI_MOUNT}/cert/{serial}").json()
    assert x509.load_pem_x509_certificate(
        stored["data"]["certificate"].encode("ascii")
    ).fingerprint(hashes.SHA256()) == issued.certificate.fingerprint(hashes.SHA256())


def test_revoking_a_certificate_this_ca_never_issued_says_so(
    bao_issuer: OpenBaoPkiIssuer,
) -> None:
    """The one non-fatal outcome, and it must not read as a published revocation.

    A bank-CA certificate enrolled through the external path is revoked by its
    own operators. Refusing our side of the deprovisioning over it would leave
    the key selectable, so it is recorded plainly instead.
    """
    stranger = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Another Bank CA")])
    foreign = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(stranger.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(stranger, hashes.SHA256())
    )

    assert bao_issuer.revoke(certificate=foreign) == "unknown_to_ca"


@pytest.mark.usefixtures("openbao_env")
def test_no_private_key_material_reaches_any_database_column(
    db_session: Session,
) -> None:
    """§3.4's central invariant, re-asserted for this backend.

    Trivially true here — there is no key material to leak, which is the point —
    so the assertion that earns its place is that the certificate is present and
    the private half is not, anywhere.
    """
    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    SignerKeyService(db_session, MAKER).issue(signer_id=identity.signer_id)
    db_session.flush()

    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).mappings():
            for column, value in row.items():
                assert "PRIVATE KEY" not in str(value), (
                    f"key material leaked into {table.name}.{column}"
                )


# --- against a real server: the full ceremony -------------------------------


@pytest.mark.usefixtures("openbao_env")
def test_the_full_certification_ceremony_runs_on_openbao(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Both officers certify a real BSD3 return, signed through Transit.

    Nothing below the subject is faked: the real export engine renders the
    return, the real ``signing.certify`` runs the real guards, OpenBao produces
    every signature, and pyHanko validates the document that comes out. This is
    the test that says "production can file".
    """
    package = _seed(db_session)

    preparer = _certify(
        db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu"
    )
    approver = _certify(
        db_session, CHECKER, package, role="approver", display_name="Ama Mensah"
    )
    db_session.refresh(package)
    assert package.attestation_state == "fully_certified"

    # Every key that took part was an OpenBao key.
    assert set(db_session.scalars(select(SignerKey.backend))) == {"openbao"}

    final = _bytes(db_session, storage, _version(db_session, approver))
    reader = PdfFileReader(io.BytesIO(final))
    embedded = reader.embedded_regular_signatures
    assert [sig.field_name for sig in embedded] == [
        pdf_signing.PREPARER_FIELD_NAME,
        pdf_signing.APPROVER_FIELD_NAME,
    ]

    trust = _trust(db_session)
    statuses = [
        validate_pdf_signature(sig, signer_validation_context=trust) for sig in embedded
    ]
    for status in statuses:
        assert status.intact, status.summary()
        assert status.valid, status.summary()
        assert status.trusted, status.summary()
    # The preparer certified revision 1; the approver's covers the whole file.
    assert statuses[0].coverage == SignatureCoverageLevel.ENTIRE_REVISION
    assert statuses[1].coverage == SignatureCoverageLevel.ENTIRE_FILE

    # The detached attestation verifies too, against the certificate on the row.
    for signature in (preparer, approver):
        key = db_session.scalar(
            select(SignerKey).where(SignerKey.signer_id == signature.signer_id)
        )
        assert key is not None
        public_key = x509.load_pem_x509_certificate(
            key.certificate_pem.encode("ascii")
        ).public_key()
        assert isinstance(public_key, ec.EllipticCurvePublicKey)
        public_key.verify(
            signature.signature_value,
            bytes.fromhex(signature.payload_digest),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )


@pytest.mark.usefixtures("openbao_env")
def test_a_filed_return_anchors_on_the_institutional_root(
    db_session: Session, storage: InMemoryStorageClient, bao: BaoServer
) -> None:
    """The half of this phase an examiner actually sees.

    Phase 1's certificates were self-signed, so trust could only ever be
    anchored on the chain the signature itself carried — ``_trust`` in the
    artifact suite anchors on each officer's OWN certificate, which is what a
    self-signed enrolment buys and no more. Here the validation context is built
    from ONE certificate the platform never signed anything with: the root the
    bootstrap wrote to ``ATTESTATION_TRUST_ROOTS``. Nothing about the officers is
    given to the verifier.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")
    approver = _certify(
        db_session, CHECKER, package, role="approver", display_name="Ama Mensah"
    )

    institutional = ValidationContext(
        trust_roots=[
            asn1_x509.Certificate.load(
                bao.root_certificate.public_bytes(serialization.Encoding.DER)
            )
        ],
        allow_fetching=False,
        revocation_mode="soft-fail",
    )
    final = _bytes(db_session, storage, _version(db_session, approver))
    embedded = PdfFileReader(io.BytesIO(final)).embedded_regular_signatures
    for status in (
        validate_pdf_signature(sig, signer_validation_context=institutional)
        for sig in embedded
    ):
        assert status.intact, status.summary()
        assert status.valid, status.summary()
        assert status.trusted, status.summary()

    # And the platform's own report says the anchoring was institutional rather
    # than the record vouching for itself — the distinction §3.5 reports and
    # which must never quietly read the other way.
    report = verify.verify_attestation(
        db_session,
        MAKER,
        package,
        storage=storage,
        trust_roots=get_settings().attestation.load_trust_roots(),
    )
    checks = {check["check"]: check for check in report["checks"]}
    pdf_check = checks[verify.CHECK_PDF_SIGNATURE]
    assert pdf_check["evidence"]["status"] == verify.STATUS_PASSED
    assert {
        finding["trust_anchor"] for finding in pdf_check["evidence"]["signatures_examined"]
    } == {"configured"}
    detached = checks[verify.CHECK_DETACHED_ATTESTATION]
    assert detached["evidence"]["status"] == verify.STATUS_PASSED
    for finding in detached["evidence"]["signatures"]:
        assert finding["certificate"]["trust_anchor"] == "configured"
        assert finding["certificate"]["trust_anchored"] is True
        assert finding["certificate"]["chain_length"] >= 1
    assert report["overall_passed"]


@pytest.mark.usefixtures("openbao_env")
def test_the_pades_ladder_now_reaches_b_lta(bao: BaoServer) -> None:
    """The profile step-down was never the limitation; the CA was.

    ``artifact_signing._profile`` steps down to B-B without an RFC 3161
    authority and to B-T without trust roots, because embedded validation
    material needs something to build against. Under phase 1 the top rung was
    unreachable in principle: per-officer self-signed certificates give an
    operator no institutional root to configure, and carry no CRL or OCSP
    endpoint for LTV material to be collected from. With a real CA both
    disappear, so the ladder is left exactly as it is and simply reaches
    further — and the appearance still prints only what was delivered.
    """
    from pyhanko.sign.timestamps import TimeStamper  # noqa: PLC0415

    settings = get_settings()
    assert settings.attestation.load_trust_roots()

    _profile, name = artifact_signing._profile(settings, None)
    assert name == "pades_b_b"  # no trusted time: nothing to build LTV around

    profile, name = artifact_signing._profile(settings, TimeStamper())
    assert name == "pades_b_lta"
    assert profile.use_pades_lta and profile.embed_validation_info
    assert profile.validation_context is not None

    # …and without an anchor it is honestly B-T, which is what phase 1 could
    # reach at best.
    unanchored = settings.model_copy(
        update={"attestation": settings.attestation.model_copy(update={"trust_roots_path": None})}
    )
    _profile, name = artifact_signing._profile(unanchored, TimeStamper())
    assert name == "pades_b_t"


@pytest.mark.usefixtures("openbao_env")
def test_an_issued_certificate_has_revocation_material_to_embed(
    db_session: Session, bao: BaoServer
) -> None:
    """B-LTA is only worth reaching if the CA publishes something to collect.

    ``_validation_context`` turns fetching ON at signing time precisely so the
    CRL/OCSP responses are gathered once and never needed again. This proves
    they resolve: a full revocation-checked path validation, over the network,
    against the institutional root alone.
    """
    from pyhanko_certvalidator import CertificateValidator  # noqa: PLC0415

    from app.services.attestation.identity import ensure_signer_identity  # noqa: PLC0415

    assert MAKER.actor_user_id is not None
    _seed(db_session)
    identity = ensure_signer_identity(db_session, MAKER, MAKER.actor_user_id)
    issued = SignerKeyService(db_session, MAKER).issue(
        signer_id=identity.signer_id, display_name="Kwesi Owusu"
    )

    context = ValidationContext(
        trust_roots=[
            asn1_x509.Certificate.load(
                bao.root_certificate.public_bytes(serialization.Encoding.DER)
            )
        ],
        allow_fetching=True,
        revocation_mode="hard-fail",
    )
    validator = CertificateValidator(
        asn1_x509.Certificate.load(
            issued.certificate.public_bytes(serialization.Encoding.DER)
        ),
        intermediate_certs=[
            asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))
            for certificate in x509.load_pem_x509_certificates(
                str(issued.record.certificate_chain_pem).encode("ascii")
            )
        ],
        validation_context=context,
    )
    path = asyncio.run(validator.async_validate_usage({"non_repudiation"}))

    assert len(path) >= 2  # leaf → … → root


@pytest.mark.usefixtures("openbao_env")
def test_the_recorded_signature_method_never_claims_pss_for_an_ecdsa_key(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Verification reads the recorded method; a wrong one is silent nonsense."""
    package = _seed(db_session)
    signature = _certify(
        db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu"
    )

    key = db_session.scalar(
        select(SignerKey).where(SignerKey.signer_id == signature.signer_id)
    )
    assert key is not None
    assert key.algorithm == ECDSA_P256_SHA256
    assert signature.signature_method == "detached_ecdsa_p256_sha256"
    assert "pss" not in signature.signature_method


@pytest.mark.usefixtures("openbao_env")
def test_the_approle_secret_never_reaches_a_log_record_or_an_audit_event(
    db_session: Session, storage: InMemoryStorageClient, bao: BaoServer
) -> None:
    """The credential is a credential. This is the executable form of that.

    A full ceremony is driven with logging captured, then EVERY column of EVERY
    table is swept — the risk is not a ``secret_id`` column, it is the value
    ending up in an audit ``details`` blob or a notification payload.
    """
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="TRACE")
    try:
        package = _seed(db_session)
        _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")
        _certify(db_session, CHECKER, package, role="approver", display_name="Ama Mensah")
        db_session.flush()
    finally:
        logger.remove(sink_id)

    for record in records:
        assert bao.secret_id not in record
    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).mappings():
            for column, value in row.items():
                assert bao.secret_id not in str(value), (
                    f"the AppRole secret id leaked into {table.name}.{column}"
                )
    assert db_session.scalar(select(AttestationSignature.id)) is not None


@pytest.mark.usefixtures("openbao_env")
def test_a_failure_message_names_the_key_but_never_the_credential(
    bao: BaoServer,
) -> None:
    """Every typed error is read by an operator; none may quote the secret."""
    signer = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id=bao.secret_id
    )
    absent = _fresh_key_ref()

    with pytest.raises(SignerKeyMaterialMissing) as missing:
        signer.sign_digest(DIGEST, key_ref=absent)
    assert absent in str(missing.value)
    assert bao.secret_id not in str(missing.value)

    wrong = OpenBaoTransitRawSigner(
        address=bao.address, role_id=bao.role_id, secret_id="not-the-real-secret-id"
    )
    with pytest.raises(SignerBackendForbidden) as forbidden:
        wrong.sign_digest(DIGEST, key_ref=absent)
    assert "not-the-real-secret-id" not in str(forbidden.value)
