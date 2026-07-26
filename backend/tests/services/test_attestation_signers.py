"""Signing backends and the signer-key lifecycle
(app/services/attestation/signers.py, keys.py — docs §3.3, §3.4).

Three things are load-bearing here and each has a test that fails loudly if it
regresses: a signature must verify against the certificate we stored, the
software backend must be impossible in production, and private key material
must never reach the database.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509.oid import NameOID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import DEFAULT_SIGNING_KEY_DIR, get_settings
from app.db.base import Base
from app.models import AuditEvent, SignerIdentity, SignerKey
from app.models.attestation import SIGNATURE_METHODS
from app.services.attestation.keys import SignerKeyError, SignerKeyService
from app.services.attestation.signers import (
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
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

VAULT_KEY = "test-vault-master-key-not-for-production-0001"
SIGNER_ID = "SGN-7K4M9PQR2VWX3YZ8"
OTHER_SIGNER_ID = "SGN-2ABC4DEF6GHJ8KMN"
DIGEST = hashlib.sha256(b"aequoros attestation payload").digest()


@pytest.fixture(autouse=True)
def _signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
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


def _ctx(org_id: str = ORG_1, user_id: Any = USER_1) -> TenantContext:
    return TenantContext(organization_id=org_id, actor_user_id=user_id)


def _seed_identity(db: Session, *, signer_id: str, org_id: str, user_id: Any) -> SignerIdentity:
    identity = SignerIdentity(
        organization_id=org_id, user_id=user_id, signer_id=signer_id, derivation_version=1
    )
    db.add(identity)
    db.flush()
    return identity


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


# --- round trip: sign a digest, verify it with `cryptography` ---------------


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
    public_key.verify(
        signature, b"aequoros attestation payload", ec.ECDSA(hashes.SHA256())
    )


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


# --- the software backend must be impossible in production -----------------


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
    """"prod" is not in the AppEnv literal, but the guard must still catch it —
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


# --- soft-key store hygiene ------------------------------------------------


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


# --- PKCS#11 stays optional; KMS stays honest ------------------------------


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


# --- key lifecycle: issue / rotate / revoke -------------------------------


def test_issue_records_custody_metadata_and_audits(db_session: Session) -> None:
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)

    issued = service.issue(
        signer_id=SIGNER_ID, display_name="Ama Mensah", organization_name="Sample Bank"
    )

    record = issued.record
    assert record.organization_id == ORG_1
    assert record.signer_id == SIGNER_ID
    assert record.backend == "software"
    assert record.algorithm == ECDSA_P256_SHA256
    assert record.status == "active"
    assert record.created_by == USER_1
    # The certificate, its thumbprint and its validity window are all recorded,
    # so verification never has to re-fetch anything.
    assert "BEGIN CERTIFICATE" in record.certificate_pem
    assert record.certificate_sha256 == issued.certificate.fingerprint(hashes.SHA256()).hex()
    assert record.not_before == issued.certificate.not_valid_before_utc
    assert record.not_after == issued.certificate.not_valid_after_utc

    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "signer_key.issued")
    )
    assert event is not None
    assert event.details["signer_id"] == SIGNER_ID
    assert event.details["signature_method"] == "detached_ecdsa_p256_sha256"


def test_the_stored_certificate_verifies_a_signature_from_the_stored_key_ref(
    db_session: Session,
) -> None:
    """End-to-end binding: the DB row's certificate and its key_ref agree.

    This is the property the certification path depends on — it signs with
    ``key.key_ref`` and files ``key.certificate_pem`` as the verification
    material. If those ever drifted apart, every signature would be
    unverifiable and nothing else would notice.
    """
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)
    record = service.issue(signer_id=SIGNER_ID, display_name="Ama Mensah").record

    signature = service.signer.sign_digest(DIGEST, key_ref=record.key_ref)

    certificate = x509.load_pem_x509_certificate(record.certificate_pem.encode("ascii"))
    public_key = certificate.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    public_key.verify(signature, DIGEST, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


def test_a_second_active_key_is_refused(db_session: Session) -> None:
    """Two active keys would make "which key was in force?" ambiguous."""
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)
    service.issue(signer_id=SIGNER_ID)

    with pytest.raises(SignerKeyError, match="already holds an active key"):
        service.issue(signer_id=SIGNER_ID)


def test_rotation_retains_the_predecessor(db_session: Session) -> None:
    """Signatures embed the certificate they were made under, so history must
    survive rotation untouched (§3.4)."""
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)
    first = service.issue(signer_id=SIGNER_ID).record
    first_id, first_ref = first.id, first.key_ref

    second = service.rotate(signer_id=SIGNER_ID, reason="Annual key rotation").record

    assert second.id != first_id
    assert second.key_ref != first_ref  # a retired label is never reused
    assert second.status == "active"
    assert first.status == "rotated"
    assert first.rotated_at is not None
    assert service.active_key(SIGNER_ID) is not None
    assert service.active_key(SIGNER_ID).id == second.id  # type: ignore[union-attr]
    assert len(service.list_keys(signer_id=SIGNER_ID)) == 2
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "signer_key.rotated")
    )
    assert event is not None
    assert event.details["previous_key_id"] == str(first_id)


def test_rotation_requires_a_reason_and_an_existing_key(db_session: Session) -> None:
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)

    with pytest.raises(SignerKeyError, match="reason is required"):
        service.rotate(signer_id=SIGNER_ID, reason="  ")
    with pytest.raises(SignerKeyError, match="no active key"):
        service.rotate(signer_id=SIGNER_ID, reason="Compromise suspected")


def test_revoke_records_the_reason_and_stops_selection(db_session: Session) -> None:
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)
    record = service.issue(signer_id=SIGNER_ID).record

    revoked = service.revoke(key_id=record.id, reason="Signer deprovisioned")

    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    assert revoked.revocation_reason == "Signer deprovisioned"
    # The signing path selects only 'active' keys, so revocation ends future use
    # while every past signature keeps its embedded certificate and timestamp.
    assert service.active_key(SIGNER_ID) is None
    with pytest.raises(SignerKeyError, match="already revoked"):
        service.revoke(key_id=record.id, reason="Again")
    assert (
        db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "signer_key.revoked")
        )
        is not None
    )


def test_revoke_requires_a_reason(db_session: Session) -> None:
    ctx = _ctx()
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, ctx)
    record = service.issue(signer_id=SIGNER_ID).record

    with pytest.raises(SignerKeyError, match="reason is required"):
        service.revoke(key_id=record.id, reason="")


def test_a_key_cannot_be_enrolled_for_an_unknown_signer(db_session: Session) -> None:
    service = SignerKeyService(db_session, _ctx())

    with pytest.raises(SignerKeyError, match="No signer identity"):
        service.issue(signer_id="SGN-NOSUCHSIGNER0001")


def test_key_reads_and_revocation_are_tenant_scoped(db_session: Session) -> None:
    """Signer ids are globally unique; the key register is still per-tenant."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    _seed_identity(db_session, signer_id=OTHER_SIGNER_ID, org_id=ORG_2, user_id=USER_2)
    own = SignerKeyService(db_session, _ctx())
    other = SignerKeyService(db_session, _ctx(org_id=ORG_2, user_id=USER_2))
    record = own.issue(signer_id=SIGNER_ID).record

    assert other.list_keys() == []
    assert other.active_key(SIGNER_ID) is None
    with pytest.raises(SignerKeyError, match="No signer key"):
        other.revoke(key_id=record.id, reason="Cross-tenant attempt")
    # An identity in another tenant is not enrollable from here either.
    with pytest.raises(SignerKeyError, match="No signer identity"):
        own.issue(signer_id=OTHER_SIGNER_ID)


def test_revoking_an_unknown_key_is_refused(db_session: Session) -> None:
    service = SignerKeyService(db_session, _ctx())

    with pytest.raises(SignerKeyError, match="No signer key"):
        service.revoke(key_id=uuid4(), reason="Nonexistent")


# --- external (HSM/CA) enrolment -----------------------------------------


def _ca_issued_certificate() -> tuple[str, str]:
    """A certificate issued from a CSR, as a bank's CA would return it."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = signer_subject(
        signer_id=SIGNER_ID, display_name="Kofi Boateng", organization_name="Sample Bank"
    )
    valid_from, valid_to = default_validity(days=90)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sample Bank CA")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(valid_to)
        .sign(key, hashes.SHA256())
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"), private_pem


def test_external_enrolment_records_the_ca_certificate(db_session: Session) -> None:
    """The production shape: the key stays in the HSM, we file the certificate."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, _ctx())
    certificate_pem, _ = _ca_issued_certificate()

    issued = service.issue(
        signer_id=SIGNER_ID, certificate_pem=certificate_pem, key_ref="hsm_signer_key_01"
    )

    assert issued.record.key_ref == "hsm_signer_key_01"
    assert issued.record.certificate_pem == certificate_pem
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "signer_key.issued")
    )
    assert event is not None
    assert event.details["self_signed"] == "False"


def test_external_enrolment_requires_a_key_ref(db_session: Session) -> None:
    """Without a reference we would not know which HSM object to invoke."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, _ctx())
    certificate_pem, _ = _ca_issued_certificate()

    with pytest.raises(SignerKeyError, match="key_ref is required"):
        service.issue(signer_id=SIGNER_ID, certificate_pem=certificate_pem)


def test_the_recorded_backend_is_the_one_that_actually_held_the_key(
    db_session: Session,
) -> None:
    """The row must not claim software custody for an HSM-held key, or vice versa."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(
        db_session, _ctx(), signer=Pkcs11RawSigner(module_path="/opt/vendor/pkcs11.so")
    )
    certificate_pem, _ = _ca_issued_certificate()

    issued = service.issue(
        signer_id=SIGNER_ID, certificate_pem=certificate_pem, key_ref="hsm_signer_key_04"
    )

    assert issued.record.backend == "pkcs11"


def test_an_unsupported_algorithm_is_refused(db_session: Session) -> None:
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, _ctx())

    with pytest.raises(SignerKeyError, match="Unsupported algorithm"):
        service.issue(signer_id=SIGNER_ID, algorithm="ed25519")


def test_self_signed_enrolment_is_refused_on_a_non_software_backend(
    db_session: Session,
) -> None:
    """We cannot generate a key inside someone else's HSM from here."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(
        db_session,
        _ctx(),
        signer=Pkcs11RawSigner(module_path="/opt/vendor/pkcs11.so"),
    )

    with pytest.raises(SignerKeyError, match="only available on the software backend"):
        service.issue(signer_id=SIGNER_ID)


# --- no private key material in the database ------------------------------


def test_private_key_material_never_reaches_any_database_column(
    db_session: Session, tmp_path: Path
) -> None:
    """§3.4's central invariant, asserted across EVERY table in the schema.

    Sweeping the whole database rather than just ``signer_keys`` is deliberate:
    the risk is not that someone adds a ``private_key`` column here, it is that
    key material leaks into an audit ``details`` blob, a notification payload,
    or a future table nobody thought about.
    """
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, _ctx())
    record = service.issue(signer_id=SIGNER_ID, display_name="Ama Mensah").record
    service.rotate(signer_id=SIGNER_ID, reason="Rotation coverage")
    db_session.flush()

    # The only place the key exists at all: the sealed file on disk.
    sealed = (tmp_path / "signing_keys" / f"{record.key_ref}.key").read_text(encoding="ascii")
    assert sealed  # sanity: the key really was created

    forbidden = ("PRIVATE KEY", "BEGIN EC PRIVATE", sealed, sealed[:64])
    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).mappings():
            for column, value in row.items():
                rendered = str(value)
                for needle in forbidden:
                    assert needle not in rendered, (
                        f"key material leaked into {table.name}.{column}"
                    )


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


def test_a_combined_certificate_and_key_bundle_is_refused(db_session: Session) -> None:
    """The realistic leak path: an operator pastes cert + key together."""
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(db_session, _ctx())
    certificate_pem, private_pem = _ca_issued_certificate()

    with pytest.raises(SignerKeyError, match="contains a private key"):
        service.issue(
            signer_id=SIGNER_ID,
            certificate_pem=certificate_pem + private_pem,
            key_ref="hsm_signer_key_02",
        )
    with pytest.raises(SignerKeyError, match="contains a private key"):
        service.issue(
            signer_id=SIGNER_ID,
            certificate_pem=certificate_pem,
            certificate_chain_pem=private_pem,
            key_ref="hsm_signer_key_03",
        )
    assert service.list_keys() == []


def test_the_default_key_store_is_an_artifacts_path_outside_the_source_tree() -> None:
    """Sealed keys must not sit next to code, where they could be packaged."""
    parts = DEFAULT_SIGNING_KEY_DIR.parts
    assert parts[-2:] == ("artifacts", "signing_keys")
    assert "app" not in parts
