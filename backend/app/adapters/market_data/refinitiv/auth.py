"""Refinitiv (LSEG) OAuth 2.0 client-credentials authentication (§7.1).

The credential dict stored in Vault (per storage.md §7) carries:
``client_id``, ``client_secret``, ``scope``, ``subscription_type``,
``refresh_token``, ``token_endpoint``, and ``contact_admin``.

MVP ships :class:`SimulatedTokenProvider`: it validates credential shape and
returns a fake short-lived session token, so the whole adapter is exercisable
against fixtures with zero live vendor calls (§14.1, §16.3). Wiring the real
RDP token endpoint is Phase 2 (§14.2) and slots in as a new
:class:`TokenProvider` implementation — nothing else in the adapter changes.

Credential failures are always classified (§12): providers raise
:class:`MarketDataError` whose ``internal_detail`` may quote the simulated
raw vendor response (including the non-secret ``client_id``), while
:func:`authenticate_credentials` converts them into a bank-facing
``AuthResult`` and NEVER raises for credential problems. ``client_secret``
values are never echoed anywhere, including internal detail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from app.adapters.market_data.base import AuthResult, CredentialSet
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.refinitiv.transport import bank_facing_for

# Every key the §7.1 Vault credential structure documents.
CREDENTIAL_KEYS: tuple[str, ...] = (
    "client_id",
    "client_secret",
    "scope",
    "subscription_type",
    "refresh_token",
    "token_endpoint",
    "contact_admin",
)
# Keys that must be present and non-empty for a token acquisition attempt.
REQUIRED_CREDENTIAL_KEYS: tuple[str, ...] = ("client_id", "client_secret")

# §7.1: RDP OAuth tokens are short-lived (typically 4 hours).
TOKEN_LIFETIME = timedelta(hours=4)

# Testing/fixture hook: a credentials dict carrying {"simulate": <state>}
# makes the simulated provider reproduce that vendor-side failure.
_SIMULATED_FAILURES: dict[str, BankFacingErrorCode] = {
    "expired": BankFacingErrorCode.CREDENTIAL_EXPIRED,
    "revoked": BankFacingErrorCode.CREDENTIAL_REVOKED,
}


class TokenProvider(Protocol):
    """Acquires an RDP session token from a credential dict."""

    def acquire(self, credentials: dict[str, Any]) -> tuple[str, datetime]:
        """Return ``(session_token, expires_at)`` or raise MarketDataError."""
        ...


class SimulatedTokenProvider:
    """MVP token provider: shape validation plus simulated vendor responses.

    Consumes no quota and touches no network. The live RDP token-endpoint
    provider (Phase 2) replaces this class behind the same protocol.
    """

    def acquire(self, credentials: dict[str, Any]) -> tuple[str, datetime]:
        client_id = credentials.get("client_id")
        for key in REQUIRED_CREDENTIAL_KEYS:
            value = credentials.get(key)
            if not isinstance(value, str) or not value.strip():
                # client_id is an application identifier, not a secret; it may
                # appear in internal diagnostics. client_secret never does.
                raise MarketDataError(
                    bank_facing_for(BankFacingErrorCode.CREDENTIAL_INVALID),
                    internal_detail=(
                        "simulated RDP token endpoint rejected the request: missing or "
                        f"empty {key!r} for client_id={client_id!r} "
                        '(raw vendor body: {"error": "invalid_client"})'
                    ),
                )
        simulate = credentials.get("simulate")
        if isinstance(simulate, str) and simulate in _SIMULATED_FAILURES:
            code = _SIMULATED_FAILURES[simulate]
            raise MarketDataError(
                bank_facing_for(code),
                internal_detail=(
                    f"simulated RDP token endpoint returned {simulate!r} for "
                    f"client_id={client_id!r}"
                ),
            )
        return f"sim-rdp-{uuid4().hex}", datetime.now(UTC) + TOKEN_LIFETIME


def acquire_session_token(
    provider: TokenProvider, credentials: CredentialSet
) -> tuple[str, datetime]:
    """Acquire a short-lived session token for one pull cycle.

    Checks the CredentialSet's own expiry before asking the provider: a
    credential set past its ``expires_at`` is classified CREDENTIAL_EXPIRED
    without a vendor round-trip. Raises :class:`MarketDataError` on any
    credential problem; callers building bank-facing results catch it.
    """
    if credentials.expires_at is not None and credentials.expires_at <= datetime.now(UTC):
        raise MarketDataError(
            bank_facing_for(BankFacingErrorCode.CREDENTIAL_EXPIRED),
            internal_detail=(
                f"credential set for institution {credentials.institution_id} expired at "
                f"{credentials.expires_at.isoformat()}"
            ),
        )
    return provider.acquire(credentials.credentials)


def authenticate_credentials(provider: TokenProvider, credentials: CredentialSet) -> AuthResult:
    """Map token acquisition into the §4.1 ``AuthResult`` contract.

    Credential problems surface as ``AuthResult(success=False)`` with a
    bank-facing error code and message — never as exceptions (§4.3), and
    never carrying raw vendor detail (§12.3).
    """
    try:
        session_token, expires_at = acquire_session_token(provider, credentials)
    except MarketDataError as exc:
        return AuthResult(
            success=False,
            session_token=None,
            expires_at=None,
            error_code=exc.bank_facing.code.value,
            error_message=exc.bank_facing.message,
        )
    return AuthResult(
        success=True,
        session_token=session_token,
        expires_at=expires_at,
        error_code=None,
        error_message=None,
    )
