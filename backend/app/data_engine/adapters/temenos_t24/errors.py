"""Bank-facing error classification for the Temenos T24 adapter.

Mirrors ``market_data/errors.py``: T24/transport failures are classified into
:class:`TemenosErrorCode` values, each with a pre-authored, bank-safe message
template, recommended actions, and an escalation severity. Raw OFS response
text, IRIS/Open-API HTTP bodies, T24 error codes, stack traces, and internal
identifiers NEVER appear in bank-facing surfaces — they travel only in
:attr:`TemenosError.internal_detail`, which is logged for AequorOS engineering.

The connection to a bank's live core is the single most sensitive integration
we operate: a leaked OFS error can expose T24 application names, field ids, and
company codes. ``str(TemenosError)`` deliberately renders only the bank-facing
message so an accidentally surfaced exception cannot leak core internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

type Severity = Literal["informational", "warning", "urgent"]


class TemenosErrorCode(Enum):
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    SESSION_LIMIT_REACHED = "SESSION_LIMIT_REACHED"
    DOMAIN_NOT_PERMITTED = "DOMAIN_NOT_PERMITTED"
    ENQUIRY_NOT_FOUND = "ENQUIRY_NOT_FOUND"
    CORE_UNAVAILABLE = "CORE_UNAVAILABLE"
    COB_IN_PROGRESS = "COB_IN_PROGRESS"
    RATE_LIMITED = "RATE_LIMITED"
    RESPONSE_MALFORMED = "RESPONSE_MALFORMED"
    NO_DATA_RETURNED = "NO_DATA_RETURNED"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


@dataclass(frozen=True)
class BankFacingError:
    """A fully rendered, bank-safe error: classified code, actionable message,
    recommended actions, and escalation severity."""

    code: TemenosErrorCode
    message: str
    actions: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True)
class MessageTemplate:
    """Pre-authored bank-facing template — never a runtime-formatted core
    error. Placeholders are limited to business-level parameters such as
    ``{core_system}``, ``{timestamp}``, ``{domain}``, and ``{mode}``."""

    message: str
    actions: tuple[str, ...]
    severity: Severity


MESSAGE_TEMPLATES: dict[TemenosErrorCode, MessageTemplate] = {
    TemenosErrorCode.CREDENTIAL_INVALID: MessageTemplate(
        message=(
            "AequorOS could not sign in to your {core_system} core banking system. The "
            "connection credentials were rejected — this usually means the service user's "
            "password was rotated or its permissions changed at your end. Please verify the "
            "AequorOS service user in {core_system} and update the credentials. AequorOS will "
            "use the last successful data pull (from {timestamp}) until this is resolved."
        ),
        actions=(
            "Update credentials",
            "View last successful pull",
            "Contact your core banking team",
        ),
        severity="urgent",
    ),
    TemenosErrorCode.CREDENTIAL_EXPIRED: MessageTemplate(
        message=(
            "The credentials for your {core_system} connection have expired. Please issue new "
            "credentials for the AequorOS service user in {core_system} and enter them in "
            "AequorOS. The last successful data pull (from {timestamp}) remains in use until "
            "new credentials are provided."
        ),
        actions=(
            "Rotate credentials",
            "View last successful pull",
            "Contact your core banking team",
        ),
        severity="urgent",
    ),
    TemenosErrorCode.CREDENTIAL_REVOKED: MessageTemplate(
        message=(
            "Access for the AequorOS service user in your {core_system} core has been revoked. "
            "Scheduled data pulls are paused. If this was unintentional, please re-enable the "
            "service user in {core_system} and enter fresh credentials in AequorOS."
        ),
        actions=("Re-authorize and update credentials", "Contact your core banking team"),
        severity="urgent",
    ),
    TemenosErrorCode.SESSION_LIMIT_REACHED: MessageTemplate(
        message=(
            "Your {core_system} core reported that the AequorOS service user has reached its "
            "concurrent session limit. AequorOS has paused new requests and will retry. If this "
            "persists, ask your core banking team to raise the session limit for the service user."
        ),
        actions=(
            "View last successful pull",
            "Contact your core banking team",
            "Review pull schedule",
        ),
        severity="warning",
    ),
    TemenosErrorCode.DOMAIN_NOT_PERMITTED: MessageTemplate(
        message=(
            "The AequorOS service user is not permitted to read {domain} from your {core_system} "
            "core. Options: (1) grant the service user access to {domain} in {core_system}, "
            "(2) remove {domain} from the AequorOS data scope, (3) provide {domain} via manual "
            "upload."
        ),
        actions=("Grant access in core", "Remove from scope", "Use manual upload"),
        severity="warning",
    ),
    TemenosErrorCode.ENQUIRY_NOT_FOUND: MessageTemplate(
        message=(
            "AequorOS could not find the {core_system} enquiry or service needed for {domain}. "
            "This can happen when a core banking release renames or relocates a component. "
            "AequorOS support has been notified; the last successful pull for {domain} remains "
            "available."
        ),
        actions=(
            "View last successful pull",
            "Contact AequorOS support",
            "Use manual upload for this domain",
        ),
        severity="warning",
    ),
    TemenosErrorCode.CORE_UNAVAILABLE: MessageTemplate(
        message=(
            "Your {core_system} core banking system is currently unreachable. AequorOS will "
            "retry automatically and continue using the last successful data pull (from "
            "{timestamp}) in the meantime. No action is needed unless the outage persists."
        ),
        actions=("View last successful pull", "Contact your core banking team if this persists"),
        severity="informational",
    ),
    TemenosErrorCode.COB_IN_PROGRESS: MessageTemplate(
        message=(
            "Your {core_system} core is running its close-of-business processing, so a fresh "
            "pull could not be completed right now. AequorOS will retry after the close-of-"
            "business window and use the last successful pull (from {timestamp}) until then."
        ),
        actions=("View last successful pull", "Review pull schedule"),
        severity="informational",
    ),
    TemenosErrorCode.RATE_LIMITED: MessageTemplate(
        message=(
            "Your {core_system} core is temporarily limiting request volume for the AequorOS "
            "service user. AequorOS has slowed its requests and will retry shortly. The last "
            "successful data pull (from {timestamp}) remains available."
        ),
        actions=("View last successful pull", "Review pull schedule"),
        severity="informational",
    ),
    TemenosErrorCode.RESPONSE_MALFORMED: MessageTemplate(
        message=(
            "AequorOS received an unexpected response from your {core_system} core while reading "
            "{domain} and could not process it safely. To avoid ingesting incorrect data, this "
            "pull was stopped. AequorOS support has been notified; the last successful pull for "
            "{domain} remains in use."
        ),
        actions=("View last successful pull", "Contact AequorOS support"),
        severity="warning",
    ),
    TemenosErrorCode.NO_DATA_RETURNED: MessageTemplate(
        message=(
            "Your {core_system} core returned no records for {domain} for {timestamp}. If you "
            "expected data for this period, please check that {domain} is populated in "
            "{core_system} for that date. The last successful pull for {domain} remains in use."
        ),
        actions=(
            "View last successful pull",
            "Verify the source date in core",
            "Contact AequorOS support",
        ),
        severity="warning",
    ),
    TemenosErrorCode.NETWORK_ERROR: MessageTemplate(
        message=(
            "AequorOS could not reach your {core_system} core due to a network problem. AequorOS "
            "will retry automatically and use the last successful data pull (from {timestamp}) in "
            "the meantime."
        ),
        actions=("View last successful pull", "Contact AequorOS support if this persists"),
        severity="informational",
    ),
    TemenosErrorCode.CONFIGURATION_ERROR: MessageTemplate(
        message=(
            "The AequorOS connection to your {core_system} core is not fully configured for "
            "{domain}. AequorOS support has been notified and will complete the setup. Your "
            "existing data is unaffected."
        ),
        actions=("Contact AequorOS support", "View last successful pull"),
        severity="warning",
    ),
}


def render_bank_facing(code: TemenosErrorCode, **params: str) -> BankFacingError:
    """Render the pre-authored template for ``code`` into a bank-facing error.

    ``params`` supplies the business-level placeholders the template uses
    (``core_system``, ``timestamp``, ``domain``, ``mode``). A missing
    placeholder raises ``KeyError`` loudly — bank-facing text with raw
    ``{placeholders}`` left in it must never ship.
    """
    template = MESSAGE_TEMPLATES[code]
    return BankFacingError(
        code=code,
        message=template.message.format(**params),
        actions=template.actions,
        severity=template.severity,
    )


class TemenosError(Exception):
    """A classified Temenos/core-banking failure.

    ``bank_facing`` is the only part that may ever reach a bank-facing surface.
    ``internal_detail`` carries the raw OFS/IRIS/Open-API response text, HTTP
    status, T24 error code, or diagnostic context for AequorOS engineering logs
    and is NEVER shown to banks — ``str(error)`` deliberately renders only the
    bank-facing message so an accidentally surfaced exception cannot leak core
    internals.
    """

    def __init__(self, bank_facing: BankFacingError, internal_detail: str) -> None:
        super().__init__(bank_facing.message)
        self.bank_facing = bank_facing
        self.internal_detail = internal_detail

    @property
    def code(self) -> TemenosErrorCode:
        return self.bank_facing.code
