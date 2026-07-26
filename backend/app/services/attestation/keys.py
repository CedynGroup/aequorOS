"""Signer key lifecycle — issue, rotate, revoke (docs/attestation_esignature.md §3.4).

``signer_keys`` is **custody metadata only**: the permanent signer id, a
``key_ref`` (PKCS#11 label / KMS key id / soft-key reference), the issued
certificate and its validity, and a status. There is no column for key material
and there must never be one — :func:`_reject_private_key_material` refuses a
PEM containing a private key even when an operator pastes a combined
certificate+key bundle, which is the realistic way key material would otherwise
end up in the database.

Lifecycle, and why each state exists:

* **issue** — enrol a key for a signer. The dev path generates a keypair in the
  soft-key store and self-signs; the production path takes a certificate the
  bank's CA issued from a CSR over an HSM-resident public key. One active key
  per signer at a time: two active keys would make "which key was in force?"
  ambiguous at verification.
* **rotate** — mint a new key/certificate and mark the predecessor ``rotated``.
  The old row is retained, never deleted: signatures reference the certificate
  by thumbprint and embed the chain, so history stays verifiable across
  rotations (§3.4 "Rotation").
* **revoke** — on deprovisioning or suspected compromise. Past signatures
  survive because each embeds a trusted timestamp proving it predates the
  revocation; revocation invalidates *future* use only, which is precisely why
  the TSA is not optional (§2.6).

Every transition is audited. This module does not commit: the caller owns the
transaction, so an audit row lives or dies with the change it describes
(``app/services/audit.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings, get_settings
from app.models import SignerIdentity, SignerKey
from app.services.attestation.signers import (
    DEFAULT_ALGORITHM,
    SIGNATURE_METHOD_BY_ALGORITHM,
    KmsRawSigner,
    Pkcs11RawSigner,
    RawSigner,
    SignerBackendError,
    SoftwareRawSigner,
    default_validity,
    get_raw_signer,
    new_key_ref,
    signer_subject,
)
from app.services.audit import record_event

#: ``signer_keys.backend`` records the custody that ACTUALLY produced the key,
#: taken from the signer instance rather than from configuration — a row that
#: claims "pkcs11" because the setting said so, while a soft key was used, would
#: misstate the one fact an auditor cares most about.
_BACKEND_BY_SIGNER: dict[type, str] = {
    SoftwareRawSigner: "software",
    Pkcs11RawSigner: "pkcs11",
    KmsRawSigner: "kms",
}


class SignerKeyError(Exception):
    """A signer key could not be issued, rotated or revoked."""


@dataclass(frozen=True)
class IssuedKey:
    """The enrolled key plus the certificate as issued.

    The certificate is returned so an admin surface can show the thumbprint and
    validity window without re-parsing the stored PEM.
    """

    record: SignerKey
    certificate: x509.Certificate


def _reject_private_key_material(pem: str, *, field: str) -> str:
    """Refuse a PEM that carries a private key.

    The database is custody METADATA. An operator pasting a combined
    certificate+key bundle is the realistic path by which key material would
    reach a column, so this is enforced rather than documented.
    """
    if "PRIVATE KEY" in pem.upper():
        raise SignerKeyError(
            f"{field} contains a private key block. signer_keys stores certificates "
            "and metadata only — private key material must never reach the database "
            "(docs/attestation_esignature.md §3.4)."
        )
    return pem


def _certificate_sha256(certificate: x509.Certificate) -> str:
    """The certificate thumbprint: SHA-256 over the DER encoding."""
    return certificate.fingerprint(hashes.SHA256()).hex()


def _certificate_pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _backend_name(signer: RawSigner, settings: Settings) -> str:
    for signer_type, name in _BACKEND_BY_SIGNER.items():
        if isinstance(signer, signer_type):
            return name
    return str(settings.attestation.signing_backend).strip().lower()


class SignerKeyService:
    """Tenant-scoped signer key lifecycle.

    Every query filters ``organization_id``: the key register is per-tenant
    even though signer ids are globally unique, so one bank can never enumerate
    or rotate another's keys.
    """

    def __init__(
        self,
        db: Session,
        ctx: TenantContext,
        *,
        signer: RawSigner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._ctx = ctx
        self._settings = settings if settings is not None else get_settings()
        # Lazily built: constructing a backend can fail (no HSM driver, software
        # backend in production), and reading the key register must not.
        self._signer = signer

    # -- reads -------------------------------------------------------------

    @property
    def signer(self) -> RawSigner:
        if self._signer is None:
            self._signer = get_raw_signer(self._settings)
        return self._signer

    def list_keys(self, *, signer_id: str | None = None) -> Sequence[SignerKey]:
        statement = select(SignerKey).where(
            SignerKey.organization_id == self._ctx.organization_id
        )
        if signer_id is not None:
            statement = statement.where(SignerKey.signer_id == signer_id)
        return self._db.scalars(statement.order_by(SignerKey.created_at.desc())).all()

    def active_key(self, signer_id: str) -> SignerKey | None:
        return self._db.scalar(
            select(SignerKey)
            .where(
                SignerKey.organization_id == self._ctx.organization_id,
                SignerKey.signer_id == signer_id,
                SignerKey.status == "active",
            )
            .order_by(SignerKey.created_at.desc())
        )

    def _require_identity(self, signer_id: str) -> SignerIdentity:
        identity = self._db.scalar(
            select(SignerIdentity).where(
                SignerIdentity.signer_id == signer_id,
                SignerIdentity.organization_id == self._ctx.organization_id,
            )
        )
        if identity is None:
            raise SignerKeyError(
                f"No signer identity {signer_id!r} exists in this organization. A "
                "signer identity is provisioned when platform access is granted "
                "(§2.4) and must exist before a key can be enrolled."
            )
        return identity

    # -- writes ------------------------------------------------------------

    def issue(  # noqa: PLR0913 - enrolment inputs are irreducible
        self,
        *,
        signer_id: str,
        display_name: str | None = None,
        organization_name: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
        validity_days: int = 365,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        key_ref: str | None = None,
    ) -> IssuedKey:
        """Enrol a signing key for ``signer_id``.

        Two modes, and the difference is custody:

        * ``certificate_pem is None`` — **dev/test**: generate a keypair in the
          soft-key store and self-sign it. Only the software backend can do
          this; anything else raises, because we cannot generate a key inside
          someone's HSM from here. A self-signed certificate chains to nothing,
          so it is verifiable but not third-party trusted (legal register L4).
        * ``certificate_pem`` supplied — **production**: the key was generated
          inside the HSM/KMS and the bank's CA signed a CSR over its public
          key. ``key_ref`` is then required, since we did not mint it.

        Refuses when the signer already holds an active key — use
        :meth:`rotate`, so the predecessor is recorded as ``rotated`` rather
        than left as a second, ambiguous "current" key.
        """
        if algorithm not in SIGNATURE_METHOD_BY_ALGORITHM:
            raise SignerKeyError(
                f"Unsupported algorithm {algorithm!r}; choose one of "
                f"{sorted(SIGNATURE_METHOD_BY_ALGORITHM)}. The choice is recorded on "
                "the key so verification never has to guess (§3.3)."
            )
        self._require_identity(signer_id)
        if self.active_key(signer_id) is not None:
            raise SignerKeyError(
                f"Signer {signer_id} already holds an active key. Rotate it instead, "
                "so the predecessor is retained with status 'rotated'."
            )
        return self._enrol(
            signer_id=signer_id,
            display_name=display_name,
            organization_name=organization_name,
            algorithm=algorithm,
            validity_days=validity_days,
            certificate_pem=certificate_pem,
            certificate_chain_pem=certificate_chain_pem,
            key_ref=key_ref,
            event_type="signer_key.issued",
            extra_details={},
        )

    def rotate(  # noqa: PLR0913 - mirrors issue(), plus the mandatory reason
        self,
        *,
        signer_id: str,
        reason: str,
        display_name: str | None = None,
        organization_name: str | None = None,
        algorithm: str | None = None,
        validity_days: int = 365,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        key_ref: str | None = None,
    ) -> IssuedKey:
        """Issue a successor key and mark the predecessor ``rotated``.

        The predecessor row is kept forever: a signature made under it embeds
        its certificate and a trusted timestamp, so rotation must never disturb
        history (§3.4 "Rotation").
        """
        if not reason.strip():
            raise SignerKeyError("A rotation reason is required (house convention).")
        previous = self.active_key(signer_id)
        if previous is None:
            raise SignerKeyError(
                f"Signer {signer_id} holds no active key to rotate; issue one first."
            )
        # Retire first: no flush in this transaction should ever see two active
        # keys for one signer, so "which key is in force?" has one answer at
        # every point — including for a reader on another connection.
        previous_details = {
            "reason": reason,
            "previous_key_id": str(previous.id),
            "previous_certificate_sha256": previous.certificate_sha256,
        }
        previous.status = "rotated"
        previous.rotated_at = datetime.now(UTC)
        self._db.flush()
        return self._enrol(
            signer_id=signer_id,
            display_name=display_name,
            organization_name=organization_name,
            algorithm=algorithm or previous.algorithm,
            validity_days=validity_days,
            certificate_pem=certificate_pem,
            certificate_chain_pem=certificate_chain_pem,
            key_ref=key_ref,
            event_type="signer_key.rotated",
            extra_details=previous_details,
        )

    def revoke(self, *, key_id: UUID, reason: str) -> SignerKey:
        """Revoke a key. Past signatures remain valid; future use does not.

        Revocation at the CA (CRL/OCSP) and disabling the token object are
        operator steps outside this process; this records the platform-side
        fact and stops the signing path from selecting the key.
        """
        if not reason.strip():
            raise SignerKeyError("A revocation reason is required (house convention).")
        key = self._db.scalar(
            select(SignerKey).where(
                SignerKey.id == key_id,
                SignerKey.organization_id == self._ctx.organization_id,
            )
        )
        if key is None:
            raise SignerKeyError(f"No signer key {key_id} in this organization.")
        if key.status == "revoked":
            raise SignerKeyError(f"Signer key {key_id} is already revoked.")
        key.status = "revoked"
        key.revoked_at = datetime.now(UTC)
        key.revocation_reason = reason
        record_event(
            self._db,
            self._ctx,
            event_type="signer_key.revoked",
            entity_type="signer_key",
            entity_id=key.id,
            details={
                "signer_id": key.signer_id,
                "key_ref": key.key_ref,
                "certificate_sha256": key.certificate_sha256,
                "reason": reason,
            },
        )
        self._db.flush()
        return key

    # -- internals ---------------------------------------------------------

    def _enrol(  # noqa: PLR0913 - single write path shared by issue/rotate
        self,
        *,
        signer_id: str,
        display_name: str | None,
        organization_name: str | None,
        algorithm: str,
        validity_days: int,
        certificate_pem: str | None,
        certificate_chain_pem: str | None,
        key_ref: str | None,
        event_type: str,
        extra_details: dict[str, str],
    ) -> IssuedKey:
        if certificate_pem is None:
            resolved_ref, certificate = self._generate_development_key(
                signer_id=signer_id,
                display_name=display_name,
                organization_name=organization_name,
                algorithm=algorithm,
                validity_days=validity_days,
            )
            pem = _certificate_pem(certificate)
            self_signed = True
        else:
            if key_ref is None:
                raise SignerKeyError(
                    "key_ref is required when enrolling an externally issued "
                    "certificate: it identifies the HSM/KMS object holding the "
                    "private key we did not generate."
                )
            pem = _reject_private_key_material(certificate_pem, field="certificate_pem")
            try:
                certificate = x509.load_pem_x509_certificate(pem.encode("ascii"))
            except Exception as exc:
                raise SignerKeyError(f"certificate_pem is not a valid certificate: {exc}") from exc
            resolved_ref = key_ref
            self_signed = False

        chain_pem = (
            _reject_private_key_material(certificate_chain_pem, field="certificate_chain_pem")
            if certificate_chain_pem is not None
            else None
        )

        record = SignerKey(
            organization_id=self._ctx.organization_id,
            signer_id=signer_id,
            backend=_backend_name(self.signer, self._settings),
            key_ref=resolved_ref,
            algorithm=algorithm,
            certificate_pem=pem,
            certificate_sha256=_certificate_sha256(certificate),
            certificate_chain_pem=chain_pem,
            not_before=certificate.not_valid_before_utc,
            not_after=certificate.not_valid_after_utc,
            status="active",
            created_by=self._ctx.actor_user_id,
        )
        self._db.add(record)
        self._db.flush()
        record_event(
            self._db,
            self._ctx,
            event_type=event_type,
            entity_type="signer_key",
            entity_id=record.id,
            details={
                "signer_id": signer_id,
                "key_ref": resolved_ref,
                "backend": record.backend,
                "algorithm": algorithm,
                "certificate_sha256": record.certificate_sha256,
                "self_signed": str(self_signed),
                "signature_method": SIGNATURE_METHOD_BY_ALGORITHM[algorithm],
                **extra_details,
            },
        )
        # The sessionmaker runs with autoflush off, so an audit row added and not
        # flushed is invisible to a subsequent read in the same transaction.
        self._db.flush()
        return IssuedKey(record=record, certificate=certificate)

    def _generate_development_key(
        self,
        *,
        signer_id: str,
        display_name: str | None,
        organization_name: str | None,
        algorithm: str,
        validity_days: int,
    ) -> tuple[str, x509.Certificate]:
        """Generate + self-sign in the soft-key store (dev/test only).

        Note the side effect ordering: the sealed key file is written before the
        database row. If the caller's transaction later rolls back, the file is
        orphaned — harmless, because ``key_ref`` values are never reused and no
        row references it, and far preferable to a committed row pointing at a
        key that was never created.
        """
        signer = self.signer
        if not isinstance(signer, SoftwareRawSigner):
            raise SignerKeyError(
                "Self-signed enrolment is only available on the software backend "
                "(development). For an HSM/KMS, generate the key on the device, have "
                "the CA sign a CSR over its public key, and pass certificate_pem "
                "with the key_ref of the device object."
            )
        key_ref = new_key_ref(signer_id)
        try:
            signer.generate_key(key_ref=key_ref, algorithm=algorithm)
            valid_from, valid_to = default_validity(days=validity_days)
            certificate = signer.self_sign_certificate(
                key_ref=key_ref,
                subject=signer_subject(
                    signer_id=signer_id,
                    display_name=display_name,
                    organization_name=organization_name,
                ),
                valid_from=valid_from,
                valid_to=valid_to,
            )
        except SignerBackendError as exc:
            raise SignerKeyError(f"Key generation failed for {signer_id}: {exc}") from exc
        return key_ref, certificate
