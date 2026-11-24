"""Operator machine clients (SPEC Part 5).

Configured at deployment through ``GATEWAY_OPERATOR_CLIENTS``: a JSON list of
``{client_id, jwks, certificate_sha256}``, where ``certificate_sha256`` is the client
certificate's SHA-256 thumbprint (base64url ``x5t#S256`` or 64 hex digits). In
``local`` and ``test`` one development operator client is created on first start; its
key, certificate and client id are written to ``<keys_dir>/operator/``. Elsewhere an
operator's credentials are issued with ``python -m app.enrolment.issue_operator``, in the
same layout.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, JsonValue

from app.core.certs import certificate_thumbprint
from app.core.clock import Clock
from app.core.encoding import b64url_encode
from app.core.settings import Settings
from app.enrolment.ca import CertificateAuthority, pem
from app.keys.jwk import public_jwk
from app.keys.provider import KeyConfigurationError, load_or_create_private_key

OPERATOR_SCOPES: tuple[str, ...] = ("ops:enrol",)
DEV_OPERATOR_ID = "dev-operator-01"


@dataclass(frozen=True)
class OperatorClient:
    client_id: str
    jwks: dict[str, JsonValue]
    thumbprint: str


class _ConfiguredOperator(BaseModel):
    client_id: str
    jwks: dict[str, JsonValue]
    certificate_sha256: str


def normalise_thumbprint(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return b64url_encode(bytes.fromhex(text))
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except ValueError as exc:
        raise KeyConfigurationError("certificate_sha256 is neither hex nor base64url") from exc
    if len(raw) != 32:
        raise KeyConfigurationError("certificate_sha256 must be a SHA-256 digest")
    return b64url_encode(raw)


def parse_operator_clients(raw: str) -> list[OperatorClient]:
    if not raw.strip():
        return []
    loaded: object = json.loads(raw)  # pyright: ignore[reportAny]
    if not isinstance(loaded, list):
        raise KeyConfigurationError("GATEWAY_OPERATOR_CLIENTS must be a JSON list")
    clients: list[OperatorClient] = []
    for item in loaded:  # pyright: ignore[reportUnknownVariableType]
        configured = _ConfiguredOperator.model_validate(item)
        clients.append(
            OperatorClient(
                client_id=configured.client_id,
                jwks=configured.jwks,
                thumbprint=normalise_thumbprint(configured.certificate_sha256),
            )
        )
    return clients


def ensure_dev_operator(directory: Path, ca: CertificateAuthority, clock: Clock) -> OperatorClient:
    """Create (once) the development operator client and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    key = load_or_create_private_key(directory / "client.key.pem", create=True)
    cert_path = directory / "client.crt.pem"
    certificate: x509.Certificate | None = None
    if cert_path.exists():
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if (
            certificate.public_key() != key.public_key()
            or certificate.not_valid_after_utc <= clock.now()
        ):
            certificate = None
    if certificate is None:
        now = clock.now()
        certificate = ca.issue(
            key.public_key(),
            common_name=DEV_OPERATOR_ID,
            organisation="Channel operator (development)",
            not_before=now - timedelta(days=400),
            not_after=now + timedelta(days=730),
        )
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return _write_descriptor(directory, ca, DEV_OPERATOR_ID, key, certificate)


#: An operator's client id: lower-case letters, digits and hyphens.
OPERATOR_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,62}")
OPERATOR_CERTIFICATE_DAYS = 730


def issue_operator_bundle(
    directory: Path,
    ca: CertificateAuthority,
    clock: Clock,
    *,
    client_id: str,
    organisation: str,
) -> OperatorClient:
    """Issue a NEW operator client into ``directory``, in the development operator's layout.

    The private key is created here and written only to ``directory``; a client such as
    AequorOS reads the same files (``operator.json`` and the paths it names) from its
    operator directory. The returned client is what ``GATEWAY_OPERATOR_CLIENTS`` lists.
    """
    if not OPERATOR_ID.fullmatch(client_id) or client_id == DEV_OPERATOR_ID:
        raise KeyConfigurationError(
            "the client id must be 3-63 lower-case letters, digits or hyphens, and not the "
            "development operator's"
        )
    directory.mkdir(parents=True, exist_ok=False)
    key = load_or_create_private_key(directory / "client.key.pem", create=True)
    now = clock.now()
    certificate = ca.issue(
        key.public_key(),
        common_name=client_id,
        organisation=organisation,
        not_before=now - timedelta(minutes=5),
        not_after=now + timedelta(days=OPERATOR_CERTIFICATE_DAYS),
    )
    (directory / "client.crt.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return _write_descriptor(directory, ca, client_id, key, certificate)


def configured_entry(client: OperatorClient) -> dict[str, JsonValue]:
    """The client's entry in ``GATEWAY_OPERATOR_CLIENTS``."""
    return {
        "client_id": client.client_id,
        "jwks": client.jwks,
        "certificate_sha256": client.thumbprint,
    }


def _write_descriptor(
    directory: Path,
    ca: CertificateAuthority,
    client_id: str,
    key: ec.EllipticCurvePrivateKey,
    certificate: x509.Certificate,
) -> OperatorClient:
    kid = f"{client_id}-key"
    jwks: dict[str, JsonValue] = {"keys": [public_jwk(key.public_key(), kid)]}
    (directory / "ca.crt.pem").write_text(pem(ca.certificate()))
    descriptor = {
        "client_id": client_id,
        "kid": kid,
        "key_file": "client.key.pem",
        "certificate_file": "client.crt.pem",
        "ca_file": "ca.crt.pem",
        "certificate_sha256": certificate_thumbprint(certificate),
        "scope": " ".join(OPERATOR_SCOPES),
    }
    (directory / "client_id").write_text(client_id + "\n")
    (directory / "operator.json").write_text(json.dumps(descriptor, indent=2) + "\n")
    os.chmod(directory / "client.key.pem", 0o600)
    return OperatorClient(client_id, jwks, certificate_thumbprint(certificate))


def load_operator_clients(
    settings: Settings, ca: CertificateAuthority | None, clock: Clock
) -> dict[str, OperatorClient]:
    clients = {
        client.client_id: client for client in parse_operator_clients(settings.operator_clients)
    }
    if settings.is_dev and ca is not None:
        dev = ensure_dev_operator(settings.keys_dir / "operator", ca, clock)
        clients.setdefault(dev.client_id, dev)
    return clients
