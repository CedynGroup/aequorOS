"""The gateway's client-certificate authority.

``LocalCertificateAuthority`` (local, test, sandbox) keeps an EC P-256 CA key and a
self-signed CA certificate in the gitignored keys directory and creates them on first
use. Production issues from a CA held in the Bank's key service, which is not built here.
"""

from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core.clock import Clock
from app.keys.provider import KeyConfigurationError, load_or_create_private_key

CA_NAME = "AequorOS Reporting Gateway client CA (development)"


class CertificateAuthority(Protocol):
    def issue(
        self,
        public_key: ec.EllipticCurvePublicKey,
        *,
        common_name: str,
        organisation: str,
        not_before: datetime,
        not_after: datetime,
    ) -> x509.Certificate: ...

    def certificate(self) -> x509.Certificate: ...


def pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


class LocalCertificateAuthority:
    def __init__(self, directory: Path, clock: Clock) -> None:
        self._directory: Path = directory
        self._clock: Clock = clock
        self._lock: threading.Lock = threading.Lock()
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._cert: x509.Certificate | None = None

    def _load(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        with self._lock:
            if self._key is not None and self._cert is not None:
                return self._key, self._cert
            key = load_or_create_private_key(self._directory / "gateway-ca.key.pem", create=True)
            cert_path = self._directory / "gateway-ca.crt.pem"
            if cert_path.exists():
                cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            else:
                name = x509.Name(
                    [
                        x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AequorOS Reporting Gateway"),
                    ]
                )
                start = self._clock.now() - timedelta(days=400)
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(name)
                    .issuer_name(name)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(start)
                    .not_valid_after(start + timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                    .add_extension(
                        x509.KeyUsage(
                            digital_signature=False,
                            content_commitment=False,
                            key_encipherment=False,
                            data_encipherment=False,
                            key_agreement=False,
                            key_cert_sign=True,
                            crl_sign=True,
                            encipher_only=False,
                            decipher_only=False,
                        ),
                        critical=True,
                    )
                    .add_extension(
                        x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
                    )
                    .sign(key, hashes.SHA256())
                )
                cert_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(cert_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(cert.public_bytes(serialization.Encoding.PEM))
            self._key, self._cert = key, cert
            return key, cert

    def certificate(self) -> x509.Certificate:
        return self._load()[1]

    def issue(
        self,
        public_key: ec.EllipticCurvePublicKey,
        *,
        common_name: str,
        organisation: str,
        not_before: datetime,
        not_after: datetime,
    ) -> x509.Certificate:
        key, ca_cert = self._load()
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation),
            ]
        )
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(public_key)
            .serial_number(int.from_bytes(secrets.token_bytes(16), "big") >> 1)
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
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
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
            )
            .sign(key, hashes.SHA256())
        )


def build_ca(directory: Path, clock: Clock, *, local_allowed: bool) -> CertificateAuthority | None:
    if not local_allowed:
        return None
    return LocalCertificateAuthority(directory, clock)


def require_ca(ca: CertificateAuthority | None) -> CertificateAuthority:
    if ca is None:
        raise KeyConfigurationError(
            "no certificate authority is configured for this environment (production issues "
            "from a CA held in the Bank's key service)"
        )
    return ca
