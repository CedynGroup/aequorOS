"""Bank-facing error classification (market_data_adapter.md §12).

Vendor errors are classified into :class:`BankFacingErrorCode` values, each
carrying a pre-authored message template, recommended actions, and an
escalation severity. Raw vendor error messages, HTTP status codes, stack
traces, and internal identifiers never appear in bank-facing surfaces (§12.3);
they travel in :class:`MarketDataError.internal_detail`, which is logged for
AequorOS engineering and never rendered to a bank.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

type Severity = Literal["informational", "warning", "urgent"]


class BankFacingErrorCode(Enum):
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    SUBSCRIPTION_LAPSED = "SUBSCRIPTION_LAPSED"
    SCOPE_NOT_PERMITTED = "SCOPE_NOT_PERMITTED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    VENDOR_UNAVAILABLE = "VENDOR_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    STALE_DATA = "STALE_DATA"
    NETWORK_ERROR = "NETWORK_ERROR"


@dataclass(frozen=True)
class BankFacingError:
    """A fully rendered, bank-safe error: classified code, actionable message,
    recommended actions, and escalation severity (§12.1)."""

    code: BankFacingErrorCode
    message: str
    actions: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True)
class MessageTemplate:
    """Pre-authored bank-facing template (§16.8): never runtime-formatted
    vendor errors. Placeholders are limited to business-level parameters
    such as ``{vendor}``, ``{timestamp}``, and ``{scope}``."""

    message: str
    actions: tuple[str, ...]
    severity: Severity


MESSAGE_TEMPLATES: dict[BankFacingErrorCode, MessageTemplate] = {
    BankFacingErrorCode.CREDENTIAL_INVALID: MessageTemplate(
        message=(
            "Your {vendor} credentials failed authentication. This usually means the "
            "credentials have been rotated or the permissions have changed at your end. "
            "Please check your {vendor} account or contact your {vendor} administrator. "
            "AequorOS will use cached market data (last updated: {timestamp}) until this "
            "is resolved."
        ),
        actions=("Update credentials", "View last successful pull", "Switch to manual upload"),
        severity="urgent",
    ),
    BankFacingErrorCode.CREDENTIAL_EXPIRED: MessageTemplate(
        message=(
            "Your {vendor} credentials have expired. Please generate new credentials in "
            "your {vendor} account and enter them in AequorOS. AequorOS will use cached "
            "market data (last updated: {timestamp}) until new credentials are provided."
        ),
        actions=("Rotate credentials", "View last successful pull", "Switch to manual upload"),
        severity="urgent",
    ),
    BankFacingErrorCode.CREDENTIAL_REVOKED: MessageTemplate(
        message=(
            "Access to your {vendor} subscription has been revoked. Scheduled market data "
            "pulls are paused. If this was unintentional, please re-authorize AequorOS in "
            "your {vendor} account and enter fresh credentials."
        ),
        actions=("Re-authorize and update credentials", "Switch to manual upload"),
        severity="urgent",
    ),
    BankFacingErrorCode.SUBSCRIPTION_LAPSED: MessageTemplate(
        message=(
            "Your {vendor} subscription appears to have lapsed or been suspended. Please "
            "contact your {vendor} account manager. AequorOS will continue using cached "
            "data and manual upload for market data until your subscription is restored."
        ),
        actions=("Contact vendor", "Switch to manual upload", "Contact AequorOS support"),
        severity="urgent",
    ),
    BankFacingErrorCode.SCOPE_NOT_PERMITTED: MessageTemplate(
        message=(
            "Your {vendor} subscription does not include access to {scope}. Options: "
            "(1) upgrade your subscription to include this dataset, (2) remove {scope} "
            "from AequorOS data scope, (3) use manual upload for {scope}."
        ),
        actions=("Contact vendor about upgrade", "Remove from scope", "Use manual upload"),
        severity="warning",
    ),
    BankFacingErrorCode.QUOTA_EXHAUSTED: MessageTemplate(
        message=(
            "You've reached your {vendor} monthly quota. Additional pulls will incur "
            "overage charges from {vendor}. AequorOS has paused automatic pulls to avoid "
            "unexpected costs. You can review your quota consumption and adjust your cap, "
            "or approve an override for critical pulls."
        ),
        actions=("Review quota", "Increase cap", "Approve override", "Contact vendor"),
        severity="warning",
    ),
    BankFacingErrorCode.VENDOR_UNAVAILABLE: MessageTemplate(
        message=(
            "{vendor} is currently unavailable. AequorOS will retry automatically and use "
            "cached market data (last updated: {timestamp}) in the meantime. No action is "
            "needed unless the outage persists."
        ),
        actions=("View last successful pull", "Switch to manual upload"),
        severity="informational",
    ),
    BankFacingErrorCode.RATE_LIMITED: MessageTemplate(
        message=(
            "{vendor} is temporarily limiting request volume on your subscription. "
            "AequorOS has slowed its pulls and will retry shortly. Cached market data "
            "(last updated: {timestamp}) remains available."
        ),
        actions=("View last successful pull", "Review pull schedule"),
        severity="informational",
    ),
    BankFacingErrorCode.UNKNOWN_INSTRUMENT: MessageTemplate(
        message=(
            "{vendor} did not recognize one or more instruments needed for {scope}. This "
            "can happen when a vendor retires or renames an instrument. AequorOS support "
            "has been notified; cached data for {scope} remains available."
        ),
        actions=("View last successful pull", "Use manual upload for this scope"),
        severity="warning",
    ),
    BankFacingErrorCode.STALE_DATA: MessageTemplate(
        message=(
            "The market data for {scope} is stale (last updated: {timestamp}) and a fresh "
            "pull could not be completed. Calculations using this data will be clearly "
            "attributed as based on stale market data."
        ),
        actions=("Trigger a refresh", "Use manual upload", "Review data source status"),
        severity="warning",
    ),
    BankFacingErrorCode.NETWORK_ERROR: MessageTemplate(
        message=(
            "AequorOS could not reach {vendor} due to a network problem. AequorOS will "
            "retry automatically and use cached market data (last updated: {timestamp}) "
            "in the meantime."
        ),
        actions=("View last successful pull", "Contact AequorOS support if this persists"),
        severity="informational",
    ),
}


def render_bank_facing(code: BankFacingErrorCode, **params: str) -> BankFacingError:
    """Render the pre-authored template for ``code`` into a bank-facing error.

    ``params`` supplies the business-level placeholders the template uses
    (``vendor``, ``timestamp``, ``scope``). A missing placeholder raises
    ``KeyError`` loudly — bank-facing text with raw ``{placeholders}`` left
    in it must never ship.
    """
    template = MESSAGE_TEMPLATES[code]
    return BankFacingError(
        code=code,
        message=template.message.format(**params),
        actions=template.actions,
        severity=template.severity,
    )


class MarketDataError(Exception):
    """A classified market data failure.

    ``bank_facing`` is the only part that may ever reach a bank-facing
    surface. ``internal_detail`` carries the raw vendor message, HTTP status,
    or diagnostic context for AequorOS engineering logs and is NEVER shown to
    banks (§12.3) — ``str(error)`` deliberately renders only the bank-facing
    message so an accidentally surfaced exception cannot leak vendor internals.
    """

    def __init__(self, bank_facing: BankFacingError, internal_detail: str) -> None:
        super().__init__(bank_facing.message)
        self.bank_facing = bank_facing
        self.internal_detail = internal_detail
