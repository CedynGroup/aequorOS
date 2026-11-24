"""The notification catalogue, preference resolution and branded rendering (pure, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.notifications import catalog
from app.notifications.catalog import EmailMode, EventDefinition, Severity
from app.notifications.models import NotificationPolicy
from app.notifications.rendering import (
    Action,
    Branding,
    DigestItem,
    Fact,
    Message,
    render_digest,
    render_email,
)
from app.notifications.service.preferences import OrgDelivery, resolve_email_mode

BRANDING = Branding(app_base_url="https://bank.example.test", organization_name="Fixture Org")
NOW = datetime(2026, 11, 23, 21, 30, tzinfo=UTC)


def _org(
    *, email_enabled: bool = True, time_zone: str = "UTC", digest_hour: int = 7
) -> OrgDelivery:
    return OrgDelivery(
        email_enabled=email_enabled,
        time_zone=time_zone,
        digest_hour=digest_hour,
        reminders_enabled=True,
        reminder_after_hours=24,
        escalate_after_hours=48,
    )


def _policy(mode: EmailMode, *, locked: bool) -> NotificationPolicy:
    return NotificationPolicy(
        organization_id="OR-TEST0001", event_key="x", email_mode=mode.value, locked=locked
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored_type", "expected"),
    [
        ("filing.review_requested", "filing.review_requested"),
        ("filing.review_overdue:pkg:2:1", "filing.review_overdue"),
        ("reporting.package.pending_approval", "filing.review_requested"),
        ("reporting.package.approval_rejected", "filing.sent_back"),
        ("reporting.regulator.acknowledged", "regulator.accepted"),
        ("reporting.regulator.rejected", "regulator.sent_back"),
        ("reporting.deadline.due_soon_7:BSD3:2026-03-31", "deadlines.approaching"),
        ("reporting.deadline.overdue:BSD3:2026-03-31:2026-07-24", "deadlines.missed"),
        ("reporting.instrument.changed:LCR:", "deadlines.rule_change"),
        ("cfp.activated", "risk.contingency_funding"),
        ("bi.alert.breached", "risk.metric_alert"),
        ("something.nobody.declared", "platform.general"),
    ],
)
def test_every_stored_type_resolves_to_one_catalogue_event(stored_type: str, expected: str) -> None:
    assert catalog.for_type(stored_type).key == expected


def test_unknown_event_keys_are_refused() -> None:
    with pytest.raises(catalog.UnknownEvent):
        catalog.get("filing.does_not_exist")


def test_a_locked_event_cannot_default_to_no_email() -> None:
    with pytest.raises(ValueError, match="locked"):
        EventDefinition(
            key="x.y",
            category=catalog.Category.PLATFORM,
            label="x",
            description="x",
            severity=Severity.INFO,
            default_email=EmailMode.OFF,
            email_locked=True,
        )


def test_every_event_is_described_for_the_preferences_screen() -> None:
    for event in catalog.EVENTS.values():
        assert event.label.strip()
        assert event.description.strip().endswith(".")
        assert event.category in catalog.CATEGORY_LABELS


# ---------------------------------------------------------------------------
# Preference resolution order
# ---------------------------------------------------------------------------


def test_catalogue_default_applies_when_nobody_chose() -> None:
    event = catalog.DEADLINE_APPROACHING
    assert resolve_email_mode(event, org=_org(), policy=None, choice=None) is EmailMode.DIGEST


def test_a_person_overrides_an_unlocked_organisation_default() -> None:
    event = catalog.FILING_REVIEW_REQUESTED
    mode = resolve_email_mode(
        event, org=_org(), policy=_policy(EmailMode.DIGEST, locked=False), choice=EmailMode.OFF
    )
    assert mode is EmailMode.OFF


def test_the_organisation_default_applies_to_people_who_did_not_choose() -> None:
    event = catalog.FILING_REVIEW_REQUESTED
    mode = resolve_email_mode(
        event, org=_org(), policy=_policy(EmailMode.DIGEST, locked=False), choice=None
    )
    assert mode is EmailMode.DIGEST


def test_a_locked_policy_beats_the_person() -> None:
    event = catalog.FILING_REVIEW_REQUESTED
    mode = resolve_email_mode(
        event, org=_org(), policy=_policy(EmailMode.IMMEDIATE, locked=True), choice=EmailMode.OFF
    )
    assert mode is EmailMode.IMMEDIATE


def test_switching_email_off_for_the_organisation_silences_unlocked_events() -> None:
    mode = resolve_email_mode(
        catalog.FILING_REVIEW_REQUESTED,
        org=_org(email_enabled=False),
        policy=None,
        choice=EmailMode.IMMEDIATE,
    )
    assert mode is EmailMode.OFF


def test_a_catalogue_locked_event_always_emails() -> None:
    event = catalog.REGULATOR_DECLINED
    mode = resolve_email_mode(
        event,
        org=_org(email_enabled=False),
        policy=_policy(EmailMode.OFF, locked=True),
        choice=EmailMode.OFF,
    )
    assert mode is EmailMode.IMMEDIATE


def test_the_digest_hour_is_read_in_the_organisation_time_zone() -> None:
    # 21:30 UTC is 16:30 in New York; the 07:00 summary is the next morning.
    assert _org(time_zone="America/New_York").next_digest_at(NOW) == datetime(
        2026, 11, 24, 12, 0, tzinfo=UTC
    )
    # Before the hour on the same local day, it is today.
    early = datetime(2026, 11, 23, 5, 0, tzinfo=UTC)
    assert _org().next_digest_at(early) == datetime(2026, 11, 23, 7, 0, tzinfo=UTC)


def test_an_unknown_time_zone_falls_back_to_utc() -> None:
    assert _org(time_zone="Not/AZone").next_digest_at(NOW) == datetime(
        2026, 11, 24, 7, 0, tzinfo=UTC
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _message() -> Message:
    return Message(
        title="CAR-RWA for 30 June 2026 is waiting for your review",
        summary="Ama Mensah sent version 1 of CAR-RWA to Review.",
        facts=(Fact("Return", "CAR-RWA"), Fact("Reporting date", "30 June 2026")),
        quote="Please check the <RWA> totals & the floor.",
        quote_label="Note from the preparer",
        action=Action("Review the return", "/submissions/returns?package=abc"),
    )


def test_the_email_carries_every_part_of_the_message_in_both_formats() -> None:
    rendered = render_email(
        _message(),
        event=catalog.FILING_REVIEW_REQUESTED,
        severity=Severity.WARNING,
        branding=BRANDING,
        recipient_name="Kofi Boateng",
    )
    assert rendered.subject == "[AequorOS] CAR-RWA for 30 June 2026 is waiting for your review"
    for part in (rendered.html, rendered.text):
        assert "Hello Kofi," in part
        assert "Ama Mensah sent version 1 of CAR-RWA to Review." in part
        assert "Reporting date" in part
        assert "Note from the preparer" in part
        assert "https://bank.example.test/submissions/returns?package=abc" in part
        assert "https://bank.example.test/settings/notifications" in part
    assert "Action needed" in rendered.html
    assert "Fixture Org" in rendered.html


def test_user_text_is_escaped_in_html_and_left_alone_in_plain_text() -> None:
    rendered = render_email(
        _message(),
        event=catalog.FILING_REVIEW_REQUESTED,
        severity=Severity.WARNING,
        branding=BRANDING,
        recipient_name=None,
    )
    assert "&lt;RWA&gt; totals &amp; the floor" in rendered.html
    assert "<RWA> totals & the floor" in rendered.text
    assert "Hello," in rendered.text


def test_the_html_loads_nothing_remote() -> None:
    rendered = render_email(
        _message(),
        event=catalog.FILING_REVIEW_REQUESTED,
        severity=Severity.WARNING,
        branding=BRANDING,
        recipient_name=None,
    )
    lowered = rendered.html.lower()
    assert "<img" not in lowered
    assert "<link" not in lowered
    assert "<script" not in lowered
    assert "url(" not in lowered


def test_a_message_round_trips_through_its_stored_form() -> None:
    message = _message()
    rebuilt = Message.from_stored(
        title=message.title,
        body=message.feed_body(),
        data=message.to_data(),
        action_path=message.action.path if message.action else None,
    )
    assert rebuilt == message


def test_a_legacy_row_without_structure_still_renders() -> None:
    rebuilt = Message.from_stored(
        title="BSD3 overdue", body="The return is past its due date.", data={}, action_path=None
    )
    assert rebuilt.summary == "The return is past its due date."
    assert rebuilt.facts == ()
    assert rebuilt.action is None


def test_the_digest_groups_items_by_category() -> None:
    rendered = render_digest(
        [
            DigestItem(catalog.DEADLINE_APPROACHING, _message(), Severity.WARNING, NOW),
            DigestItem(catalog.RULE_CHANGE, _message(), Severity.INFO, NOW),
        ],
        branding=BRANDING,
        recipient_name="Kofi",
        period_label="23 November 2026",
    )
    assert rendered.subject == "[AequorOS] Your daily summary: 2 updates"
    assert rendered.html.index("Reporting deadlines and rule changes") > 0
    assert rendered.text.count("CAR-RWA for 30 June 2026") == 2


def test_an_empty_message_is_refused() -> None:
    with pytest.raises(ValueError, match="title and a summary"):
        Message(title=" ", summary="x")
