"""Bloomberg credential handling (market_data_adapter.md §6.1).

Bloomberg B-PIPE and Data License subscriptions use application-identifier
based auth: the bank provisions permissions for a specific application
identifier on their subscription and hands AequorOS credentials scoped to it.
The Vault-stored credential structure carries ``application_identifier``,
``serial_number``, ``authentication_endpoint``, ``certificate`` (PEM client
certificate — Bloomberg uses cert-based auth for enterprise APIs),
``subscription_tier``, and ``contact_admin`` (the bank's Bloomberg
administrator, surfaced in error messages so the bank knows who to contact).

Live seam (Phase 2): a ``BlpapiSessionProvider`` implementing
:class:`BloombergSessionProvider` will perform certificate-based auth against
``authentication_endpoint`` and return a real session handle. MVP ships
:class:`SimulatedSessionProvider`, which validates the §6.1 credential shape
without any live vendor call — CI and day-to-day development never require
Bloomberg access (§6.5). Raw vendor diagnostics travel only in
``MarketDataError.internal_detail`` and never surface to banks (§12.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from app.adapters.market_data.errors import (
    BankFacingError,
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)

if TYPE_CHECKING:
    from app.adapters.market_data.base import CredentialSet
    from app.adapters.market_data.scope_taxonomy import DataScope

VENDOR_DISPLAY_NAME = "Bloomberg"

# §6.1 credential structure. The certificate is a PEM string.
REQUIRED_CREDENTIAL_FIELDS: tuple[str, ...] = (
    "application_identifier",
    "serial_number",
    "certificate",
)

# PEM boundary markers for the SSL client certificate Bloomberg enterprise
# APIs authenticate with (§6.1). Structural validation only — full X.509
# chain verification is the live session provider's concern (Phase 2).
_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"

# The auth seam does not know the last-successful-pull time; the notification
# layer owns re-rendering messages with the real cache timestamp (§12.4).
_NO_CACHE_TIMESTAMP = "not available"

# Business-level parameter appended to the pre-authored CREDENTIAL_INVALID
# template when the credential set names the bank's Bloomberg administrator.
_CONTACT_ADMIN_SENTENCE = " Your Bloomberg administrator on record is {contact_admin}."


@dataclass(frozen=True)
class BloombergSession:
    """An authenticated Bloomberg session handle for one pull cycle.

    Short-lived and never persisted (§4.1). ``scopes_permitted`` is the
    simulation seam for subscription-permission failures: when False, every
    request made on this session classifies as SCOPE_NOT_PERMITTED at pull
    time (a live session raises the equivalent from the vendor response).
    """

    application_identifier: str
    serial_number: str
    authentication_endpoint: str | None
    subscription_tier: str | None
    scopes_permitted: bool = True


class BloombergSessionProvider(Protocol):
    """Opens vendor sessions from credential sets.

    ``open_session`` raises :class:`MarketDataError` (bank-facing classified,
    raw vendor detail internal-only) when the vendor rejects the credentials.
    """

    def open_session(self, credentials: CredentialSet) -> BloombergSession: ...


class SimulatedSessionProvider:
    """Shape-validates §6.1 credentials without any live Bloomberg call.

    Simulation controls (used by fixtures and the onboarding sandbox):
    - ``{"simulate": "lapsed"}`` — the vendor reports a lapsed subscription.
    - ``{"simulate": "not_permitted"}`` — auth succeeds but every scope pull
      classifies as SCOPE_NOT_PERMITTED.
    - ``"simulated_vendor_error"`` — raw vendor error text the simulated
      vendor "returned"; recorded in ``internal_detail`` only, proving raw
      vendor messages never reach bank-facing surfaces.
    """

    def open_session(self, credentials: CredentialSet) -> BloombergSession:
        payload = credentials.credentials
        raw_vendor_error = str(payload.get("simulated_vendor_error") or "n/a")

        if payload.get("simulate") == "lapsed":
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.SUBSCRIPTION_LAPSED, vendor=VENDOR_DISPLAY_NAME
                ),
                internal_detail=(
                    f"simulated lapsed subscription; raw vendor response: {raw_vendor_error}"
                ),
            )

        missing = [name for name in REQUIRED_CREDENTIAL_FIELDS if not payload.get(name)]
        if missing:
            raise MarketDataError(
                _credential_invalid(payload.get("contact_admin")),
                internal_detail=(
                    f"credential shape invalid: missing {', '.join(missing)}; "
                    f"raw vendor response: {raw_vendor_error}"
                ),
            )

        # The certificate must be a structurally valid PEM before any live
        # provider would attempt a handshake; a malformed cert is a credential
        # problem, classified so nothing internal leaks (§12.3).
        if not certificate_is_valid_pem(str(payload["certificate"])):
            raise MarketDataError(
                _credential_invalid(payload.get("contact_admin")),
                internal_detail=(
                    "credential shape invalid: certificate is not a valid PEM client "
                    f"certificate; raw vendor response: {raw_vendor_error}"
                ),
            )

        # Cert-based auth: an expired client certificate is CREDENTIAL_EXPIRED,
        # distinct from an invalid one, so the bank sees the right remediation.
        expiry = _certificate_expiry(payload.get("certificate_not_after"))
        if expiry is not None and expiry <= datetime.now(UTC):
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.CREDENTIAL_EXPIRED,
                    vendor=VENDOR_DISPLAY_NAME,
                    timestamp=_NO_CACHE_TIMESTAMP,
                ),
                internal_detail=(
                    f"client certificate expired at {expiry.isoformat()} for application "
                    f"{payload.get('application_identifier')!r}"
                ),
            )

        return BloombergSession(
            application_identifier=str(payload["application_identifier"]),
            serial_number=str(payload["serial_number"]),
            authentication_endpoint=_optional_str(payload.get("authentication_endpoint")),
            subscription_tier=_optional_str(payload.get("subscription_tier")),
            scopes_permitted=payload.get("simulate") != "not_permitted",
        )


class LiveBloombergSessionProvider:
    """Phase 2 seam for certificate-based B-PIPE / Data License auth (§6.1).

    Fenced until real credentials are wired (questions/Q03): opening a session
    lazily imports the vendor ``blpapi`` driver (torch lazy-import pattern) and,
    because live transport is not configured in MVP, classifies as
    ``VENDOR_UNAVAILABLE`` before any network call. When Phase 2 lands, this
    method performs the TLS mutual-auth handshake against
    ``authentication_endpoint`` using the PEM ``certificate`` and returns a
    real :class:`BloombergSession`; nothing else in the adapter changes.
    """

    def open_session(self, credentials: CredentialSet) -> BloombergSession:
        try:
            import blpapi  # type: ignore[import-not-found]  # noqa: F401, PLC0415
        except ImportError as exc:
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.VENDOR_UNAVAILABLE,
                    vendor=VENDOR_DISPLAY_NAME,
                    timestamp=_NO_CACHE_TIMESTAMP,
                ),
                internal_detail=(
                    "live Bloomberg session provider requires the blpapi driver, which is "
                    "not installed (Phase 2)"
                ),
            ) from exc
        # Driver present but live wiring is still fenced (Q03): never attempt a
        # real handshake until credentials are provisioned and the fence lifts.
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.VENDOR_UNAVAILABLE,
                vendor=VENDOR_DISPLAY_NAME,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail=(
                f"live Bloomberg auth is fenced (Phase 2) for institution "
                f"{credentials.institution_id}"
            ),
        )


def certificate_is_valid_pem(certificate: str) -> bool:
    """True if ``certificate`` is a structurally well-formed PEM certificate.

    Structural check only (matching BEGIN/END markers wrapping a non-empty
    body): full X.509 parsing and chain validation belong to the live
    handshake, not to shape validation.
    """
    text = certificate.strip()
    if not text.startswith(_PEM_BEGIN) or not text.endswith(_PEM_END):
        return False
    body = text[len(_PEM_BEGIN) : len(text) - len(_PEM_END)].strip()
    return bool(body)


def _certificate_expiry(value: object) -> datetime | None:
    """Parse an optional ISO-8601 certificate expiry into an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def ensure_scope_permitted(session: BloombergSession, scope: DataScope) -> None:
    """Classify a permission-denied session as SCOPE_NOT_PERMITTED (§12.1).

    Called by extractors before any vendor request so subscription-permission
    failures surface per scope at pull time, not as auth failures.
    """
    if session.scopes_permitted:
        return
    raise MarketDataError(
        render_bank_facing(
            BankFacingErrorCode.SCOPE_NOT_PERMITTED,
            vendor=VENDOR_DISPLAY_NAME,
            scope=scope.value,
        ),
        internal_detail=(
            f"subscription (serial {session.serial_number}) does not permission scope {scope.value}"
        ),
    )


def _credential_invalid(contact_admin: object) -> BankFacingError:
    rendered = render_bank_facing(
        BankFacingErrorCode.CREDENTIAL_INVALID,
        vendor=VENDOR_DISPLAY_NAME,
        timestamp=_NO_CACHE_TIMESTAMP,
    )
    if not contact_admin:
        return rendered
    return BankFacingError(
        code=rendered.code,
        message=rendered.message + _CONTACT_ADMIN_SENTENCE.format(contact_admin=contact_admin),
        actions=rendered.actions,
        severity=rendered.severity,
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value else None
