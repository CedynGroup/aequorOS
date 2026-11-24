"""Structured messages and their branded rendering (backend/docs/notifications.md).

A producer never writes email HTML. It hands :func:`app.notifications.public.publish`
a :class:`Message` — a title, a one-paragraph summary, labelled facts, an
optional quoted comment and one call to action — and this module renders it
three ways from the same data: the in-app feed row, a branded HTML email and
its plain-text alternative. One layout means every email the platform sends
looks like one product, and a new event costs no template.

**What the HTML deliberately does not do.** It loads nothing remote: no hosted
logo, no web font, no tracking pixel. A remote image in a bank's mail client is
a read receipt nobody consented to, and a blocked image is a broken header. The
wordmark is text, styles are inline, and the layout is tables, because that is
what Outlook renders.

Rendering is pure: no database, no settings lookup beyond what the caller
passes in :class:`Branding`, so it is tested without fixtures and the digest
reuses it unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.notifications.catalog import CATEGORY_LABELS, EventDefinition, Severity

_TEMPLATES: Final = Path(__file__).with_name("templates")

_ENV: Final = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

#: Accent colour per severity, used for the bar above the headline and the label.
_SEVERITY_ACCENT: Final[Mapping[Severity, str]] = {
    Severity.INFO: "#3B6FE0",
    Severity.WARNING: "#C27A0E",
    Severity.CRITICAL: "#C2362B",
}
_SEVERITY_LABEL: Final[Mapping[Severity, str]] = {
    Severity.INFO: "Update",
    Severity.WARNING: "Action needed",
    Severity.CRITICAL: "Urgent",
}

#: Feed and email bodies are stored and displayed; a quoted comment can be long.
QUOTE_LIMIT: Final = 1200


@dataclass(frozen=True, slots=True)
class Fact:
    """One labelled value in the message's details table ("Return", "Due")."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class Action:
    """The single call to action. ``path`` is relative to the bank app's origin."""

    label: str
    path: str


@dataclass(frozen=True, slots=True)
class Message:
    """What a producer says. Everything shown to a person is derived from this."""

    title: str
    summary: str
    facts: tuple[Fact, ...] = ()
    quote: str | None = None
    quote_label: str | None = None
    action: Action | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.summary.strip():
            msg = "A notification needs a title and a summary."
            raise ValueError(msg)

    def feed_body(self) -> str:
        """The in-app body: the summary, then the quoted comment when there is one."""
        if self.quote:
            label = self.quote_label or "Comment"
            return f"{self.summary}\n\n{label}: {_clip(self.quote)}"
        return self.summary

    def to_data(self) -> dict[str, object]:
        """The JSON stored on the notification row, from which email re-renders."""
        return {
            "facts": [{"label": fact.label, "value": fact.value} for fact in self.facts],
            "quote": None if self.quote is None else _clip(self.quote),
            "quote_label": self.quote_label,
            "action": None
            if self.action is None
            else {"label": self.action.label, "path": self.action.path},
        }

    @classmethod
    def from_stored(
        cls, *, title: str, body: str, data: Mapping[str, object], action_path: str | None
    ) -> Message:
        """Rebuild the message from a notification row (legacy rows have no ``data``)."""
        facts_raw = data.get("facts")
        facts: list[Fact] = []
        if isinstance(facts_raw, list):
            for item in cast(list[object], facts_raw):
                if isinstance(item, dict):
                    entry = cast(dict[str, object], item)
                    label, value = entry.get("label"), entry.get("value")
                    if isinstance(label, str) and isinstance(value, str):
                        facts.append(Fact(label, value))
        action: Action | None = None
        action_raw = data.get("action")
        if isinstance(action_raw, dict):
            entry = cast(dict[str, object], action_raw)
            label, path = entry.get("label"), entry.get("path")
            if isinstance(label, str) and isinstance(path, str):
                action = Action(label, path)
        if action is None and action_path:
            action = Action("Open in AequorOS", action_path)
        quote = data.get("quote")
        quote_label = data.get("quote_label")
        summary = body
        if isinstance(quote, str) and quote:
            # The stored body already carries the quote for the feed; the email
            # shows it as its own block, so strip it from the summary.
            marker = f"\n\n{quote_label if isinstance(quote_label, str) else 'Comment'}: "
            summary = body.split(marker, 1)[0]
        return cls(
            title=title,
            summary=summary or title,
            facts=tuple(facts),
            quote=quote if isinstance(quote, str) and quote else None,
            quote_label=quote_label if isinstance(quote_label, str) else None,
            action=action,
        )


@dataclass(frozen=True, slots=True)
class Branding:
    """Deployment and organisation context the layout needs."""

    app_base_url: str
    organization_name: str | None = None
    product_name: str = "AequorOS"

    def url(self, path: str) -> str:
        if path.startswith(("https://", "http://")):
            return path
        return f"{self.app_base_url.rstrip('/')}/{path.lstrip('/')}"

    @property
    def preferences_url(self) -> str:
        return self.url("/settings/notifications")


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class DigestItem:
    """One notification inside a digest email."""

    event: EventDefinition
    message: Message
    severity: Severity
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _Section:
    title: str
    items: Sequence[DigestItem] = field(default_factory=tuple)


def _clip(text: str, limit: int = QUOTE_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def subject_for(title: str, product_name: str = "AequorOS") -> str:
    """``[AequorOS] <title>`` — matches the relay's one subject convention."""
    return f"[{product_name}] {title}"


def render_email(
    message: Message,
    *,
    event: EventDefinition,
    severity: Severity,
    branding: Branding,
    recipient_name: str | None,
) -> RenderedEmail:
    """The branded HTML email and its plain-text twin for one notification."""
    context = {
        "message": message,
        "event": event,
        "category_label": _category_label(event),
        "accent": _SEVERITY_ACCENT[severity],
        "severity_label": _SEVERITY_LABEL[severity],
        "branding": branding,
        "greeting": _greeting(recipient_name),
        "action_url": None if message.action is None else branding.url(message.action.path),
        "preheader": _clip(message.summary, 140),
    }
    return RenderedEmail(
        subject=subject_for(message.title, branding.product_name),
        text=_ENV.get_template("notification.txt").render(context),
        html=_ENV.get_template("notification.html").render(context),
    )


def render_digest(
    items: Sequence[DigestItem],
    *,
    branding: Branding,
    recipient_name: str | None,
    period_label: str,
) -> RenderedEmail:
    """One email carrying every digest-mode notification a person accumulated."""
    if not items:
        msg = "A digest needs at least one item."
        raise ValueError(msg)
    sections: dict[str, list[DigestItem]] = {}
    for item in sorted(items, key=lambda entry: entry.created_at):
        sections.setdefault(_category_label(item.event), []).append(item)
    count = len(items)
    title = f"Your daily summary: {count} update{'s' if count != 1 else ''}"
    context = {
        "title": title,
        "sections": [_Section(name, tuple(entries)) for name, entries in sections.items()],
        "branding": branding,
        "greeting": _greeting(recipient_name),
        "period_label": period_label,
        "accents": _SEVERITY_ACCENT,
        "preheader": f"{count} update{'s' if count != 1 else ''} from {period_label}.",
    }
    return RenderedEmail(
        subject=subject_for(title, branding.product_name),
        text=_ENV.get_template("digest.txt").render(context),
        html=_ENV.get_template("digest.html").render(context),
    )


def _greeting(name: str | None) -> str:
    first = (name or "").strip().split(" ")[0]
    return f"Hello {first}," if first else "Hello,"


def _category_label(event: EventDefinition) -> str:
    return CATEGORY_LABELS[event.category]


__all__ = [
    "QUOTE_LIMIT",
    "Action",
    "Branding",
    "DigestItem",
    "Fact",
    "Message",
    "RenderedEmail",
    "render_digest",
    "render_email",
    "subject_for",
]
