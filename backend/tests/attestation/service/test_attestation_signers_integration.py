"""Signing backends and the signer-key lifecycle
(app/attestation/service/signers.py, keys.py — docs §3.3, §3.4).

Three things are load-bearing here and each has a test that fails loudly if it
regresses: a signature must verify against the certificate we stored, the
software backend must be impossible in production, and private key material
must never reach the database.

This module's other-suite cases live in ``test_attestation_signers.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.service.keys import SignerKeyError, SignerKeyService
from app.attestation.service.signers import (
    ECDSA_P256_SHA256,
    Pkcs11RawSigner,
    default_validity,
    signer_subject,
)
from app.db.base import Base
from app.models import AuditEvent, SignerIdentity
from tests.attestation.service.test_attestation_signers import DIGEST, SIGNER_ID
from tests.attestation.service.test_attestation_signers import _signing_env as _signing_env
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

OTHER_SIGNER_ID = "SGN-2ABC4DEF6GHJ8KMN"


def _ctx(org_id: str = ORG_1, user_id: Any = USER_1) -> TenantContext:
    return TenantContext(organization_id=org_id, actor_user_id=user_id)


def _seed_identity(db: Session, *, signer_id: str, org_id: str, user_id: Any) -> SignerIdentity:
    identity = SignerIdentity(
        organization_id=org_id, user_id=user_id, signer_id=signer_id, derivation_version=1
    )
    db.add(identity)
    db.flush()
    return identity


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
        db_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "signer_key.revoked"))
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


def test_self_signed_enrolment_is_refused_on_a_backend_that_cannot_generate(
    db_session: Session,
) -> None:
    """We cannot generate a key inside someone else's HSM from here.

    The software store and OpenBao Transit both can — each mints the key in its
    own custody — so the refusal is about backends that hold key material we
    have no way to create, not about "not software".
    """
    _seed_identity(db_session, signer_id=SIGNER_ID, org_id=ORG_1, user_id=USER_1)
    service = SignerKeyService(
        db_session,
        _ctx(),
        signer=Pkcs11RawSigner(module_path="/opt/vendor/pkcs11.so"),
    )

    with pytest.raises(SignerKeyError, match="software and OpenBao backends"):
        service.issue(signer_id=SIGNER_ID)


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
                    assert needle not in rendered, f"key material leaked into {table.name}.{column}"


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
