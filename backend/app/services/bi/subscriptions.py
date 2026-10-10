"""Scheduled subscriptions: one delivery per recipient, rendered as that recipient.

``docs/bi.md`` §Phase 3, requirements P3-U1, P3-U2 and P3-U3:

* ``bi_subscriptions`` and ``bi_subscription_deliveries``;
* ``scheduler.run_tick`` enqueues jobs due within the hour with ``run_after`` set
  to the exact minute, and ``on_new_data`` fires from the mart build;
* **each delivery renders as the recipient, never with the owner's authority**;
* mail goes through the relay (``app/services/mailer.py``); only aggregated
  artifacts are attached and confidential content gets a sign-in link.

**The crux: authority is the RECIPIENT's.** A subscription is created by one
person and delivered to others. Rendering once with the creator's access and
mailing the result to everybody is a data breach whose audit trail points at the
wrong person — so nothing is rendered per RUN here. Every recipient gets their
own ``authorize_query`` decision, their own compilation, their own execution and
their own bytes, and a recipient the evaluator refuses gets no email at all
(``status='denied'``, with the member ids they were missing and no figure
anywhere). There is deliberately no cache keyed on the query: a cache is exactly
how a per-recipient render silently becomes a per-run one.

**The classification decides attachment vs link, and it decides it BEFORE any
row is read.** ``app/services/bi/exports/policy.py`` — the same classifier the
interactive exports use, reading the catalogue's own sensitivity declarations
rather than a flag on a request — answers ``summary`` or ``record_level`` from
the member set alone. Only ``summary`` may be attached. A ``record_level``
subscription therefore never executes its query at all: the recipient gets a
sign-in link, and the rows never leave the database, let alone the building.

**Double-sending is prevented by the database, not by care.**
``bi_subscription_deliveries`` is unique on
``(organization, subscription, recipient, scheduled_for)`` and the ``pending``
claim is COMMITTED before anything is sent. A duplicate tick, a replayed queue
row, a reclaimed job running concurrently with the original: all three collide on
that key and send nothing. The cost of that choice is at-most-once rather than
at-least-once — a crash between the claim and the send leaves a delivery that
says ``pending`` for ever — and that is the intended trade: a second copy of a
board pack is a disclosure event, a missing one is a visible row somebody can
act on.

**The data scope (Phase 4).** Each recipient's own declared scope is resolved to
branch codes and passed to ``compile_query`` as the unremovable injected filter,
in :func:`_render_for`. Because every recipient is already authorized, compiled,
executed and rendered separately — nothing is computed once and reused, by
construction, since the provenance block names the recipient — two recipients with
different branch scopes genuinely receive different figures in the same run, and
each artifact SAYS which slice it covers (``ExportContext.data_scope_label``).
A recipient whose scope cannot be expressed as one filter (more branches than an
``IN`` list may carry) is still refused with ``data_scope_unsupported``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Permission
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi.catalogue import CATALOGUE_VERSION, Catalogue, catalogue
from app.identity.public import Bank, User
from app.models import BiMartBuild, Job
from app.models.bi import MART_BUILD_SCOPES
from app.models.bi_notifications import BiSubscription, BiSubscriptionDelivery
from app.schemas.bi import BiQuery, BiTime
from app.services import job_queue, jurisdictions, mailer
from app.services.bi import data_scope, exports, provenance, query_log
from app.services.bi.authorization import authorize_query_conjunctive, query_members
from app.services.bi.errors import BiQueryError
from app.services.bi.exports import policy, runner
from app.services.bi.versions import BUILDER_VERSION

#: The two job types. Literals here, never imported from ``app.jobs``, so the
#: enqueue side does not depend on the handler side (the ``bi/enqueue.py``
#: rule); ``job_queue.enqueue`` validates them against ``JOB_TYPES``.
JOB_TYPE_SCAN = "bi_subscription_scan"
JOB_TYPE_RUN = "bi_subscription_run"

#: ``jobs.entity_type`` for a run: the institution, like every other BI job.
ENTITY_TYPE = "bank"

#: Where a recipient is sent when the content may not be attached. The BI query
#: surface, on the authenticated bank product, whose origin is deployment
#: configuration (``BANK_APP_BASE_URL``) and never a literal host here.
SIGN_IN_PATH = "/explore"

#: The most recipients one run may deliver to. It is the schema's cap as well
#: (``app/schemas/bi_notifications.py``), and it is load-bearing twice: it bounds
#: how long one ``bi_subscription_run`` can occupy a worker — which is what its
#: stale-job window is set against — and it keeps a distribution list a list
#: rather than a mailing.
MAX_RECIPIENTS = 50

#: ``jobs.progress`` / delivery ``reason`` strings. Stable, and never a figure,
#: a filter value or a member label.
REASON_ALREADY_DELIVERED = "already_delivered"
REASON_RECIPIENT_INACTIVE = "recipient_inactive"
REASON_DATA_SCOPE_UNSUPPORTED = "data_scope_unsupported"
REASON_DISCLOSURE_CLASS_CHANGED = "disclosure_class_changed"
REASON_NO_FIGURES = "no_figures_for_date"
REASON_NO_BUILD = "no_build_for_institution"
#: The date HAS rows, but its latest build did not succeed, so they were
#: written by an earlier build against an earlier book (audit A360 H2).
REASON_STALE_BUILD = "latest_build_did_not_succeed"
REASON_UNSUPPORTED_TIME_WINDOW = "unsupported_time_window"
REASON_RECIPIENT_CAP_EXCEEDED = "recipient_cap_exceeded"
REASON_ATTACHMENT_OVER_SIZE_CAP = "attachment_over_size_cap"
REASON_RELAY_UNAVAILABLE = "relay_unavailable"
REASON_RELAY_NOT_CONFIGURED = "relay_not_configured"
REASON_SUBSCRIPTION_INACTIVE = "subscription_inactive"
REASON_UNSUPPORTED_FORMAT = "unsupported_artifact_format"

#: A relay conversation, opened lazily. ``mailer.open_relay`` by default; a test
#: passes its own so no socket is ever opened.
RelayFactory = Callable[[], AbstractContextManager[mailer.Relay]]


class SubscriptionError(Exception):
    """A run that cannot proceed: a malformed row, a tenant mismatch."""


class SubscriptionRelayError(Exception):
    """The relay failed mid-batch. The queue's bounded retry is the recovery.

    Raised AFTER the failed recipient's row is committed, so the attempt is on
    the record before the worker rolls back, and the recipients this batch never
    claimed are picked up by the retry.
    """


# --- when a subscription is due ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DueRun:
    """One (subscription, exact minute) the scan should enqueue."""

    subscription_id: UUID
    organization_id: str
    bank_id: str
    #: The exact UTC minute the run is due; the identity of the run, and the
    #: fourth column of the delivery table's unique key.
    scheduled_for: datetime
    trigger: str = "schedule"


def _zone_for(db: Session, bank: Bank) -> ZoneInfo:
    """The institution's own time zone, from the ``jurisdictions`` registry.

    Jurisdiction is data (``banks.jurisdiction_code`` → ``jurisdictions.timezone``),
    so "07:30" means half past seven where the bank is, not where the server is.
    A registry row with no zone recorded falls back to UTC — the only
    jurisdiction-neutral choice available — and says so in the log rather than
    guessing an offset.
    """

    jurisdiction = jurisdictions.get_jurisdiction(db, bank)
    name = jurisdiction.timezone if jurisdiction is not None else None
    if not name:
        logger.info(
            "bi.subscriptions.no_timezone bank={} jurisdiction={}",
            bank.id,
            bank.jurisdiction_code,
        )
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("bi.subscriptions.unknown_timezone bank={} zone={}", bank.id, name)
        return ZoneInfo("UTC")


def _candidate_local_dates(start: datetime, end: datetime, zone: ZoneInfo) -> list[date]:
    """Every local calendar date the UTC window could touch, with a day either side.

    The window is an hour, so this is two or three dates. The margin exists
    because a fixed local minute maps to a UTC instant through an offset that is
    not the same all year, and a scan that only looked at the window's own local
    dates would miss the run on the day an offset changes.
    """

    first = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _is_due_on(subscription: BiSubscription, day: date) -> bool:
    """Whether this subscription's cadence fires on ``day`` at all."""

    if subscription.cadence == "daily":
        return True
    if subscription.cadence == "weekly":
        return day.isoweekday() == subscription.day_of_week
    if subscription.cadence == "monthly":
        return day.day == subscription.day_of_month
    return False


def due_runs(
    db: Session, *, organization_id: str, window_start: datetime, window_end: datetime
) -> list[DueRun]:
    """Every clock-scheduled run of one tenant due in ``[start, end)``.

    Pure read: it enqueues nothing, so the caller owns the flag check and the
    enqueue. ``on_new_data`` subscriptions are not clocks and never appear here.
    """

    subscriptions = list(
        db.scalars(
            select(BiSubscription)
            .where(
                BiSubscription.organization_id == organization_id,
                BiSubscription.is_active.is_(True),
                BiSubscription.cadence != "on_new_data",
            )
            .order_by(BiSubscription.created_at, BiSubscription.id)
        )
    )
    if not subscriptions:
        return []
    zones: dict[str, ZoneInfo] = {}
    due: list[DueRun] = []
    for subscription in subscriptions:
        if subscription.hour is None or subscription.minute is None:
            # Unreachable through the CHECK constraints; a row that got here
            # another way is not schedulable and is skipped rather than guessed.
            continue
        zone = zones.get(subscription.bank_id)
        if zone is None:
            bank = db.scalar(
                select(Bank).where(
                    Bank.id == subscription.bank_id, Bank.organization_id == organization_id
                )
            )
            if bank is None:
                continue
            zone = _zone_for(db, bank)
            zones[subscription.bank_id] = zone
        for day in _candidate_local_dates(window_start, window_end, zone):
            if not _is_due_on(subscription, day):
                continue
            local = datetime(
                day.year,
                day.month,
                day.day,
                subscription.hour,
                subscription.minute,
                tzinfo=zone,
            )
            moment = local.astimezone(UTC)
            if window_start <= moment < window_end:
                due.append(
                    DueRun(
                        subscription_id=subscription.id,
                        organization_id=organization_id,
                        bank_id=subscription.bank_id,
                        scheduled_for=moment,
                    )
                )
    return due


def coalesce_key_for(subscription_id: UUID, scheduled_for: datetime) -> str:
    """``bi-sub:{subscription}:{minute}`` — one queued run per subscription and minute."""

    minute = scheduled_for.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
    return f"bi-sub:{subscription_id}:{minute}"


def _enqueue_run(db: Session, run: DueRun) -> Job | None:
    """Queue one run, unless this exact run has ever been queued before.

    ``job_queue.enqueue``'s coalescing merges only jobs that are still QUEUED and
    unclaimed, so on its own it would let a second tick create a second job for a
    run that had already executed. The existence check on the key is what holds
    the cadence — the ``bi_retention`` / ``reporting_deadline_scan`` idiom — and
    the delivery table's unique constraint is the guarantee behind it.
    """

    key = coalesce_key_for(run.subscription_id, run.scheduled_for)
    existing = db.scalar(
        select(Job.id)
        .where(
            Job.organization_id == run.organization_id,
            Job.job_type == JOB_TYPE_RUN,
            Job.coalesce_key == key,
        )
        .limit(1)
    )
    if existing is not None:
        return None
    return job_queue.enqueue(
        db,
        run.organization_id,
        JOB_TYPE_RUN,
        bank_id=run.bank_id,
        payload={
            "organization_id": run.organization_id,
            "bank_id": run.bank_id,
            "subscription_id": str(run.subscription_id),
            "scheduled_for": run.scheduled_for.astimezone(UTC).isoformat(),
            "trigger": run.trigger,
            "builder_version": BUILDER_VERSION,
        },
        # The exact minute the spec names. A run due at 07:30 waits until 07:30
        # even though the tick that found it ran at 07:00.
        run_after=run.scheduled_for,
        coalesce_key=key,
        entity_type=ENTITY_TYPE,
        entity_id=run.bank_id,
    )


def enqueue_due_runs(
    db: Session, *, organization_id: str, window_start: datetime, window_end: datetime
) -> list[Job]:
    """Queue every clock run due in the window. Flushes; the caller commits."""

    if not get_settings().bi.subscriptions_enabled:
        return []
    queued = [
        job
        for run in due_runs(
            db, organization_id=organization_id, window_start=window_start, window_end=window_end
        )
        if (job := _enqueue_run(db, run)) is not None
    ]
    db.flush()
    return queued


def enqueue_on_new_data(
    db: Session, *, organization_id: str, bank_id: str, as_of: date, completed_at: datetime
) -> list[Job]:
    """Queue every ``on_new_data`` subscription of one bank after a mart build.

    Called from the ``bi_mart_refresh`` handler on a SUCCEEDED build only, which
    is what makes this idempotent without a second mechanism: the builder returns
    ``skipped`` when a slice's fingerprint has not moved, so a rebuild that
    changed nothing produces no run, and a run's identity — ``completed_at``
    truncated to the minute — therefore names a build that genuinely changed the
    figures.
    """

    if not get_settings().bi.subscriptions_enabled:
        return []
    scheduled_for = completed_at.astimezone(UTC).replace(second=0, microsecond=0)
    subscriptions = list(
        db.scalars(
            select(BiSubscription)
            .where(
                BiSubscription.organization_id == organization_id,
                BiSubscription.bank_id == bank_id,
                BiSubscription.is_active.is_(True),
                BiSubscription.cadence == "on_new_data",
            )
            .order_by(BiSubscription.created_at, BiSubscription.id)
        )
    )
    queued: list[Job] = []
    for subscription in subscriptions:
        job = _enqueue_run(
            db,
            DueRun(
                subscription_id=subscription.id,
                organization_id=organization_id,
                bank_id=bank_id,
                scheduled_for=scheduled_for,
                trigger="new_data",
            ),
        )
        if job is not None:
            job.payload = {**job.payload, "as_of_date": as_of.isoformat()}
            queued.append(job)
    db.flush()
    return queued


# --- running one delivery batch -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What happened for ONE recipient of one run."""

    recipient_user_id: UUID
    status: str
    delivery_mode: str | None = None
    disclosure_class: str | None = None
    reason: str | None = None
    denied_members: tuple[str, ...] = ()
    row_count: int | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None
    as_of_date: date | None = None
    build_fingerprint: str | None = None

    def audit_details(self) -> dict[str, Any]:
        """The audit-event body the job writes. Ids and counts, never a figure."""

        record: dict[str, Any] = {
            "recipient_user_id": str(self.recipient_user_id),
            "status": self.status,
            "delivery_mode": self.delivery_mode,
            "disclosure_class": self.disclosure_class,
            "as_of_date": None if self.as_of_date is None else self.as_of_date.isoformat(),
        }
        if self.reason is not None:
            record["reason"] = self.reason
        if self.denied_members:
            record["denied_members"] = list(self.denied_members)
        if self.status == "sent":
            record.update(
                {
                    "row_count": self.row_count,
                    "artifact_sha256": self.artifact_sha256,
                    "artifact_size_bytes": self.artifact_size_bytes,
                    "build_fingerprint": self.build_fingerprint,
                }
            )
        return record


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """One ``bi_subscription_run``: the subscription, and one row per recipient."""

    subscription_id: UUID
    bank_id: str
    scheduled_for: datetime
    trigger: str
    as_of_date: date | None
    deliveries: tuple[DeliveryOutcome, ...] = field(default_factory=tuple)
    #: Set when the whole run was refused before any recipient was considered.
    reason: str | None = None

    def progress(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for delivery in self.deliveries:
            counts[delivery.status] = counts.get(delivery.status, 0) + 1
        record: dict[str, Any] = {
            "subscription_id": str(self.subscription_id),
            "scheduled_for": self.scheduled_for.astimezone(UTC).isoformat(),
            "trigger": self.trigger,
            "as_of_date": None if self.as_of_date is None else self.as_of_date.isoformat(),
            "recipients": len(self.deliveries),
            "statuses": counts,
        }
        if self.reason is not None:
            record["reason"] = self.reason
        return record


@dataclass(frozen=True, slots=True)
class RunRequest:
    """One queue row, validated against the session it will run in."""

    subscription: BiSubscription
    bank: Bank
    scheduled_for: datetime
    trigger: str
    #: Present for ``new_data``; a clock run resolves the date from the marts.
    as_of_date: date | None


def read_request(db: Session, job: Job) -> RunRequest:
    """The payload as typed values, proven to belong to the job's own tenant.

    ``worker.run_once`` has already bound the session to ``job.organization_id``.
    What is checked here is that the PAYLOAD agrees with the row: a replayed or
    hand-edited payload must not point a tenant-bound session at a sibling bank,
    a sibling tenant or another subscription.
    """

    payload = job.payload or {}
    payload_org = payload.get("organization_id")
    if payload_org is not None and str(payload_org) != job.organization_id:
        raise SubscriptionError(
            f"Job payload organization_id {payload_org!r} does not match the job's "
            f"organization {job.organization_id!r}."
        )
    raw_id = payload.get("subscription_id")
    if not raw_id:
        raise SubscriptionError("Job payload is missing subscription_id.")
    try:
        subscription_id = UUID(str(raw_id))
    except ValueError as exc:
        raise SubscriptionError("Job payload subscription_id is not a UUID.") from exc
    subscription = db.scalar(
        select(BiSubscription).where(
            BiSubscription.id == subscription_id,
            BiSubscription.organization_id == job.organization_id,
        )
    )
    if subscription is None:
        raise SubscriptionError(f"Subscription {subscription_id} not found for organization.")
    bank_id = job.bank_id or subscription.bank_id
    if str(bank_id) != subscription.bank_id:
        raise SubscriptionError(
            f"Job bank {bank_id!r} does not match the subscription's bank {subscription.bank_id!r}."
        )
    bank = db.scalar(
        select(Bank).where(
            Bank.id == subscription.bank_id, Bank.organization_id == job.organization_id
        )
    )
    if bank is None:
        raise SubscriptionError(f"Bank {subscription.bank_id} not found for organization.")
    raw_moment = payload.get("scheduled_for")
    if not raw_moment:
        raise SubscriptionError("Job payload is missing scheduled_for.")
    try:
        scheduled_for = datetime.fromisoformat(str(raw_moment))
    except ValueError as exc:
        raise SubscriptionError("Job payload scheduled_for is not an ISO timestamp.") from exc
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=UTC)
    trigger = str(payload.get("trigger") or "schedule")
    if trigger not in ("schedule", "new_data"):
        raise SubscriptionError(f"Job payload trigger {trigger!r} is not a delivery trigger.")
    raw_as_of = payload.get("as_of_date")
    as_of: date | None = None
    if raw_as_of:
        try:
            as_of = date.fromisoformat(str(raw_as_of))
        except ValueError as exc:
            raise SubscriptionError("Job payload as_of_date is not an ISO date.") from exc
    return RunRequest(
        subscription=subscription,
        bank=bank,
        scheduled_for=scheduled_for,
        trigger=trigger,
        as_of_date=as_of,
    )


def latest_built_as_of(db: Session, *, organization_id: str, bank_id: str) -> date | None:
    """The newest date whose marts are COMPLETE for this institution.

    Every build scope must stand ``succeeded``: a partial build is a half-written
    projection, and mailing a pack from one would state figures the platform
    itself has not finished computing.
    """

    return db.scalar(
        select(BiMartBuild.as_of_date)
        .where(
            BiMartBuild.organization_id == organization_id,
            BiMartBuild.bank_id == bank_id,
            BiMartBuild.status == "succeeded",
        )
        .group_by(BiMartBuild.as_of_date)
        .having(func.count(distinct(BiMartBuild.scope)) >= len(MART_BUILD_SCOPES))
        .order_by(BiMartBuild.as_of_date.desc())
        .limit(1)
    )


def _query_for(subscription: BiSubscription, as_of: date) -> BiQuery:
    """The subscription's query with the reporting date REBOUND to ``as_of``.

    A date frozen at authoring would mail the same month for ever, so the stored
    query names a window shape and the run supplies the date. Only a single
    ``as_of`` is supported: a stored ``range`` or ``compare_to`` would be an
    absolute historical window that a recurring delivery drifts away from, and
    there is no "previous period" token in ``BiQuery`` to rebind instead. A row
    carrying either is refused rather than silently reinterpreted.
    """

    query = BiQuery.model_validate(subscription.query)
    if query.time.range is not None or query.time.compare_to is not None:
        raise SubscriptionError(REASON_UNSUPPORTED_TIME_WINDOW)
    return query.model_copy(update={"time": BiTime(as_of=as_of)})


def run_subscription(
    db: Session,
    job: Job,
    *,
    relay_factory: RelayFactory | None = None,
) -> RunOutcome:
    """Deliver one run: one authorization, one render and one email PER recipient.

    Writes ``bi_subscription_deliveries`` and ``bi_query_log`` and nothing else;
    the ``audit_events`` rows are the handler's (the plane guard). Commits as it
    goes — each recipient's claim is committed before anything is sent — so a
    caller must not assume a single transaction across the batch.
    """

    request = read_request(db, job)
    subscription = request.subscription
    base = RunOutcome(
        subscription_id=subscription.id,
        bank_id=request.bank.id,
        scheduled_for=request.scheduled_for,
        trigger=request.trigger,
        as_of_date=request.as_of_date,
    )
    if not subscription.is_active:
        return _refused(base, REASON_SUBSCRIPTION_INACTIVE)
    if not mailer.relay_configured():
        # Nothing is claimed and nothing is recorded as delivered: a deployment
        # with no relay has not failed to send, it cannot send at all.
        return _refused(base, REASON_RELAY_NOT_CONFIGURED)
    recipients = [
        user_id
        for user_id in (_as_uuid(raw) for raw in subscription.recipient_user_ids)
        if user_id is not None
    ]
    if len(recipients) > MAX_RECIPIENTS:
        return _refused(base, REASON_RECIPIENT_CAP_EXCEEDED)

    as_of = request.as_of_date or latest_built_as_of(
        db, organization_id=job.organization_id, bank_id=request.bank.id
    )
    # Nothing to deliver, for either of the two reasons there can be none. The
    # second is newer and easy to miss: a date whose latest build FAILED still
    # HAS rows — the builder rolls a failed rebuild back to the previous ones —
    # and since audit A360 H2 `build_fingerprint` answers with a digest for that
    # state rather than ``None``, nothing downstream would have noticed. Mailing
    # a board pack of figures the bank's own book has moved past is the worst
    # form that defect takes: it leaves the building, it reaches people who will
    # act on it, and its provenance line would name a build that did not succeed.
    # A skipped run is recoverable; a delivered one is not. (`stale_dates` is
    # only reached when `as_of` is known — `or` short-circuits.)
    if as_of is None or provenance.stale_dates(
        db, organization_id=job.organization_id, bank_id=request.bank.id, window=(as_of, as_of)
    ):
        return _refused(base, REASON_NO_BUILD if as_of is None else REASON_STALE_BUILD)
    try:
        query = _query_for(subscription, as_of)
    except SubscriptionError as exc:
        return _refused(base, str(exc))

    cat = catalogue()
    members = query_members(cat, query)
    export_class = policy.classify(members)
    # The classification decides this BEFORE a row is read: a record-level
    # subscription never executes its query, so its rows never leave the
    # database. Only an aggregated artifact is ever attached.
    mode = "attachment" if export_class == policy.SUMMARY else "link"
    fingerprint = provenance.build_fingerprint(
        db, organization_id=job.organization_id, bank_id=request.bank.id, window=(as_of, as_of)
    )

    outcomes: list[DeliveryOutcome] = []
    with ExitStack() as stack:
        relay = _LazyRelay(stack, relay_factory or mailer.open_relay)
        for recipient_id in recipients:
            outcome = _deliver_one(
                db,
                job=job,
                request=request,
                cat=cat,
                query=query,
                export_class=export_class,
                mode=mode,
                as_of=as_of,
                fingerprint=fingerprint,
                recipient_id=recipient_id,
                relay=relay,
            )
            if outcome is not None:
                outcomes.append(outcome)
    return RunOutcome(
        subscription_id=subscription.id,
        bank_id=request.bank.id,
        scheduled_for=request.scheduled_for,
        trigger=request.trigger,
        as_of_date=as_of,
        deliveries=tuple(outcomes),
    )


def _refused(base: RunOutcome, reason: str) -> RunOutcome:
    return RunOutcome(
        subscription_id=base.subscription_id,
        bank_id=base.bank_id,
        scheduled_for=base.scheduled_for,
        trigger=base.trigger,
        as_of_date=base.as_of_date,
        deliveries=(),
        reason=reason,
    )


class _LazyRelay:
    """Opens the relay on the FIRST message and reuses it for the rest.

    A run whose every recipient is refused must not open a connection at all —
    that is both wasteful and, on a deployment whose relay is down, a failure
    report about a batch that had nothing to send.
    """

    __slots__ = ("_factory", "_relay", "_stack")

    def __init__(self, stack: ExitStack, factory: RelayFactory) -> None:
        self._stack = stack
        self._factory = factory
        self._relay: mailer.Relay | None = None

    def send_message(self, message: EmailMessage) -> None:
        if self._relay is None:
            self._relay = self._stack.enter_context(self._factory())
        self._relay.send_message(message)


def _existing_claim(
    db: Session, *, request: RunRequest, recipient_id: UUID
) -> BiSubscriptionDelivery | None:
    """This recipient's row for this exact run, if one already exists."""

    return db.scalar(
        select(BiSubscriptionDelivery).where(
            BiSubscriptionDelivery.organization_id == request.subscription.organization_id,
            BiSubscriptionDelivery.subscription_id == request.subscription.id,
            BiSubscriptionDelivery.recipient_user_id == recipient_id,
            BiSubscriptionDelivery.scheduled_for == request.scheduled_for,
        )
    )


def _claim(  # noqa: PLR0913 - one delivery row is its named parts
    db: Session,
    *,
    request: RunRequest,
    recipient_id: UUID,
    as_of: date,
    export_class: str,
    fingerprint: str | None,
) -> BiSubscriptionDelivery | None:
    """Insert and COMMIT this recipient's ``pending`` row, or ``None`` if taken.

    The commit is the claim. Two workers racing the same run collide on
    ``uq_bi_subscription_deliveries_run_recipient`` and exactly one proceeds; the
    other confirms a row now exists and sends nothing.

    ``delivery_mode`` and ``artifact_format`` are deliberately NULL here: a claim
    is "this recipient is mine to deliver to", not a statement about what was
    delivered, and the row's own CHECK requires an attachment to account for its
    bytes — which do not exist yet. They are set in :func:`_send`, with the
    digest, in the same write.

    An ``IntegrityError`` is only read as "already claimed" when a row is
    actually there afterwards. Anything else — a CHECK, a foreign key — is
    re-raised: swallowing it would report a delivery as sent that never happened,
    which is the worst failure this module could have.
    """

    row = BiSubscriptionDelivery(
        organization_id=request.subscription.organization_id,
        bank_id=request.bank.id,
        subscription_id=request.subscription.id,
        recipient_user_id=recipient_id,
        scheduled_for=request.scheduled_for,
        trigger=request.trigger,
        status="pending",
        as_of_date=as_of,
        disclosure_class=export_class,
        delivery_mode=None,
        artifact_format=None,
        member_ids=[],
        denied_members=[],
        build_fingerprint=fingerprint,
        builder_version=BUILDER_VERSION,
        created_at=utc_now(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if _existing_claim(db, request=request, recipient_id=recipient_id) is None:
            raise
        return None
    return row


def _deliver_one(  # noqa: PLR0913, PLR0911 - one recipient, and every way it can end
    db: Session,
    *,
    job: Job,
    request: RunRequest,
    cat: Catalogue,
    query: BiQuery,
    export_class: policy.ExportClass,
    mode: str,
    as_of: date,
    fingerprint: str | None,
    recipient_id: UUID,
    relay: _LazyRelay,
) -> DeliveryOutcome | None:
    """Authorize, render and send for ONE recipient.

    A recipient this run has already been delivered to (or refused for) is
    reported with the outcome THAT row records, not with a fresh one: reporting a
    second "sent" would put two deliveries in the audit trail where one happened,
    and reporting a fresh "sent" over a row that says ``denied`` would be a
    straightforward lie about a disclosure.
    """

    taken = _existing_claim(db, request=request, recipient_id=recipient_id)
    row = (
        None
        if taken is not None
        else _claim(
            db,
            request=request,
            recipient_id=recipient_id,
            as_of=as_of,
            export_class=export_class,
            fingerprint=fingerprint,
        )
    )
    if row is None:
        # Either the pre-check found it, or a concurrent worker won the insert.
        settled = taken or _existing_claim(db, request=request, recipient_id=recipient_id)
        return DeliveryOutcome(
            recipient_user_id=recipient_id,
            status=settled.status if settled is not None else "pending",
            delivery_mode=None if settled is None else settled.delivery_mode,
            disclosure_class=None if settled is None else settled.disclosure_class,
            reason=REASON_ALREADY_DELIVERED,
            as_of_date=as_of if settled is None else settled.as_of_date,
            build_fingerprint=None if settled is None else settled.build_fingerprint,
        )

    recipient = db.scalar(
        select(User).where(
            User.id == recipient_id, User.organization_id == request.subscription.organization_id
        )
    )
    if recipient is None or not recipient.is_active:
        return _finish(db, row, status="denied", reason=REASON_RECIPIENT_INACTIVE)

    ctx = TenantContext(
        organization_id=recipient.organization_id,
        actor_user_id=recipient.id,
        authorization_version=recipient.authorization_version,
    )
    # A link discloses nothing but the existence of the content, so it asks for
    # ``view`` alone; an ATTACHED artifact asks for the complete export sentence
    # the interactive path asks for. Record-level content is never attached, so
    # ``export`` is in practice never required of a subscription — the rule is
    # written out anyway so a future attachable class cannot lose it. And it is
    # ONE conjunctive decision, not a loop keeping the last permission's answer:
    # the loop this replaced (audit A360-1) would have rendered the whole
    # institution for a recipient whose ``view`` covered one branch and whose
    # ``export`` covered the book, because ``export`` came last. The shared
    # helper combines the scopes identical-or-refuse, as ``read_bi`` does.
    required: Sequence[Permission] = (
        policy.permissions_for(export_class) if mode == "attachment" else (Permission.VIEW,)
    )
    attempt = query_log.QueryRecord(
        organization_id=job.organization_id,
        bank_id=request.bank.id,
        principal_user_id=recipient.id,
        surface=exports.QUERY_LOG_SURFACE,
        query_hash=query_log.query_digest(query),
        decision=query_log.DECISION_DENIED,
        catalogue_version=CATALOGUE_VERSION,
    )
    try:
        decision = authorize_query_conjunctive(
            db, ctx, request.bank, cat, query, permissions=required, surface="subscription"
        )
    except BiQueryError as exc:
        query_log.record(db, attempt)
        return _finish(db, row, status="failed", reason=exc.code)
    if not decision.allowed:
        query_log.record(
            db, _with(attempt, member_ids=decision.member_ids, denied=decision.denied_members)
        )
        return _finish(
            db,
            row,
            status="denied",
            reason=decision.reason,
            member_ids=decision.member_ids,
            denied_members=decision.denied_members,
        )

    if mode != "attachment":
        message = _compose_link(request, recipient, as_of=as_of)
        return _send(db, row, relay, message, mode="link", member_ids=decision.member_ids)

    rendered = _render_for(
        db, request=request, cat=cat, query=query, decision=decision, recipient=recipient
    )
    if rendered.refusal is not None:
        query_log.record(db, _with(attempt, member_ids=decision.member_ids))
        status = "no_data" if rendered.refusal == REASON_NO_FIGURES else "failed"
        if rendered.refusal in (REASON_DISCLOSURE_CLASS_CHANGED, REASON_DATA_SCOPE_UNSUPPORTED):
            status = "denied"
        return _finish(
            db, row, status=status, reason=rendered.refusal, member_ids=decision.member_ids
        )
    assert rendered.payload is not None and rendered.context is not None  # noqa: S101

    # The read is logged HERE, before the size decision: the mart was read either
    # way, and whether the bytes travelled or a link did is a delivery fact
    # rather than a reason to leave a served query out of the reviewer's log.
    query_log.record(
        db,
        _with(
            attempt,
            member_ids=rendered.member_ids,
            decision=query_log.DECISION_ALLOWED,
            row_count=rendered.row_count,
            duration_ms=rendered.duration_ms,
            build_fingerprint=rendered.context.build_fingerprint,
        ),
    )

    cap = get_settings().bi.subscription_attachment_max_bytes
    if len(rendered.payload) > cap:
        # Too large for a relay to carry. The content is still aggregated, so the
        # recipient is entitled to it — they are sent to it rather than refused.
        message = _compose_link(
            request, recipient, as_of=as_of, note=REASON_ATTACHMENT_OVER_SIZE_CAP
        )
        return _send(
            db,
            row,
            relay,
            message,
            mode="link",
            member_ids=decision.member_ids,
            reason=REASON_ATTACHMENT_OVER_SIZE_CAP,
        )

    message = _compose_attachment(
        request,
        recipient,
        as_of=as_of,
        payload=rendered.payload,
        filename=rendered.filename or "",
        media_type=rendered.media_type or "",
        row_count=rendered.row_count or 0,
    )
    return _send(
        db,
        row,
        relay,
        message,
        mode="attachment",
        member_ids=rendered.member_ids,
        artifact_format=request.subscription.artifact_format,
        row_count=rendered.row_count,
        payload=rendered.payload,
    )


@dataclass(frozen=True, slots=True)
class _Rendered:
    """The artifact for ONE recipient, or the reason there is not one."""

    refusal: str | None = None
    payload: bytes | None = None
    context: Any = None
    filename: str | None = None
    media_type: str | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    member_ids: tuple[str, ...] = ()


def _render_for(  # noqa: PLR0913 - one render and every input it is made from
    db: Session,
    *,
    request: RunRequest,
    cat: Catalogue,
    query: BiQuery,
    decision: Any,
    recipient: User,
) -> _Rendered:
    """Compile, run and render THIS recipient's copy. Nothing is shared or cached.

    The caps are the interactive ones (``BI_UI_ROW_CAP`` /
    ``BI_INTERACTIVE_TIMEOUT_MS``) rather than the export ones: a mailed pack is
    something a person reads, not a bulk extraction, and the run's stale-job
    window is set against this budget times the recipient cap.

    ``injected_filters`` is where THIS recipient's ``decision.data_scope`` becomes
    an unremovable filter, and it is what makes two recipients' bytes differ. The
    scope is resolved here rather than by the caller so it cannot be resolved once
    and reused across recipients, and the same scope is handed to
    ``build_context`` so the artifact states the slice it covers.
    """

    settings = get_settings().bi
    try:
        scope = data_scope.resolve(
            db,
            decision.data_scope,
            organization_id=request.subscription.organization_id,
            bank_id=request.bank.id,
        )
        injected = scope.filters
    except BiQueryError:
        return _Rendered(refusal=REASON_DATA_SCOPE_UNSUPPORTED)
    try:
        run = runner.run_query(
            db,
            cat=cat,
            query=query,
            organization_id=request.subscription.organization_id,
            bank_id=request.bank.id,
            injected_filters=injected,
            row_cap=settings.ui_row_cap,
            timeout_ms=settings.interactive_timeout_ms,
        )
    except BiQueryError as exc:
        return _Rendered(refusal=exc.code)
    if not run.result.rows:
        return _Rendered(refusal=REASON_NO_FIGURES)
    # Fail closed on the COMPILED member set, exactly as the export job does: a
    # compilation that widened the set must not be served under a classification
    # that covered the narrower one.
    if policy.classify_ids(cat, run.compiled.member_ids) != policy.SUMMARY:
        return _Rendered(refusal=REASON_DISCLOSURE_CLASS_CHANGED)
    fmt = _export_format(request.subscription.artifact_format)
    if fmt is None:
        return _Rendered(refusal=REASON_UNSUPPORTED_FORMAT)
    context = runner.build_context(
        db,
        bank=request.bank,
        query=query,
        cat=cat,
        data_scope=scope,
        export_class=policy.SUMMARY,
        user_label=recipient.email,
    )
    table = runner.to_table(run, context)
    payload = exports.render(fmt, table, context)
    return _Rendered(
        payload=payload,
        context=context,
        filename=exports.filename_for(
            fmt, bank_id=request.bank.id, as_of_label=context.as_of_label
        ),
        media_type=exports.MEDIA_TYPES[fmt],
        row_count=run.row_count,
        duration_ms=run.result.elapsed_ms,
        member_ids=run.compiled.member_ids,
    )


def _with(  # noqa: PLR0913 - one log row's optional facts, each named
    record: query_log.QueryRecord,
    *,
    member_ids: Sequence[str] = (),
    denied: Sequence[str] = (),
    decision: str | None = None,
    row_count: int | None = None,
    duration_ms: int | None = None,
    build_fingerprint: str | None = None,
) -> query_log.QueryRecord:
    """A copy of the log record with what the attempt learned."""

    return query_log.QueryRecord(
        organization_id=record.organization_id,
        bank_id=record.bank_id,
        principal_user_id=record.principal_user_id,
        surface=record.surface,
        query_hash=record.query_hash,
        decision=decision or record.decision,
        catalogue_version=record.catalogue_version,
        member_ids=tuple(member_ids),
        denied_members=tuple(denied),
        row_count=row_count,
        duration_ms=duration_ms,
        build_fingerprint=build_fingerprint,
    )


# --- the message --------------------------------------------------------------------------------

#: Why the recipient is receiving this at all, and how to stop it. Every
#: scheduled message carries it: an unexplained recurring email is how a bank's
#: staff learn to filter the platform out.
_FOOTER = (
    "\n\n--\nYou receive this because it was added to a scheduled report in AequorOS for "
    "{institution}. To change or stop it, sign in and open Explore."
)

#: What the recipient is told about an attached file. It is a statement of what
#: the classification guarantees, not a disclaimer.
_ATTACHMENT_NOTE = (
    "The attached file holds totals only. It names no counterparty and no individual record."
)

#: What the recipient is told when the content may not travel.
_LINK_NOTE = "This report identifies individual records, so it is not attached. Sign in to read it:"

#: And when it is aggregated but simply too large for the relay.
_SIZE_NOTE = "This report is too large to attach. Sign in to read or download it:"


def _sign_in_url() -> str:
    """Where a recipient signs in. The origin is deployment configuration."""

    return f"{get_settings().bi.bank_app_base_url}{SIGN_IN_PATH}"


def _subject(request: RunRequest, as_of: date) -> str:
    return mailer.subject_line(f"{request.subscription.name} — {as_of.isoformat()}")


def _compose_attachment(  # noqa: PLR0913 - one message and everything printed on it
    request: RunRequest,
    recipient: User,
    *,
    as_of: date,
    payload: bytes,
    filename: str,
    media_type: str,
    row_count: int,
) -> EmailMessage:
    body = (
        f"{request.subscription.name}\n\n"
        f"{request.bank.name} — position as at {as_of.isoformat()}.\n"
        f"{row_count} rows. Amounts are stated in the institution's reporting currency.\n\n"
        f"{_ATTACHMENT_NOTE}" + _FOOTER.format(institution=request.bank.name)
    )
    return mailer.compose(
        subject=_subject(request, as_of),
        body=body,
        recipients=[recipient.email],
        sender_address=mailer.sender(),
        attachments=[mailer.Attachment(filename=filename, content=payload, media_type=media_type)],
    )


def _compose_link(
    request: RunRequest, recipient: User, *, as_of: date, note: str | None = None
) -> EmailMessage:
    lead = _SIZE_NOTE if note == REASON_ATTACHMENT_OVER_SIZE_CAP else _LINK_NOTE
    body = (
        f"{request.subscription.name}\n\n"
        f"{request.bank.name} — position as at {as_of.isoformat()} is ready.\n\n"
        f"{lead}\n{_sign_in_url()}" + _FOOTER.format(institution=request.bank.name)
    )
    return mailer.compose(
        subject=_subject(request, as_of),
        body=body,
        recipients=[recipient.email],
        sender_address=mailer.sender(),
    )


# --- finishing a delivery row --------------------------------------------------------------------


def _finish(  # noqa: PLR0913 - the terminal state of one delivery row
    db: Session,
    row: BiSubscriptionDelivery,
    *,
    status: str,
    reason: str | None = None,
    member_ids: Sequence[str] = (),
    denied_members: Sequence[str] = (),
) -> DeliveryOutcome:
    """Record a terminal, UNSENT outcome for one recipient and commit it."""

    row.status = status
    row.reason = reason
    row.member_ids = list(member_ids)
    row.denied_members = list(denied_members)
    if status != "sent":
        # A refused or failed delivery carries no artifact accounting: the CHECK
        # constraint requires an attachment to account for its bytes, and there
        # are none.
        row.delivery_mode = None
        row.artifact_format = None
        row.artifact_sha256 = None
        row.artifact_size_bytes = None
    db.commit()
    return DeliveryOutcome(
        recipient_user_id=row.recipient_user_id,
        status=status,
        delivery_mode=row.delivery_mode,
        disclosure_class=row.disclosure_class,
        reason=reason,
        denied_members=tuple(denied_members),
        as_of_date=row.as_of_date,
        build_fingerprint=row.build_fingerprint,
    )


def _send(  # noqa: PLR0913 - one send and the evidence it leaves
    db: Session,
    row: BiSubscriptionDelivery,
    relay: _LazyRelay,
    message: EmailMessage,
    *,
    mode: str,
    member_ids: Sequence[str],
    artifact_format: str | None = None,
    row_count: int | None = None,
    payload: bytes | None = None,
    reason: str | None = None,
) -> DeliveryOutcome:
    """Hand one message to the relay and record what went; commit either way.

    The mode, the format and the digest land TOGETHER: a row may only claim an
    attachment while accounting for its bytes, and the only moment both are known
    is the moment the message exists.
    """

    row.member_ids = list(member_ids)
    row.row_count = row_count
    row.delivery_mode = mode
    row.artifact_format = artifact_format if mode == "attachment" else None
    if payload is not None and mode == "attachment":
        row.artifact_sha256 = hashlib.sha256(payload).hexdigest()
        row.artifact_size_bytes = len(payload)
    try:
        relay.send_message(message)
    except mailer.TRANSPORT_ERRORS as exc:
        row.status = "failed"
        row.reason = REASON_RELAY_UNAVAILABLE
        row.error = f"{type(exc).__name__}: {exc}"[:2000]
        row.delivery_mode = None
        row.artifact_format = None
        row.artifact_sha256 = None
        row.artifact_size_bytes = None
        # Committed BEFORE raising: the worker rolls its session back on the way
        # to a retry, and the attempt must survive that.
        db.commit()
        raise SubscriptionRelayError(str(exc)) from exc
    row.status = "sent"
    row.reason = reason
    row.sent_at = utc_now()
    db.commit()
    return DeliveryOutcome(
        recipient_user_id=row.recipient_user_id,
        status="sent",
        delivery_mode=row.delivery_mode,
        disclosure_class=row.disclosure_class,
        reason=reason,
        row_count=row.row_count,
        artifact_sha256=row.artifact_sha256,
        artifact_size_bytes=row.artifact_size_bytes,
        as_of_date=row.as_of_date,
        build_fingerprint=row.build_fingerprint,
    )


def _export_format(value: str) -> exports.ExportFormat | None:
    """The stored format as a renderer name, or ``None`` when there is no renderer.

    The CHECK constraint and the schema both hold this vocabulary, so a row
    reaching ``None`` was written another way; it refuses rather than raising,
    because one unrenderable subscription must not fail a whole batch.
    """

    if value in exports.EXPORT_FORMATS:
        return cast("exports.ExportFormat", value)
    return None


def _as_uuid(raw: object) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (AttributeError, TypeError, ValueError):
        return None


def deliveries_for(
    db: Session, *, organization_id: str, subscription_id: UUID
) -> Iterator[BiSubscriptionDelivery]:
    """Every delivery of one subscription, newest first. For the owner's history."""

    return iter(
        db.scalars(
            select(BiSubscriptionDelivery)
            .where(
                BiSubscriptionDelivery.organization_id == organization_id,
                BiSubscriptionDelivery.subscription_id == subscription_id,
            )
            .order_by(BiSubscriptionDelivery.scheduled_for.desc())
        )
    )


__all__ = [
    "ENTITY_TYPE",
    "JOB_TYPE_RUN",
    "JOB_TYPE_SCAN",
    "MAX_RECIPIENTS",
    # The one reason constant that crosses a module boundary: the handler reads it
    # to decide that a repeat pass has nothing to audit.
    "REASON_ALREADY_DELIVERED",
    "SIGN_IN_PATH",
    "DeliveryOutcome",
    "DueRun",
    "RunOutcome",
    "RunRequest",
    "SubscriptionError",
    "SubscriptionRelayError",
    "coalesce_key_for",
    "deliveries_for",
    "due_runs",
    "enqueue_due_runs",
    "enqueue_on_new_data",
    "latest_built_as_of",
    "read_request",
    "run_subscription",
]
