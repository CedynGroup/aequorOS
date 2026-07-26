"""The ``RawSigner`` port and its custody backends
(docs/attestation_esignature.md §3.3, §3.4).

`cryptography` has no PKCS#11 backend, and in production the private key must
never enter this process at all. So the raw signing operation sits behind a
deliberately narrow port — one method to sign a digest, one to fetch the
certificate — with four implementations of very different trustworthiness:

===========  ==========================================================
backend      custody
===========  ==========================================================
``openbao``  Production, and the one this platform ships with. Keys live
             in a self-hosted OpenBao Transit mount, created
             non-exportable; we hold an AppRole credential and a key
             name. Implemented in ``attestation.openbao``.
``pkcs11``   Production. Key generated inside the HSM/token with
             ``CKA_SENSITIVE=true`` / ``CKA_EXTRACTABLE=false``; we hold
             a label, never key material. Requires the ``hsm`` extra.
             **Written but never executed** — no token exists to run it
             against, so treat its correctness as unproven.
``kms``      Alternative production backend. **Not built** — the stub
             raises rather than pretend, see :class:`KmsRawSigner`.
``software`` Dev/test ONLY. A sealed key on the application host, which
             the application can read — therefore NOT under the
             signatory's sole control, and refused when ``APP_ENV`` is
             production (§3.3, legal register L1).
===========  ==========================================================

Two invariants hold across all four:

* **No key material in the database.** ``signer_keys`` stores a ``key_ref``,
  the issued certificate and metadata; nothing else. The soft-key store is a
  file tree, deliberately outside the database, so no schema change can ever
  make a private key a queryable column (asserted by
  ``tests/services/test_attestation_signers.py``).
* **The algorithm is recorded, never guessed.** Every key row carries its
  ``algorithm``, which maps to exactly one ``signature_method`` on the
  signature record (:data:`SIGNATURE_METHOD_BY_ALGORITHM`), so verification
  reads the method instead of inferring it from the signature bytes.

Default algorithm is ECDSA P-256 with SHA-256; RSA-2048/PSS is supported for
banks whose HSM or CA requires it (§3.3).

**The PDF bridge.** ``pdf_signing`` needs a pyHanko ``Signer``, which is a
richer object than :class:`RawSigner`: pyHanko drives the CMS construction
itself and asks the backend for raw signatures as it goes. Each backend
therefore also exposes :meth:`pdf_signer`, a CONTEXT MANAGER — the PKCS#11
implementation holds an authenticated token session for the duration of the
ceremony, and a plain factory would have nowhere to close it. A backend that
cannot produce one raises :class:`PdfSignerUnavailable`; there is no ``None``
return and no unsigned fallback, because the alternative to a signed artifact
is an UNSIGNED document filed with the regulator.
"""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.hazmat.primitives.asymmetric.types import (
    CertificateIssuerPrivateKeyTypes,
    PrivateKeyTypes,
)
from cryptography.x509.oid import NameOID
from pyhanko.sign import signers as pyhanko_signers
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.core.config import Settings, get_settings
from app.models.attestation import SIGNATURE_METHODS

#: Algorithm identifiers persisted in ``signer_keys.algorithm``. The strings
#: contain "ecdsa"/"rsa" because the certification path derives the recorded
#: ``signature_method`` from them (see signing.py).
ECDSA_P256_SHA256: Final = "ecdsa_p256_sha256"
RSA_2048_PSS_SHA256: Final = "rsa_2048_pss_sha256"
DEFAULT_ALGORITHM: Final = ECDSA_P256_SHA256

#: algorithm → the ``attestation_signatures.signature_method`` value recorded
#: alongside every detached signature. Verification reads this; it never has to
#: infer a scheme from the signature bytes.
SIGNATURE_METHOD_BY_ALGORITHM: Final[dict[str, str]] = {
    ECDSA_P256_SHA256: "detached_ecdsa_p256_sha256",
    RSA_2048_PSS_SHA256: "detached_rsa_pss_sha256",
}

# Fail at import if the two vocabularies ever drift: a signature_method the
# model's CHECK constraint rejects would surface as an IntegrityError deep in a
# signing transaction, long after the mistake was made.
_UNKNOWN_METHODS = set(SIGNATURE_METHOD_BY_ALGORITHM.values()) - set(SIGNATURE_METHODS)
if _UNKNOWN_METHODS:  # pragma: no cover - guards a code change, not a runtime path
    raise RuntimeError(
        f"SIGNATURE_METHOD_BY_ALGORITHM maps to methods absent from "
        f"app.models.attestation.SIGNATURE_METHODS: {sorted(_UNKNOWN_METHODS)}"
    )

#: Both supported algorithms hash with SHA-256, so a digest that is not 32
#: bytes is a caller error (typically a hex string, or the wrong digest).
_DIGEST_BYTES: Final = 32
#: PSS salt length equal to the digest length (RFC 8017 recommendation).
_PSS_SALT_BYTES: Final = 32
#: The soft-key envelope's "vendor" slot — binds a sealed file to this purpose.
_SOFT_KEY_PURPOSE: Final = "attestation_signer"
#: ``key_ref`` doubles as a filename in the soft-key store, so it is restricted
#: to characters that cannot escape the directory (no dots-dots, no slashes).
_KEY_REF_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
#: Environments where a key the application can read is unacceptable.
_PRODUCTION_ENVS: Final = frozenset({"production", "prod"})


class SignerBackendError(RuntimeError):
    """Base class for signing-backend failures."""


class SignerBackendUnavailable(SignerBackendError):
    """The configured backend cannot operate (missing driver, PIN, or config)."""


class SignerBackendForbidden(SignerBackendError):
    """The backend is refused in this environment — see :class:`SoftwareRawSigner`."""


class SignerKeyMaterialMissing(SignerBackendError):
    """No key or certificate exists for the requested ``key_ref``."""


class PdfSignerUnavailable(SignerBackendError):
    """This custody backend cannot produce a pyHanko ``Signer``.

    Raised rather than returned as ``None`` on purpose. The caller is the
    certification ceremony, and the only thing it could do with a null is skip
    the document — filing an UNSIGNED return while recording a signature that
    claims otherwise. Every failure to build the bridge must therefore stop the
    ceremony.
    """


@runtime_checkable
class RawSigner(Protocol):
    """The narrow signing surface (§3.3).

    ``sign_digest`` receives an ALREADY-HASHED value and produces a signature
    over it; implementations must not hash again. ``certificate`` returns the
    certificate bound to the same key, because a third party parses the
    certificate — not our database — to check who signed.
    """

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes: ...

    def certificate(self, *, key_ref: str) -> x509.Certificate: ...


@runtime_checkable
class PdfSignerBackend(Protocol):
    """A custody backend that can also drive pyHanko's PAdES path (§3.2).

    Separate from :class:`RawSigner` because the two are genuinely different
    capabilities: signing a digest we computed is not the same as handing a
    library the authority to sign whatever it constructs. A backend may
    legitimately have the first and not the second (the KMS stub does), and the
    ceremony must be able to tell the difference before it starts.
    """

    def pdf_signer(
        self,
        *,
        key_ref: str,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> AbstractContextManager[pyhanko_signers.Signer]: ...


def _require_digest(digest: bytes) -> bytes:
    if not isinstance(digest, (bytes, bytearray)):
        raise ValueError("sign_digest expects raw digest bytes, not hex text.")
    if len(digest) != _DIGEST_BYTES:
        raise ValueError(
            f"sign_digest expects a {_DIGEST_BYTES}-byte SHA-256 digest, got "
            f"{len(digest)} bytes — pass the raw digest, not its hex encoding."
        )
    return bytes(digest)


def _require_key_ref(key_ref: str) -> str:
    if not _KEY_REF_PATTERN.match(key_ref):
        raise ValueError(
            "key_ref must be 1-120 characters of [A-Za-z0-9_-]; it is used as a "
            "PKCS#11 label and as a soft-key filename, so path separators and "
            "relative segments are refused."
        )
    return key_ref


def new_key_ref(signer_id: str) -> str:
    """A fresh key reference for a signer.

    Carries the signer id for operator legibility plus random bytes so a
    rotation never reuses a label — an HSM object and its retired predecessor
    must be distinguishable at the token level.
    """
    body = signer_id.replace("-", "_")
    return _require_key_ref(f"{body}_{secrets.token_hex(4)}")


# --- pyHanko interop --------------------------------------------------------


def _asn1(certificate: x509.Certificate) -> asn1_x509.Certificate:
    """``cryptography`` certificate → the asn1crypto object pyHanko speaks."""
    return asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))


def _load_chain(certificate_chain_pem: str | None) -> Sequence[x509.Certificate]:
    if not certificate_chain_pem:
        return ()
    try:
        return x509.load_pem_x509_certificates(certificate_chain_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PdfSignerUnavailable(
            f"The enrolled certificate chain could not be parsed: {exc}. pyHanko embeds "
            "the chain in the signature, so a chain we cannot read would produce a "
            "document a third party cannot validate."
        ) from exc


def _load_certificate(certificate_pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PdfSignerUnavailable(
            f"The enrolled signer certificate could not be parsed: {exc}."
        ) from exc


# --- software backend (dev/test only) ---------------------------------------


class SoftwareRawSigner:
    """Soft-key :class:`RawSigner` — **development and tests only**.

    The private key is sealed with AES-256-GCM under ``CREDENTIAL_VAULT_MASTER_KEY``
    (the house vault primitive, reused rather than re-implemented) and stored as
    a file, never as a database column. Initialisation is refused outright when
    ``APP_ENV`` is production: a key the application process can decrypt is not
    a key under the signatory's sole control, so permitting it in production
    would quietly hollow out the whole Act 772 attribution argument (§3.3;
    legal register L1) — better a loud failure at startup than a signature that
    cannot be defended.

    Deliberately NOT a general-purpose key store: it exists so the attestation
    workflow, the detached-attestation format, and the verifier can be built
    and tested end to end without an HSM.
    """

    def __init__(
        self,
        *,
        key_dir: Path | None = None,
        master_key_material: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        env = str(resolved.app.app_env).strip().lower()
        if env in _PRODUCTION_ENVS:
            raise SignerBackendForbidden(
                "SoftwareRawSigner is refused when APP_ENV="
                f"{resolved.app.app_env!r}. A software key can be read by this "
                "process, so it does not satisfy the sole-control requirement the "
                "attestation design rests on (docs/attestation_esignature.md §3.3). "
                "Configure SIGNING_BACKEND=pkcs11 with an HSM/token."
            )
        material = (
            master_key_material
            if master_key_material is not None
            else resolved.market_data.credential_vault_master_key
        )
        if not material:
            raise SignerBackendUnavailable(
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; the soft-key store "
                "cannot seal or open a signing key."
            )
        self._key = derive_master_key(material)
        self._dir = Path(key_dir) if key_dir is not None else resolved.attestation.software_key_dir

    # -- storage layout ----------------------------------------------------

    def _sealed_path(self, key_ref: str) -> Path:
        return self._dir / f"{_require_key_ref(key_ref)}.key"

    def _cert_path(self, key_ref: str) -> Path:
        return self._dir / f"{_require_key_ref(key_ref)}.crt"

    def _load_private_key(self, key_ref: str) -> PrivateKeyTypes:
        path = self._sealed_path(key_ref)
        if not path.exists():
            raise SignerKeyMaterialMissing(f"No soft key is stored for key_ref {key_ref!r}.")
        try:
            envelope = decrypt_credential_envelope(self._key, path.read_text(encoding="ascii"))
        except CredentialVaultError as exc:
            # Re-typed into this module's hierarchy: a caller catching
            # SignerBackendError must not have to know the vault's exceptions.
            raise SignerBackendError(
                f"Sealed key for {key_ref!r} could not be opened (wrong "
                f"CREDENTIAL_VAULT_MASTER_KEY or a corrupt file): {exc}"
            ) from exc
        # The envelope names the key_ref it was sealed for, so a swapped or
        # renamed file cannot be used to sign under another signer's reference.
        if envelope.get("institution_id") != key_ref or envelope.get("vendor") != _SOFT_KEY_PURPOSE:
            raise SignerKeyMaterialMissing(
                f"Sealed key file for {key_ref!r} is bound to a different reference."
            )
        credentials = envelope.get("credentials") or {}
        encoded = credentials.get("private_key_pkcs8_b64")
        if not isinstance(encoded, str):
            raise SignerKeyMaterialMissing(
                f"Sealed key file for {key_ref!r} carries no private key."
            )
        return serialization.load_der_private_key(base64.b64decode(encoded), password=None)

    # -- provisioning (dev) ------------------------------------------------

    def generate_key(self, *, key_ref: str, algorithm: str = DEFAULT_ALGORITHM) -> str:
        """Generate and seal a fresh keypair. Refuses to overwrite an existing one."""
        _require_key_ref(key_ref)
        if algorithm == ECDSA_P256_SHA256:
            private_key: CertificateIssuerPrivateKeyTypes = ec.generate_private_key(
                ec.SECP256R1()
            )
        elif algorithm == RSA_2048_PSS_SHA256:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            raise ValueError(
                f"Unsupported signing algorithm {algorithm!r}; "
                f"choose one of {sorted(SIGNATURE_METHOD_BY_ALGORITHM)}."
            )
        path = self._sealed_path(key_ref)
        if path.exists():
            raise SignerBackendError(
                f"A soft key already exists for key_ref {key_ref!r}; rotation must "
                "mint a new reference rather than overwrite key material."
            )
        pkcs8 = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        sealed = encrypt_credential_envelope(
            self._key,
            institution_id=key_ref,
            vendor=_SOFT_KEY_PURPOSE,
            credentials={"private_key_pkcs8_b64": base64.b64encode(pkcs8).decode("ascii")},
            issued_at=datetime.now(UTC),
            expires_at=None,
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(sealed, encoding="ascii")
        # Owner-only: the sealed blob is useless without the master key, but a
        # world-readable key file is still an unnecessary invitation.
        path.chmod(0o600)
        return key_ref

    def public_key(self, *, key_ref: str) -> Any:
        return self._load_private_key(key_ref).public_key()

    def create_csr(
        self, *, key_ref: str, subject: x509.Name
    ) -> x509.CertificateSigningRequest:
        """A CSR for a real CA to sign (§3.4 "Certificate").

        The dev path can self-sign instead, but the CSR route is the one a bank
        uses with its own PKI or an accredited provider (legal register L4).
        """
        private_key = self._load_private_key(key_ref)
        if not isinstance(private_key, (ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
            raise SignerBackendError(f"Key {key_ref!r} is not an EC or RSA key.")
        return (
            x509.CertificateSigningRequestBuilder()
            .subject_name(subject)
            .sign(private_key, hashes.SHA256())
        )

    def self_sign_certificate(
        self,
        *,
        key_ref: str,
        subject: x509.Name,
        valid_from: datetime,
        valid_to: datetime,
    ) -> x509.Certificate:
        """Self-signed certificate for the dev/test path only.

        A self-signed certificate chains to nothing, so a signature made under
        it is verifiable but not *trusted* by a third party. Production issues
        the certificate from the bank's PKI or an accredited CA (L4).
        """
        private_key = self._load_private_key(key_ref)
        if not isinstance(private_key, (ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
            raise SignerBackendError(f"Key {key_ref!r} is not an EC or RSA key.")
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(valid_from)
            .not_valid_after(valid_to)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                # Non-repudiation is the point of the key; restrict it to that.
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=True,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(private_key, hashes.SHA256())
        )
        self.store_certificate(key_ref=key_ref, certificate=certificate)
        return certificate

    def store_certificate(self, *, key_ref: str, certificate: x509.Certificate) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cert_path(key_ref).write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )

    # -- RawSigner ---------------------------------------------------------

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes:
        """Sign a pre-computed SHA-256 digest.

        ``Prehashed`` is used on both branches: the caller already hashed the
        canonical payload, and re-hashing would sign the wrong value.
        """
        payload = _require_digest(digest)
        private_key = self._load_private_key(key_ref)
        if isinstance(private_key, ec.EllipticCurvePrivateKey):
            return private_key.sign(
                payload, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
            )
        if isinstance(private_key, rsa.RSAPrivateKey):
            return private_key.sign(
                payload,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=_PSS_SALT_BYTES
                ),
                utils.Prehashed(hashes.SHA256()),
            )
        raise SignerBackendError(
            f"Key {key_ref!r} is a {type(private_key).__name__}; only ECDSA P-256 "
            "and RSA-PSS are supported."
        )

    def certificate(self, *, key_ref: str) -> x509.Certificate:
        path = self._cert_path(key_ref)
        if not path.exists():
            raise SignerKeyMaterialMissing(
                f"No certificate is stored for key_ref {key_ref!r}."
            )
        return x509.load_pem_x509_certificate(path.read_bytes())

    @contextmanager
    def pdf_signer(
        self,
        *,
        key_ref: str,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> Iterator[pyhanko_signers.Signer]:
        """pyHanko ``Signer`` over the sealed soft key — dev/test only.

        The sealed key is opened here and handed straight to pyHanko's
        in-memory signer. On this backend the process can read the key by
        construction — which is exactly why ``__init__`` refuses production —
        but the DER never crosses this method's boundary: the caller receives
        an opaque ``Signer``, not key material, so the same call site works
        unchanged against an HSM.

        ``certificate_pem`` is the certificate ENROLLED on ``signer_keys``, and
        it wins over whatever the soft-key store happens to hold. The detached
        attestation embeds that certificate, so using any other one here would
        produce two artefacts attributed to two different certificates for a
        single act.
        """
        private_key = self._load_private_key(key_ref)
        certificate = (
            _load_certificate(certificate_pem)
            if certificate_pem is not None
            else self.certificate(key_ref=key_ref)
        )
        chain = _load_chain(certificate_chain_pem)
        yield pyhanko_signers.SimpleSigner(
            signing_cert=_asn1(certificate),
            signing_key=asn1_keys.PrivateKeyInfo.load(
                private_key.private_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            ),
            cert_registry=SimpleCertificateStore.from_certs(
                [_asn1(issuer) for issuer in chain]
            ),
            prefer_pss=algorithm == RSA_2048_PSS_SHA256,
        )


# --- PKCS#11 backend (production) ------------------------------------------


class Pkcs11RawSigner:
    """HSM/token :class:`RawSigner` over pyHanko's PKCS#11 support (§3.4).

    The key never leaves the token: we open a session, ask the token to sign a
    digest, and close the session — the same session mechanism pyHanko's
    ``PKCS11Signer`` uses for the PDF path, so one token serves both artefacts.

    ``python-pkcs11`` is an OPTIONAL dependency (``uv sync --extra hsm``) and is
    imported lazily inside the operations, so a deployment with no HSM — and
    the test suite — never pays for a driver it cannot load.

    ``key_ref`` is the PKCS#11 object label for both the private key and its
    certificate. Signature encoding is handled explicitly: a token returns
    ECDSA as raw ``r || s``, which must be DER-encoded before it can appear in
    CMS or be verified by ``cryptography`` (pyHanko does the same conversion).
    """

    def __init__(
        self,
        *,
        module_path: str,
        token_label: str | None = None,
        slot: int | None = None,
        pin_provider: Callable[[], str | None] | None = None,
        user_pin: str | None = None,
    ) -> None:
        if not module_path:
            raise SignerBackendUnavailable(
                "PKCS11_MODULE_PATH is not configured; the HSM backend needs the "
                "path to the vendor PKCS#11 module (.so/.dll)."
            )
        self._module_path = module_path
        self._token_label = token_label
        self._slot = slot
        # A provider is preferred over a stored PIN: §3.4 puts the PIN in the
        # encrypted vault, retrieved per operation and discarded.
        self._pin_provider = pin_provider
        self._user_pin = user_pin

    @staticmethod
    def _load_drivers() -> tuple[Any, Any, Any]:
        """Import the optional PKCS#11 stack, or explain how to install it.

        Returns the driver, pyHanko's PKCS#11 helpers, and the raw-ECDSA→DER
        encoder — a token returns ``r || s``, which is not what CMS or
        ``cryptography`` expect.
        """
        try:
            import pkcs11 as p11  # type: ignore[import-not-found]  # noqa: PLC0415
            from pkcs11.util.ec import (  # type: ignore[import-not-found]  # noqa: PLC0415
                encode_ecdsa_signature,
            )
            from pyhanko.sign import pkcs11 as pyhanko_pkcs11  # noqa: PLC0415
        except ImportError as exc:
            raise SignerBackendUnavailable(
                "The PKCS#11 signing backend requires the optional 'hsm' extra "
                "(python-pkcs11). Install it with `uv sync --extra hsm` in backend/, "
                "and make sure the vendor PKCS#11 module is present on the host."
            ) from exc
        return p11, pyhanko_pkcs11, encode_ecdsa_signature

    def _pin(self) -> str | None:
        if self._pin_provider is not None:
            return self._pin_provider()
        return self._user_pin

    def _open_session(self, pyhanko_pkcs11: Any) -> Any:
        token_criteria = (
            pyhanko_pkcs11.TokenCriteria(label=self._token_label)
            if self._token_label is not None
            else None
        )
        try:
            return pyhanko_pkcs11.open_pkcs11_session(
                self._module_path,
                slot_no=self._slot,
                token_criteria=token_criteria,
                user_pin=self._pin(),
            )
        except Exception as exc:  # driver errors are vendor-specific
            raise SignerBackendUnavailable(
                f"Could not open a PKCS#11 session on {self._module_path!r}: {exc}"
            ) from exc

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes:
        payload = _require_digest(digest)
        _require_key_ref(key_ref)
        p11, pyhanko_pkcs11, encode_ecdsa_signature = self._load_drivers()
        session = self._open_session(pyhanko_pkcs11)
        try:
            key = session.get_key(
                object_class=p11.ObjectClass.PRIVATE_KEY, label=key_ref
            )
            if key.key_type == p11.KeyType.EC:
                # CKM_ECDSA signs a pre-hashed value; the result is raw r||s.
                raw = key.sign(payload, mechanism=p11.Mechanism.ECDSA)
                return bytes(encode_ecdsa_signature(bytes(raw)))
            if key.key_type == p11.KeyType.RSA:
                # CKM_RSA_PKCS_PSS also takes the hash as input (unlike the
                # SHA256_RSA_PKCS_PSS variants, which would hash it again).
                return bytes(
                    key.sign(
                        payload,
                        mechanism=p11.Mechanism.RSA_PKCS_PSS,
                        mechanism_param=(
                            p11.Mechanism.SHA256,
                            p11.MGF.SHA256,
                            _PSS_SALT_BYTES,
                        ),
                    )
                )
            raise SignerBackendError(
                f"PKCS#11 key {key_ref!r} has unsupported type {key.key_type!r}."
            )
        except SignerBackendError:
            raise
        except Exception as exc:
            raise SignerBackendError(
                f"PKCS#11 signing failed for key {key_ref!r}: {exc}"
            ) from exc
        finally:
            session.close()

    def certificate(self, *, key_ref: str) -> x509.Certificate:
        p11, pyhanko_pkcs11, _ = self._load_drivers()
        session = self._open_session(pyhanko_pkcs11)
        try:
            obj = session.get_object(
                object_class=p11.ObjectClass.CERTIFICATE, label=key_ref
            )
            return x509.load_der_x509_certificate(bytes(obj[p11.Attribute.VALUE]))
        except Exception as exc:
            raise SignerKeyMaterialMissing(
                f"No PKCS#11 certificate labelled {key_ref!r} on the token: {exc}"
            ) from exc
        finally:
            session.close()

    @contextmanager
    def pdf_signer(
        self,
        *,
        key_ref: str,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> Iterator[pyhanko_signers.Signer]:
        """pyHanko's own ``PKCS11Signer``, bound to a session we own.

        pyHanko has a first-class PKCS#11 signer, so the token — not this
        process — performs every signature the PAdES construction needs; there
        is deliberately no shim that would pull a key out to sign locally.

        A context manager because the session IS the custody boundary (§3.4):
        it is opened for the ceremony and closed on the way out, including on
        failure, so no path can leave an authenticated token session open after
        the officer's single authorised act.
        """
        _p11, pyhanko_pkcs11, _encode = self._load_drivers()
        session = self._open_session(pyhanko_pkcs11)
        try:
            yield pyhanko_pkcs11.PKCS11Signer(
                session,
                # Both labels are the same object reference; passing them
                # explicitly avoids pyHanko inferring one from the other.
                cert_label=key_ref,
                key_label=key_ref,
                # The enrolled certificate wins over the token's copy for the
                # same reason as the software backend: one act, one certificate.
                signing_cert=(
                    _asn1(_load_certificate(certificate_pem))
                    if certificate_pem is not None
                    else None
                ),
                ca_chain=frozenset(
                    _asn1(issuer) for issuer in _load_chain(certificate_chain_pem)
                ),
                prefer_pss=algorithm == RSA_2048_PSS_SHA256,
            )
        finally:
            session.close()


# --- cloud KMS backend (documented stub) -----------------------------------


class KmsRawSigner:
    """Cloud-KMS :class:`RawSigner` — **deliberately not implemented**.

    Every method raises :class:`NotImplementedError`. Faking this backend would
    be worse than not having it: the design's custody claim (§3.4 — key
    generated with a non-extractable policy, used only through an authorised
    API call) is only true if the wiring actually exists.

    What a real implementation must supply, per provider:

    * **AWS KMS** — ``boto3`` (already a dependency) ``kms:Sign`` with
      ``MessageType='DIGEST'`` and ``SigningAlgorithm='ECDSA_SHA_256'`` or
      ``'RSASSA_PSS_SHA_256'``; the certificate is NOT in KMS, so
      ``kms:GetPublicKey`` plus a CSR signed by the bank's CA is required, and
      the resulting certificate must be stored on the ``signer_keys`` row.
    * **GCP Cloud KMS** — ``google-cloud-kms``
      ``AsymmetricSign`` with a ``digest`` field, key purpose
      ``ASYMMETRIC_SIGN``, protection level ``HSM``.
    * **Azure Key Vault** — ``azure-keyvault-keys`` ``CryptographyClient.sign``
      with ``SignatureAlgorithm.es256`` / ``ps256`` against a managed-HSM key.

    All three additionally need: IAM scoped so only the signing path may invoke
    the key, an audit trail exported to the bank, key rotation mapped onto
    :class:`~app.services.attestation.keys.SignerKeyService`, and a residency
    decision (an offshore KMS is legal register L8). None of that is guesswork
    we should encode on the bank's behalf.
    """

    def __init__(self, *, provider: str | None = None, key_id: str | None = None) -> None:
        self.provider = provider
        self.key_id = key_id

    def _unbuilt(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            f"KmsRawSigner.{operation} is not implemented. SIGNING_BACKEND='kms' "
            f"(provider={self.provider!r}, key_id={self.key_id!r}) requires cloud KMS "
            "wiring that does not exist yet: an asymmetric-sign call over a DIGEST "
            "(AWS kms:Sign MessageType=DIGEST / GCP AsymmetricSign / Azure "
            "CryptographyClient.sign), certificate issuance from the public key via "
            "CSR to the bank's CA, IAM scoped to the signing path, and an Act 843 "
            "cross-border decision (legal register L8). Use SIGNING_BACKEND=pkcs11 "
            "for production signing today."
        )

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes:
        raise self._unbuilt("sign_digest")

    def certificate(self, *, key_ref: str) -> x509.Certificate:
        raise self._unbuilt("certificate")

    def pdf_signer(
        self,
        *,
        key_ref: str,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> AbstractContextManager[pyhanko_signers.Signer]:
        # Not a @contextmanager: the raise must happen when the bridge is asked
        # for, not when it is entered, so an unbuilt backend is reported before
        # a ceremony commits to anything.
        raise self._unbuilt("pdf_signer")


# --- factory ---------------------------------------------------------------


def get_raw_signer(settings: Settings | None = None) -> RawSigner:
    """Build the configured :class:`RawSigner`.

    Raises rather than falling back: a deployment that asked for an HSM and got
    a soft key would produce signatures whose custody story is false.
    """
    resolved = settings if settings is not None else get_settings()
    attestation = resolved.attestation
    backend = str(attestation.signing_backend).strip().lower()
    if backend == "openbao":
        # Imported here, not at module scope: attestation.openbao imports the
        # port and the typed errors from this module, so a top-level import
        # would be circular.
        from app.services.attestation.openbao import build_openbao_signer  # noqa: PLC0415

        return build_openbao_signer(resolved)
    if backend == "pkcs11":
        return Pkcs11RawSigner(
            module_path=attestation.pkcs11_module_path or "",
            token_label=attestation.pkcs11_token_label,
            slot=attestation.pkcs11_slot,
            user_pin=attestation.pkcs11_user_pin,
        )
    if backend == "kms":
        return KmsRawSigner(
            provider=attestation.kms_provider, key_id=attestation.kms_key_id
        )
    if backend == "software":
        return SoftwareRawSigner(settings=resolved)
    raise SignerBackendUnavailable(
        f"Unknown SIGNING_BACKEND={backend!r}; expected 'openbao', 'pkcs11', 'kms' "
        "or 'software'."
    )


@contextmanager
def get_pdf_signer(  # noqa: PLR0913 - the enrolled key row's fields, unpacked
    *,
    key_ref: str,
    certificate_pem: str,
    certificate_chain_pem: str | None = None,
    algorithm: str = DEFAULT_ALGORITHM,
    signer: RawSigner | None = None,
    settings: Settings | None = None,
) -> Iterator[pyhanko_signers.Signer]:
    """The pyHanko ``Signer`` for one enrolled key, for the length of one act.

    Takes the ``signer_keys`` row's values rather than the ORM object, matching
    ``pdf_signing``'s rule that the PDF layer never depends on the database
    schema — the certificate and the key reference are the whole contract.

    Every failure mode ends in :class:`PdfSignerUnavailable`, including the KMS
    stub's ``NotImplementedError``: the caller has exactly one decision to make
    ("can this deployment sign the document?") and should not have to enumerate
    three exception families to make it.
    """
    backend = signer if signer is not None else get_raw_signer(settings)
    if not isinstance(backend, PdfSignerBackend):
        raise PdfSignerUnavailable(
            f"The configured signing backend ({type(backend).__name__}) cannot produce a "
            "pyHanko Signer, so the return PDF cannot be signed. Configure a backend that "
            "can, or relax require_signed_pdf for this return under Regulatory Reporting "
            "→ Settings — an explicit, audited act rather than a silent unsigned filing."
        )
    try:
        context = backend.pdf_signer(
            key_ref=key_ref,
            certificate_pem=certificate_pem,
            certificate_chain_pem=certificate_chain_pem,
            algorithm=algorithm,
        )
    except NotImplementedError as exc:
        raise PdfSignerUnavailable(str(exc)) from exc
    with context as pdf_signer:
        yield pdf_signer


def default_validity(*, days: int = 365) -> tuple[datetime, datetime]:
    """Certificate validity window for the dev/self-signed path."""
    now = datetime.now(UTC)
    return now, now + timedelta(days=days)


def signer_subject(
    *, signer_id: str, display_name: str | None, organization_name: str | None
) -> x509.Name:
    """Certificate subject for a signer (§3.4 "Certificate").

    The permanent, opaque ``SGN-…`` identifier is the stable component: names
    and titles change, and the name may later be redacted under Act 843, while
    the identifier keeps the certificate attributable to a determinate person
    (legal register L10). It is carried in the serialNumber attribute because
    that is the X.520 slot for a subject's registered identifier.
    """
    attributes = [x509.NameAttribute(NameOID.SERIAL_NUMBER, signer_id)]
    if display_name:
        attributes.insert(0, x509.NameAttribute(NameOID.COMMON_NAME, display_name))
    else:
        attributes.insert(0, x509.NameAttribute(NameOID.COMMON_NAME, signer_id))
    if organization_name:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization_name))
    return x509.Name(attributes)
