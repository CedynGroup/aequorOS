"""Transport policy shared by aequorOS service clients and entrypoints."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.core.config import Settings


class TransportSecurityError(RuntimeError):
    """A connection would violate the transport policy, before any data is sent."""


def plaintext_allowed(settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    return resolved.app.app_env in {"local", "test"} and resolved.tls.allow_plaintext


def require_https(url: str, *, field: str, settings: Settings | None = None) -> str:
    parts = urlsplit(url)
    if not parts.hostname or parts.scheme not in {"https", "http"}:
        raise TransportSecurityError(f"{field} must be an absolute HTTPS URL.")
    if parts.scheme != "https" and not plaintext_allowed(settings):
        # Never include a URL: it can carry credentials or tenant identifiers.
        raise TransportSecurityError(f"{field} requires HTTPS with certificate verification.")
    return url


def client_context(cafile: str | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=cafile or get_settings().tls.ca_bundle)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def database_connect_args(url: str, *, settings: Settings | None = None) -> dict[str, str]:
    resolved = settings or get_settings()
    if plaintext_allowed(resolved):
        return {}
    parsed = make_url(url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"} or not parsed.host:
        raise TransportSecurityError("Database transport requires PostgreSQL with a hostname.")
    query = parsed.query
    if query.get("sslmode") != "verify-full":
        raise TransportSecurityError("Database URL requires sslmode=verify-full.")
    if any(key in query for key in ("host", "hostaddr", "service")):
        raise TransportSecurityError(
            "Database URL must declare its hostname only in the authority."
        )
    if query.get("ssl_min_protocol_version", "TLSv1.2") not in {"TLSv1.2", "TLSv1.3"}:
        raise TransportSecurityError("Database TLS minimum must be TLSv1.2 or TLSv1.3.")
    ca = query.get("sslrootcert") or resolved.tls.ca_bundle or ssl.get_default_verify_paths().cafile
    if not isinstance(ca, str) or not ca:
        raise TransportSecurityError("Database TLS requires a trusted CA bundle.")
    return {
        "sslmode": "verify-full",
        "sslrootcert": ca,
        "ssl_min_protocol_version": str(query.get("ssl_min_protocol_version", "TLSv1.2")),
        # libpq otherwise prefers GSS encryption, which bypasses TLS negotiation.
        "gssencmode": "disable",
    }


def validate_service_transports(settings: Settings) -> None:
    """Fail deployed startup before opening any configured service connection."""
    if settings.app.app_env in {"staging", "production"} and settings.tls.allow_plaintext:
        raise TransportSecurityError("TLS_ALLOW_PLAINTEXT is permitted only in local/test.")
    for url in (
        settings.database.database_url,
        settings.worker.worker_database_url,
        settings.bi.database_url,
    ):
        if url:
            database_connect_args(url, settings=settings)
    for field, url in (
        ("S3_ENDPOINT", settings.storage.endpoint_url),
        ("OPENBAO_ADDR", settings.attestation.openbao_addr),
        ("TSA_URL", settings.tsa.tsa_url),
    ):
        if url:
            require_https(url, field=field, settings=settings)
    if (
        settings.smtp.enabled
        and not settings.smtp.smtp_starttls
        and not plaintext_allowed(settings)
    ):
        raise TransportSecurityError("SMTP_STARTTLS must be enabled.")


class _EndpointMetadata(Protocol):
    endpoint_url: str


class _EndpointClient(Protocol):
    meta: _EndpointMetadata


def require_boto_tls(client: object) -> None:
    """Validate the SDK's resolved endpoint, including environment overrides."""
    require_https(cast(_EndpointClient, client).meta.endpoint_url, field="SDK endpoint")


class RequireTLSMiddleware:
    """Reject plaintext ASGI requests before authentication on deployed services."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = cast(str, scope["type"])
        if kind in {"http", "websocket"} and cast(str, scope.get("scheme")) not in {"https", "wss"}:
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await PlainTextResponse("HTTPS is required.", status_code=400)(scope, receive, send)
            return
        await self.app(scope, receive, send)
