"""Authenticated-session seam for the Temenos T24 adapter.

A :class:`TemenosSession` is an opaque, already-authenticated handle a
transport uses to fetch domains. How it is obtained differs per mode:

- **OFS**: an ``OFS.SOURCE`` sign-on producing a session token.
- **IRIS**: an OAuth2 / bearer flow against the IRIS provider container.
- **Open API**: a client-credentials token against the Transact API gateway.

The provider is a seam, exactly like the transport: MVP ships only
:class:`SimulatedSessionProvider`, which mints a well-formed session WITHOUT
any network call, so the whole stack is testable end-to-end via fixtures. Live
providers plug in behind the :class:`SessionProvider` Protocol in a later phase
without touching extractors, catalogs, or the pull orchestration.

Credentials never live on the session object beyond what a transport needs to
present; raw secrets are held by the credential vault and passed in, never
logged. A failed sign-on surfaces as a classified :class:`TemenosError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

CONNECTION_MODES: tuple[str, ...] = ("OFS", "IRIS", "OPEN_API")


@dataclass(frozen=True)
class TemenosCredentials:
    """The secret material a provider needs to authenticate. Held in memory
    only for the duration of a sign-on; never serialized into a session, a
    log line, or a bank-facing surface."""

    username: str = ""
    password: str = ""
    api_key: str = ""
    client_id: str = ""
    client_secret: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    _KNOWN = ("username", "password", "api_key", "client_id", "client_secret")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TemenosCredentials:
        """Build credentials from a bank-supplied dict; unknown keys go to extra."""
        known = {k: str(data[k]) for k in cls._KNOWN if k in data and data[k] is not None}
        extra = {
            str(k): str(v)
            for k, v in data.items()
            if k not in cls._KNOWN and v is not None
        }
        return cls(**known, extra=extra)


# The credential fields each connection mode requires. Validation checks shape,
# not liveness, so a misconfigured connection fails fast without a live core.
REQUIRED_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "OFS": ("username", "password"),
    "IRIS": ("client_id", "client_secret"),
    "OPEN_API": ("client_id", "client_secret"),
}


def missing_credential_fields(mode: str, credentials: TemenosCredentials) -> list[str]:
    """The required credential fields absent for a mode (empty if well-formed).

    IRIS/Open API accept a single ``api_key`` bearer as an alternative to the
    client-credentials pair.
    """
    if mode in ("IRIS", "OPEN_API") and credentials.api_key:
        return []
    required = REQUIRED_CREDENTIAL_FIELDS.get(mode, ())
    return [name for name in required if not getattr(credentials, name, "")]


@dataclass(frozen=True)
class TemenosSession:
    """An authenticated handle for one connection mode.

    ``token`` is whatever the mode's sign-on returned (an OFS session id, a
    bearer token). ``company`` is the T24 company/entity context the session is
    scoped to. ``expires_epoch`` is advisory — the transport re-signs on when a
    request is rejected as expired, so a slightly stale clock never blocks a
    pull.
    """

    mode: str
    endpoint: str
    token: str
    company: str | None = None
    expires_epoch: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionProvider(Protocol):
    """Establishes an authenticated :class:`TemenosSession` for a mode.

    Implementations translate any authentication fault into a classified
    :class:`~app.adapters.temenos_t24.errors.TemenosError` — never a raw
    transport exception, and never one carrying core-internal text.
    """

    def sign_on(
        self,
        mode: str,
        endpoint: str,
        credentials: TemenosCredentials,
        *,
        company: str | None = None,
    ) -> TemenosSession: ...


class SimulatedSessionProvider:
    """Mints a well-formed session without any network call.

    This is the MVP/default provider and the one every fixture test uses: it
    proves the sign-on -> fetch -> extract -> translate contract offline. The
    token is a deterministic, secret-free marker so nothing sensitive can leak
    through a session that is logged or persisted by mistake.
    """

    def __init__(self, *, token_prefix: str = "SIMULATED-T24-SESSION") -> None:
        self._token_prefix = token_prefix

    def sign_on(
        self,
        mode: str,
        endpoint: str,
        credentials: TemenosCredentials,
        *,
        company: str | None = None,
    ) -> TemenosSession:
        if mode not in CONNECTION_MODES:
            known = ", ".join(CONNECTION_MODES)
            raise ValueError(f"Unknown connection mode {mode!r}. Known modes: {known}.")
        # Deterministic, credential-free token. The username (not the secret)
        # is echoed so a captured session is still traceable to a service user.
        token = f"{self._token_prefix}:{mode}:{credentials.username or 'anon'}"
        return TemenosSession(
            mode=mode,
            endpoint=endpoint,
            token=token,
            company=company,
            metadata={"provider": "simulated"},
        )
