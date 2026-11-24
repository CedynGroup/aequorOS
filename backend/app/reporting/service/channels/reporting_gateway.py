"""AequorOS Reporting Gateway channel — signed filings through the gateway's ``/v1`` API.

The gateway (``gateway/`` in this repository) is AequorOS' own reporting
channel, run by us and configured for the regulator on a subdomain it
controls. Banks never set it up: the channel operator enrols each bank from
the staff console (:mod:`app.reporting.service.gateway_enrolment`), which
issues the bank's API client and creates its filing signature key. Four layers
protect a filing (``gateway/ARCHITECTURE.md``):

1. Mutual TLS with the bank's client certificate.
2. An access token from ``/oauth/token``, obtained with a client assertion signed
   by the client key (``private_key_jwt``) and bound to the certificate.
3. A manifest signature (compact JWS, ES256) made with the bank's own signing
   key (:mod:`app.core.key_management.signing`). The key is decrypted only here,
   for a package the bank's preparer and approver have already approved.
4. The gateway's signed receipt, verified here against its published JWKS before
   the filing is recorded as submitted.

Config (channel ``reporting_gateway``, written by enrolment): ``api_base_url``,
``institution_code``, ``environment``, ``client_id``, ``client_certificate_pem``
(public), ``signing_key`` (``provider`` ``vault``, ``kid``, ``jwk_thumbprint``;
or ``aws_kms`` with ``key_id``, ``region``, ``owner_account``),
``timeout_seconds``; in local/test only, ``forward_client_certificate`` sends the
certificate in the header the gateway's ingress would set.
Credentials (vaulted, never shown): ``client_private_key_pem`` and, for a
``vault`` key, ``signing_private_key_pem``.

The payload is built from the package snapshot by
:func:`app.reporting.service.gateway_catalog.gateway_payload`, in the shape of the
return definition the gateway publishes for that return (exported from the same
AequorOS templates), with every figure an exact decimal string.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.core.key_management.signing import (
    SigningProvider,
    VaultSigningProvider,
    sign_compact_jws,
    signing_provider_for,
)
from app.core.key_management.types import KeyReference, KeyUnavailableError
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service.channels.base import FiledArtifact, SubmissionPollStatus
from app.reporting.service.channels.errors import ChannelError, ChannelPreconditionError
from app.reporting.service.channels.gateway_http import (
    DEFAULT_TIMEOUT_SECONDS,
    GatewayErrors,
    GatewayIdentity,
    GatewayRefusal,
    GatewaySession,
    gateway_base_url,
)
from app.reporting.service.channels.jcs import CanonicalizationError, canonical_json

CHANNEL_CODE = "reporting_gateway"
SOFTWARE_NAME = "AequorOS"
_SUBMITTABLE_STATUSES = ("approved",)
_IDEMPOTENCY_NAMESPACE = uuid.UUID("6f1d2b54-3c0a-4e5e-9a52-6a3c1f0b7d21")

#: Gateway state → the pipeline's poll vocabulary.
_POLL_STATUS: dict[str, SubmissionPollStatus] = {
    "received": "pending",
    "validating": "pending",
    "in_review": "pending",
    "queried": "pending",
    "accepted": "acknowledged",
    "superseded": "acknowledged",
    "rejected": "rejected",
}

#: Bank-safe wording per gateway error code; nothing else from the gateway is shown.
_SAFE_ERRORS: dict[str, str] = {
    "forbidden": (
        "Your bank's filing on the AequorOS Reporting Gateway is suspended; contact support."
    ),
    "certificate_required": (
        "The AequorOS Reporting Gateway did not receive your bank's client certificate."
    ),
    "certificate_mismatch": (
        "Your bank's client certificate does not match its gateway credentials."
    ),
    "invalid_client_assertion": (
        "The AequorOS Reporting Gateway refused your bank's API credentials."
    ),
    "insufficient_scope": "Your bank's gateway credentials do not allow filing returns.",
    "signing_key_unknown": "Your bank's signature key is not registered on the gateway.",
    "signing_key_revoked": "Your bank's signature key has been withdrawn on the gateway.",
    "signature_invalid": "The gateway could not verify your bank's signature on this filing.",
    "manifest_mismatch": "The filing manifest did not match its signature.",
    "payload_digest_mismatch": "The filing content did not match its signed fingerprint.",
    "declaration_digest_mismatch": "The filing declaration did not match its signed fingerprint.",
    "institution_mismatch": "The institution code does not match your bank's gateway credentials.",
    "return_unknown": "The AequorOS Reporting Gateway does not accept this return yet.",
    "return_version_not_applicable": "This return version does not apply to the reporting date.",
    "schema_invalid": "The AequorOS Reporting Gateway rejected the return's structure.",
    "duplicate_submission": (
        "This return is already filed for that date; send a correction instead."
    ),
    "supersedes_not_current": (
        "The return being corrected is no longer the current filed version."
    ),
    "amendment_reason_required": "A correction must state its reason.",
    "idempotency_conflict": (
        "A different filing was already sent with this request; contact support."
    ),
    "rate_limited": "The AequorOS Reporting Gateway is busy; try again shortly.",
}

_BANK_ERRORS = GatewayErrors(
    by_code=_SAFE_ERRORS,
    refused="The AequorOS Reporting Gateway refused the request.",
    unreachable="The AequorOS Reporting Gateway could not be reached.",
    unavailable="The AequorOS Reporting Gateway is unavailable.",
    no_token="The AequorOS Reporting Gateway issued no access token; contact support.",
)

_NOT_ENROLLED = (
    "Your bank is not yet enrolled on the AequorOS Reporting Gateway. "
    "Ask your AequorOS team to enrol it."
)


@dataclass(frozen=True)
class GatewayReceipt:
    """What the channel learned from a successful filing."""

    receipt_id: str
    receipt: dict[str, Any]
    receipt_jws: str
    status: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_es256_jws(token: str, keys: Sequence[Mapping[str, object]]) -> bytes:
    """Verify a compact ES256 JWS against a JWKS; return its payload bytes.

    Raises ``ValueError`` for a malformed token or unknown key and
    ``InvalidSignature`` when the signature does not verify.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWS")
    header = cast(object, json.loads(_b64url_decode(parts[0])))
    if not isinstance(header, dict):
        raise ValueError("JWS header is not an object")
    fields = cast(dict[str, object], header)
    if fields.get("alg") != "ES256":
        raise ValueError("unexpected JWS algorithm")
    kid = fields.get("kid")
    jwk = next((key for key in keys if key.get("kid") == kid), None)
    if jwk is None or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("JWS signed by an unknown key")
    public_key = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_b64url_decode(str(jwk.get("x") or "")), "big"),
        int.from_bytes(_b64url_decode(str(jwk.get("y") or "")), "big"),
        ec.SECP256R1(),
    ).public_key()
    raw = _b64url_decode(parts[2])
    if len(raw) != 64:
        raise ValueError("ES256 signature must be 64 bytes")
    signature = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    public_key.verify(
        signature, f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(hashes.SHA256())
    )
    return _b64url_decode(parts[1])


class ReportingGatewayChannel:
    """The AequorOS Reporting Gateway behind the SubmissionChannel seam."""

    channel_code = CHANNEL_CODE

    def __init__(  # noqa: PLR0913 - one constructor wires the whole filing context
        self,
        *,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
        prior_events: Sequence[Any] = (),
        currency: str | None = None,
        declaration: dict[str, Any] | None = None,
        supersedes_receipt_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
        signer: SigningProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        del prior_events  # regulator-side state is authoritative
        self._config = dict(config or {})
        self._credentials = dict(credentials or {})
        self._currency = currency
        self._declaration = dict(declaration or {})
        self._supersedes_receipt_id = supersedes_receipt_id
        self._transport = transport
        self._signer = signer
        self._clock = clock or (lambda: datetime.now(UTC))
        self.last_detail: dict[str, Any] = {}
        self.last_receipt: GatewayReceipt | None = None
        self._documents: list[tuple[str, str, bytes]] = []

    def carry_documents(self, documents: Sequence[tuple[str, str, bytes]]) -> None:
        """File these ``(name, media_type, content)`` documents with the return.

        Each is listed in the signed manifest by SHA-256 and size, so the bank's
        signature covers the Excel, CSV and PDF as well as the structured payload.
        """
        self._documents = list(documents)

    # -- configuration -------------------------------------------------------

    def _required(self, key: str) -> str:
        value = str(self._config.get(key) or "").strip()
        if not value:
            raise ChannelPreconditionError(_NOT_ENROLLED)
        return value

    @property
    def base_url(self) -> str:
        try:
            return gateway_base_url(
                self._required("api_base_url"), field="reporting gateway address"
            )
        except ChannelPreconditionError as exc:
            raise ChannelPreconditionError(
                f"{exc.operator_message} Ask your AequorOS team to check the gateway enrolment."
            ) from exc

    def _identity(self) -> GatewayIdentity:
        private_key_pem = str(self._credentials.get("client_private_key_pem") or "")
        if not private_key_pem:
            raise ChannelPreconditionError(_NOT_ENROLLED)
        return GatewayIdentity(
            base_url=self.base_url,
            client_id=self._required("client_id"),
            certificate_pem=self._required("client_certificate_pem"),
            private_key_pem=private_key_pem,
            forward_certificate=bool(self._config.get("forward_client_certificate")),
            timeout_seconds=float(
                cast(float, self._config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
            ),
        )

    def _session(self) -> GatewaySession:
        return GatewaySession(
            self._identity(), _BANK_ERRORS, transport=self._transport, clock=self._clock
        )

    def signing_kid(self) -> str:
        raw = cast(Mapping[str, Any], self._config.get("signing_key") or {})
        kid = str(raw.get("kid") or "").strip()
        if not kid:
            raise ChannelPreconditionError(_NOT_ENROLLED)
        return kid

    def _signing_key(self) -> tuple[SigningProvider, KeyReference]:
        """The bank's signing key and who holds it. Decrypted for this filing only."""
        raw = cast(Mapping[str, Any], self._config.get("signing_key") or {})
        kid = self.signing_kid()
        provider = str(raw.get("provider") or "").strip()
        if provider == "vault":
            pem = str(self._credentials.get("signing_private_key_pem") or "")
            if not pem:
                raise ChannelPreconditionError(_NOT_ENROLLED)
            reference = KeyReference("vault", kid, "", "")
            return self._signer or VaultSigningProvider(pem), reference
        key_id = str(raw.get("key_id") or "").strip()
        if not provider or not key_id:
            raise ChannelPreconditionError(_NOT_ENROLLED)
        reference = KeyReference(
            provider, key_id, str(raw.get("region") or ""), str(raw.get("owner_account") or "")
        )
        return self._signer or signing_provider_for(provider), reference

    # -- receipts ------------------------------------------------------------

    def _verify_receipt(
        self, session: GatewaySession, envelope: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> GatewayReceipt:
        receipt = cast(Mapping[str, Any], envelope.get("receipt") or {})
        receipt_jws = str(envelope.get("receipt_jws") or "")
        jwks = session.send("GET", "/v1/.well-known/jwks.json")
        keys = cast(Sequence[Mapping[str, object]], jwks.get("keys") or [])
        try:
            signed = verify_es256_jws(receipt_jws, keys)
        except (ValueError, InvalidSignature) as exc:
            raise ChannelError(
                "The gateway's receipt could not be verified; contact support.",
                internal_detail=type(exc).__name__,
            ) from exc
        if signed != canonical_json(receipt):
            raise ChannelError("The gateway's receipt does not match its signature.")
        expected = {
            "institution_code": manifest["institution_code"],
            "return_code": manifest["return_code"],
            "reporting_date": manifest["reporting_date"],
            "payload_sha256": manifest["payload_sha256"],
            "manifest_sha256": _sha256(canonical_json(manifest)),
        }
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise ChannelError(
                    "The gateway's receipt does not match the filing sent.",
                    internal_detail=f"receipt field {field} differs",
                )
        receipt_id = str(receipt.get("receipt_id") or "")
        if not receipt_id:
            raise ChannelError("The gateway's receipt has no receipt id.")
        return GatewayReceipt(
            receipt_id=receipt_id,
            receipt=dict(receipt),
            receipt_jws=receipt_jws,
            status=str(envelope.get("status") or "received"),
        )

    # -- protocol ------------------------------------------------------------

    def _authorised_at(self, package: RegulatoryPackage) -> datetime:
        """When the bank authorised this filing: its final approval.

        Both the declaration and the manifest carry it, so the filing for one
        package revision is the same content however often it is sent; a retry
        after a timeout is then recognised by the gateway as the same filing.
        """
        raw = self._declaration.get("approved_at")
        if isinstance(raw, datetime):
            moment = raw
        elif isinstance(raw, str) and raw:
            moment = datetime.fromisoformat(raw)
        else:
            moment = cast(datetime | None, getattr(package, "updated_at", None)) or self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).replace(microsecond=0)

    def build_filing(self, package: RegulatoryPackage, return_version: str) -> dict[str, Any]:
        """The signed filing request for ``package`` (no network)."""
        # The catalogue imports this package's errors; importing it here keeps the
        # channels package importable on its own.
        from app.reporting.service import gateway_catalog  # noqa: PLC0415

        if not self._currency:
            raise ChannelPreconditionError("The bank has no reporting currency configured.")
        authorised_at = self._authorised_at(package)
        try:
            payload = gateway_catalog.gateway_payload(package, self._currency)
            declaration: dict[str, Any] = {
                "prepared_by_role": str(self._declaration.get("prepared_by_role") or "Preparer"),
                "approved_by_role": str(self._declaration.get("approved_by_role") or "Approver"),
                "approved_at": authorised_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "amendment_reason": self._declaration.get("amendment_reason"),
                "software": {
                    "name": SOFTWARE_NAME,
                    "version": str(self._config.get("software_version") or "unversioned"),
                },
            }
            if self._supersedes_receipt_id and not declaration["amendment_reason"]:
                declaration["amendment_reason"] = (
                    f"Corrected version {package.version} prepared in {SOFTWARE_NAME}."
                )
            manifest: dict[str, Any] = {
                "institution_code": self._required("institution_code"),
                "return_code": package.return_code,
                "return_version": return_version,
                "reporting_date": package.reporting_date.isoformat(),
                "basis": package.basis,
                "payload_sha256": _sha256(canonical_json(payload)),
                "declaration_sha256": _sha256(canonical_json(declaration)),
                "supersedes_receipt_id": self._supersedes_receipt_id,
                "created_at": authorised_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if self._documents:
                manifest["documents"] = [
                    {
                        "name": name,
                        "media_type": media_type,
                        "sha256": _sha256(content),
                        "bytes": len(content),
                    }
                    for name, media_type, content in self._documents
                ]
            signer, reference = self._signing_key()
            signature = sign_compact_jws(
                signer,
                reference,
                header={"alg": "ES256", "kid": self.signing_kid(), "typ": "srg-manifest+jws"},
                payload=canonical_json(manifest),
                encode_json=canonical_json,
            )
        except CanonicalizationError as exc:
            raise ChannelPreconditionError(str(exc)) from exc
        except KeyUnavailableError as exc:
            raise ChannelPreconditionError(
                f"Your bank's filing signature key could not be used: {exc}"
            ) from exc
        filing: dict[str, Any] = {
            "manifest": manifest,
            "payload": payload,
            "declaration": declaration,
            "signature": signature,
        }
        if self._documents:
            filing["documents"] = [
                {
                    "name": name,
                    "media_type": media_type,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
                for name, media_type, content in self._documents
            ]
        return filing

    # -- supervisor queries ----------------------------------------------------

    def list_queries(self) -> list[dict[str, Any]]:
        """Every query the regulator has raised on this bank's filings, newest first."""
        with self._session() as session:
            token = session.access_token("queries:read")
            body = session.send("GET", "/v1/queries?status=all", token=token)
        items = body.get("items")
        if not isinstance(items, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[object], items)
            if isinstance(item, dict)
        ]

    def acknowledge_query(self, number: str) -> None:
        """Tell the regulator the query has reached the bank's system."""
        with self._session() as session:
            token = session.access_token("queries:write")
            session.send(
                "POST", f"/v1/queries/{quote(number, safe='')}/acknowledgements", token=token
            )

    def respond_to_query(
        self, number: str, body: str, resubmission_receipt_id: str | None
    ) -> dict[str, Any]:
        """Send the bank's reply; the regulator's record of it is returned."""
        with self._session() as session:
            token = session.access_token("queries:write")
            return session.send(
                "POST",
                f"/v1/queries/{quote(number, safe='')}/responses",
                token=token,
                json_body={"body": body, "resubmission_receipt_id": resubmission_receipt_id},
            )

    def submit(self, package: RegulatoryPackage, artifacts: Sequence[FiledArtifact]) -> str:
        # The documents themselves arrive through ``carry_documents``: reading them
        # needs the database and the bank's storage, which this plugin does not hold.
        del artifacts
        if package.status not in _SUBMITTABLE_STATUSES:
            raise ChannelPreconditionError(
                f"Only an approved package can be filed; this package is '{package.status}'."
            )
        idempotency_key = str(
            uuid.uuid5(
                _IDEMPOTENCY_NAMESPACE,
                f"{package.id}:{package.version}:{package.submission_revision}",
            )
        )
        with self._session() as session:
            token = session.access_token("returns:read submissions:write submissions:read")
            unheld_receipt = self._release_unheld_supersession(session, token)
            path = (
                f"/v1/submissions/{quote(self._supersedes_receipt_id, safe='')}/amendments"
                if self._supersedes_receipt_id
                else "/v1/submissions"
            )
            definition = session.send(
                "GET", f"/v1/returns/{quote(package.return_code, safe='')}", token=token
            )
            filing = self.build_filing(
                package, applicable_version(definition, package.reporting_date.isoformat())
            )
            envelope = session.send(
                "POST",
                path,
                token=token,
                json_body=filing,
                headers={"Idempotency-Key": idempotency_key},
            )
            receipt = self._verify_receipt(
                session, envelope, cast(dict[str, Any], filing["manifest"])
            )
        self.last_receipt = receipt
        self.last_detail = {
            "gateway": self.base_url,
            "receipt": receipt.receipt,
            "receipt_jws": receipt.receipt_jws,
            "gateway_status": receipt.status,
            "signing_kid": self.signing_kid(),
            "approved_by_role": filing["declaration"]["approved_by_role"],
            "return_version": filing["manifest"]["return_version"],
            "supersedes_receipt_id": self._supersedes_receipt_id,
            "idempotency_key": idempotency_key,
            "documents": cast(dict[str, Any], filing["manifest"]).get("documents", []),
        }
        if unheld_receipt is not None:
            self.last_detail["superseded_receipt_not_on_gateway"] = unheld_receipt
        return receipt.receipt_id

    def _release_unheld_supersession(self, session: GatewaySession, token: str) -> str | None:
        """Drop the amendment link when the gateway holds no record of that receipt.

        A gateway environment that was reset, or restored from an earlier point,
        no longer holds filings it once receipted. There is then nothing to amend,
        so this version goes as an original filing; the gateway still refuses it as
        a duplicate if a current version exists for that return and date. Returns
        the receipt the gateway did not hold, for the submission record.
        """
        receipt = self._supersedes_receipt_id
        if not receipt:
            return None
        try:
            session.send("GET", f"/v1/submissions/{quote(receipt, safe='')}", token=token)
        except GatewayRefusal as exc:
            if exc.status_code != 404 or exc.code != "not_found":
                raise
            self._supersedes_receipt_id = None
            self._declaration = {**self._declaration, "amendment_reason": None}
            return receipt
        return None

    def poll(self, external_ref: str) -> SubmissionPollStatus:
        return self.poll_with_detail(external_ref)[0]

    def poll_with_detail(self, external_ref: str) -> tuple[SubmissionPollStatus, dict[str, Any]]:
        with self._session() as session:
            token = session.access_token("submissions:read")
            try:
                data = session.send(
                    "GET", f"/v1/submissions/{quote(external_ref, safe='')}", token=token
                )
            except GatewayRefusal as exc:
                if exc.status_code == 404 and exc.code == "not_found":
                    raise ChannelError(
                        f"The AequorOS Reporting Gateway has no record of filing "
                        f"{external_ref}. To file this return again, record a correction "
                        "on the return under Returns, then generate the corrected version.",
                        internal_detail=exc.internal_detail,
                    ) from exc
                raise
        state = str(data.get("status") or "received")
        detail = {
            "gateway_status": state,
            "validation": data.get("validation"),
            "decided_at": data.get("decided_at"),
            "superseded_by": data.get("superseded_by"),
        }
        return _POLL_STATUS.get(state, "pending"), detail

    def connection_test(self) -> dict[str, Any]:
        """Obtain a token and the gateway's keys without filing anything."""
        with self._session() as session:
            session.access_token("obligations:read")
            jwks = session.send("GET", "/v1/.well-known/jwks.json")
        return {"token": True, "gateway_keys": len(cast(list[object], jwks.get("keys") or []))}


def certificate_facts(certificate_pem: str) -> dict[str, str]:
    """Public facts of a client certificate for display (never the key)."""
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
    return {
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "not_after": certificate.not_valid_after_utc.isoformat(),
        "fingerprint_sha256": certificate.fingerprint(hashes.SHA256()).hex(),
    }


def applicable_version(detail: Mapping[str, Any], reporting_date: str) -> str:
    """The published return version that applies to ``reporting_date``."""
    for version in cast(Sequence[Mapping[str, Any]], detail.get("versions") or []):
        if version.get("status") != "published":
            continue
        first = str(version.get("first_reporting_date") or "")
        last = cast(object, version.get("last_reporting_date"))
        if first <= reporting_date and (last is None or reporting_date <= str(last)):
            return str(version.get("version"))
    raise ChannelPreconditionError(
        "The AequorOS Reporting Gateway publishes no version of this return for the reporting date."
    )
