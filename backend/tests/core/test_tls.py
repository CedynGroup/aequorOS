"""Transport policy failures and real, hermetic certificate handshakes."""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.adapters.database_direct.config import ConnectionConfig, TlsConfig
from app.adapters.database_direct.drivers.base import require_verified_transport
from app.adapters.database_direct.errors import DatabaseDirectError
from app.core.config import Settings, TsaSettings, get_settings
from app.core.serve import listener_options
from app.core.tls import (
    RequireTLSMiddleware,
    TransportSecurityError,
    client_context,
    database_connect_args,
    require_https,
    validate_service_transports,
)
from app.core.tls_evidence import probe_https
from app.integrations.storage.s3 import S3ObjectStorage
from app.main import create_app
from app.services.attestation.tsa import build_pdf_timestamper
from app.services.regulatory_reporting.channels.orass_api import OrassApiChannel


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("OPENBAO_ADDR", "")
    monkeypatch.setenv("TSA_URL", "")


@pytest.mark.parametrize("field", ["DATABASE_URL", "WORKER_DATABASE_URL", "BI_DATABASE_URL"])
def test_production_startup_refuses_unverified_database(
    production: None, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(field, "postgresql+psycopg://u:secret@db.example/bank?sslmode=require")
    with pytest.raises(TransportSecurityError, match="verify-full") as error:
        create_app()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field", ["S3_ENDPOINT", "OPENBAO_ADDR", "TSA_URL"])
def test_production_startup_refuses_plaintext_service(
    production: None, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(field, "http://service.example")
    with pytest.raises(TransportSecurityError, match=field):
        create_app()


def test_production_refuses_local_escape_hatch(
    production: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "1")
    with pytest.raises(TransportSecurityError, match="local/test"):
        validate_service_transports(Settings())


@pytest.mark.parametrize("env", ["production", "staging"])
def test_deployed_environment_never_allows_plaintext(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    with pytest.raises(TransportSecurityError, match="HTTPS"):
        require_https("http://service.example", field="test service")


def test_local_plaintext_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    with pytest.raises(TransportSecurityError):
        require_https("http://localhost", field="local service")
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "1")
    get_settings.cache_clear()
    assert require_https("http://localhost", field="local service") == "http://localhost"
    assert database_connect_args("sqlite://") == {}


@pytest.mark.parametrize(
    "query",
    [
        "sslmode=disable",
        "sslmode=prefer",
        "sslmode=verify-ca",
        "sslmode=verify-full&ssl_min_protocol_version=TLSv1.1",
        "sslmode=verify-full&host=other.example",
    ],
)
def test_database_refuses_downgrade_options(production: None, query: str) -> None:
    with pytest.raises(TransportSecurityError):
        database_connect_args(f"postgresql+psycopg://u@db.example/bank?{query}")


def test_database_pins_verified_tls(production: None) -> None:
    args = database_connect_args("postgresql+psycopg://u@db.example/bank?sslmode=verify-full")
    assert args["sslmode"] == "verify-full"
    assert args["ssl_min_protocol_version"] == "TLSv1.2"
    assert args["sslrootcert"]
    assert args["gssencmode"] == "disable"


def test_listener_refuses_missing_keys(production: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("TLS_KEY_FILE", raising=False)
    with pytest.raises(TransportSecurityError, match="TLS_CERT_FILE"):
        listener_options()


def test_plaintext_request_is_rejected_before_handler() -> None:
    called = False

    async def endpoint(request: Request) -> PlainTextResponse:
        nonlocal called
        called = True
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(RequireTLSMiddleware)
    with TestClient(app) as client:
        assert client.get("/", headers={"X-Forwarded-Proto": "https"}).status_code == 400
    assert not called
    with TestClient(app, base_url="https://service.example") as client:
        assert client.get("/").status_code == 200
    assert called


def certificate(tmp_path: Path, *, expired: bool = False) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@contextmanager
def tls_peer(cert: Path, key: Path) -> Generator[int]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        port = cast(int, listener.getsockname()[1])

        def serve() -> None:
            with listener.accept()[0] as connection:
                try:
                    with context.wrap_socket(connection, server_side=True) as secure:
                        secure.recv(1)
                except (ssl.SSLError, ConnectionError):
                    pass

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            yield port
        finally:
            thread.join(timeout=6)
            assert not thread.is_alive()


def test_negotiation_evidence_authenticates_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert, key = certificate(tmp_path)
    monkeypatch.setenv("TLS_CA_BUNDLE", str(cert))
    with tls_peer(cert, key) as port:
        result = probe_https(f"https://localhost:{port}/")
    assert result["certificate_hostname_verified"] is True
    assert result["negotiated_version"] in {"TLSv1.2", "TLSv1.3"}
    assert result["minimum_version"] == "TLSv1.2"
    assert len(str(result["certificate_sha256"])) == 64


@pytest.mark.parametrize("failure", ["untrusted", "hostname", "expired"])
def test_real_handshake_refuses_invalid_certificate(tmp_path: Path, failure: str) -> None:
    cert, key = certificate(tmp_path, expired=failure == "expired")
    context = client_context(None if failure == "untrusted" else str(cert))
    hostname = "wrong.example" if failure == "hostname" else "localhost"
    with (
        tls_peer(cert, key) as port,
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        pytest.raises(ssl.SSLCertVerificationError),
    ):
        context.wrap_socket(raw, server_hostname=hostname)


def test_storage_refuses_plaintext_before_sdk_connection(production: None) -> None:
    with pytest.raises(TransportSecurityError, match="S3_ENDPOINT"):
        S3ObjectStorage(
            region_name="us-east-1",
            endpoint_url="http://store.example",
            access_key_id="synthetic",
            secret_access_key="synthetic",
            force_path_style=True,
        )


def test_tsa_refuses_plaintext(production: None) -> None:
    with pytest.raises(TransportSecurityError, match="TSA_URL"):
        build_pdf_timestamper(TsaSettings(TSA_URL="http://tsa.example"))


def test_orass_refuses_certificate_bypass(production: None) -> None:
    channel = OrassApiChannel(config={"verify_tls": False})
    with pytest.raises(TransportSecurityError, match="verification"):
        channel.poll("synthetic-reference")


def test_native_core_refuses_unverified_tls(production: None) -> None:
    for tls in (TlsConfig(enabled=False), TlsConfig(verify_server_certificate=False)):
        with pytest.raises(DatabaseDirectError):
            require_verified_transport(ConnectionConfig(backend="sqlserver", tls=tls))


def test_listener_runtime_requires_tls12(
    production: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from uvicorn.config import create_ssl_context  # noqa: PLC0415

    cert, key = certificate(tmp_path)
    monkeypatch.setenv("TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("TLS_KEY_FILE", str(key))
    options = listener_options()
    assert options["ssl_version"] == ssl.PROTOCOL_TLS_SERVER
    context = create_ssl_context(
        certfile=str(options["ssl_certfile"]),
        keyfile=str(options["ssl_keyfile"]),
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        password=None,
        cert_reqs=ssl.CERT_NONE,
        ca_certs=None,
        ciphers="TLSv1",
    )
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_postgres_plaintext_peer_cannot_receive_startup_credentials(production: None) -> None:
    from sqlalchemy.exc import OperationalError  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415

    received: list[bytes] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        port = cast(int, listener.getsockname()[1])

        def refuse_ssl() -> None:
            with listener.accept()[0] as peer:
                peer.settimeout(5)
                received.append(peer.recv(8))
                peer.sendall(b"N")  # PostgreSQL's explicit refusal of SSLRequest.
                received.append(peer.recv(1024))

        thread = threading.Thread(target=refuse_ssl)
        thread.start()
        engine = get_engine(
            f"postgresql+psycopg://synthetic:synthetic@localhost:{port}/bank?sslmode=verify-full"
        )
        try:
            with pytest.raises(OperationalError, match="does not support SSL"):
                engine.connect()
        finally:
            engine.dispose()
            thread.join(timeout=6)
        assert not thread.is_alive()
    assert received == [b"\x00\x00\x00\x08\x04\xd2\x16\x2f", b""]


@pytest.mark.parametrize("kind", ["dashboard", "console"])
def test_next_container_gateway_terminates_verified_tls(tmp_path: Path, kind: str) -> None:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    cert, key = certificate(tmp_path)
    upstream = tmp_path / kind / "upstream.cjs"
    upstream.parent.mkdir()
    upstream.write_text(
        r"""
const server = require('node:http').createServer((req, res) => {
  if (req.url === '/fail-before-headers') {
    res.destroy();
  } else if (req.url === '/stream' || req.url === '/fail-during-stream') {
    res.writeHead(200, {'content-length': '12'});
    res.write('first:');
    setTimeout(() => {
      if (req.url === '/stream') res.end('second');
      else res.destroy();
    }, 50);
  } else {
    res.end(req.url + ':' + req.headers['x-forwarded-proto']);
  }
});
server.listen(Number(process.env.PORT), process.env.HOSTNAME);
"""
    )
    with socket.socket() as outer, socket.socket() as inner:
        outer.bind(("127.0.0.1", 0))
        inner.bind(("127.0.0.1", 0))
        port = cast(int, outer.getsockname()[1])
        loopback_port = cast(int, inner.getsockname()[1])
    launcher = Path(__file__).resolve().parents[3] / "deploy/tls/serve-next.cjs"
    process = subprocess.Popen(
        ["node", str(launcher), str(upstream)],
        env={
            "PATH": os.environ["PATH"],
            "NODE_ENV": "production",
            "TLS_CERT_FILE": str(cert),
            "TLS_KEY_FILE": str(key),
            "NEXT_PUBLIC_RISK_API_BASE_URL": "https://api.example",
            "OPERATOR_API_URL": "https://operator.example",
            "PORT": str(port),
            "NEXT_LOOPBACK_PORT": str(loopback_port),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with httpx.Client(verify=client_context(str(cert)), trust_env=False) as client:
            response = None
            for _attempt in range(100):
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1])
                try:
                    response = client.get(f"https://localhost:{port}/synthetic?proof=1")
                    if response.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.05)
            assert response is not None and response.status_code == 200
            assert response.text == "/synthetic?proof=1:https"
            with pytest.raises(httpx.RemoteProtocolError):
                client.get(f"https://localhost:{port}/fail-during-stream", timeout=2)
            for path, status, body in (
                ("/stream", 200, "first:second"),
                ("/fail-before-headers", 502, ""),
                ("/healthy", 200, "/healthy:https"),
            ):
                response = client.get(f"https://localhost:{port}{path}")
                assert (response.status_code, response.text) == (status, body)
        with (
            httpx.Client(trust_env=False) as untrusted,
            pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"),
        ):
            untrusted.get(f"https://localhost:{port}/")
        with httpx.Client(trust_env=False) as plaintext:
            # Node may close the connection or return a transport-level 4xx;
            # neither may reach the upstream handler.
            try:
                result = plaintext.get(f"http://localhost:{port}/")
                assert result.status_code >= 400
            except httpx.RemoteProtocolError:
                pass
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


class _EncodedCertificate(Protocol):
    def dump(self) -> bytes: ...


class _CertificateFetchClient(Protocol):
    async def fetch_certs(self, url: str, origin: str) -> Iterable[_EncodedCertificate]: ...


def test_certificate_fetcher_refuses_plaintext(production: None) -> None:
    import asyncio  # noqa: PLC0415

    from app.services.attestation.tls_fetchers import verified_fetchers  # noqa: PLC0415

    fetchers = verified_fetchers()
    with pytest.raises(TransportSecurityError, match="certificate validation evidence"):
        asyncio.run(
            cast(_CertificateFetchClient, fetchers.cert_fetcher).fetch_certs(
                "http://ca.example/issuer", "certificate"
            )
        )


def test_certificate_fetcher_preserves_https_content_and_refuses_redirects(
    production: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415
    from pyhanko_certvalidator.errors import (  # type: ignore[reportMissingTypeStubs]  # noqa: PLC0415
        CertificateFetchError,
    )

    from app.services.attestation.tls_fetchers import verified_fetchers  # noqa: PLC0415

    cert, _key = certificate(tmp_path)
    content = x509.load_pem_x509_certificate(cert.read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    seen: list[str] = []
    client_class = httpx.Client
    redirect = False

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if redirect:
            return httpx.Response(302, headers={"Location": "http://ca.example/issuer"})
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/pkix-cert"}
        )

    def client_factory(
        *, verify: ssl.SSLContext, trust_env: bool, follow_redirects: bool, timeout: float
    ) -> httpx.Client:
        assert verify.check_hostname and verify.verify_mode == ssl.CERT_REQUIRED
        assert verify.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert not trust_env and not follow_redirects
        return client_class(
            transport=httpx.MockTransport(handle),
            verify=verify,
            trust_env=trust_env,
            follow_redirects=follow_redirects,
            timeout=timeout,
        )

    monkeypatch.setattr("app.services.attestation.tls_fetchers.httpx.Client", client_factory)
    result = list(
        asyncio.run(
            cast(_CertificateFetchClient, verified_fetchers().cert_fetcher).fetch_certs(
                "https://ca.example/issuer", "certificate"
            )
        )
    )
    assert len(result) == 1
    assert result[0].dump() == content
    redirect = True
    with pytest.raises(CertificateFetchError):
        asyncio.run(
            cast(_CertificateFetchClient, verified_fetchers().cert_fetcher).fetch_certs(
                "https://ca.example/redirect", "certificate"
            )
        )
    assert seen == ["https://ca.example/issuer", "https://ca.example/redirect"]
