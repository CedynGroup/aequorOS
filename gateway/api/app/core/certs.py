"""X.509 helpers: the ``x5t#S256`` thumbprint and PEM parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from app.core.clock import utc
from app.core.encoding import b64url_encode


@dataclass(frozen=True)
class PresentedCertificate:
    pem: str
    thumbprint: str
    serial: str
    not_before: datetime
    not_after: datetime


def certificate_thumbprint(certificate: x509.Certificate) -> str:
    """``x5t#S256``: base64url SHA-256 of the DER certificate."""
    return b64url_encode(certificate.fingerprint(hashes.SHA256()))


def describe_certificate(certificate: x509.Certificate) -> PresentedCertificate:
    return PresentedCertificate(
        pem=certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        thumbprint=certificate_thumbprint(certificate),
        serial=format(certificate.serial_number, "x"),
        not_before=utc(certificate.not_valid_before_utc),
        not_after=utc(certificate.not_valid_after_utc),
    )


def parse_certificate_pem(pem: str) -> PresentedCertificate:
    return describe_certificate(x509.load_pem_x509_certificate(pem.encode("ascii")))
