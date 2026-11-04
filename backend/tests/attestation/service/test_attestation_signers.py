"""Signing backends and the signer-key lifecycle
(app/attestation/service/signers.py, keys.py — docs §3.3, §3.4).

Three things are load-bearing here and each has a test that fails loudly if it
regresses: a signature must verify against the certificate we stored, the
software backend must be impossible in production, and private key material
must never reach the database.

This module's other-suite cases live in ``test_attestation_signers_integration.py``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509.oid import NameOID

from app.attestation.models import SIGNATURE_METHODS
from app.attestation.service.signers import (
    ECDSA_P256_SHA256,
    RSA_2048_PSS_SHA256,
    SIGNATURE_METHOD_BY_ALGORITHM,
    KmsRawSigner,
    Pkcs11RawSigner,
    SignerBackendError,
    SignerBackendForbidden,
    SignerBackendUnavailable,
    SignerKeyMaterialMissing,
    SoftwareRawSigner,
    default_validity,
    get_raw_signer,
    new_key_ref,
    signer_subject,
)
from app.core.config import DEFAULT_SIGNING_KEY_DIR, get_settings
from app.models import SignerKey

VAULT_KEY = "test-vault-master-key-not-for-production-0001"
SIGNER_ID = "SGN-7K4M9PQR2VWX3YZ8"
DIGEST = hashlib.sha256(b"aequoros attestation payload").digest()


@pytest.fixture(name="_signing_env", autouse=True)
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A configured vault and a throwaway soft-key store.

    The suite blanks CREDENTIAL_VAULT_MASTER_KEY so a developer's .env cannot
    leak in; the soft-key store needs one, and it must never write into
    backend/artifacts.
    """
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", VAULT_KEY)
    monkeypatch.setenv("SIGNING_SOFTWARE_KEY_DIR", str(tmp_path / "signing_keys"))
    monkeypatch.setenv("SIGNING_BACKEND", "software")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def soft_signer() -> SoftwareRawSigner:
    return SoftwareRawSigner()


def _issued_key(soft_signer: SoftwareRawSigner, *, algorithm: str = ECDSA_P256_SHA256) -> str:
    key_ref = new_key_ref(SIGNER_ID)
    soft_signer.generate_key(key_ref=key_ref, algorithm=algorithm)
    valid_from, valid_to = default_validity(days=30)
    soft_signer.self_sign_certificate(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=SIGNER_ID, display_name="Ama Mensah", organization_name="Sample Bank"
        ),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return key_ref


def test_ecdsa_round_trip_verifies_against_the_issued_certificate(
    soft_signer: SoftwareRawSigner,
) -> None:
    """The default algorithm: ECDSA P-256 / SHA-256 (§3.3)."""
    key_ref = _issued_key(soft_signer)

    signature = soft_signer.sign_digest(DIGEST, key_ref=key_ref)

    certificate = soft_signer.certificate(key_ref=key_ref)
    public_key = certificate.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    # Verifies as a signature over the DIGEST …
    public_key.verify(signature, DIGEST, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    # … and equivalently as a SHA-256 signature over the original message, which
    # is what an independent verifier with only the message would compute.
    public_key.verify(signature, b"aequoros attestation payload", ec.ECDSA(hashes.SHA256()))


def test_a_signature_does_not_verify_over_a_different_digest(
    soft_signer: SoftwareRawSigner,
) -> None:
    key_ref = _issued_key(soft_signer)
    signature = soft_signer.sign_digest(DIGEST, key_ref=key_ref)
    public_key = soft_signer.certificate(key_ref=key_ref).public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)

    with pytest.raises(InvalidSignature):
        public_key.verify(
            signature,
            hashlib.sha256(b"tampered figures").digest(),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )


def test_rsa_pss_round_trip_verifies(soft_signer: SoftwareRawSigner) -> None:
    """RSA-2048/PSS is supported where a bank's HSM or CA requires it (§3.3)."""
    key_ref = _issued_key(soft_signer, algorithm=RSA_2048_PSS_SHA256)

    signature = soft_signer.sign_digest(DIGEST, key_ref=key_ref)

    public_key = soft_signer.certificate(key_ref=key_ref).public_key()
    assert isinstance(public_key, rsa.RSAPublicKey)
    public_key.verify(
        signature,
        DIGEST,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        utils.Prehashed(hashes.SHA256()),
    )


def test_every_algorithm_maps_to_a_recorded_signature_method() -> None:
    """Verification must never guess the scheme (§3.3)."""
    assert set(SIGNATURE_METHOD_BY_ALGORITHM) == {ECDSA_P256_SHA256, RSA_2048_PSS_SHA256}
    assert set(SIGNATURE_METHOD_BY_ALGORITHM.values()) <= set(SIGNATURE_METHODS)


def test_certificate_subject_carries_the_permanent_signer_id(
    soft_signer: SoftwareRawSigner,
) -> None:
    """A third party parses the certificate, not our database (§3.4)."""
    key_ref = _issued_key(soft_signer)

    certificate = soft_signer.certificate(key_ref=key_ref)

    serials = certificate.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    assert [attr.value for attr in serials] == [SIGNER_ID]


def test_csr_is_signed_by_the_key_it_names(soft_signer: SoftwareRawSigner) -> None:
    """The production route: CSR out, CA-issued certificate back (§3.4)."""
    key_ref = _issued_key(soft_signer)

    csr = soft_signer.create_csr(
        key_ref=key_ref,
        subject=signer_subject(
            signer_id=SIGNER_ID, display_name="Ama Mensah", organization_name="Sample Bank"
        ),
    )

    assert csr.is_signature_valid


def test_software_signer_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """G-critical: a key this process can read is not under sole control (L1)."""
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()

    with pytest.raises(SignerBackendForbidden, match="sole.control|SoftwareRawSigner"):
        SoftwareRawSigner()

    # And the factory must not quietly hand back a soft signer either.
    with pytest.raises(SignerBackendForbidden):
        get_raw_signer()


def test_software_signer_is_refused_for_a_bare_prod_env_value() -> None:
    """ "prod" is not in the AppEnv literal, but the guard must still catch it —
    an environment string is exactly the kind of thing that gets shortened."""
    settings = get_settings()
    prod_app = settings.app.model_copy(update={"app_env": "prod"})
    prod_settings = settings.model_copy(update={"app": prod_app})

    with pytest.raises(SignerBackendForbidden):
        SoftwareRawSigner(settings=prod_settings)


def test_software_signer_requires_a_configured_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(SignerBackendUnavailable, match="CREDENTIAL_VAULT_MASTER_KEY"):
        SoftwareRawSigner()


def test_key_ref_cannot_escape_the_key_store(soft_signer: SoftwareRawSigner) -> None:
    """key_ref is a filename here and a PKCS#11 label in production."""
    for hostile in ("../../etc/passwd", "a/b", "", "x" * 200):
        with pytest.raises(ValueError, match="key_ref"):
            soft_signer.sign_digest(DIGEST, key_ref=hostile)


def test_a_sealed_key_cannot_be_reused_under_another_reference(
    soft_signer: SoftwareRawSigner, tmp_path: Path
) -> None:
    """The envelope binds ciphertext to its key_ref, so renaming a file cannot
    make one signer's key sign as another's."""
    key_ref = _issued_key(soft_signer)
    store = tmp_path / "signing_keys"
    impostor = "IMPOSTOR_0000"
    (store / f"{impostor}.key").write_text(
        (store / f"{key_ref}.key").read_text(encoding="ascii"), encoding="ascii"
    )

    with pytest.raises(SignerKeyMaterialMissing, match="bound to a different reference"):
        soft_signer.sign_digest(DIGEST, key_ref=impostor)


def test_generate_refuses_to_overwrite_existing_key_material(
    soft_signer: SoftwareRawSigner,
) -> None:
    key_ref = _issued_key(soft_signer)

    with pytest.raises(SignerBackendError, match="already exists"):
        soft_signer.generate_key(key_ref=key_ref)


def test_a_key_sealed_under_a_different_master_key_is_refused(tmp_path: Path) -> None:
    """A rotated or wrong CREDENTIAL_VAULT_MASTER_KEY must be a typed failure,
    not a raw vault exception leaking out of the signing path."""
    key_dir = tmp_path / "signing_keys"
    original = SoftwareRawSigner(key_dir=key_dir, master_key_material=VAULT_KEY)
    key_ref = new_key_ref(SIGNER_ID)
    original.generate_key(key_ref=key_ref)
    stranger = SoftwareRawSigner(key_dir=key_dir, master_key_material="a-different-master-key")

    with pytest.raises(SignerBackendError, match="could not be opened"):
        stranger.sign_digest(DIGEST, key_ref=key_ref)


def test_missing_key_and_certificate_raise_typed_errors(
    soft_signer: SoftwareRawSigner,
) -> None:
    with pytest.raises(SignerKeyMaterialMissing):
        soft_signer.sign_digest(DIGEST, key_ref="NOT_ENROLLED_0001")
    with pytest.raises(SignerKeyMaterialMissing):
        soft_signer.certificate(key_ref="NOT_ENROLLED_0001")


def test_a_hex_digest_is_refused(soft_signer: SoftwareRawSigner) -> None:
    """A hex string would be signed as 64 bytes of ASCII, binding nothing."""
    key_ref = _issued_key(soft_signer)

    with pytest.raises(ValueError, match="32-byte SHA-256 digest"):
        soft_signer.sign_digest(DIGEST.hex().encode("ascii"), key_ref=key_ref)


def test_pkcs11_backend_explains_the_missing_hsm_extra() -> None:
    """python-pkcs11 is intentionally not installed by default."""
    signer = Pkcs11RawSigner(module_path="/opt/vendor/pkcs11.so", token_label="bank-token")

    with pytest.raises(SignerBackendUnavailable, match="hsm"):
        signer.sign_digest(DIGEST, key_ref="SIGNER_KEY_1")
    with pytest.raises(SignerBackendUnavailable, match="uv sync --extra hsm"):
        signer.certificate(key_ref="SIGNER_KEY_1")


def test_pkcs11_backend_requires_a_module_path() -> None:
    with pytest.raises(SignerBackendUnavailable, match="PKCS11_MODULE_PATH"):
        Pkcs11RawSigner(module_path="")


def test_kms_backend_raises_not_implemented_with_the_required_wiring() -> None:
    """A faked KMS backend would make the §3.4 custody claim false."""
    signer = KmsRawSigner(provider="aws", key_id="arn:aws:kms:eu-west-1:1:key/abc")

    with pytest.raises(NotImplementedError, match="asymmetric-sign call over a DIGEST"):
        signer.sign_digest(DIGEST, key_ref="arn:aws:kms:eu-west-1:1:key/abc")
    with pytest.raises(NotImplementedError, match="pkcs11"):
        signer.certificate(key_ref="arn:aws:kms:eu-west-1:1:key/abc")


def test_factory_selects_the_configured_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_raw_signer(), SoftwareRawSigner)

    monkeypatch.setenv("SIGNING_BACKEND", "kms")
    get_settings.cache_clear()
    assert isinstance(get_raw_signer(), KmsRawSigner)

    monkeypatch.setenv("SIGNING_BACKEND", "pkcs11")
    monkeypatch.setenv("PKCS11_MODULE_PATH", "/opt/vendor/pkcs11.so")
    get_settings.cache_clear()
    assert isinstance(get_raw_signer(), Pkcs11RawSigner)


def test_pkcs11_selected_without_a_module_path_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNING_BACKEND", "pkcs11")
    get_settings.cache_clear()

    with pytest.raises(SignerBackendUnavailable):
        get_raw_signer()


def test_an_unknown_backend_is_refused_rather_than_defaulted() -> None:
    """Falling back to software would silently downgrade custody."""
    settings = get_settings()
    broken = settings.model_copy(
        update={"attestation": settings.attestation.model_copy(update={"signing_backend": "hsm?"})}
    )

    with pytest.raises(SignerBackendUnavailable, match="Unknown SIGNING_BACKEND"):
        get_raw_signer(broken)


def test_signer_keys_has_no_column_that_could_hold_key_material() -> None:
    """Pins the schema: a future ``private_key_pem`` column fails here."""
    columns = {column.name for column in SignerKey.__table__.columns}

    assert columns == {
        "id",
        "organization_id",
        "signer_id",
        "backend",
        "key_ref",
        "algorithm",
        "certificate_pem",
        "certificate_sha256",
        "certificate_chain_pem",
        "not_before",
        "not_after",
        "status",
        "rotated_at",
        "revoked_at",
        "revocation_reason",
        "created_by",
        "created_at",
        "updated_at",
    }


def test_the_default_key_store_is_an_artifacts_path_outside_the_source_tree() -> None:
    """Sealed keys must not sit next to code, where they could be packaged."""
    parts = DEFAULT_SIGNING_KEY_DIR.parts
    assert parts[-2:] == ("artifacts", "signing_keys")
    assert "app" not in parts
