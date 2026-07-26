"""OpenBao — the production signing backend: Transit signs, PKI certifies
(docs/attestation_esignature.md §3.4).

Until this existed, production could not produce a signature at all:
``SoftwareRawSigner`` refuses to initialise when ``APP_ENV`` is production (a key
the application can decrypt is not sole control), ``Pkcs11RawSigner`` had never
executed because no token exists, and ``KmsRawSigner`` is a documented stub that
raises. Signing is required for every return by default, so "cannot sign" means
"cannot file". This closes that with self-hosted OpenBao (the Linux Foundation's
MPL-2.0 fork of HashiCorp Vault) on a dedicated host.

**What Transit gives, precisely.** A Transit key is generated inside the server
with ``exportable=false`` and ``allow_plaintext_backup=false``, and the API has
no operation that returns the private half — the same property PKCS#11 gets from
``CKA_EXTRACTABLE=false``. This process holds an AppRole credential and a key
NAME; it never holds key material. The custody claim is therefore "a separate,
separately-administered server performs the signature", which is weaker than a
signer-held token (legal register L1 is unchanged by this module) and much
stronger than a soft key on the application host.

**The certificate comes from a CA, not from the key itself.**
:class:`OpenBaoPkiIssuer` submits a CSR to OpenBao's PKI engine and files what
comes back, so the officer's certificate chains to an institutional root an
examiner can be handed once and use forever (L4). There is no self-signing path
left: an earlier revision of this module assembled the certificate locally and
signed it through the Transit key, which made a *verifiable* certificate that
chained to nothing — and a deployment that asked for CA-issued certificates and
silently got those would misstate its trust story exactly the way
``get_raw_signer`` refuses to.

**Proving possession without the key.** A CSR must be signed by the private key,
and Transit will not release it. ``cryptography`` has no external-signing path
for ``CertificateSigningRequestBuilder``, so :meth:`OpenBaoPkiIssuer.build_csr`
assembles the ``CertificationRequestInfo`` with ``asn1crypto``, hashes its DER,
and signs that through the same :meth:`OpenBaoTransitRawSigner.sign_digest` the
detached attestation uses. The assembled request is then parsed back and its
signature checked against the public key before it is submitted — a malformed
CSR that a CA happened to accept would be a latent trap, discovered years later
on a filed return.

**Key naming.** ``aequoros-<organization_id>-<signer_id>-<nonce>`` under the
Transit mount: per-officer, tenant-prefixed so an ACL policy can scope one tenant
(``path "transit/sign/aequoros-OR-XXXXXXXX-*"``) without Enterprise namespaces,
and nonce-suffixed so a rotation never reuses a retired name. The design note
asked for ``aequoros/<org>/<signer>``; Transit's own route refuses it — the key
name is matched by ``^keys/(?P<name>\\w(([\\w-.]+)?\\w)?)$``, which admits no
slashes — so the separator is a hyphen. Trailing-``*`` prefix globs are how
OpenBao ACL paths scope anyway, so the property the slashes were for survives.

**The credential.** ``OPENBAO_SECRET_ID`` is a secret. It appears in exactly one
place — the body of the AppRole login — and nowhere else: not in a log record,
not in an exception message, not in an audit event. ``tests/services/
test_attestation_openbao.py`` asserts that rather than trusting this paragraph.

**Never a fallback.** Every failure raises out of the ``SignerBackendError``
family. A deployment that asked for OpenBao and silently got a soft key would
produce signatures whose custody story is false, which is worse than a filing
that visibly could not proceed.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from asn1crypto import algos as asn1_algos
from asn1crypto import csr as asn1_csr
from asn1crypto import keys as asn1_keys
from asn1crypto import pem as asn1_pem
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from pyhanko.sign import signers as pyhanko_signers
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.core.config import Settings, get_settings
from app.services.attestation.signers import (
    DEFAULT_ALGORITHM,
    ECDSA_P256_SHA256,
    RSA_2048_PSS_SHA256,
    PdfSignerUnavailable,
    SignerBackendError,
    SignerBackendForbidden,
    SignerBackendUnavailable,
    SignerKeyMaterialMissing,
    _asn1,
    _load_certificate,
    _load_chain,
    _require_digest,
    _require_key_ref,
)

#: The stem every key this platform creates carries, so an operator listing the
#: mount can tell our objects from anything else the bank keeps there.
KEY_NAME_PREFIX: Final = "aequoros"

#: algorithm → Transit key type. Both are signature-only key types; Transit
#: refuses ``sign`` on anything else, so a mistyped key fails at creation rather
#: than at an officer's ceremony.
TRANSIT_KEY_TYPES: Final[dict[str, str]] = {
    ECDSA_P256_SHA256: "ecdsa-p256",
    RSA_2048_PSS_SHA256: "rsa-2048",
}
_ALGORITHM_BY_TRANSIT_TYPE: Final[dict[str, str]] = {
    value: key for key, value in TRANSIT_KEY_TYPES.items()
}

#: OpenBao emits HashiCorp's wire prefix verbatim (``vault:v<n>:``) for client
#: compatibility, so the literal stays even though the product does not.
_SIGNATURE_PATTERN: Final = re.compile(r"^vault:v(?P<version>\d+):(?P<value>[A-Za-z0-9+/=]+)$")

#: Transit's hash name for SHA-256. Sent explicitly on every request: with
#: ``prehashed=true`` this is what tells the server how long the input should be,
#: and relying on a server-side default would make the request's meaning depend
#: on the server's version rather than on us.
_TRANSIT_SHA256: Final = "sha2-256"

#: Renew the client token this long before its lease expires. Comfortably longer
#: than one ceremony, so a token cannot expire between the detached attestation
#: and the signature on the document.
_TOKEN_RENEW_MARGIN: Final = timedelta(seconds=60)

#: Placeholder length for pyHanko's dry run, matching what its own PKCS#11 signer
#: allocates. Over-allocating is free (pyHanko pads ``/Contents``); under-
#: allocating fails the signature, so this is deliberately generous.
_DRY_RUN_SIGNATURE_BYTES: Final = 512


def transit_key_stem(*, organization_id: str, signer_id: str) -> str:
    """The tenant-prefixed, per-officer stem an ACL policy globs on."""
    return f"{KEY_NAME_PREFIX}-{organization_id}-{signer_id}"


def new_transit_key_ref(*, organization_id: str, signer_id: str) -> str:
    """A fresh Transit key name for one enrolment.

    The nonce is what keeps a rotation's predecessor distinguishable ON THE
    SERVER, matching ``signers.new_key_ref``'s reason for existing: an operator
    auditing the mount must be able to tell a retired officer key from the
    current one without consulting our database.
    """
    stem = transit_key_stem(organization_id=organization_id, signer_id=signer_id)
    return _require_key_ref(f"{stem}-{secrets.token_hex(4)}")


class _Token:
    """A client token and what we know about its lease."""

    __slots__ = ("expires_at", "renewable", "value")

    def __init__(self, *, value: str, lease_seconds: int, renewable: bool) -> None:
        self.value = value
        self.renewable = renewable
        # A lease of 0 means "never expires" (root-ish tokens in dev mode). Treat
        # that as a far horizon rather than as "already expired", which would
        # re-login on every call.
        self.expires_at = (
            datetime.now(UTC) + timedelta(seconds=lease_seconds)
            if lease_seconds > 0
            else datetime.max.replace(tzinfo=UTC)
        )

    def needs_refresh(self) -> bool:
        return datetime.now(UTC) + _TOKEN_RENEW_MARGIN >= self.expires_at


class OpenBaoTransitRawSigner:
    """``RawSigner`` over OpenBao's Transit engine (§3.3, §3.4).

    ``key_ref`` is the Transit key NAME. Nothing else identifies the key: there
    is no handle to keep alive and no session to hold, which is why this backend
    — unlike PKCS#11 — needs no ceremony-length resource. The context manager on
    :meth:`pdf_signer` exists only to satisfy the shared bridge contract.

    Constructing this does NO network I/O. The platform must boot, and every
    non-signing surface must work, when OpenBao is unreachable; what must fail is
    the signature, and it does.
    """

    def __init__(  # noqa: PLR0913 - one endpoint, its credential, and its TLS
        self,
        *,
        address: str,
        role_id: str,
        secret_id: str,
        transit_mount: str = "transit",
        namespace: str | None = None,
        ca_cert: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not address:
            raise SignerBackendUnavailable(
                "OPENBAO_ADDR is not configured; the OpenBao signing backend needs "
                "the address of the server that holds the officers' keys."
            )
        if not role_id or not secret_id:
            raise SignerBackendUnavailable(
                "OPENBAO_ROLE_ID and OPENBAO_SECRET_ID are not both configured; the "
                "OpenBao signing backend authenticates with an AppRole and cannot "
                "reach the Transit mount without one."
            )
        self._address = address.rstrip("/")
        self._role_id = role_id
        self._secret_id = secret_id
        self._mount = transit_mount.strip("/") or "transit"
        self._namespace = namespace
        self._ca_cert = ca_cert
        self._timeout = timeout_seconds
        self._transport = transport  # test seam (httpx.MockTransport)
        self._token: _Token | None = None
        # Two officers can certify concurrently, and both would otherwise race to
        # log in and overwrite each other's token mid-request.
        self._lock = threading.Lock()
        # A Transit key's TYPE is immutable, so it is cached; its certificate is
        # not, and is re-read every time.
        self._key_types: dict[str, str] = {}

    def __repr__(self) -> str:
        """Address and mount only — a repr must never carry the AppRole secret."""
        return f"<OpenBaoTransitRawSigner {self._address} mount={self._mount!r}>"

    # -- transport ---------------------------------------------------------

    @contextmanager
    def _client(self) -> Iterator[httpx.Client]:
        """One client per call, as the ORASS channel does.

        A pooled long-lived client would be faster, but nothing in the
        ``RawSigner`` port has a close hook to hang its lifetime on, and a leaked
        connection pool per ceremony is a worse trade than a handshake per call.
        """
        headers = {"X-Vault-Namespace": self._namespace} if self._namespace else {}
        client = httpx.Client(
            base_url=f"{self._address}/v1",
            headers=headers,
            timeout=self._timeout,
            # `verify` takes a CA bundle path or True. There is deliberately no
            # way to pass False: the signing channel carries the digests an
            # officer is committing to, and an unverified TLS peer could be
            # anyone.
            verify=self._ca_cert if self._ca_cert else True,
            transport=self._transport,
        )
        try:
            yield client
        finally:
            client.close()

    @staticmethod
    def _errors(response: httpx.Response) -> str:
        """OpenBao's own error text, which never contains our credential.

        The server echoes nothing of the request body; its ``errors`` array is
        the operator's only handle on what it refused, so it is carried through
        rather than replaced with a generic message.
        """
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()[:200] or f"HTTP {response.status_code}"
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            return "; ".join(str(error).strip() for error in errors)
        return f"HTTP {response.status_code}"

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """The response body as JSON, or a typed failure.

        A proxy or captive portal answering 200 with HTML is a real deployment
        state, and it must surface as "OpenBao is unavailable" rather than as a
        bare ``ValueError`` from inside the signing transaction.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise SignerBackendUnavailable(
                f"OpenBao at {self._address} returned a non-JSON response "
                f"(HTTP {response.status_code}); something other than OpenBao is "
                "answering at that address."
            ) from exc
        if not isinstance(payload, dict):
            raise SignerBackendUnavailable(
                f"OpenBao at {self._address} returned an unexpected response shape."
            )
        return payload

    def _send(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> httpx.Response:
        headers = {"X-Vault-Token": token} if token else {}
        try:
            return client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            # Transport failure is a deployment problem, not a signature problem.
            # The URL is safe to name (it is configuration); the exception is not
            # allowed to carry anything from the request body.
            raise SignerBackendUnavailable(
                f"OpenBao at {self._address} could not be reached "
                f"({type(exc).__name__}). No signature was produced."
            ) from exc

    # -- authentication ----------------------------------------------------

    def _login(self, client: httpx.Client) -> _Token:
        """AppRole login. The ONLY place the secret id is ever used."""
        response = self._send(
            client,
            "POST",
            "auth/approle/login",
            json={"role_id": self._role_id, "secret_id": self._secret_id},
        )
        if response.status_code in (400, 403):
            # OpenBao answers 400 "invalid role or secret ID" for a revoked,
            # expired or wrong secret id, and 403 when the role itself is denied.
            # Both are the same operational fact and neither may quote the
            # credential back.
            raise SignerBackendForbidden(
                f"OpenBao rejected the AppRole login for role_id {self._role_id}: "
                f"{self._errors(response)}. Re-issue the secret id and update "
                "OPENBAO_SECRET_ID."
            )
        if response.status_code >= 400:
            raise SignerBackendUnavailable(
                f"OpenBao at {self._address} refused the AppRole login: "
                f"{self._errors(response)}"
            )
        auth = self._payload(response).get("auth") or {}
        token = auth.get("client_token")
        if not isinstance(token, str) or not token:
            raise SignerBackendUnavailable(
                f"OpenBao at {self._address} returned no client token for the AppRole "
                "login; the response did not carry an auth block."
            )
        return _Token(
            value=token,
            lease_seconds=int(auth.get("lease_duration") or 0),
            renewable=bool(auth.get("renewable")),
        )

    def _renew(self, client: httpx.Client, token: _Token) -> _Token | None:
        """Extend the current lease, or ``None`` if the server declined.

        Renewal is preferred over re-login so a deployment whose AppRole limits
        ``secret_id_num_uses`` does not burn a use on every token refresh.
        """
        response = self._send(
            client, "POST", "auth/token/renew-self", json={}, token=token.value
        )
        if response.status_code >= 400:
            return None
        auth = self._payload(response).get("auth") or {}
        return _Token(
            value=token.value,
            lease_seconds=int(auth.get("lease_duration") or 0),
            renewable=bool(auth.get("renewable")),
        )

    def _authenticate(self, client: httpx.Client, *, force: bool = False) -> str:
        with self._lock:
            token = self._token
            if force or token is None:
                token = self._login(client)
            elif token.needs_refresh():
                token = (self._renew(client, token) if token.renewable else None) or self._login(
                    client
                )
            self._token = token
            return token.value

    def _call(
        self, method: str, path: str, *, json: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        """One authenticated Transit call, re-authenticating once on 403.

        A 403 is what a *revoked or expired* token looks like, and it is also
        what a policy denial looks like. Retrying once with a fresh token
        separates them: if the second attempt is also refused, the AppRole
        genuinely lacks the capability and the caller gets
        :class:`SignerBackendForbidden` rather than an infinite retry.
        """
        with self._client() as client:
            token = self._authenticate(client)
            response = self._send(client, method, path, json=json, token=token)
            if response.status_code == 403:
                token = self._authenticate(client, force=True)
                response = self._send(client, method, path, json=json, token=token)
            return response

    # -- Transit paths -----------------------------------------------------

    def _key_path(self, key_ref: str) -> str:
        return f"{self._mount}/keys/{_require_key_ref(key_ref)}"

    def _sign_path(self, key_ref: str) -> str:
        return f"{self._mount}/sign/{_require_key_ref(key_ref)}"

    def _key_metadata(self, key_ref: str) -> dict[str, Any]:
        response = self._call("GET", self._key_path(key_ref))
        if response.status_code == 404:
            raise SignerKeyMaterialMissing(
                f"No OpenBao Transit key named {key_ref!r} exists on the "
                f"{self._mount!r} mount."
            )
        if response.status_code == 403:
            raise SignerBackendForbidden(
                f"The OpenBao AppRole may not read Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao could not describe Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )
        data = self._payload(response).get("data")
        if not isinstance(data, dict):
            raise SignerBackendError(
                f"OpenBao returned no key data for Transit key {key_ref!r}."
            )
        return data

    @staticmethod
    def _latest(data: Mapping[str, Any]) -> dict[str, Any]:
        versions = data.get("keys")
        latest = str(data.get("latest_version") or "")
        if not isinstance(versions, dict) or latest not in versions:
            raise SignerKeyMaterialMissing(
                f"OpenBao Transit key {data.get('name')!r} reports no current version."
            )
        version = versions[latest]
        if not isinstance(version, dict):
            raise SignerKeyMaterialMissing(
                f"OpenBao Transit key {data.get('name')!r} reports an unreadable version."
            )
        return version

    def algorithm(self, key_ref: str) -> str:
        """The algorithm the key ACTUALLY is, read from the server and cached.

        Never inferred from configuration: ``signature_method`` on the signature
        row is derived from the enrolled algorithm, and a row that claimed PSS
        for an ECDSA key would make every verification of it fail for a reason no
        one could see. Public because the CSR's ``AlgorithmIdentifier`` has to
        name the same scheme the signature will be made under.
        """
        cached = self._key_types.get(key_ref)
        if cached is not None:
            return cached
        key_type = str(self._key_metadata(key_ref).get("type") or "")
        algorithm = _ALGORITHM_BY_TRANSIT_TYPE.get(key_type)
        if algorithm is None:
            raise SignerBackendError(
                f"OpenBao Transit key {key_ref!r} is of type {key_type!r}; this "
                f"platform signs with {sorted(TRANSIT_KEY_TYPES.values())} only."
            )
        self._key_types[key_ref] = algorithm
        return algorithm

    # -- provisioning ------------------------------------------------------

    def create_key(self, *, key_ref: str, algorithm: str = DEFAULT_ALGORITHM) -> str:
        """Create a non-exportable signing key for one officer.

        ``exportable`` and ``allow_plaintext_backup`` are sent as ``false``
        explicitly rather than left to the server's defaults: they are the whole
        custody claim, and a future OpenBao whose defaults differed would
        otherwise silently produce an extractable key.
        """
        key_type = TRANSIT_KEY_TYPES.get(algorithm)
        if key_type is None:
            raise SignerBackendError(
                f"Unsupported signing algorithm {algorithm!r}; "
                f"choose one of {sorted(TRANSIT_KEY_TYPES)}."
            )
        response = self._call(
            "POST",
            self._key_path(key_ref),
            json={
                "type": key_type,
                "exportable": False,
                "allow_plaintext_backup": False,
            },
        )
        if response.status_code == 403:
            raise SignerBackendForbidden(
                f"The OpenBao AppRole may not create Transit key {key_ref!r}: "
                f"{self._errors(response)}. Grant 'create'/'update' on "
                f"{self._mount}/keys/{KEY_NAME_PREFIX}-* for this tenant."
            )
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao refused to create Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )
        self._key_types[key_ref] = algorithm
        return key_ref

    def public_key(self, *, key_ref: str) -> ec.EllipticCurvePublicKey | rsa.RSAPublicKey:
        """The public half, as OpenBao reports it for the current key version."""
        version = self._latest(self._key_metadata(key_ref))
        pem = version.get("public_key")
        if not isinstance(pem, str) or not pem.strip():
            raise SignerKeyMaterialMissing(
                f"OpenBao Transit key {key_ref!r} reports no public key."
            )
        try:
            loaded = serialization.load_pem_public_key(pem.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SignerBackendError(
                f"OpenBao returned an unreadable public key for {key_ref!r}: {exc}"
            ) from exc
        if not isinstance(loaded, (ec.EllipticCurvePublicKey, rsa.RSAPublicKey)):
            raise SignerBackendError(
                f"OpenBao Transit key {key_ref!r} carries a "
                f"{type(loaded).__name__}; only ECDSA P-256 and RSA are supported."
            )
        return loaded

    def store_certificate(
        self,
        *,
        key_ref: str,
        certificate: x509.Certificate,
        chain: Sequence[x509.Certificate] = (),
    ) -> None:
        """File the certificate and its issuers against the key ON the server.

        Transit keeps a ``certificate_chain`` per key version, so the certificate
        lives beside the key it belongs to — the same shape PKCS#11 has, where
        the token holds both. Our database keeps its own copy on ``signer_keys``;
        this one is what makes the mount self-describing to an operator who is
        looking at OpenBao and not at us. The issuers travel with it for the same
        reason: an operator holding only the mount can then build the whole path.
        """
        response = self._call(
            "POST",
            f"{self._key_path(key_ref)}/set-certificate",
            json={"certificate_chain": certificates_to_pem([certificate, *chain])},
        )
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao refused the certificate for Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )

    # -- RawSigner ---------------------------------------------------------

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes:
        """Sign a pre-computed SHA-256 digest on the server.

        ``prehashed=true`` because the caller already hashed the canonical
        payload; letting Transit hash again would sign the wrong value.
        ``marshaling_algorithm='asn1'`` because CMS and ``cryptography`` both
        expect a DER-encoded ``(r, s)`` — the alternative, ``jws``, is a
        url-safe raw pair that neither can read.
        """
        payload = _require_digest(digest)
        algorithm = self.algorithm(key_ref)
        body: dict[str, Any] = {
            "input": base64.b64encode(payload).decode("ascii"),
            "prehashed": True,
            "hash_algorithm": _TRANSIT_SHA256,
        }
        if algorithm == RSA_2048_PSS_SHA256:
            # salt_length='hash' pins the salt to the digest length (RFC 8017's
            # recommendation and what the soft backend uses), rather than
            # Transit's 'auto', so both backends emit the same shape.
            body["signature_algorithm"] = "pss"
            body["salt_length"] = "hash"
        else:
            body["marshaling_algorithm"] = "asn1"
        response = self._call("POST", self._sign_path(key_ref), json=body)
        if response.status_code == 403:
            raise SignerBackendForbidden(
                f"The OpenBao AppRole may not sign with Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )
        if response.status_code == 404 or (
            response.status_code == 400 and "not found" in self._errors(response).lower()
        ):
            raise SignerKeyMaterialMissing(
                f"OpenBao holds no Transit signing key named {key_ref!r} on the "
                f"{self._mount!r} mount."
            )
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao refused to sign with Transit key {key_ref!r}: "
                f"{self._errors(response)}"
            )
        data = self._payload(response).get("data") or {}
        return _decode_signature(data.get("signature"), key_ref=key_ref)

    def certificate(self, *, key_ref: str) -> x509.Certificate:
        version = self._latest(self._key_metadata(key_ref))
        chain = version.get("certificate_chain")
        if not isinstance(chain, str) or not chain.strip():
            raise SignerKeyMaterialMissing(
                f"OpenBao Transit key {key_ref!r} carries no certificate chain. A "
                "certificate is filed against the key when it is enrolled; enrol "
                "the key through SignerKeyService rather than creating it by hand."
            )
        try:
            return x509.load_pem_x509_certificates(chain.encode("ascii"))[0]
        except (ValueError, IndexError, UnicodeEncodeError) as exc:
            raise SignerBackendError(
                f"The certificate chain OpenBao holds for {key_ref!r} could not be "
                f"parsed: {exc}"
            ) from exc

    # -- the pyHanko bridge ------------------------------------------------

    @contextmanager
    def pdf_signer(
        self,
        *,
        key_ref: str,
        certificate_pem: str | None = None,
        certificate_chain_pem: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> Iterator[pyhanko_signers.Signer]:
        """A pyHanko ``Signer`` that signs through Transit.

        pyHanko builds the CMS itself and asks the backend for raw signatures as
        it goes, which is exactly the shape ``ExternalSigner`` was written for:
        it holds a certificate and a certificate store and no key material at
        all. The one thing it does NOT do is call anything — its
        ``async_sign_raw`` returns a fixed value, for the interrupted-signing
        flow — so :class:`_TransitPdfSigner` overrides that single method and
        routes it to the same ``sign_digest`` the detached attestation uses. One
        key, one custody boundary, two artefacts.

        A context manager only because the port says so. There is no session to
        hold open here, which is the honest difference between this backend and
        PKCS#11 and is worth not hiding.

        ``certificate_pem`` is the certificate ENROLLED on ``signer_keys`` and it
        wins over the copy Transit holds: the detached attestation embeds the
        enrolled one, and two artefacts for a single act must not be attributed
        to two different certificates.
        """
        certificate = (
            _load_certificate(certificate_pem)
            if certificate_pem is not None
            else self.certificate(key_ref=key_ref)
        )
        if algorithm == RSA_2048_PSS_SHA256 and not isinstance(
            certificate.public_key(), rsa.RSAPublicKey
        ):
            # The recorded method drives verification, so a mechanism that
            # disagreed with the key would produce a document nothing can check.
            raise PdfSignerUnavailable(
                f"Signer key {key_ref!r} is enrolled as {algorithm!r} but its "
                "certificate carries a non-RSA public key; refusing to sign under a "
                "mechanism the key cannot satisfy."
            )
        yield _TransitPdfSigner(
            signer=self,
            key_ref=key_ref,
            signing_cert=_asn1(certificate),
            cert_registry=SimpleCertificateStore.from_certs(
                [_asn1(issuer) for issuer in _load_chain(certificate_chain_pem)]
            ),
            prefer_pss=algorithm == RSA_2048_PSS_SHA256,
        )


class _TransitPdfSigner(pyhanko_signers.ExternalSigner):
    """``ExternalSigner`` whose raw signature comes from Transit.

    pyHanko hands us the DER of the signed attributes plus the digest algorithm
    it wants; we hash and forward. Only SHA-256 is accepted — both enrolled
    algorithms hash with SHA-256 and ``sign_digest`` refuses anything that is not
    a 32-byte digest, so a request for another hash is a mismatch to report, not
    to satisfy quietly.
    """

    def __init__(  # noqa: PLR0913 - ExternalSigner's own surface, plus the route
        self,
        *,
        signer: OpenBaoTransitRawSigner,
        key_ref: str,
        signing_cert: asn1_x509.Certificate,
        cert_registry: SimpleCertificateStore,
        prefer_pss: bool,
    ) -> None:
        super().__init__(
            signing_cert=signing_cert,
            cert_registry=cert_registry,
            # No fixed value: async_sign_raw below produces a real one. The
            # placeholder length only ever reaches the dry run.
            signature_value=_DRY_RUN_SIGNATURE_BYTES,
            prefer_pss=prefer_pss,
        )
        self._signer = signer
        self._key_ref = key_ref

    async def async_sign_raw(
        self, data: bytes, digest_algorithm: str, dry_run: bool = False
    ) -> bytes:
        if dry_run:
            return b"0" * _DRY_RUN_SIGNATURE_BYTES
        normalised = digest_algorithm.replace("-", "").lower()
        if normalised != "sha256":
            raise PdfSignerUnavailable(
                f"pyHanko asked for a {digest_algorithm!r} signature, but the OpenBao "
                "backend signs SHA-256 digests only (both enrolled algorithms are "
                "SHA-256). Refusing rather than signing the wrong digest."
            )
        # A blocking call inside the coroutine, matching pyHanko's own
        # SimpleSigner: the whole certification path is synchronous and runs this
        # loop to completion, so there is no other work to yield to.
        return self._signer.sign_digest(hashlib.sha256(data).digest(), key_ref=self._key_ref)


# --- PKI issuance -----------------------------------------------------------


@dataclass(frozen=True)
class IssuedCertificate:
    """What the CA handed back for one officer's key.

    ``chain`` is the issuers only, ordered leaf-ward (issuing CA first, root
    last) — the order ``verify._verify_chain_links`` walks and the order pyHanko
    embeds. The leaf is deliberately not repeated in it: ``signer_keys`` keeps
    the leaf in ``certificate_pem`` and the issuers in ``certificate_chain_pem``,
    and a chain that also carried the leaf would make the two columns disagree
    about what the certificate is.
    """

    certificate: x509.Certificate
    chain: tuple[x509.Certificate, ...]

    @property
    def chain_pem(self) -> str:
        return certificates_to_pem(self.chain)


class OpenBaoPkiIssuer:
    """Certificate issuance from OpenBao's PKI engine (§3.4, legal register L4).

    Composed with :class:`OpenBaoTransitRawSigner` rather than standing beside
    it, because the CSR has to be signed by the key it attests to and that key
    lives behind exactly one door. Composition also means one AppRole login, one
    token cache and one TLS configuration for both halves of an enrolment.

    ``pki_mount`` is the **issuing** mount — the intermediate CA. Its ``sign``
    and ``revoke`` paths are what this class uses; the root lives on its own
    mount, offline as far as this process is concerned, and is reached only
    through the chain the issuing mount returns.

    ``role`` is not a formality. Issuing through ``sign/{role}`` rather than
    ``sign-verbatim`` is what makes the CA — not this process — the thing that
    decides key usage, lifetime and which subjects are acceptable. A compromised
    application cannot mint itself a CA certificate or a 40-year officer key,
    because the role forbids both server-side.

    Constructing this does NO network I/O, for the same reason the signer does
    not: the platform must boot when OpenBao is unreachable.
    """

    def __init__(
        self,
        *,
        signer: OpenBaoTransitRawSigner,
        pki_mount: str,
        role: str,
    ) -> None:
        self._signer = signer
        self._mount = pki_mount.strip("/")
        self._role = role.strip("/")
        if not self._mount or not self._role:
            raise SignerBackendUnavailable(
                "OPENBAO_PKI_MOUNT and OPENBAO_PKI_ROLE must both be set; the OpenBao "
                "backend issues every officer certificate from the PKI engine and has "
                "no self-signing fallback."
            )

    def __repr__(self) -> str:
        return f"<OpenBaoPkiIssuer mount={self._mount!r} role={self._role!r}>"

    # -- the CSR -----------------------------------------------------------

    def build_csr(self, *, key_ref: str, subject: x509.Name) -> str:
        """A PEM CSR whose signature was made by the Transit key.

        Assembled by hand because possession must be proved by the private key
        and Transit will not release it: ``cryptography``'s CSR builder can only
        sign with a key object it holds, so the ``CertificationRequestInfo`` is
        built with ``asn1crypto``, its DER hashed, and that digest signed through
        the same custody boundary as everything else.

        The request is parsed back and verified before it is returned. A CSR that
        a CA happens to accept but whose signature does not check would produce a
        certificate whose proof-of-possession is a fiction — the kind of defect
        that surfaces only when someone finally audits a filed return.
        """
        public_key = self._signer.public_key(key_ref=key_ref)
        algorithm = self._signer.algorithm(key_ref)
        public_der = public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        info = asn1_csr.CertificationRequestInfo(
            {
                "version": "v1",
                "subject": asn1_x509.Name.load(subject.public_bytes()),
                "subject_pk_info": asn1_keys.PublicKeyInfo.load(public_der),
                # No extension_request: the role decides key usage and validity,
                # so an attribute asking for them would be either ignored or a
                # request to be granted something the CA is meant to withhold.
                "attributes": [],
            }
        )
        signature_algorithm = _signed_digest_algorithm(algorithm)
        signature = self._signer.sign_digest(
            hashlib.sha256(info.dump()).digest(), key_ref=key_ref
        )
        request = asn1_csr.CertificationRequest(
            {
                "certification_request_info": info,
                "signature_algorithm": signature_algorithm,
                "signature": signature,
            }
        )
        pem = asn1_pem.armor("CERTIFICATE REQUEST", request.dump()).decode("ascii")
        _require_well_formed_csr(pem, public_der=public_der, key_ref=key_ref)
        return pem

    # -- issuance ----------------------------------------------------------

    def issue(
        self,
        *,
        key_ref: str,
        subject: x509.Name,
        signer_id: str,
        ttl_seconds: int,
    ) -> IssuedCertificate:
        """Submit the CSR and file what the CA returns.

        ``common_name`` and ``serial_number`` are sent explicitly rather than
        left to ``use_csr_common_name``: OpenBao's ``sign`` endpoint builds the
        subject from the role and the request, not from the CSR, so a subject we
        merely put in the request would silently not arrive. Both are then
        re-read off the issued certificate — the permanent signer id is the whole
        point of the subject, and a CA that dropped it would leave a certificate
        nobody can tie to a signer record.
        """
        csr_pem = self.build_csr(key_ref=key_ref, subject=subject)
        response = self._signer._call(  # noqa: SLF001 - one endpoint, one token cache
            "POST",
            f"{self._mount}/sign/{self._role}",
            json={
                "csr": csr_pem,
                "common_name": _common_name(subject, fallback=signer_id),
                # RFC 4519 §2.31 subject serialNumber — NOT the certificate's own
                # serial number, which the CA assigns.
                "serial_number": signer_id,
                "ttl": f"{int(ttl_seconds)}s",
                "format": "pem",
            },
        )
        if response.status_code == 403:
            raise SignerBackendForbidden(
                f"The OpenBao AppRole may not issue from {self._mount}/sign/{self._role}: "
                f"{self._signer._errors(response)}. Grant 'update' on that path."  # noqa: SLF001
            )
        if response.status_code == 404:
            raise SignerBackendUnavailable(
                f"OpenBao has no PKI role {self._role!r} on mount {self._mount!r}. Run "
                "scripts/bootstrap_openbao_pki.py against this server — the mount, the "
                "root, the intermediate and the signing role are operational setup that "
                "does not exist by default."
            )
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao refused to issue a certificate for {key_ref!r} from "
                f"{self._mount}/sign/{self._role}: {self._signer._errors(response)}"  # noqa: SLF001
            )
        data = self._signer._payload(response).get("data")  # noqa: SLF001
        if not isinstance(data, dict):
            raise SignerBackendError(
                f"OpenBao returned no certificate data for {key_ref!r}."
            )
        issued = _parse_issued(data, key_ref=key_ref)
        _require_subject_identity(issued.certificate, signer_id=signer_id)
        _require_public_key(issued.certificate, public_der=self._public_der(key_ref))
        self._signer.store_certificate(
            key_ref=key_ref, certificate=issued.certificate, chain=issued.chain
        )
        return issued

    def _public_der(self, key_ref: str) -> bytes:
        return self._signer.public_key(key_ref=key_ref).public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    # -- revocation --------------------------------------------------------

    def revoke(self, *, certificate: x509.Certificate) -> str:
        """Put the certificate on the CA's CRL. Returns what actually happened.

        A revocation that updates a database row and leaves the certificate
        verifying is theatre: the question an auditor asks about a departed
        officer is whether their certificate still checks out, and only the CA
        can answer no. So this is called inside the same transaction as the row
        change, and anything other than "revoked" or "the CA never issued this"
        raises — the row must not be able to claim more than the CA will confirm.

        ``"unknown_to_ca"`` is the one non-fatal outcome. A certificate this
        mount did not issue (a bank CA's own, enrolled through the external path)
        is revoked at that CA by its own operators, and refusing to record our
        side of a deprovisioning because of it would leave the key selectable.
        """
        serial = _serial_hex(certificate)
        response = self._signer._call(  # noqa: SLF001 - one endpoint, one token cache
            "POST", f"{self._mount}/revoke", json={"serial_number": serial}
        )
        if response.status_code == 403:
            raise SignerBackendForbidden(
                f"The OpenBao AppRole may not revoke on {self._mount}/revoke: "
                f"{self._signer._errors(response)}. Grant 'update' on that path — "  # noqa: SLF001
                "without it a revoked officer's certificate keeps verifying."
            )
        if response.status_code >= 400:
            errors = self._signer._errors(response)  # noqa: SLF001
            if response.status_code == 400 and "not found" in errors.lower():
                return "unknown_to_ca"
            raise SignerBackendError(
                f"OpenBao refused to revoke certificate {serial}: {errors}"
            )
        return "revoked"

    # -- the trust anchor --------------------------------------------------

    def ca_chain(self) -> list[x509.Certificate]:
        """The issuing mount's own chain — its intermediate and that CA's issuers."""
        response = self._signer._call("GET", f"{self._mount}/ca_chain")  # noqa: SLF001
        if response.status_code >= 400:
            raise SignerBackendError(
                f"OpenBao returned no CA chain for mount {self._mount!r}: "
                f"{self._signer._errors(response)}"  # noqa: SLF001
            )
        try:
            chain = x509.load_pem_x509_certificates(response.text.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SignerBackendError(
                f"The CA chain OpenBao holds for mount {self._mount!r} could not be "
                f"parsed: {exc}"
            ) from exc
        if not chain:
            raise SignerBackendUnavailable(
                f"OpenBao mount {self._mount!r} has no CA certificate; the intermediate "
                "has not been generated and signed. Run scripts/bootstrap_openbao_pki.py."
            )
        return chain

    def trust_anchor(self) -> x509.Certificate:
        """The root an operator writes to ``ATTESTATION_TRUST_ROOTS``.

        The self-signed CA in the issuing mount's chain, not the intermediate:
        the intermediate rides in every signature's embedded chain and rotates,
        whereas the anchor is the one thing an examiner should be handed once.
        Shared with ``scripts/bootstrap_openbao_pki.py`` so the file an operator
        configures and the anchor the platform believes in cannot drift apart.
        """
        return self_signed_anchor(self.ca_chain(), source=f"mount {self._mount!r}")


def build_openbao_pki_issuer(
    settings: Settings | None = None, *, signer: OpenBaoTransitRawSigner | None = None
) -> OpenBaoPkiIssuer:
    """The configured issuer. Called by ``keys.SignerKeyService``."""
    resolved = settings if settings is not None else get_settings()
    attestation = resolved.attestation
    return OpenBaoPkiIssuer(
        signer=signer if signer is not None else build_openbao_signer(resolved),
        pki_mount=attestation.openbao_pki_mount,
        role=attestation.openbao_pki_role,
    )


# --- helpers ----------------------------------------------------------------


def certificates_to_pem(certificates: Sequence[x509.Certificate]) -> str:
    """Concatenated PEM, in the order given."""
    return "".join(
        certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
        for certificate in certificates
    )


def self_signed_anchor(
    chain: Sequence[x509.Certificate], *, source: str
) -> x509.Certificate:
    """The self-signed CA in a chain — the only certificate that anchors anything.

    Picked by the property rather than by position: a trust root that was really
    the intermediate would make verification pass on a chain nobody outside this
    deployment can build. An intermediate signed by a root that is NOT in the
    chain (a bank's existing offline CA) has no anchor to offer, and saying so is
    better than writing the wrong certificate into ``ATTESTATION_TRUST_ROOTS``.
    """
    for certificate in reversed(list(chain)):
        if certificate.subject == certificate.issuer:
            return certificate
    raise SignerBackendError(
        f"No self-signed root is present in the CA chain from {source}, so the "
        "trust anchor cannot be determined. The intermediate was signed by a root "
        "held elsewhere — write that root to ATTESTATION_TRUST_ROOTS by hand."
    )


def _common_name(subject: x509.Name, *, fallback: str) -> str:
    """The subject CN, or the signer id when the officer has no display name.

    OpenBao roles set ``require_cn`` by default and an empty common name is
    refused, so the fallback is what keeps enrolment working for a signer whose
    name has been withheld or later redacted (Act 843; legal register L10).
    """
    values = [
        str(attribute.value)
        for attribute in subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    ]
    return values[0] if values and values[0] else fallback


def _signed_digest_algorithm(algorithm: str) -> asn1_algos.SignedDigestAlgorithm:
    """The ``AlgorithmIdentifier`` that describes how the CSR was signed.

    RSA-PSS carries its parameters IN the identifier (RFC 4055): a bare
    ``rsassa_pss`` OID with no parameters means SHA-1 with a 20-byte salt, so a
    request that omitted them would be verified against the wrong scheme and
    rejected — or worse, accepted by a lenient CA. ECDSA needs none; the digest
    is named by the OID itself.
    """
    if algorithm != RSA_2048_PSS_SHA256:
        return asn1_algos.SignedDigestAlgorithm({"algorithm": "sha256_ecdsa"})
    return asn1_algos.SignedDigestAlgorithm(
        {
            "algorithm": "rsassa_pss",
            "parameters": asn1_algos.RSASSAPSSParams(
                {
                    "hash_algorithm": {"algorithm": "sha256"},
                    "mask_gen_algorithm": {
                        "algorithm": "mgf1",
                        "parameters": {"algorithm": "sha256"},
                    },
                    # Salt length equal to the digest length, matching what
                    # sign_digest asks Transit for (salt_length='hash').
                    "salt_length": 32,
                    "trailer_field": "trailer_field_bc",
                }
            ),
        }
    )


def _require_well_formed_csr(pem: str, *, public_der: bytes, key_ref: str) -> None:
    """Parse the assembled request back and check it proves what it claims."""
    try:
        parsed = x509.load_pem_x509_csr(pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SignerBackendError(
            f"The CSR assembled for {key_ref!r} is not a readable "
            f"CertificationRequest: {exc}"
        ) from exc
    if not parsed.is_signature_valid:
        raise SignerBackendError(
            f"The CSR assembled for {key_ref!r} does not verify against its own public "
            "key. Submitting it would ask a CA to certify a proof of possession that "
            "is not one."
        )
    if (
        parsed.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != public_der
    ):
        raise SignerBackendError(
            f"The CSR assembled for {key_ref!r} carries a different public key than "
            "OpenBao holds for that Transit key."
        )


def _parse_issued(data: Mapping[str, Any], *, key_ref: str) -> IssuedCertificate:
    """``data.certificate`` + ``data.ca_chain`` → a leaf and an ordered chain."""
    certificate_pem = data.get("certificate")
    if not isinstance(certificate_pem, str) or not certificate_pem.strip():
        raise SignerBackendError(
            f"OpenBao issued no certificate for {key_ref!r}; the response carried no "
            "certificate field."
        )
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SignerBackendError(
            f"OpenBao returned an unreadable certificate for {key_ref!r}: {exc}"
        ) from exc

    raw_chain = data.get("ca_chain")
    issuers: list[x509.Certificate] = []
    for entry in raw_chain if isinstance(raw_chain, list) else []:
        if not isinstance(entry, str):
            continue
        try:
            issuers.extend(x509.load_pem_x509_certificates(entry.encode("ascii")))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SignerBackendError(
                f"OpenBao returned an unreadable CA chain for {key_ref!r}: {exc}"
            ) from exc
    if not issuers and isinstance(data.get("issuing_ca"), str):
        issuers = x509.load_pem_x509_certificates(str(data["issuing_ca"]).encode("ascii"))

    chain = _order_chain(certificate, issuers)
    if not chain:
        raise SignerBackendError(
            f"OpenBao returned a CA chain that does not issue the certificate it just "
            f"signed for {key_ref!r}. A chain that cannot be built is a chain no third "
            "party can validate, so the enrolment is refused rather than stored."
        )
    return IssuedCertificate(certificate=certificate, chain=tuple(chain))


def _order_chain(
    leaf: x509.Certificate, issuers: Sequence[x509.Certificate]
) -> list[x509.Certificate]:
    """Order the issuers leaf-ward, keeping only those that actually sign.

    Ordered here rather than trusted from the response: ``certificate_chain_pem``
    is walked in order by the verifier and embedded in order by pyHanko, and a
    chain whose order depended on an OpenBao release would break both at some
    future upgrade for a reason nothing in the report would name.
    """
    remaining = list(issuers)
    ordered: list[x509.Certificate] = []
    current = leaf
    while remaining:
        for index, candidate in enumerate(remaining):
            try:
                current.verify_directly_issued_by(candidate)
            except (ValueError, TypeError, InvalidSignature):
                continue
            ordered.append(candidate)
            remaining.pop(index)
            current = candidate
            break
        else:
            # Nothing left issues the current certificate: either the root has
            # been reached (it signs itself and is already in `ordered`) or the
            # response carried certificates from another path.
            break
    return ordered


def _require_subject_identity(certificate: x509.Certificate, *, signer_id: str) -> None:
    """The permanent signer id must be readable FROM THE CERTIFICATE.

    It lives in the subject ``serialNumber`` (X.520 2.5.4.5) — the slot for a
    subject's registered identifier, already used by ``signers.signer_subject``,
    and the one the PKI role can constrain (``allowed_serial_numbers``), so the
    CA itself refuses a subject that is not a platform signer. A SAN
    ``otherName`` would need a private OID no examiner's tooling resolves and a
    role setting that constrains nothing.

    Checked rather than assumed because OpenBao's ``sign`` endpoint composes the
    subject from the role and the request: a role without
    ``allowed_serial_numbers`` silently drops it, and the failure would only show
    up when someone tried to tie a filed certificate back to a signer record.
    """
    values = [
        str(attribute.value)
        for attribute in certificate.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    ]
    if values == [signer_id]:
        return
    raise SignerBackendError(
        f"The certificate OpenBao issued carries subject serialNumber {values!r}, not "
        f"the signer id {signer_id!r}. The signer id must be machine-readable from the "
        "certificate alone; grant the PKI role allowed_serial_numbers=['SGN-*'] (see "
        "scripts/bootstrap_openbao_pki.py) rather than storing a certificate that "
        "cannot be attributed."
    )


def _require_public_key(certificate: x509.Certificate, *, public_der: bytes) -> None:
    """The certificate must certify the key we asked it to certify."""
    issued = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if issued != public_der:
        raise SignerBackendError(
            "The certificate OpenBao issued carries a different public key than the "
            "Transit key it was requested for; every signature made under it would "
            "fail verification."
        )


def _serial_hex(certificate: x509.Certificate) -> str:
    """The colon-separated hex serial OpenBao's PKI store is keyed by.

    OpenBao formats the DER integer's big-endian magnitude bytes, so the value is
    derived from the certificate rather than remembered from the issuance
    response — a key enrolled before this code existed, or restored from a
    backup, must still be revocable.
    """
    magnitude = max((certificate.serial_number.bit_length() + 7) // 8, 1)
    return certificate.serial_number.to_bytes(magnitude, "big").hex(":")


def _decode_signature(value: Any, *, key_ref: str) -> bytes:
    """``vault:v<n>:<base64>`` → raw DER.

    The prefix is not decoration: it names the key version that produced the
    signature. Parsing it strictly (rather than splitting on the last colon)
    means a response shape we do not recognise fails here instead of becoming
    signature bytes nothing can verify.
    """
    if not isinstance(value, str):
        raise SignerBackendError(
            f"OpenBao returned no signature for Transit key {key_ref!r}."
        )
    match = _SIGNATURE_PATTERN.match(value)
    if match is None:
        raise SignerBackendError(
            f"OpenBao returned a signature for {key_ref!r} in an unrecognised format "
            "(expected 'vault:v<n>:<base64>')."
        )
    try:
        return base64.b64decode(match.group("value"), validate=True)
    except (ValueError, TypeError) as exc:
        raise SignerBackendError(
            f"OpenBao returned an undecodable signature for {key_ref!r}: {exc}"
        ) from exc


def build_openbao_signer(settings: Settings | None = None) -> OpenBaoTransitRawSigner:
    """The configured signer. Called by ``signers.get_raw_signer``."""
    attestation = (settings if settings is not None else get_settings()).attestation
    return OpenBaoTransitRawSigner(
        address=attestation.openbao_addr or "",
        role_id=attestation.openbao_role_id or "",
        secret_id=attestation.openbao_secret_id or "",
        transit_mount=attestation.openbao_transit_mount,
        namespace=attestation.openbao_namespace,
        ca_cert=attestation.openbao_ca_cert,
        timeout_seconds=attestation.openbao_timeout_seconds,
    )


__all__ = [
    "KEY_NAME_PREFIX",
    "TRANSIT_KEY_TYPES",
    "IssuedCertificate",
    "OpenBaoPkiIssuer",
    "OpenBaoTransitRawSigner",
    "build_openbao_pki_issuer",
    "build_openbao_signer",
    "certificates_to_pem",
    "self_signed_anchor",
    "new_transit_key_ref",
    "transit_key_stem",
]
