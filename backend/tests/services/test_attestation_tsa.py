"""RFC 3161 trusted time (app/services/attestation/tsa.py, gap G4).

Every test here runs against a LOCAL responder: pyHanko's ``DummyTimeStamper``
acts as its own TSA with a throwaway RSA key, and the HTTP tests replace
``requests.post`` with a transport that feeds the same responder. No test in
this file makes a network call, and none may ever be changed to.

The properties pinned are the ones that make a timestamp evidence rather than
decoration: the token covers the digest we sent (which pyHanko itself does not
check), genTime is what we record, failures raise typed errors so callers fail
closed, and — Act 930 — nothing but a hash goes on the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from asn1crypto import cms, tsp
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.sign.timestamps import DummyTimeStamper, TimeStamper

from app.core.config import TsaSettings
from app.services.attestation.tsa import (
    SUPPORTED_HASH_ALGORITHMS,
    TsaClient,
    TsaNotConfigured,
    TsaResponseInvalid,
    TsaUnavailable,
    build_pdf_timestamper,
    get_timestamper,
    request_timestamp,
    timestamp_digest,
)

TSA_URL = "https://tsa.test.invalid/tsr"
FIXED_TIME = datetime(2026, 7, 25, 10, 30, 15, tzinfo=UTC)
DIGEST = bytes(range(32))


def _tsa_settings(**overrides: Any) -> TsaSettings:
    values: dict[str, Any] = {"tsa_url": TSA_URL, "tsa_timeout_seconds": 3.5}
    values.update(overrides)
    return TsaSettings(**values)


@pytest.fixture(scope="module")
def dummy_authority() -> DummyTimeStamper:
    """A local RFC 3161 authority signing with a throwaway RSA-2048 key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test TSA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    return DummyTimeStamper(
        tsa_cert=asn1_x509.Certificate.load(
            certificate.public_bytes(serialization.Encoding.DER)
        ),
        tsa_key=asn1_keys.PrivateKeyInfo.load(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        fixed_dt=FIXED_TIME,
    )


def _token_imprint(token_der: bytes) -> tuple[str, bytes]:
    token = cms.ContentInfo.load(token_der)
    tst_info = token["content"]["encap_content_info"]["content"].parsed
    imprint = tst_info["message_imprint"]
    return imprint["hash_algorithm"]["algorithm"].native, bytes(
        imprint["hashed_message"].native
    )


# --- the happy path --------------------------------------------------------


def test_timestamp_digest_returns_token_bytes_and_the_authority_time(
    dummy_authority: DummyTimeStamper,
) -> None:
    token_der, asserted_at = timestamp_digest(
        DIGEST, settings=_tsa_settings(), timestamper=dummy_authority
    )

    # The DER is what lands in attestation_signatures.tsa_token: it must be
    # self-describing evidence, parseable without our code.
    algorithm, covered = _token_imprint(token_der)
    assert algorithm == "sha256"
    assert covered == DIGEST
    # genTime, not the host clock, is authoritative.
    assert asserted_at == FIXED_TIME
    assert asserted_at.tzinfo is not None


def test_request_timestamp_reports_the_authority_and_serial(
    dummy_authority: DummyTimeStamper,
) -> None:
    token = request_timestamp(DIGEST, settings=_tsa_settings(), timestamper=dummy_authority)

    assert token.tsa_url == TSA_URL
    assert token.hash_algorithm == "sha256"
    assert token.digest == DIGEST
    assert token.asserted_at == FIXED_TIME
    # The serial is the only identifier of the request the authority retains;
    # recording it is what makes an OCSP/CRL question answerable later.
    assert isinstance(token.serial_number, int)


def test_client_satisfies_the_signing_path_timestamper_port(
    dummy_authority: DummyTimeStamper,
) -> None:
    """signing.py injects an object with .timestamp_digest(digest) and .url."""
    client = TsaClient(_tsa_settings(), timestamper=dummy_authority)

    token_der, asserted_at = client.timestamp_digest(DIGEST)

    assert client.url == TSA_URL
    assert asserted_at == FIXED_TIME
    assert _token_imprint(token_der)[1] == DIGEST


# --- Act 930: only a hash leaves the perimeter -----------------------------


class _CapturingTransport:
    """Stand-in for ``requests.post`` that records the exact bytes sent."""

    def __init__(self, authority: DummyTimeStamper) -> None:
        self._authority = authority
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, data: bytes, **kwargs: Any) -> Any:
        self.calls.append({"url": url, "data": data, **kwargs})
        response = self._authority.request_tsa_response(tsp.TimeStampReq.load(data))
        return _FakeHttpResponse(response.dump())


class _FakeHttpResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.headers = {"Content-Type": "application/timestamp-reply"}


def test_only_a_hash_is_transmitted_over_http(
    monkeypatch: pytest.MonkeyPatch, dummy_authority: DummyTimeStamper
) -> None:
    """The Act 930 secrecy claim, asserted on the actual wire bytes (§5.2).

    An RFC 3161 request has room for exactly one caller-supplied payload. This
    parses what was posted and pins the field set, so a future change that
    started attaching anything else to a request would fail here.
    """
    transport = _CapturingTransport(dummy_authority)
    monkeypatch.setattr("pyhanko.sign.timestamps.requests_client.requests.post", transport)

    token_der, asserted_at = timestamp_digest(
        DIGEST, settings=_tsa_settings(tsa_username="u", tsa_password="p")
    )

    assert asserted_at == FIXED_TIME
    assert _token_imprint(token_der)[1] == DIGEST
    assert len(transport.calls) == 1
    call = transport.calls[0]
    sent = tsp.TimeStampReq.load(call["data"])
    # Nothing but version, imprint, cert_req and the nonce is present …
    present = {name for name, value in sent.native.items() if value is not None}
    assert present <= {"version", "message_imprint", "cert_req", "nonce"}
    # … and the imprint is our digest, not content of any kind.
    imprint: Any = sent["message_imprint"]
    assert bytes(imprint["hashed_message"].native) == DIGEST
    assert sent["extensions"].native is None
    # A whole RFC 3161 request is tens of bytes; document content could not fit.
    assert len(call["data"]) < 100


def test_http_client_passes_url_timeout_and_credentials(
    monkeypatch: pytest.MonkeyPatch, dummy_authority: DummyTimeStamper
) -> None:
    transport = _CapturingTransport(dummy_authority)
    monkeypatch.setattr("pyhanko.sign.timestamps.requests_client.requests.post", transport)

    timestamp_digest(
        DIGEST, settings=_tsa_settings(tsa_username="tsa-user", tsa_password="tsa-secret")
    )

    call = transport.calls[0]
    assert call["url"] == TSA_URL
    # An explicit, finite timeout: a hung authority must not hold a signing
    # request open for the default socket lifetime.
    assert call["timeout"] == 3.5
    assert call["auth"] == ("tsa-user", "tsa-secret")
    assert call["headers"]["Content-Type"] == "application/timestamp-query"
    assert call["headers"]["Accept"] == "application/timestamp-reply"


def test_no_credentials_configured_sends_no_auth(
    monkeypatch: pytest.MonkeyPatch, dummy_authority: DummyTimeStamper
) -> None:
    transport = _CapturingTransport(dummy_authority)
    monkeypatch.setattr("pyhanko.sign.timestamps.requests_client.requests.post", transport)

    timestamp_digest(DIGEST, settings=_tsa_settings())

    assert transport.calls[0]["auth"] is None


# --- nothing is trusted blindly -------------------------------------------


class _WrongDigestAuthority(DummyTimeStamper):
    """An authority that timestamps a DIFFERENT digest than it was asked about.

    The realistic shapes of this are a confused responder, a proxy that
    replayed another request, or an attacker substituting a token. pyHanko's
    response handling checks status and nonce, both of which stay correct here —
    only the imprint is wrong.
    """

    def request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        imprint: Any = req["message_imprint"]
        swapped = tsp.TimeStampReq(
            {
                "version": req["version"],
                "message_imprint": tsp.MessageImprint(
                    {
                        "hash_algorithm": imprint["hash_algorithm"],
                        "hashed_message": bytes(32),
                    }
                ),
                "nonce": req["nonce"],
                "cert_req": req["cert_req"],
            }
        )
        return super().request_tsa_response(swapped)


def test_a_token_over_another_digest_is_refused(dummy_authority: DummyTimeStamper) -> None:
    hostile = _WrongDigestAuthority(
        tsa_cert=dummy_authority.tsa_cert,
        tsa_key=dummy_authority.tsa_key,
        fixed_dt=FIXED_TIME,
    )

    with pytest.raises(TsaResponseInvalid, match="does not cover the digest"):
        timestamp_digest(DIGEST, settings=_tsa_settings(), timestamper=hostile)


class _RefusingAuthority(TimeStamper):
    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        return tsp.TimeStampResp(
            {
                "status": tsp.PKIStatusInfo(
                    {
                        "status": tsp.PKIStatus("rejection"),
                        "status_string": tsp.PKIFreeText(["policy not accepted"]),
                    }
                )
            }
        )


def test_a_refused_request_raises_response_invalid() -> None:
    with pytest.raises(TsaResponseInvalid, match="refused"):
        timestamp_digest(DIGEST, settings=_tsa_settings(), timestamper=_RefusingAuthority())


class _UnreachableAuthority(TimeStamper):
    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        raise OSError("connection refused")


def test_an_unreachable_authority_raises_unavailable() -> None:
    """Distinct from TsaResponseInvalid: the operator needs to know which."""
    with pytest.raises(TsaUnavailable, match="unreachable"):
        timestamp_digest(DIGEST, settings=_tsa_settings(), timestamper=_UnreachableAuthority())


def test_transport_failure_through_the_real_http_client_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pyHanko wraps IOErrors in TimestampRequestError; we must still classify
    that as 'the authority is down', not 'the authority answered wrongly'."""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise OSError("name resolution failed")

    monkeypatch.setattr("pyhanko.sign.timestamps.requests_client.requests.post", explode)

    with pytest.raises(TsaUnavailable):
        timestamp_digest(DIGEST, settings=_tsa_settings())


class _MalformedAuthority(TimeStamper):
    """Grants the request but returns something that is not a TST."""

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        return tsp.TimeStampResp(
            {
                "status": tsp.PKIStatusInfo({"status": tsp.PKIStatus("granted")}),
                "time_stamp_token": cms.ContentInfo(
                    {"content_type": cms.ContentType("data"), "content": b"not a token"}
                ),
            }
        )


def test_a_token_that_is_not_cms_signed_data_is_refused() -> None:
    with pytest.raises(TsaResponseInvalid):
        timestamp_digest(DIGEST, settings=_tsa_settings(), timestamper=_MalformedAuthority())


# --- fail closed on configuration -----------------------------------------


def test_unconfigured_tsa_refuses_rather_than_using_host_time() -> None:
    """The G4 invariant: no TSA means no signature, never a host timestamp."""
    unconfigured = _tsa_settings(tsa_url=None)

    assert unconfigured.enabled is False
    assert get_timestamper(unconfigured) is None
    with pytest.raises(TsaNotConfigured):
        timestamp_digest(DIGEST, settings=unconfigured)
    with pytest.raises(TsaNotConfigured):
        build_pdf_timestamper(unconfigured)


def test_blank_url_reads_as_unconfigured() -> None:
    """Empty-string-means-unset, so a deployment cannot half-configure a TSA."""
    assert _tsa_settings(tsa_url="   ").enabled is False


def test_configured_tsa_yields_a_client() -> None:
    client = get_timestamper(_tsa_settings())

    assert client is not None
    assert client.url == TSA_URL
    assert client.hash_algorithm == "sha256"


def test_unsupported_hash_algorithm_is_refused() -> None:
    assert "sha1" not in SUPPORTED_HASH_ALGORITHMS
    with pytest.raises(TsaNotConfigured, match="not supported"):
        timestamp_digest(DIGEST, settings=_tsa_settings(tsa_hash_algorithm="sha1"))


def test_a_hex_digest_is_refused(dummy_authority: DummyTimeStamper) -> None:
    """A hex string is 64 characters and would silently be timestamped as data."""
    with pytest.raises(ValueError, match="raw digest"):
        timestamp_digest(
            DIGEST.hex().encode("ascii"),  # type: ignore[arg-type] - deliberate misuse
            settings=_tsa_settings(),
            timestamper=dummy_authority,
        )


def test_a_wrong_length_digest_is_refused(dummy_authority: DummyTimeStamper) -> None:
    with pytest.raises(ValueError, match="requires 32"):
        timestamp_digest(b"too short", settings=_tsa_settings(), timestamper=dummy_authority)


def test_pdf_timestamper_carries_the_configured_transport_settings() -> None:
    """The PAdES path and the detached path must use the same authority (§3.2)."""
    stamper = build_pdf_timestamper(_tsa_settings(tsa_username="u", tsa_password="p"))

    assert stamper.url == TSA_URL
    assert stamper.timeout == 3.5
    assert stamper.auth == ("u", "p")
