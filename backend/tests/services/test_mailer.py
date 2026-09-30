"""``app.services.mailer``: the extracted relay, and what the extraction must preserve.

The module was extracted from ``notification_email_mirror.py`` (D-170), so half of
this file is about the extraction being faithful rather than about new behaviour:
the relay is opened once, STARTTLS and login happen only when configured and in
that order, and a transport exception travels INTACT — the mirror records
``type(exc).__name__`` in its job progress, so wrapping it would silently rename
every historical failure. ``tests/services/test_notification_email_mirror.py`` is
the other half: it exercises the original caller, unchanged, over the new
transport.

**No socket is ever opened here.** ``smtplib.SMTP`` is replaced by a fake that
records what it was asked to do, exactly as the mirror's own suite does it.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import get_settings
from app.services import mailer

HOST = "smtp.test.local"
SENDER = "aequoros@test.local"


class _FakeSmtp:
    """Records every call; class-level stores survive the context manager."""

    opened: list[dict[str, Any]] = []
    sent: list[EmailMessage] = []
    calls: list[str] = []
    fail_on_send: BaseException | None = None
    fail_on_connect: BaseException | None = None

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        if _FakeSmtp.fail_on_connect is not None:
            raise _FakeSmtp.fail_on_connect
        _FakeSmtp.opened.append({"host": host, "port": port, "timeout": timeout})

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        _FakeSmtp.calls.append("close")

    def starttls(self) -> None:
        _FakeSmtp.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        _FakeSmtp.calls.append(f"login:{username}:{password}")

    def send_message(self, message: EmailMessage) -> None:
        if _FakeSmtp.fail_on_send is not None:
            raise _FakeSmtp.fail_on_send
        _FakeSmtp.calls.append("send")
        _FakeSmtp.sent.append(message)


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakeSmtp.opened = []
    _FakeSmtp.sent = []
    _FakeSmtp.calls = []
    _FakeSmtp.fail_on_send = None
    _FakeSmtp.fail_on_connect = None


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSmtp]:
    monkeypatch.setenv("SMTP_HOST", HOST)
    monkeypatch.setenv("SMTP_FROM", SENDER)
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    return _FakeSmtp


@pytest.fixture
def _no_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)


# --- configuration is the only switch ----------------------------------------


def test_mail_is_disabled_until_a_relay_is_configured(_no_relay: None) -> None:
    """The honest default: a deployment that configures nothing sends nothing.

    There is no fallback host. An unconfigured platform must not discover a
    relay of its own, which is the only way "mail goes through the bank's own
    in-country relay" can be a property rather than a hope.
    """

    assert mailer.relay_configured() is False
    with pytest.raises(mailer.MailerNotConfigured):
        mailer.sender()
    with pytest.raises(mailer.MailerNotConfigured), mailer.open_relay():
        pytest.fail("a relay was opened with nothing configured")
    assert _FakeSmtp.opened == []


def test_a_configured_relay_reports_itself_and_its_sender(relay: type[_FakeSmtp]) -> None:
    assert mailer.relay_configured() is True
    assert mailer.sender() == SENDER


def test_the_relay_is_opened_once_for_many_messages(relay: type[_FakeSmtp]) -> None:
    """One conversation per batch: the mirror drains an outbox, a run mails N people."""

    with mailer.open_relay() as open_relay:
        for index in range(3):
            open_relay.send_message(
                mailer.compose(
                    subject=f"n{index}",
                    body="body",
                    recipients=["one@bank.test"],
                    sender_address=SENDER,
                )
            )
    assert len(relay.opened) == 1
    assert relay.opened[0]["host"] == HOST
    assert relay.opened[0]["timeout"] == mailer.CONNECT_TIMEOUT_SECONDS
    assert relay.calls.count("send") == 3
    assert relay.calls[-1] == "close"


def test_starttls_precedes_login_and_both_are_configuration(
    relay: type[_FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credentials only when set, and never before the channel is protected."""

    monkeypatch.setenv("SMTP_USERNAME", "relay-user")
    monkeypatch.setenv("SMTP_PASSWORD", "relay-secret")
    get_settings.cache_clear()
    with mailer.open_relay():
        pass
    assert relay.calls[:2] == ["starttls", "login:relay-user:relay-secret"]


def test_starttls_can_be_turned_off_for_a_relay_that_has_no_tls(
    relay: type[_FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMTP_STARTTLS", "0")
    get_settings.cache_clear()
    with mailer.open_relay():
        pass
    assert "starttls" not in relay.calls


def test_no_credentials_means_no_login(relay: type[_FakeSmtp]) -> None:
    with mailer.open_relay():
        pass
    assert not any(call.startswith("login") for call in relay.calls)


# --- the message --------------------------------------------------------------


def test_a_composed_message_is_plain_text_with_the_subject_convention(
    relay: type[_FakeSmtp],
) -> None:
    message = mailer.compose(
        subject=mailer.subject_line("Board pack"),
        body="One line.\nAnother.",
        recipients=["a@bank.test", "b@bank.test"],
        sender_address=SENDER,
    )
    assert str(message["Subject"]) == "[AequorOS] Board pack"
    assert str(message["From"]) == SENDER
    assert str(message["To"]) == "a@bank.test, b@bank.test"
    assert message.get_content_type() == "text/plain"
    assert message.get_content() == "One line.\nAnother.\n"


def test_an_attachment_travels_as_its_own_part_with_its_own_type(
    relay: type[_FakeSmtp],
) -> None:
    """The evidence the delivery row's digest is about: these exact bytes."""

    payload = b"branch,balance\nHead office,400.00\n"
    message = mailer.compose(
        subject="with a file",
        body="See attached.",
        recipients=["a@bank.test"],
        sender_address=SENDER,
        attachments=[
            mailer.Attachment(filename="pack.csv", content=payload, media_type="text/csv")
        ],
    )
    parts = [part for part in message.iter_attachments()]
    assert len(parts) == 1
    assert parts[0].get_filename() == "pack.csv"
    assert parts[0].get_content_type() == "text/csv"
    assert parts[0].get_payload(decode=True) == payload


def test_a_binary_attachment_keeps_its_bytes_exactly(relay: type[_FakeSmtp]) -> None:
    """A PDF is the format a board pack actually uses; base64 must round-trip it."""

    payload = b"%PDF-1.4\n\x00\x01\x02\xff"
    message = mailer.compose(
        subject="pdf",
        body="body",
        recipients=["a@bank.test"],
        sender_address=SENDER,
        attachments=[
            mailer.Attachment(filename="pack.pdf", content=payload, media_type="application/pdf")
        ],
    )
    part = next(iter(message.iter_attachments()))
    assert part.get_payload(decode=True) == payload


def test_the_media_type_is_split_into_its_two_halves() -> None:
    attachment = mailer.Attachment(
        filename="x.xlsx",
        content=b"",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert attachment.maintype == "application"
    assert attachment.subtype == ("vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --- failures are re-raised, not translated ----------------------------------


@pytest.mark.parametrize(
    "error",
    [smtplib.SMTPException("simulated outage"), OSError("connection refused")],
    ids=["smtp", "socket"],
)
def test_a_transport_failure_reaches_the_caller_as_its_own_class(
    relay: type[_FakeSmtp], error: BaseException
) -> None:
    """``job.progress["error"]`` is ``type(exc).__name__``; wrapping would rename it.

    Both arms of :data:`mailer.TRANSPORT_ERRORS` are exercised, because the mirror
    has always caught the socket errors as well as the protocol ones and an
    extraction that quietly narrowed that would turn an outage into a crash.
    """

    relay.fail_on_send = error
    with pytest.raises(mailer.TRANSPORT_ERRORS) as caught, mailer.open_relay() as open_relay:
        open_relay.send_message(
            mailer.compose(subject="s", body="b", recipients=["a@bank.test"], sender_address=SENDER)
        )
    assert type(caught.value) is type(error)


def test_a_connection_failure_reaches_the_caller_too(relay: type[_FakeSmtp]) -> None:
    relay.fail_on_connect = OSError("no route to host")
    with pytest.raises(mailer.TRANSPORT_ERRORS), mailer.open_relay():
        pytest.fail("the relay opened despite a connection failure")
