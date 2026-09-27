"""``app.services.bi.subscriptions``: whose authority, which minute, and how many.

The three properties this suite exists for, in the order they matter:

1. **A delivery carries the RECIPIENT's authority, not the owner's.**
   :func:`test_a_delivery_is_rendered_as_the_recipient_and_not_as_the_owner` is
   the test the whole feature rests on: one owner who can see everything, two
   recipients who cannot see the same things, and the assertion that the narrow
   recipient's mailbox holds nothing at all while the broad one's artifact is
   attributed to THEM. The artifact is CSV so the attribution is inspectable in
   the bytes — the provenance block's "Exported by" field is the recipient's own
   address, which is only true if the render happened under their context.
2. **Confidential content is never attached.** The decision is the export
   policy's, taken from the catalogue's own sensitivity declarations BEFORE a row
   is read — so a record-level subscription has no query log row at all, which is
   the observable form of "the rows never left the database".
3. **A run cannot send twice.** Proved three ways: a second call to the same run
   sends nothing, a second enqueue of the same minute queues nothing, and the
   delivery row's unique key refuses the duplicate at the database
   (``tests/models/test_bi_notification_models.py``).

**No socket is ever opened.** ``smtplib.SMTP`` is replaced by a fake that records
the messages, exactly as the notification mirror's own suite does it, and the real
``app/services/mailer.py`` path is exercised through it.
"""

from __future__ import annotations

import smtplib
from datetime import UTC, date, datetime
from email.message import EmailMessage
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.jobs import bi_subscriptions as subscription_job
from app.models import AuditEvent, Bank, Job, User
from app.models.bi import BiQueryLog
from app.models.bi_notifications import BiSubscription, BiSubscriptionDelivery
from app.services import authorization, job_queue, scheduler
from app.services.bi import subscriptions
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_mart_builder import AS_OF, build, seed_book

LOANS = "loans.balance_rc"
#: A dimension the catalogue declares ``restricted``: it names a legal person, so
#: the export policy puts any query touching it in the record-level class.
COUNTERPARTY_NAME = "counterparty.name"

HOST = "smtp.test.local"
SENDER = "aequoros@test.local"
SCHEDULED = datetime(2026, 6, 30, 7, 30, tzinfo=UTC)


class _FakeSmtp:
    """Records the messages; class-level stores survive the context manager."""

    sent: list[EmailMessage] = []
    opened = 0
    fail_on_send: BaseException | None = None

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        _FakeSmtp.opened += 1

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        if _FakeSmtp.fail_on_send is not None:
            raise _FakeSmtp.fail_on_send
        _FakeSmtp.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_fake_smtp() -> None:
    _FakeSmtp.sent = []
    _FakeSmtp.opened = 0
    _FakeSmtp.fail_on_send = None


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSmtp]:
    """A configured relay and an enabled feature, with no network anywhere."""

    monkeypatch.setenv("SMTP_HOST", HOST)
    monkeypatch.setenv("SMTP_FROM", SENDER)
    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    return _FakeSmtp


@pytest.fixture
def bank(db_session: Session) -> Bank:
    institution = seed_book(db_session)
    build(db_session)
    db_session.commit()
    return institution


@pytest.fixture
def _job_types_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit this track's job types to the queue allow-list for the test.

    ``job_queue.JOB_TYPES`` / ``JOB_LANES`` belong to another track and the
    registration is a one-line change there (named in the task report). This only
    ADDS missing names, so it becomes a no-op rather than a lie once it lands.
    """

    wanted = (subscriptions.JOB_TYPE_SCAN, subscriptions.JOB_TYPE_RUN, "bi_alert_evaluate")
    missing = tuple(name for name in wanted if name not in job_queue.JOB_TYPES)
    if missing:
        monkeypatch.setattr(job_queue, "JOB_TYPES", (*job_queue.JOB_TYPES, *missing))
        monkeypatch.setattr(
            job_queue,
            "JOB_LANES",
            {**job_queue.JOB_LANES, **dict.fromkeys(missing, "bi")},
        )


def _user(db: Session, email: str) -> User:
    row = User(
        id=uuid4(),
        organization_id=ORG_1,
        email=email,
        display_name=email.split("@", 1)[0],
        role="viewer",
        authorization_version=1,
    )
    db.add(row)
    db.flush()
    return row


def _grant(
    db: Session,
    user_id: UUID,
    *,
    module: ModuleScope = ModuleScope.ALL,
    sensitivity: SensitivityScope = SensitivityScope.ALL,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise BI subscription authorization.",
        commit=False,
    )
    db.flush()


def _subscription(db: Session, *, recipients: list[UUID], **overrides: Any) -> BiSubscription:
    values: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "name": f"Pack {uuid4().hex[:8]}",
        "owner_user_id": USER_1,
        "query": {"measures": [LOANS], "time": {"as_of": AS_OF.isoformat()}},
        "artifact_format": "csv",
        "cadence": "daily",
        "hour": 7,
        "minute": 30,
        "recipient_user_ids": [str(one) for one in recipients],
        "is_active": True,
    }
    values.update(overrides)
    row = BiSubscription(**values)
    db.add(row)
    db.flush()
    return row


def _run_job(db: Session, subscription: BiSubscription, **overrides: Any) -> Job:
    payload: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "subscription_id": str(subscription.id),
        "scheduled_for": SCHEDULED.isoformat(),
        "trigger": "schedule",
        "builder_version": 1,
    }
    payload.update(overrides.pop("payload", {}))
    row = Job(
        organization_id=ORG_1,
        job_type=subscriptions.JOB_TYPE_RUN,
        status="running",
        bank_id=SAMPLE_BANK_ID,
        payload=payload,
        **overrides,
    )
    db.add(row)
    db.flush()
    return row


def _deliveries(db: Session, subscription: BiSubscription) -> list[BiSubscriptionDelivery]:
    return list(
        db.scalars(
            select(BiSubscriptionDelivery).where(
                BiSubscriptionDelivery.subscription_id == subscription.id
            )
        )
    )


def _attachments(message: EmailMessage) -> list[Any]:
    return list(message.iter_attachments())


def _recipients_of(message: EmailMessage) -> str:
    return str(message["To"])


# --- THE crux: whose authority rendered this ------------------------------------


def test_a_delivery_is_rendered_as_the_recipient_and_not_as_the_owner(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The whole feature rests on this.

    ``USER_1`` owns the subscription and holds an organization-wide sentence over
    every module at every sensitivity: they can see the figure. One recipient
    holds a sentence over a DIFFERENT module and one holds nothing at all —
    neither could have asked this question themselves. A third holds what the
    owner holds.

    What is asserted is the absence, not merely the presence: exactly ONE message
    exists, it is addressed to the broad recipient, and the artifact attached to
    it names THAT recipient as the person it was produced for. If the run had
    rendered once under the owner's authority and fanned the bytes out, there
    would be three messages and the attribution would read ``USER_1``.
    """

    broad = _user(db_session, "sub.broad@bank.test")
    _grant(db_session, broad.id)
    wrong_module = _user(db_session, "sub.wrongmodule@bank.test")
    _grant(db_session, wrong_module.id, module=ModuleScope.LIQUIDITY)
    ungranted = _user(db_session, "sub.none@bank.test")
    owner = db_session.get(User, USER_1)
    assert owner is not None

    subscription = _subscription(db_session, recipients=[broad.id, wrong_module.id, ungranted.id])
    db_session.commit()

    outcome = subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    assert len(relay.sent) == 1
    message = relay.sent[0]
    assert _recipients_of(message) == broad.email

    attachment = _attachments(message)[0]
    body = attachment.get_payload(decode=True).decode("utf-8")
    assert broad.email in body, "the artifact must be attributed to the recipient"
    assert owner.email not in body, "the owner's authority must not appear on it"

    statuses = {one.recipient_user_id: one for one in _deliveries(db_session, subscription)}
    assert statuses[broad.id].status == "sent"
    assert statuses[broad.id].delivery_mode == "attachment"
    assert statuses[broad.id].artifact_sha256 is not None

    for refused in (wrong_module.id, ungranted.id):
        row = statuses[refused]
        assert row.status == "denied", refused
        assert row.delivery_mode is None
        assert row.artifact_sha256 is None
        assert row.artifact_size_bytes is None
        assert row.row_count is None
        assert LOANS in row.denied_members, "the refusal must name what was missing"

    assert {one.status for one in outcome.deliveries} == {"sent", "denied"}


def test_a_refused_recipient_receives_no_figure_and_no_member_name(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The negative, stated as its own test so it cannot be lost in a refactor.

    Nothing addressed to the refused recipient exists — not an empty artifact,
    not a "you are not authorized" note naming the report's measures, not a
    subject line quoting them. The only mail that exists is the authorized one.
    """

    refused = _user(db_session, "sub.refused@bank.test")
    subscription = _subscription(db_session, recipients=[refused.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    assert relay.sent == []
    assert relay.opened == 0, "a run with nothing to send must not open the relay"
    row = _deliveries(db_session, subscription)[0]
    assert row.status == "denied"
    # The refusal is recorded as member IDS, never as a figure or a filter value.
    assert row.row_count is None
    assert row.artifact_size_bytes is None


def test_a_deactivated_recipient_is_refused_rather_than_skipped(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    gone = _user(db_session, "sub.gone@bank.test")
    _grant(db_session, gone.id)
    gone.is_active = False
    subscription = _subscription(db_session, recipients=[gone.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert relay.sent == []
    row = _deliveries(db_session, subscription)[0]
    assert row.status == "denied"
    assert row.reason == subscriptions.REASON_RECIPIENT_INACTIVE


# --- confidential content gets a link ------------------------------------------


def test_record_level_content_is_never_attached_and_is_never_even_read(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The classifier decides this from the members, before a row is read.

    An emailed spreadsheet of named obligors leaves the platform's control
    entirely, so the content does not travel at all: the recipient gets a sign-in
    link. That the query was never executed is observable — a link delivery writes
    no ``bi_query_log`` row, because no mart read happened.
    """

    reader = _user(db_session, "sub.reader@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(
        db_session,
        recipients=[reader.id],
        query={
            "measures": [LOANS],
            "dimensions": [COUNTERPARTY_NAME],
            "time": {"as_of": AS_OF.isoformat()},
        },
    )
    db_session.commit()
    before = len(list(db_session.scalars(select(BiQueryLog.id))))

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    assert len(relay.sent) == 1
    message = relay.sent[0]
    assert _attachments(message) == []
    text = message.get_content()
    assert subscriptions.SIGN_IN_PATH in text
    assert get_settings().bi.bank_app_base_url in text

    row = _deliveries(db_session, subscription)[0]
    assert row.status == "sent"
    assert row.delivery_mode == "link"
    assert row.disclosure_class == "record_level"
    assert row.artifact_sha256 is None

    after = len(list(db_session.scalars(select(BiQueryLog.id))))
    assert after == before, "a link delivery must read no mart rows at all"


def test_an_aggregated_subscription_is_attached_and_logged_as_a_read(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The other side of the same rule, so the classifier is not vacuously strict."""

    reader = _user(db_session, "sub.summary@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(db_session, recipients=[reader.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    row = _deliveries(db_session, subscription)[0]
    assert row.disclosure_class == "summary"
    assert row.delivery_mode == "attachment"
    assert row.row_count is not None and row.row_count > 0
    logged = list(
        db_session.scalars(select(BiQueryLog).where(BiQueryLog.principal_user_id == reader.id))
    )
    assert [one.decision for one in logged] == ["allowed"]
    assert logged[0].surface == "export"


def test_an_artifact_over_the_size_cap_becomes_a_link(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Too large to carry is not the same as not entitled: they are sent to it.

    A relay that bounces an oversized message fails the whole run, so the size is
    checked before the send rather than discovered by it.
    """

    monkeypatch.setenv("BI_SUBSCRIPTION_ATTACHMENT_MAX_BYTES", "1")
    get_settings.cache_clear()
    reader = _user(db_session, "sub.big@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(db_session, recipients=[reader.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    assert _attachments(relay.sent[0]) == []
    row = _deliveries(db_session, subscription)[0]
    assert row.status == "sent"
    assert row.delivery_mode == "link"
    assert row.reason == subscriptions.REASON_ATTACHMENT_OVER_SIZE_CAP
    assert row.artifact_sha256 is None
    # The mart WAS read on this path, so the reviewer's log must say so even
    # though the bytes did not travel.
    logged = list(
        db_session.scalars(select(BiQueryLog).where(BiQueryLog.principal_user_id == reader.id))
    )
    assert [one.decision for one in logged] == ["allowed"]


# --- a run cannot send twice ----------------------------------------------------


def test_running_the_same_run_twice_sends_one_message(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """Idempotence as a tested property: a bank never receives two board packs.

    The claim is COMMITTED before anything is sent, so the second pass finds the
    row and stops — and the same collision is what a reclaimed job running beside
    the original hits.
    """

    reader = _user(db_session, "sub.twice@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(db_session, recipients=[reader.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert len(relay.sent) == 1

    second = subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert len(relay.sent) == 1, "the second pass must send nothing"
    assert [one.reason for one in second.deliveries] == [subscriptions.REASON_ALREADY_DELIVERED]
    assert len(_deliveries(db_session, subscription)) == 1


def test_a_second_pass_reports_the_settled_outcome_and_audits_nothing(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """A repeat pass must not invent a second disclosure.

    The recipient here was REFUSED the first time. If the "already delivered"
    branch reported a fresh ``sent``, the audit trail would say a denied
    recipient received the pack — and the job progress would say so too.
    """

    refused = _user(db_session, "sub.settled@bank.test")
    subscription = _subscription(db_session, recipients=[refused.id])
    db_session.commit()

    first = _run_job(db_session, subscription)
    subscription_job.run_bi_subscription_run(db_session, first)
    audited_once = len(
        list(
            db_session.scalars(
                select(AuditEvent.id).where(AuditEvent.entity_id == str(subscription.id))
            )
        )
    )
    assert audited_once == 1

    second = _run_job(db_session, subscription)
    subscription_job.run_bi_subscription_run(db_session, second)

    assert second.progress["statuses"] == {"denied": 1}
    assert relay.sent == []
    assert (
        len(
            list(
                db_session.scalars(
                    select(AuditEvent.id).where(AuditEvent.entity_id == str(subscription.id))
                )
            )
        )
        == audited_once
    ), "a repeat pass must add no audit event"


def test_a_different_minute_is_a_different_run(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The guard must not be so broad that tomorrow's pack is refused too."""

    reader = _user(db_session, "sub.tomorrow@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(db_session, recipients=[reader.id])
    db_session.commit()

    subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    tomorrow = datetime(2026, 7, 1, 7, 30, tzinfo=UTC)
    subscriptions.run_subscription(
        db_session,
        _run_job(db_session, subscription, payload={"scheduled_for": tomorrow.isoformat()}),
    )
    assert len(relay.sent) == 2
    assert len(_deliveries(db_session, subscription)) == 2


# --- the clock ------------------------------------------------------------------


def _foreign_bank(db: Session) -> Bank:
    """A bank in a jurisdiction whose offset is NOT the server's.

    Africa/Nairobi is UTC+03:00 with no daylight saving, so a local 09:30 is
    06:30 UTC — which makes the assertion about WHOSE clock is being read
    unambiguous. The currency and the zone both come from the jurisdictions
    registry; nothing here is a literal about a country.
    """

    row = Bank(
        id="BK-SUBTZ001",
        organization_id=ORG_1,
        name="Offset Bank",
        short_name="Offset",
        currency="KES",
        jurisdiction_code="KE",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(row)
    db.flush()
    return row


def test_a_daily_run_is_due_in_the_institutions_own_time_zone(
    db_session: Session, bank: Bank
) -> None:
    """ "07:30" means half past seven where the bank is, not where the server is."""

    foreign = _foreign_bank(db_session)
    subscription = _subscription(
        db_session, recipients=[USER_1], bank_id=foreign.id, hour=9, minute=30
    )
    db_session.commit()

    due = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 30, 6, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, 7, 0, tzinfo=UTC),
    )
    mine = [one for one in due if one.subscription_id == subscription.id]
    assert len(mine) == 1
    assert mine[0].scheduled_for == datetime(2026, 6, 30, 6, 30, tzinfo=UTC)

    # And the naive reading — 09:30 UTC — finds nothing.
    naive = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, 10, 0, tzinfo=UTC),
    )
    assert [one for one in naive if one.subscription_id == subscription.id] == []


def test_a_weekly_run_is_due_only_on_its_weekday(db_session: Session, bank: Bank) -> None:
    # 2026-06-30 is a Tuesday (ISO weekday 2).
    assert date(2026, 6, 30).isoweekday() == 2
    tuesday = _subscription(
        db_session, recipients=[USER_1], cadence="weekly", day_of_week=2, hour=7, minute=30
    )
    friday = _subscription(
        db_session, recipients=[USER_1], cadence="weekly", day_of_week=5, hour=7, minute=30
    )
    db_session.commit()

    due = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 30, 7, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
    )
    found = {one.subscription_id for one in due}
    assert tuesday.id in found
    assert friday.id not in found


def test_a_monthly_run_is_due_only_on_its_day(db_session: Session, bank: Bank) -> None:
    """28 is the highest day a monthly subscription may name, so that it fires in
    every month rather than skipping the short ones."""

    on_the_day = _subscription(
        db_session, recipients=[USER_1], cadence="monthly", day_of_month=28, hour=7, minute=30
    )
    other = _subscription(
        db_session, recipients=[USER_1], cadence="monthly", day_of_month=15, hour=7, minute=30
    )
    db_session.commit()

    due = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 28, 7, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
    )
    found = {one.subscription_id for one in due}
    assert on_the_day.id in found
    assert other.id not in found


def test_an_on_new_data_subscription_is_never_on_the_clock(db_session: Session, bank: Bank) -> None:
    subscription = _subscription(
        db_session,
        recipients=[USER_1],
        cadence="on_new_data",
        hour=None,
        minute=None,
    )
    db_session.commit()
    due = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 30, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    assert [one for one in due if one.subscription_id == subscription.id] == []


def test_an_inactive_subscription_is_never_due(db_session: Session, bank: Bank) -> None:
    subscription = _subscription(db_session, recipients=[USER_1], is_active=False)
    db_session.commit()
    due = subscriptions.due_runs(
        db_session,
        organization_id=ORG_1,
        window_start=datetime(2026, 6, 30, 7, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
    )
    assert [one for one in due if one.subscription_id == subscription.id] == []


# --- the enqueue seam -----------------------------------------------------------


def test_nothing_is_enqueued_while_the_feature_is_off(db_session: Session, bank: Bank) -> None:
    _subscription(db_session, recipients=[USER_1])
    db_session.commit()
    assert (
        subscriptions.enqueue_due_runs(
            db_session,
            organization_id=ORG_1,
            window_start=datetime(2026, 6, 30, 7, 0, tzinfo=UTC),
            window_end=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
        )
        == []
    )
    assert (
        subscriptions.enqueue_on_new_data(
            db_session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            as_of=AS_OF,
            completed_at=SCHEDULED,
        )
        == []
    )


@pytest.mark.usefixtures("_job_types_registered")
def test_a_due_run_is_queued_for_its_exact_minute_and_only_once(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """``run_after`` is the minute, not the hour the sweep ran in.

    And a second sweep of the same hour queues nothing: the existence check on
    the key holds the cadence, because a COMPLETED job keeps its key and enqueue
    coalescing merges only rows that are still queued.
    """

    subscription = _subscription(db_session, recipients=[USER_1], hour=7, minute=30)
    db_session.commit()
    window = (
        datetime(2026, 6, 30, 7, 0, tzinfo=UTC),
        datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
    )

    queued = subscriptions.enqueue_due_runs(
        db_session, organization_id=ORG_1, window_start=window[0], window_end=window[1]
    )
    assert len(queued) == 1
    job = queued[0]
    assert job.run_after is not None
    assert job.run_after.replace(tzinfo=UTC) == datetime(2026, 6, 30, 7, 30, tzinfo=UTC)
    assert job.coalesce_key == subscriptions.coalesce_key_for(
        subscription.id, datetime(2026, 6, 30, 7, 30, tzinfo=UTC)
    )

    again = subscriptions.enqueue_due_runs(
        db_session, organization_id=ORG_1, window_start=window[0], window_end=window[1]
    )
    assert again == []
    assert (
        len(
            list(
                db_session.scalars(select(Job.id).where(Job.job_type == subscriptions.JOB_TYPE_RUN))
            )
        )
        == 1
    )


@pytest.mark.usefixtures("_job_types_registered")
def test_on_new_data_queues_only_the_on_new_data_subscriptions(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The mart build fires these; the clock fires the others."""

    event_driven = _subscription(
        db_session, recipients=[USER_1], cadence="on_new_data", hour=None, minute=None
    )
    clocked = _subscription(db_session, recipients=[USER_1])
    db_session.commit()

    queued = subscriptions.enqueue_on_new_data(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        as_of=AS_OF,
        completed_at=datetime(2026, 6, 30, 7, 31, 45, tzinfo=UTC),
    )
    assert len(queued) == 1
    assert queued[0].payload["subscription_id"] == str(event_driven.id)
    assert queued[0].payload["trigger"] == "new_data"
    assert queued[0].payload["as_of_date"] == AS_OF.isoformat()
    # The seconds are dropped: the run's identity is a minute, which is what the
    # delivery table's unique key is keyed on.
    assert queued[0].payload["scheduled_for"].endswith("07:31:00+00:00")
    assert str(clocked.id) not in {str(one.payload.get("subscription_id")) for one in queued}


# --- refusals before any recipient is considered --------------------------------


def test_a_run_with_no_relay_configured_claims_nothing(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment with no relay has not failed to send; it cannot send.

    Nothing is claimed, so the run is still deliverable once a relay exists —
    which is the opposite of recording a fleet of ``failed`` deliveries that can
    never be retried.
    """

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    get_settings.cache_clear()
    subscription = _subscription(db_session, recipients=[USER_1])
    db_session.commit()

    outcome = subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert outcome.reason == subscriptions.REASON_RELAY_NOT_CONFIGURED
    assert outcome.deliveries == ()
    assert _deliveries(db_session, subscription) == []


def test_a_bank_with_no_built_mart_delivers_nothing(
    db_session: Session, relay: type[_FakeSmtp]
) -> None:
    """A subscription cannot report a position the platform has not computed."""

    seed_book(db_session, live=False)
    db_session.commit()
    subscription = _subscription(db_session, recipients=[USER_1])
    db_session.commit()

    outcome = subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert outcome.reason == subscriptions.REASON_NO_BUILD
    assert relay.sent == []


def test_a_subscription_over_the_recipient_cap_is_refused_whole(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """An unbounded fan-out would occupy a worker past its own reclaim window,
    which is the ``etl_dedup`` failure: a live job declared dead and run twice."""

    reader = _user(db_session, "sub.capped@bank.test")
    _grant(db_session, reader.id)
    subscription = _subscription(db_session, recipients=[reader.id])
    subscription.recipient_user_ids = [
        str(uuid4()) for _ in range(subscriptions.MAX_RECIPIENTS + 1)
    ]
    db_session.commit()

    outcome = subscriptions.run_subscription(db_session, _run_job(db_session, subscription))
    assert outcome.reason == subscriptions.REASON_RECIPIENT_CAP_EXCEEDED
    assert relay.sent == []
    assert _deliveries(db_session, subscription) == []


def test_a_relay_outage_records_the_attempt_and_asks_for_a_retry(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """The attempt must survive the worker's rollback, and the rest stay unclaimed.

    At-most-once is the deliberate trade (D-180): the recipient whose send may or
    may not have gone out is NOT retried, while the recipients this attempt never
    reached are, because they hold no claim.
    """

    first = _user(db_session, "sub.outage.one@bank.test")
    _grant(db_session, first.id)
    second = _user(db_session, "sub.outage.two@bank.test")
    _grant(db_session, second.id)
    subscription = _subscription(db_session, recipients=[first.id, second.id])
    db_session.commit()
    relay.fail_on_send = smtplib.SMTPException("simulated outage")

    with pytest.raises(subscriptions.SubscriptionRelayError):
        subscriptions.run_subscription(db_session, _run_job(db_session, subscription))

    rows = _deliveries(db_session, subscription)
    assert len(rows) == 1, "the batch stopped rather than burning every recipient"
    assert rows[0].status == "failed"
    assert rows[0].reason == subscriptions.REASON_RELAY_UNAVAILABLE
    assert rows[0].error is not None and "SMTPException" in rows[0].error


# --- the handlers ---------------------------------------------------------------


def test_the_run_handler_is_inert_while_the_feature_is_off(db_session: Session, bank: Bank) -> None:
    subscription = _subscription(db_session, recipients=[USER_1])
    db_session.commit()
    job = _run_job(db_session, subscription)
    subscription_job.run_bi_subscription_run(db_session, job)
    assert job.progress == {
        "status": "skipped",
        "reason": subscription_job.SKIP_REASON_DISABLED,
    }


def test_the_run_handler_audits_every_delivery(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp]
) -> None:
    """One audit event per recipient: a refusal at delivery time is what a
    reviewer looks for, so it is recorded as loudly as a success."""

    sent_to = _user(db_session, "sub.audit.ok@bank.test")
    _grant(db_session, sent_to.id)
    denied_to = _user(db_session, "sub.audit.no@bank.test")
    subscription = _subscription(db_session, recipients=[sent_to.id, denied_to.id])
    db_session.commit()

    job = _run_job(db_session, subscription)
    subscription_job.run_bi_subscription_run(db_session, job)

    events = list(
        db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(subscription.id)))
    )
    by_type = {one.event_type: one for one in events}
    assert set(by_type) == {
        subscription_job.EVENT_DELIVERED,
        subscription_job.EVENT_DENIED,
    }
    assert by_type[subscription_job.EVENT_DELIVERED].actor_user_id == sent_to.id
    assert by_type[subscription_job.EVENT_DENIED].actor_user_id == denied_to.id
    assert job.progress["statuses"] == {"sent": 1, "denied": 1}


@pytest.mark.usefixtures("_job_types_registered")
def test_the_scan_handler_enqueues_the_hour_it_runs_in(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan is the BI-lane half of the spec's due-within-the-hour sweep."""

    captured: dict[str, Any] = {}

    def fake_enqueue(
        session: Session, *, organization_id: str, window_start: Any, window_end: Any
    ) -> list[Job]:
        captured.update(
            {
                "organization_id": organization_id,
                "window_start": window_start,
                "window_end": window_end,
            }
        )
        return []

    monkeypatch.setattr(subscriptions, "enqueue_due_runs", fake_enqueue)
    job = Job(
        organization_id=ORG_1,
        job_type=subscriptions.JOB_TYPE_SCAN,
        status="running",
        payload={"builder_version": 1},
    )
    db_session.add(job)
    db_session.flush()

    subscription_job.run_bi_subscription_scan(db_session, job)

    assert captured["organization_id"] == ORG_1
    assert captured["window_start"].minute == 0
    assert captured["window_end"] - captured["window_start"] == (
        datetime(2026, 1, 1, 1, tzinfo=UTC) - datetime(2026, 1, 1, 0, tzinfo=UTC)
    )
    assert job.progress["runs_enqueued"] == 0


# --- the hourly tick ------------------------------------------------------------


def test_the_subscriptions_flag_alone_keeps_the_tick_chain_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hazard this project has already paid for once.

    A scheduled branch whose flag is absent from ``any_scheduling_enabled`` never
    runs: the tick short-circuits as inert and does not even reschedule itself,
    so the feature is silently dead on a deployment that enabled only it.
    """

    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "1")
    get_settings.cache_clear()
    assert scheduler.any_scheduling_enabled(get_settings()) is True


@pytest.mark.usefixtures("_job_types_registered")
def test_the_tick_enqueues_one_subscription_scan_per_org_per_hour(
    db_session: Session, bank: Bank, relay: type[_FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick's whole part in this: one scan job, and no BI import to make it.

    The tick runs in the CORE lane and may import only the two thin BI seam
    modules, so it cannot read ``bi_subscriptions`` itself — it hands the hour to
    the ``bi`` lane and the scan does the reading (D-181).
    """

    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "1")
    get_settings.cache_clear()
    _subscription(db_session, recipients=[USER_1])
    db_session.commit()

    def tick() -> Job:
        job = Job(
            organization_id=ORG_1,
            job_type="scheduled_tick",
            status="running",
            payload={},
        )
        db_session.add(job)
        db_session.flush()
        scheduler.run_tick(db_session, job)
        return job

    first = tick()
    assert first.progress["bi_subscription_scan_enqueued"] is True
    second = tick()
    assert second.progress["bi_subscription_scan_enqueued"] is False, (
        "a second tick inside the same hour must not queue a second scan"
    )
    scans = list(db_session.scalars(select(Job).where(Job.job_type == subscriptions.JOB_TYPE_SCAN)))
    assert len(scans) == 1
    assert scans[0].organization_id == ORG_1
    assert scans[0].bank_id is None, "the scan names no bank: it sweeps the tenant"


def test_the_tick_says_nothing_about_subscriptions_while_they_are_off(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is absent, not ``False`` — the notification mirror's own idiom.

    A key that appeared unconditionally would change every existing assertion
    about this dict for a feature that is switched off.
    """

    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    monkeypatch.delenv("BI_SUBSCRIPTIONS_ENABLED", raising=False)
    get_settings.cache_clear()
    job = Job(organization_id=ORG_1, job_type="scheduled_tick", status="running", payload={})
    db_session.add(job)
    db_session.flush()

    scheduler.run_tick(db_session, job)
    assert "bi_subscription_scan_enqueued" not in job.progress
    assert (
        db_session.scalar(select(Job.id).where(Job.job_type == subscriptions.JOB_TYPE_SCAN)) is None
    )
