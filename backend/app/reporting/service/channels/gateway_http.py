"""One authenticated connection to the reporting gateway.

Every gateway call, whether a bank's filing or the channel operator's
enrolment, is made the same way (``gateway/ARCHITECTURE.md``):

1. Mutual TLS with the caller's client certificate.
2. An access token from ``/oauth/token``, obtained with a client assertion
   signed by the client key (``private_key_jwt``); the gateway binds the token
   to the certificate.

:class:`GatewaySession` owns both, plus the one place where a gateway answer
becomes a channel error. Callers choose the wording for each gateway error
code, so a bank never sees operator detail and nothing the gateway says is
shown unless it is in that table.
"""

from __future__ import annotations

import logging
import os
import ssl
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
import jwt

from app.core.config import is_undeployed_environment
from app.core.key_management.signing import jwk_thumbprint, load_p256_private_key
from app.core.key_management.types import KeyUnavailableError
from app.core.outbound import (
    DEFAULT_ALLOWED_SCHEMES,
    OutboundTargetBlocked,
    check_url,
    redirect_guard,
)
from app.core.tls import client_context
from app.reporting.service.channels.errors import (
    ChannelDowntimeError,
    ChannelError,
    ChannelPreconditionError,
)

logger = logging.getLogger(__name__)

CERT_HEADER = "X-Forwarded-Tls-Client-Cert"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ASSERTION_LIFETIME_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 30.0


def gateway_base_url(raw: str, *, field: str = "reporting gateway address") -> str:
    """The gateway address, checked by the egress guard (http only in local/test)."""
    base_url = raw.strip().rstrip("/")
    schemes = (
        frozenset({"https", "http"}) if is_undeployed_environment() else DEFAULT_ALLOWED_SCHEMES
    )
    try:
        check_url(base_url, field=field, allowed_schemes=schemes)
    except OutboundTargetBlocked as exc:
        logger.warning("reporting gateway blocked by the egress guard (%s)", exc.reason)
        raise ChannelPreconditionError(exc.message) from exc
    return base_url


@dataclass(frozen=True)
class GatewayIdentity:
    """Who is calling: one API client with its certificate and key."""

    base_url: str
    client_id: str
    certificate_pem: str
    private_key_pem: str
    #: Local/test only: send the certificate in the header the gateway's ingress
    #: would set, for a gateway running without a TLS terminator.
    forward_certificate: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    #: The ``kid`` the client's key is registered under in its JWKS. AequorOS
    #: registers bank clients under the key's RFC 7638 thumbprint (the default);
    #: an operator client keeps the id it was configured with.
    key_id: str | None = None


@dataclass(frozen=True)
class GatewayErrors:
    """The words a caller shows for each gateway outcome."""

    by_code: Mapping[str, str]
    refused: str
    unreachable: str
    unavailable: str
    no_token: str
    #: Staff surfaces only: add the gateway's own explanation to the message. A bank
    #: never sees gateway wording.
    show_detail: bool = False


class GatewayRefusal(ChannelError):
    """The gateway answered 4xx; ``code`` is its machine-readable reason."""

    def __init__(self, operator_message: str, *, code: str, status_code: int) -> None:
        super().__init__(operator_message, internal_detail=f"HTTP {status_code} {code}")
        self.code = code
        self.status_code = status_code


def _write_private(directory: str, name: str, content: str) -> str:
    path = Path(directory, name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    return str(path)


class GatewaySession:
    """An open, mutually authenticated HTTP client for one gateway identity."""

    def __init__(
        self,
        identity: GatewayIdentity,
        errors: GatewayErrors,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._identity = identity
        self._errors = errors
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = httpx.Client(
            base_url=identity.base_url,
            timeout=identity.timeout_seconds,
            verify=self._ssl_context(),
            trust_env=False,
            transport=transport,
            follow_redirects=False,
            headers=self._forwarded_certificate(),
            event_hooks={"response": [redirect_guard(field="reporting gateway redirect")]},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.close()

    # -- transport security --------------------------------------------------

    def _ssl_context(self) -> ssl.SSLContext:
        context = client_context()
        with tempfile.TemporaryDirectory(prefix="reporting-gateway-") as directory:
            context.load_cert_chain(
                _write_private(directory, "client.crt", self._identity.certificate_pem),
                _write_private(directory, "client.key", self._identity.private_key_pem),
            )
        return context

    def _forwarded_certificate(self) -> dict[str, str]:
        if not self._identity.forward_certificate:
            return {}
        if not is_undeployed_environment():
            raise ChannelPreconditionError(
                "Forwarding the client certificate in a header is permitted only in local/test."
            )
        return {CERT_HEADER: quote(self._identity.certificate_pem, safe="")}

    # -- authentication ------------------------------------------------------

    def client_assertion(self) -> str:
        try:
            key = load_p256_private_key(self._identity.private_key_pem)
        except KeyUnavailableError as exc:
            raise ChannelPreconditionError(f"The gateway client key is unusable: {exc}") from exc
        now = int(self._clock().timestamp())
        client_id = self._identity.client_id
        return jwt.encode(
            {
                "iss": client_id,
                "sub": client_id,
                "aud": self._identity.base_url + "/oauth/token",
                "jti": uuid.uuid4().hex,
                "iat": now,
                "exp": now + ASSERTION_LIFETIME_SECONDS,
            },
            key,
            algorithm="ES256",
            headers={"kid": self._identity.key_id or jwk_thumbprint(key.public_key())},
        )

    def access_token(self, scope: str) -> str:
        data = self.send(
            "POST",
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": self.client_assertion(),
                "scope": scope,
            },
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ChannelError(
                self._errors.no_token, internal_detail=f"token response keys={sorted(data)}"
            )
        return token

    # -- requests ------------------------------------------------------------

    def send(  # noqa: PLR0913 - one place maps every transport outcome
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: object | None = None,
        data: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """One request; the JSON object answered, or ``{}`` for an empty body."""
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._client.request(
                method, path, json=json_body, data=data, headers=request_headers
            )
        except httpx.HTTPError as exc:
            raise ChannelDowntimeError(
                self._errors.unreachable, internal_detail=type(exc).__name__
            ) from exc
        if response.status_code >= 500:
            raise ChannelDowntimeError(
                self._errors.unavailable, internal_detail=f"HTTP {response.status_code}"
            )
        try:
            body: object = cast(object, response.json()) if response.content else None
        except ValueError:
            body = None
        payload: dict[str, Any] = cast(dict[str, Any], body) if isinstance(body, dict) else {}
        if response.status_code >= 400:
            code = str(payload.get("code") or "")
            message = self._errors.by_code.get(code, self._errors.refused)
            detail = payload.get("detail")
            if self._errors.show_detail and isinstance(detail, str) and detail:
                message = f"{message} The gateway said: {detail}"
            raise GatewayRefusal(message, code=code, status_code=response.status_code)
        return payload
