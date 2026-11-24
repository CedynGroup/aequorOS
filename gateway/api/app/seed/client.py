"""Client-side helpers that act as an institution's filing software (seed and tests).

They build exactly what an external client would: a P-256 client key and CSR, a JWKS for
``private_key_jwt``, a separate content-signing key, and signed filings.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import JsonValue

from app.core.clock import iso
from app.core.encoding import jcs_sha256
from app.core.jcs import canonicalize
from app.keys.jwk import public_jwk
from app.keys.jws import sign_compact
from app.keys.provider import LocalKey

MANIFEST_TYP = "srg-manifest+jws"


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def csr_pem(key: ec.EllipticCurvePrivateKey, common_name: str) -> str:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def private_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")


@dataclass
class InstitutionKeys:
    """An institution's two keys: the API client key (mTLS + assertions) and the content
    signing key whose public half is registered at enrolment."""

    client_key: ec.EllipticCurvePrivateKey = field(default_factory=new_key)
    signing_key: ec.EllipticCurvePrivateKey = field(default_factory=new_key)
    client_kid: str = "client-key-1"
    signing_kid: str = "sign-key-1"

    def client_jwks(self) -> dict[str, JsonValue]:
        return {"keys": [public_jwk(self.client_key.public_key(), self.client_kid)]}

    def signing_jwk(self) -> dict[str, JsonValue]:
        return public_jwk(self.signing_key.public_key(), self.signing_kid)


def declaration(
    *,
    approved_at: datetime,
    amendment_reason: str | None = None,
    software: tuple[str, str] | None = ("AequorOS", "2026.11"),
    prepared_by_role: str = "Head of Financial Reporting",
    approved_by_role: str = "Chief Finance Officer",
) -> dict[str, JsonValue]:
    return {
        "prepared_by_role": prepared_by_role,
        "approved_by_role": approved_by_role,
        "approved_at": iso(approved_at),
        "amendment_reason": amendment_reason,
        "software": {"name": software[0], "version": software[1]} if software else None,
    }


def line_payload(lines: dict[str, int]) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = dict(lines)
    return {"currency": "GHS", "unit": "thousands", "lines": out}


def build_filing(
    *,
    signing_key: ec.EllipticCurvePrivateKey,
    kid: str,
    institution_code: str,
    return_code: str,
    return_version: str,
    reporting_date: date,
    payload: dict[str, JsonValue],
    declaration_body: dict[str, JsonValue],
    created_at: datetime,
    basis: str = "solo",
    supersedes_receipt_id: str | None = None,
    documents: list[tuple[str, str, bytes]] | None = None,
) -> dict[str, JsonValue]:
    """A signed filing. ``documents`` are ``(name, media_type, content)``: each is listed
    in the manifest by SHA-256 and size, so the signature covers it, and sent alongside."""
    manifest: dict[str, JsonValue] = {
        "institution_code": institution_code,
        "return_code": return_code,
        "return_version": return_version,
        "reporting_date": reporting_date.isoformat(),
        "basis": basis,
        "payload_sha256": jcs_sha256(payload),
        "declaration_sha256": jcs_sha256(declaration_body),
        "supersedes_receipt_id": supersedes_receipt_id,
        "created_at": iso(created_at),
    }
    if documents:
        manifest["documents"] = [
            {
                "name": name,
                "media_type": media_type,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
            for name, media_type, content in documents
        ]
    signature = sign_compact(LocalKey(kid, signing_key), canonicalize(manifest), typ=MANIFEST_TYP)
    body: dict[str, JsonValue] = {
        "manifest": manifest,
        "payload": payload,
        "declaration": declaration_body,
        "signature": signature,
    }
    if documents:
        body["documents"] = [
            {
                "name": name,
                "media_type": media_type,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
            for name, media_type, content in documents
        ]
    return body


def idempotency_key() -> str:
    return str(uuid.uuid4())


def jti() -> str:
    return secrets.token_hex(16)
