"""The notification event catalogue: every kind of message the platform can send.

One entry per *event* — "a return is waiting for your review", "the regulator
accepted a return" — never per screen or per recipient. The catalogue is what
makes the platform extensible: a feature that wants to tell somebody something
adds one :class:`EventDefinition` here and calls ``notifications.publish`` with
it. Preferences, organisation policy, the delivery log, digests, branded email
and the in-app feed all pick the new event up from this file without another
change (backend/docs/notifications.md §Adding an event).

Pure data. Nothing here imports another feature, so every producer can import
the catalogue through ``app.notifications.public`` without creating an import
cycle or widening the feature-boundary baseline.

``legacy_prefixes`` maps the free-form ``type`` strings written before the
catalogue existed (``reporting.package.pending_approval``,
``reporting.deadline.due_soon_7:...``) onto an event, so the old ``emit`` call
sites inherit preferences and branded email without being rewritten first.
Longest prefix wins; an unmatched type falls back to :data:`GENERAL`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class Category(StrEnum):
    """How the preferences screen groups events. Order here is display order."""

    FILING = "filing"
    REGULATOR = "regulator"
    ATTESTATION = "attestation"
    DEADLINES = "deadlines"
    RISK = "risk"
    PLATFORM = "platform"


CATEGORY_LABELS: Final[MappingProxyType[Category, str]] = MappingProxyType(
    {
        Category.FILING: "Return review and filing",
        Category.REGULATOR: "Regulator responses",
        Category.ATTESTATION: "Signatures",
        Category.DEADLINES: "Reporting deadlines and rule changes",
        Category.RISK: "Risk alerts",
        Category.PLATFORM: "Other updates",
    }
)


class EmailMode(StrEnum):
    """How an event reaches a person's mailbox. The in-app feed always receives it."""

    IMMEDIATE = "immediate"
    DIGEST = "digest"
    OFF = "off"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class EventDefinition:
    """One kind of notification and the defaults every delivery decision starts from.

    ``email_locked`` is for messages a person must not be able to silence (a
    security notice, a regulator's final refusal): neither the person nor the
    organisation policy can move them off ``default_email``.
    """

    key: str
    category: Category
    label: str
    description: str
    severity: Severity
    default_email: EmailMode
    email_locked: bool = False
    legacy_prefixes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.email_locked and self.default_email is EmailMode.OFF:
            msg = f"{self.key}: a locked event cannot default to no email."
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Return review and filing — the internal maker-checker chain
# ---------------------------------------------------------------------------

FILING_REVIEW_REQUESTED: Final = EventDefinition(
    key="filing.review_requested",
    category=Category.FILING,
    label="A return is waiting for your review",
    description=(
        "A return reached a review stage you can act on — for example a Preparer sent it "
        "to Review, or a Reviewer sent it on to Validation."
    ),
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.package.pending_approval",),
)
FILING_STAGE_APPROVED: Final = EventDefinition(
    key="filing.stage_approved",
    category=Category.FILING,
    label="A return you prepared was approved",
    description="A reviewer or validator approved a return you prepared or handled earlier.",
    severity=Severity.INFO,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.package.approved",),
)
FILING_SENT_BACK: Final = EventDefinition(
    key="filing.sent_back",
    category=Category.FILING,
    label="A return was sent back to you",
    description="A reviewer or validator returned a return to your stage with comments to address.",
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.package.approval_rejected",),
)
FILING_FILED: Final = EventDefinition(
    key="filing.filed",
    category=Category.FILING,
    label="A return was filed with the regulator",
    description="A return you worked on was transmitted to the regulator.",
    severity=Severity.INFO,
    default_email=EmailMode.IMMEDIATE,
)
FILING_REVIEW_OVERDUE: Final = EventDefinition(
    key="filing.review_overdue",
    category=Category.FILING,
    label="Reminder: a return is still waiting for review",
    description=(
        "A return has been waiting at a stage you can act on for longer than your "
        "organisation's reminder window."
    ),
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
)
FILING_REVIEW_ESCALATED: Final = EventDefinition(
    key="filing.review_escalated",
    category=Category.FILING,
    label="Escalation: a return is stuck in review",
    description=(
        "A return has been waiting at one stage past your organisation's escalation window. "
        "Sent to the people who can reassign or decide it."
    ),
    severity=Severity.CRITICAL,
    default_email=EmailMode.IMMEDIATE,
)
FILING_FAILED: Final = EventDefinition(
    key="filing.failed",
    category=Category.FILING,
    label="A return could not be generated or sent",
    description="Generating a return, or transmitting it to the regulator, failed.",
    severity=Severity.CRITICAL,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.package_failed", "reporting.submission_failed"),
)

# ---------------------------------------------------------------------------
# Regulator responses
# ---------------------------------------------------------------------------

REGULATOR_RECEIVED: Final = EventDefinition(
    key="regulator.received",
    category=Category.REGULATOR,
    label="The regulator received a return",
    description="The regulator's system issued a receipt for a filed return and is validating it.",
    severity=Severity.INFO,
    default_email=EmailMode.IMMEDIATE,
)
REGULATOR_ACCEPTED: Final = EventDefinition(
    key="regulator.accepted",
    category=Category.REGULATOR,
    label="The regulator accepted a return",
    description="The regulator accepted a filed return.",
    severity=Severity.INFO,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.regulator.acknowledged",),
)
REGULATOR_SENT_BACK: Final = EventDefinition(
    key="regulator.sent_back",
    category=Category.REGULATOR,
    label="The regulator sent a return back for correction",
    description="The regulator returned a filed return for correction, usually with comments.",
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
    email_locked=True,
    legacy_prefixes=("reporting.regulator.rejected",),
)
REGULATOR_DECLINED: Final = EventDefinition(
    key="regulator.declined",
    category=Category.REGULATOR,
    label="The regulator declined a return",
    description="The regulator refused a filed return outright.",
    severity=Severity.CRITICAL,
    default_email=EmailMode.IMMEDIATE,
    email_locked=True,
    legacy_prefixes=("reporting.regulator.declined",),
)
REGULATOR_QUERY: Final = EventDefinition(
    key="regulator.query",
    category=Category.REGULATOR,
    label="The regulator raised a query",
    description="The regulator asked a question about a filed return that needs a response.",
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
)

# ---------------------------------------------------------------------------
# Signatures, deadlines, risk, other
# ---------------------------------------------------------------------------

SIGNATURE_REQUESTED: Final = EventDefinition(
    key="attestation.signature_requested",
    category=Category.ATTESTATION,
    label="Your signature is requested",
    description="You were nominated to sign a return.",
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("attestation.signature_requested",),
)
DEADLINE_APPROACHING: Final = EventDefinition(
    key="deadlines.approaching",
    category=Category.DEADLINES,
    label="A reporting deadline is approaching",
    description="A return is due in 7, 3 or 1 days and has not been filed.",
    severity=Severity.WARNING,
    default_email=EmailMode.DIGEST,
    legacy_prefixes=("reporting.deadline.due_soon",),
)
DEADLINE_MISSED: Final = EventDefinition(
    key="deadlines.missed",
    category=Category.DEADLINES,
    label="A reporting deadline has passed",
    description="A return is overdue, or a filing made by fallback still needs re-uploading.",
    severity=Severity.CRITICAL,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("reporting.deadline.overdue", "reporting.deadline.reupload_pending"),
)
RULE_CHANGE: Final = EventDefinition(
    key="deadlines.rule_change",
    category=Category.DEADLINES,
    label="A regulatory rule is changing",
    description="A rule that affects your returns changed status or is about to take effect.",
    severity=Severity.INFO,
    default_email=EmailMode.DIGEST,
    legacy_prefixes=("reporting.instrument.",),
)
CONTINGENCY_FUNDING: Final = EventDefinition(
    key="risk.contingency_funding",
    category=Category.RISK,
    label="Contingency funding plan activated or stood down",
    description="The contingency funding plan was escalated or de-escalated.",
    severity=Severity.CRITICAL,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("cfp.",),
)
METRIC_ALERT: Final = EventDefinition(
    key="risk.metric_alert",
    category=Category.RISK,
    label="A metric alert fired or cleared",
    description="A dashboard alert you subscribe to crossed or returned inside its threshold.",
    severity=Severity.WARNING,
    default_email=EmailMode.IMMEDIATE,
    legacy_prefixes=("bi.alert.",),
)
GENERAL: Final = EventDefinition(
    key="platform.general",
    category=Category.PLATFORM,
    label="Other updates",
    description="Anything else AequorOS needs to tell you.",
    severity=Severity.INFO,
    default_email=EmailMode.DIGEST,
)


def _build_registry(*events: EventDefinition) -> MappingProxyType[str, EventDefinition]:
    registry: dict[str, EventDefinition] = {}
    for event in events:
        if event.key in registry:
            msg = f"Duplicate notification event key {event.key!r}."
            raise ValueError(msg)
        registry[event.key] = event
    return MappingProxyType(registry)


#: Every event, in the order the preferences screen lists them within a category.
EVENTS: Final[MappingProxyType[str, EventDefinition]] = _build_registry(
    FILING_REVIEW_REQUESTED,
    FILING_STAGE_APPROVED,
    FILING_SENT_BACK,
    FILING_FILED,
    FILING_REVIEW_OVERDUE,
    FILING_REVIEW_ESCALATED,
    FILING_FAILED,
    REGULATOR_RECEIVED,
    REGULATOR_ACCEPTED,
    REGULATOR_SENT_BACK,
    REGULATOR_DECLINED,
    REGULATOR_QUERY,
    SIGNATURE_REQUESTED,
    DEADLINE_APPROACHING,
    DEADLINE_MISSED,
    RULE_CHANGE,
    CONTINGENCY_FUNDING,
    METRIC_ALERT,
    GENERAL,
)

_LEGACY_PREFIXES: Final[tuple[tuple[str, EventDefinition], ...]] = tuple(
    sorted(
        ((prefix, event) for event in EVENTS.values() for prefix in event.legacy_prefixes),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


class UnknownEvent(KeyError):
    """A caller named an event key the catalogue does not declare."""


def get(key: str) -> EventDefinition:
    try:
        return EVENTS[key]
    except KeyError as exc:
        raise UnknownEvent(key) from exc


def for_type(notification_type: str) -> EventDefinition:
    """The event a stored ``notifications.type`` belongs to.

    Catalogue keys match exactly or as the prefix of a dedupe-suffixed type
    (``filing.review_overdue:<package>:<stage>``); pre-catalogue types match
    through ``legacy_prefixes``; anything else is :data:`GENERAL`.
    """
    head = notification_type.split(":", 1)[0]
    if head in EVENTS:
        return EVENTS[head]
    for prefix, event in _LEGACY_PREFIXES:
        if notification_type.startswith(prefix):
            return event
    return GENERAL


__all__ = [
    "CATEGORY_LABELS",
    "EVENTS",
    "Category",
    "EmailMode",
    "EventDefinition",
    "Severity",
    "UnknownEvent",
    "for_type",
    "get",
]
