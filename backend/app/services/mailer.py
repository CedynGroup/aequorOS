"""The platform's ONE outbound mail path, over the deployment's SMTP relay.

Extracted from ``app/services/notification_email_mirror.py`` (D-170), which had
been the only mail path in the codebase and is now this module's first caller.
Everything here was already there; what is new is that it is now reusable and
that a second feature — BI subscriptions — cannot grow a second transport, a
second sender resolution or a second set of failure semantics.

**What this module is, exactly.** A relay connection and a message builder. It
decides nothing about WHO may receive WHAT: the caller has already made that
decision, and for BI subscriptions it makes it once per recipient
(``app/services/bi/subscriptions.py``). Nothing here reads a tenant table.

**Configuration, never code.** The relay is ``SMTP_*``
(``app.core.config.SmtpSettings``), which a deployment points at the bank's own
in-country relay; mail is disabled — the honest default — until both a host and
a sender are configured, and :func:`relay_configured` is the one question a
caller asks. There is no country, currency or regulator value anywhere in this
module, and no default host: an unconfigured deployment sends nothing rather
than falling back to somebody's server.

**Failures are re-raised, not translated.** :data:`TRANSPORT_ERRORS` is the
tuple a caller catches, and the original ``smtplib`` / ``OSError`` exception
travels intact — the notification mirror records ``type(exc).__name__`` in its
job progress, and wrapping would have renamed every historical failure. The
relay is best-effort by design at both call sites: an SMTP outage must never
fail the business action that produced the message.

**One connection, many messages.** :func:`open_relay` is a context manager
because the mirror drains a batch and a subscription run mails one message per
recipient; opening a connection per message would be the same conversation N
times and would make a partial failure harder to reason about, not easier.

**Attachments are the caller's decision and the caller's evidence.** This module
will attach whatever bytes it is handed. What may be attached at all is decided
by ``app/services/bi/exports/policy.py`` — only an aggregated artifact — and the
delivery row records the digest of what went. Keeping the policy out of here is
deliberate: a transport that classified content would be a second classifier.
"""

from __future__ import annotations

import smtplib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

#: Every subject line the platform sends carries it, so a bank's mail rules can
#: match on one string. The product name is not a jurisdiction value.
SUBJECT_PREFIX = "[AequorOS]"

#: How long one relay conversation may block. The relay is in-country and on the
#: same network in the deployments this ships to; a longer timeout would hold a
#: worker open on a relay that is not answering.
CONNECT_TIMEOUT_SECONDS = 30

#: What a caller catches. ``smtplib.SMTPException`` covers the protocol and
#: ``OSError`` the socket (DNS, refused connection, TLS). Exposed as a tuple so
#: both call sites catch the same set and neither has to import ``smtplib``.
TRANSPORT_ERRORS: tuple[type[Exception], ...] = (smtplib.SMTPException, OSError)


class MailerNotConfigured(RuntimeError):
    """Mail was attempted on a deployment with no relay configured.

    Raised rather than silently dropped: a caller that has not checked
    :func:`relay_configured` has a bug, and a swallowed send is indistinguishable
    from a delivered one.
    """


@dataclass(frozen=True, slots=True)
class Attachment:
    """One file to attach: its name, its bytes and what it is.

    ``media_type`` is a full MIME type (``application/pdf``); it is split on the
    single ``/`` because ``EmailMessage.add_attachment`` takes the two halves
    separately.
    """

    filename: str
    content: bytes
    media_type: str

    @property
    def maintype(self) -> str:
        return self.media_type.split("/", 1)[0]

    @property
    def subtype(self) -> str:
        parts = self.media_type.split("/", 1)
        return parts[1] if len(parts) == 2 else ""


def relay_configured() -> bool:
    """Whether this deployment can send mail at all (host AND sender set)."""

    return get_settings().smtp.enabled


def sender() -> str:
    """The configured From address, or :class:`MailerNotConfigured`."""

    address = get_settings().smtp.smtp_from
    if not address:
        raise MailerNotConfigured(
            "No outbound mail sender is configured. Set SMTP_FROM (and SMTP_HOST) "
            "to the institution's own relay."
        )
    return address


def subject_line(title: str) -> str:
    """``[AequorOS] <title>`` — the one subject convention, in one place."""

    return f"{SUBJECT_PREFIX} {title}"


def compose(
    *,
    subject: str,
    body: str,
    recipients: Sequence[str],
    sender_address: str,
    attachments: Sequence[Attachment] = (),
) -> EmailMessage:
    """A plain-text message, with attachments when the caller supplies them.

    Plain text and nothing else: an HTML body would be a second rendering of
    figures that already have a governed renderer, and a remote image in a mail
    client is a read receipt nobody consented to.
    """

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender_address
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    for attachment in attachments:
        message.add_attachment(
            attachment.content,
            maintype=attachment.maintype,
            subtype=attachment.subtype,
            filename=attachment.filename,
        )
    return message


class Relay:
    """An open conversation with the relay. Send as many messages as needed."""

    __slots__ = ("_client",)

    def __init__(self, client: smtplib.SMTP) -> None:
        self._client = client

    def send_message(self, message: EmailMessage) -> None:
        """Hand one message to the relay. Raises :data:`TRANSPORT_ERRORS`."""

        self._client.send_message(message)


@contextmanager
def open_relay() -> Iterator[Relay]:
    """Open, authenticate and close one relay conversation.

    STARTTLS and login happen only when configured, in that order, exactly as
    the notification mirror did them. Raises :class:`MailerNotConfigured` when
    the deployment has no relay, and :data:`TRANSPORT_ERRORS` for anything the
    relay or the network does.
    """

    settings = get_settings().smtp
    if not settings.enabled or settings.smtp_host is None:
        raise MailerNotConfigured(
            "No outbound mail relay is configured. Set SMTP_HOST and SMTP_FROM."
        )
    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=CONNECT_TIMEOUT_SECONDS)
    with client:
        if settings.smtp_starttls:
            client.starttls()
        if settings.smtp_username and settings.smtp_password:
            client.login(settings.smtp_username, settings.smtp_password)
        yield Relay(client)


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "SUBJECT_PREFIX",
    "TRANSPORT_ERRORS",
    "Attachment",
    "MailerNotConfigured",
    "Relay",
    "compose",
    "open_relay",
    "relay_configured",
    "sender",
    "subject_line",
]
