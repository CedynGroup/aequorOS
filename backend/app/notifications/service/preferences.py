"""Who gets which notification by email, and when (backend/docs/notifications.md §Preferences).

The in-app feed always receives every notification a person may see: it is the
record. Email is the part people and administrators shape, and one function,
:func:`resolve_email_mode`, decides it, in this order:

1. an event the catalogue locks (a regulator's refusal) always uses its default;
2. an organisation that has switched email off receives none;
3. an administrator's *locked* policy for the event applies to everyone;
4. otherwise the person's own choice, if they made one;
5. otherwise the organisation's default for the event, if an administrator set one;
6. otherwise the catalogue default.

Nothing here sends anything. :mod:`publishing` asks this module once per publish
for every recipient at the same time, so a fan-out to twenty officers costs
three queries, not sixty.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.notifications import catalog
from app.notifications.catalog import EmailMode, EventDefinition
from app.notifications.models import (
    NotificationPolicy,
    NotificationPreference,
    NotificationSettings,
)

#: Defaults for an organisation that has never saved its settings. Mirrors the
#: column defaults so a missing row and a fresh row behave identically.
DEFAULT_TIME_ZONE = "UTC"
DEFAULT_DIGEST_HOUR = 7
DEFAULT_REMINDER_HOURS = 24
DEFAULT_ESCALATE_HOURS = 48


@dataclass(frozen=True, slots=True)
class OrgDelivery:
    """The organisation's settings, with defaults filled in."""

    email_enabled: bool
    time_zone: str
    digest_hour: int
    reminders_enabled: bool
    reminder_after_hours: int
    escalate_after_hours: int

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_TIME_ZONE)

    def next_digest_at(self, now: datetime) -> datetime:
        """The next digest send time strictly after ``now``, in UTC."""
        local_now = now.astimezone(self.zone)
        candidate = datetime.combine(
            local_now.date(), time(hour=self.digest_hour), tzinfo=self.zone
        )
        if candidate <= local_now:
            candidate = datetime.combine(
                local_now.date() + timedelta(days=1), time(hour=self.digest_hour), tzinfo=self.zone
            )
        return candidate.astimezone(UTC)


def org_delivery(db: Session, organization_id: str) -> OrgDelivery:
    row = db.get(NotificationSettings, organization_id)
    if row is None:
        return OrgDelivery(
            email_enabled=True,
            time_zone=DEFAULT_TIME_ZONE,
            digest_hour=DEFAULT_DIGEST_HOUR,
            reminders_enabled=True,
            reminder_after_hours=DEFAULT_REMINDER_HOURS,
            escalate_after_hours=DEFAULT_ESCALATE_HOURS,
        )
    return OrgDelivery(
        email_enabled=row.email_enabled,
        time_zone=row.time_zone,
        digest_hour=row.digest_hour,
        reminders_enabled=row.reminders_enabled,
        reminder_after_hours=row.reminder_after_hours,
        escalate_after_hours=row.escalate_after_hours,
    )


def policies(db: Session, organization_id: str) -> dict[str, NotificationPolicy]:
    return {
        row.event_key: row
        for row in db.scalars(
            select(NotificationPolicy).where(NotificationPolicy.organization_id == organization_id)
        )
    }


def user_choices(
    db: Session, organization_id: str, user_ids: Iterable[UUID], event_key: str | None = None
) -> dict[tuple[UUID, str], EmailMode]:
    ids = list(user_ids)
    if not ids:
        return {}
    query = select(NotificationPreference).where(
        NotificationPreference.organization_id == organization_id,
        NotificationPreference.user_id.in_(ids),
    )
    if event_key is not None:
        query = query.where(NotificationPreference.event_key == event_key)
    return {(row.user_id, row.event_key): EmailMode(row.email_mode) for row in db.scalars(query)}


def resolve_email_mode(
    event: EventDefinition,
    *,
    org: OrgDelivery,
    policy: NotificationPolicy | None,
    choice: EmailMode | None,
) -> EmailMode:
    """The single answer to "does this person get this event by email, and when"."""
    if event.email_locked:
        return event.default_email
    if not org.email_enabled:
        return EmailMode.OFF
    if policy is not None and policy.locked:
        return EmailMode(policy.email_mode)
    if choice is not None:
        return choice
    if policy is not None:
        return EmailMode(policy.email_mode)
    return event.default_email


def resolve_for_recipients(
    db: Session, organization_id: str, event: EventDefinition, user_ids: Iterable[UUID]
) -> tuple[OrgDelivery, dict[UUID, EmailMode]]:
    """Email mode per recipient for one event, in three queries."""
    ids = list(dict.fromkeys(user_ids))
    org = org_delivery(db, organization_id)
    policy = policies(db, organization_id).get(event.key)
    choices = user_choices(db, organization_id, ids, event.key)
    return org, {
        user_id: resolve_email_mode(
            event, org=org, policy=policy, choice=choices.get((user_id, event.key))
        )
        for user_id in ids
    }


# ---------------------------------------------------------------------------
# Writes (the API calls these; they never commit — the route does)
# ---------------------------------------------------------------------------


class PreferenceError(ValueError):
    """A preference write the catalogue or policy does not allow."""


def set_user_choices(
    db: Session,
    organization_id: str,
    user_id: UUID,
    choices: Mapping[str, EmailMode | None],
) -> None:
    """Save one person's choices. ``None`` returns an event to the organisation default."""
    existing = {
        row.event_key: row
        for row in db.scalars(
            select(NotificationPreference).where(
                NotificationPreference.organization_id == organization_id,
                NotificationPreference.user_id == user_id,
            )
        )
    }
    policy_rows = policies(db, organization_id)
    for key, mode in choices.items():
        event = catalog.get(key)
        policy = policy_rows.get(key)
        if mode is not None and (event.email_locked or (policy is not None and policy.locked)):
            raise PreferenceError(
                f"“{event.label}” is set by your organisation and cannot be changed here."
            )
        row = existing.get(key)
        if mode is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(
                NotificationPreference(
                    organization_id=organization_id,
                    user_id=user_id,
                    event_key=key,
                    email_mode=mode.value,
                )
            )
        else:
            row.email_mode = mode.value
    db.flush()


@dataclass(frozen=True, slots=True)
class PolicyChange:
    email_mode: EmailMode
    locked: bool


def set_policies(
    db: Session,
    organization_id: str,
    actor_user_id: UUID | None,
    changes: Mapping[str, PolicyChange | None],
) -> None:
    """Save the organisation's per-event rules. ``None`` removes the rule."""
    existing = policies(db, organization_id)
    for key, change in changes.items():
        event = catalog.get(key)
        if change is not None and event.email_locked:
            raise PreferenceError(f"“{event.label}” is always emailed and has no policy to set.")
        row = existing.get(key)
        if change is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(
                NotificationPolicy(
                    organization_id=organization_id,
                    event_key=key,
                    email_mode=change.email_mode.value,
                    locked=change.locked,
                    updated_by=actor_user_id,
                )
            )
        else:
            row.email_mode = change.email_mode.value
            row.locked = change.locked
            row.updated_by = actor_user_id
    db.flush()


def save_org_delivery(
    db: Session, organization_id: str, actor_user_id: UUID | None, settings: OrgDelivery
) -> None:
    try:
        ZoneInfo(settings.time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PreferenceError(f"“{settings.time_zone}” is not a recognised time zone.") from exc
    if not 0 <= settings.digest_hour <= 23:  # noqa: PLR2004 - hours of a day
        raise PreferenceError("The summary hour must be between 0 and 23.")
    if settings.reminder_after_hours < 1:
        raise PreferenceError("Reminders must wait at least one hour.")
    if settings.escalate_after_hours <= settings.reminder_after_hours:
        raise PreferenceError("Escalation must come after the first reminder.")
    row = db.get(NotificationSettings, organization_id)
    if row is None:
        row = NotificationSettings(organization_id=organization_id)
        db.add(row)
    row.email_enabled = settings.email_enabled
    row.time_zone = settings.time_zone
    row.digest_hour = settings.digest_hour
    row.reminders_enabled = settings.reminders_enabled
    row.reminder_after_hours = settings.reminder_after_hours
    row.escalate_after_hours = settings.escalate_after_hours
    row.updated_by = actor_user_id
    db.flush()


def clear_user_choices(db: Session, organization_id: str, user_id: UUID) -> None:
    db.execute(
        delete(NotificationPreference).where(
            NotificationPreference.organization_id == organization_id,
            NotificationPreference.user_id == user_id,
        )
    )


__all__ = [
    "DEFAULT_DIGEST_HOUR",
    "OrgDelivery",
    "PolicyChange",
    "PreferenceError",
    "clear_user_choices",
    "org_delivery",
    "policies",
    "resolve_email_mode",
    "resolve_for_recipients",
    "save_org_delivery",
    "set_policies",
    "set_user_choices",
    "user_choices",
]
