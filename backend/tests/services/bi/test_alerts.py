"""``app.services.bi.alerts``: the threshold, the authority, and the silence.

Every expectation is worked from the fixture book and the two registers, never
from a number invented here: the gross-loan total is
``test_mart_builder.FIXTURE_LOANS_RC`` (the sum of the fixture's converted loans)
and a governed line is whatever ``app.services.bi.limits`` — the one door to a
register — answers for the same measure and date. Asserting the alert AGREES with
that door is the whole of "a threshold is passed in, never inferred": there is no
arithmetic here that could produce a line of its own.

What is pinned, and why each one matters:

* **a governed alert carries no number of its own**, and an alert on a measure
  with no register code is UNEVALUATED rather than judged against nothing;
* **the owner is re-authorized at evaluation time.** An alert created months ago
  by someone whose grant has since been revoked must yield no figure — the
  interactive page being closed to them is not enough;
* **who is TOLD is each recipient's own decision.** The distribution list is not
  a grant;
* **a book that stays in breach notifies once**, and a transient failure between
  two evaluations does not manufacture a second notification;
* **re-evaluating the same mart build does nothing at all.**
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
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
from app.db.base import utc_now
from app.domain.bi.catalogue import catalogue
from app.jobs import bi_alerts as alert_job
from app.models import Bank, Job, Notification, User
from app.models.bi import BiMartBuild
from app.models.bi_notifications import BiAlert, BiAlertEvent
from app.services import authorization, job_queue
from app.services.bi import alerts, limits, provenance
from app.services.bi.errors import BiQueryTimeout
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_mart_builder import AS_OF, FIXTURE_LOANS_RC, build, seed_book

LOANS = "loans.balance_rc"
#: A liquidity ratio the fixture's board register holds a limit for, and which the
#: live plane computes — the pair a governed alert needs.
LCR_LIVE = "engine.lcr_pct.crd.live"
#: A measure the catalogue declares NO threshold code for, so a governed alert on
#: it can only be unevaluated.
NO_REGISTER_CODE = LOANS


@pytest.fixture
def bank(db_session: Session) -> Bank:
    """The canonical fixture bank, with its live plane and its marts built."""

    institution = seed_book(db_session)
    build(db_session)
    db_session.commit()
    return institution


@pytest.fixture
def fingerprint(db_session: Session, bank: Bank) -> str:
    value = provenance.build_fingerprint(
        db_session, organization_id=ORG_1, bank_id=bank.id, window=(AS_OF, AS_OF)
    )
    assert value is not None, "the fixture book must have a built mart to alert on"
    return value


@pytest.fixture
def _job_types_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit this track's job types to the queue's allow-list for the test.

    ``job_queue.JOB_TYPES`` / ``JOB_LANES`` are owned by another track and the
    registration is a one-line change there (named in the task report). Patching
    them here keeps the enqueue seam testable without editing a file this track
    does not own; when the registration lands, this fixture becomes a no-op
    rather than a lie, because it only ADDS missing names.
    """

    wanted = (alerts.JOB_TYPE, "bi_subscription_scan", "bi_subscription_run")
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
    """One indivisible organization-wide viewer sentence for ``user_id``."""

    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise BI alert authorization.",
        commit=False,
    )
    db.flush()


def _revoke_everything(db: Session, user_id: UUID) -> None:
    """Withdraw every sentence the principal holds, the way a revocation does."""

    for binding in db.scalars(
        select(authorization.AuthorizationBinding).where(
            authorization.AuthorizationBinding.organization_id == ORG_1,
            authorization.AuthorizationBinding.principal_user_id == user_id,
        )
    ):
        db.delete(binding)
    db.flush()


def _alert(db: Session, **overrides: Any) -> BiAlert:
    values: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "name": f"Alert {uuid4().hex[:8]}",
        "measure_id": LOANS,
        "filters": [],
        "direction": "below",
        "threshold_basis": "stated",
        "threshold": FIXTURE_LOANS_RC + Decimal("1"),
        "owner_user_id": USER_1,
        "notify_user_ids": [],
        "is_active": True,
    }
    values.update(overrides)
    row = BiAlert(**values)
    db.add(row)
    db.flush()
    return row


def _prior_event(db: Session, alert_row: BiAlert, state: str, *, days_ago: int = 1) -> BiAlertEvent:
    """A verdict from an earlier build, so a transition has something to move from."""

    row = BiAlertEvent(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        alert_id=alert_row.id,
        as_of_date=AS_OF - timedelta(days=days_ago),
        build_fingerprint="0" * 64,
        state=state,
        observed_value=None if state == "not_evaluated" else Decimal("1"),
        threshold_value=None if state == "not_evaluated" else Decimal("2"),
        threshold_basis="stated",
        reason="no_figure" if state == "not_evaluated" else None,
        evaluated_at=utc_now() - timedelta(days=days_ago),
        builder_version=1,
    )
    db.add(row)
    db.flush()
    return row


def _evaluate(db: Session) -> alerts.BankAlertEvaluation:
    return alerts.evaluate_bank(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF)


def _events(db: Session, alert_row: BiAlert) -> list[BiAlertEvent]:
    return list(
        db.scalars(
            select(BiAlertEvent)
            .where(BiAlertEvent.alert_id == alert_row.id)
            .order_by(BiAlertEvent.evaluated_at)
        )
    )


# --- the figure and the line ---------------------------------------------------


def test_a_figure_within_a_stated_threshold_records_nothing(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """Silence is the correct output, and it leaves no row to notify from."""

    row = _alert(db_session, direction="below", threshold=Decimal("1"))
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "unchanged"
    assert outcome.event is None
    assert outcome.observed_value == FIXTURE_LOANS_RC
    assert _events(db_session, row) == []


def test_a_figure_past_a_stated_threshold_breaches_with_its_evidence(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """The bank's OWN number is the line, and the row carries both sides of it."""

    line = FIXTURE_LOANS_RC + Decimal("1")
    row = _alert(db_session, direction="below", threshold=line)
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)

    assert outcome.result == "breached"
    assert outcome.observed_value == FIXTURE_LOANS_RC
    assert outcome.threshold_value == line
    event = outcome.event
    assert event is not None
    assert event.state == "breached"
    assert event.threshold_basis == "stated"
    assert event.limit_source is None
    assert event.build_fingerprint == fingerprint
    assert LOANS in event.member_ids
    assert event.reason is None


def test_the_direction_decides_which_side_is_the_breach(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """``above`` and ``below`` are the bank's statement, not the measure's own
    favourable direction: a bank may want to hear that a figure has RISEN."""

    high = _alert(db_session, direction="above", threshold=Decimal("1"))
    low = _alert(db_session, direction="below", threshold=Decimal("1"))
    evaluation = _evaluate(db_session)
    results = {one.alert_id: one.result for one in evaluation.outcomes}
    assert results[high.id] == "breached"
    assert results[low.id] == "unchanged"


def test_a_governed_alert_takes_the_register_s_line_and_says_where_it_came_from(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """The line must be the limit resolver's answer, to the digit.

    Computed HERE through the same door the service uses, so a change in either
    register moves the expectation instead of leaving a literal asserting
    yesterday's limit — and so that the alert cannot be seen to agree with a
    number this file made up.
    """

    measure = catalogue().member(LCR_LIVE)
    expected = limits.measure_limit(db_session, bank, measure, as_of=AS_OF)  # type: ignore[arg-type]
    assert limits.is_governed(expected), (
        "the fixture's board register must hold this measure's limit for the test to "
        f"mean anything: {expected}"
    )

    # Both sides of the same line, because a verdict is recorded only on a
    # transition: the ``below`` alert shows the register's number governing an
    # UNBREACHED figure, and the ``above`` one shows it reaching the event row
    # with its attribution. The direction is the bank's own statement either way.
    within = _alert(
        db_session,
        measure_id=LCR_LIVE,
        threshold_basis="governed_limit",
        threshold=None,
        direction="below",
    )
    breaching = _alert(
        db_session,
        measure_id=LCR_LIVE,
        threshold_basis="governed_limit",
        threshold=None,
        direction="above",
    )
    evaluation = _evaluate(db_session)
    outcomes = {one.alert_id: one for one in evaluation.outcomes}

    quiet = outcomes[within.id]
    assert quiet.result == "unchanged", quiet.reason
    assert quiet.threshold_value == expected.value
    assert quiet.observed_value is not None

    loud = outcomes[breaching.id]
    event = loud.event
    assert event is not None, loud.reason
    assert event.threshold_basis == "governed_limit"
    assert event.threshold_value == expected.value
    assert event.limit_source == expected.source
    assert event.observed_value is not None


def test_a_governed_alert_on_a_measure_with_no_register_code_is_unevaluated(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """Never zero, never "within limit": an absent line is a stated absence.

    This is the case a naive implementation turns into a breach of everything, by
    defaulting the missing threshold to nothing at all.
    """

    row = _alert(
        db_session,
        measure_id=NO_REGISTER_CODE,
        threshold_basis="governed_limit",
        threshold=None,
    )
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "not_evaluated"
    assert outcome.reason == alerts.REASON_NO_GOVERNED_LIMIT
    event = outcome.event
    assert event is not None
    assert event.observed_value is None
    assert event.threshold_value is None
    assert not outcome.notifies


def test_a_refused_query_is_not_the_same_fact_as_a_missing_figure(
    db_session: Session, bank: Bank, fingerprint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "The platform would not answer" and "there is no figure" are two facts.

    Both produce no verdict, and both must stay distinguishable in the event row:
    a timeout is an engineering problem and an absent figure is a data one, and a
    single reason would send whoever reads the alert history to the wrong place.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise BiQueryTimeout(10)

    monkeypatch.setattr(alerts, "execute", refuse)
    row = _alert(db_session, direction="above", threshold=Decimal("1"))
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "not_evaluated"
    assert outcome.reason == alerts.REASON_QUERY_REFUSED


# --- authority ------------------------------------------------------------------


def test_an_owner_whose_grant_was_revoked_produces_no_figure(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """The alert outlives the grant, so the grant is checked at evaluation time."""

    owner = _user(db_session, "alert.owner@bank.test")
    _grant(db_session, owner.id)
    row = _alert(db_session, owner_user_id=owner.id)
    _revoke_everything(db_session, owner.id)

    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "not_evaluated"
    assert outcome.reason == alerts.REASON_AUTHORIZATION_REVOKED
    assert outcome.event is not None
    assert outcome.event.observed_value is None


def test_a_deactivated_owner_produces_no_figure(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    owner = _user(db_session, "alert.gone@bank.test")
    _grant(db_session, owner.id)
    row = _alert(db_session, owner_user_id=owner.id)
    owner.is_active = False
    db_session.flush()

    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.reason == alerts.REASON_AUTHORIZATION_REVOKED


def test_a_recipient_is_told_only_if_their_own_authority_admits_the_measure(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """The distribution list is not a grant.

    The owner here can see everything. One recipient holds a sentence over a
    DIFFERENT module, one holds nothing, and one holds the whole institution —
    and the event records the split, so a withheld notification is a decision on
    the record rather than an omission.
    """

    admitted = _user(db_session, "alert.admitted@bank.test")
    _grant(db_session, admitted.id)
    wrong_module = _user(db_session, "alert.wrongmodule@bank.test")
    _grant(db_session, wrong_module.id, module=ModuleScope.LIQUIDITY)
    ungranted = _user(db_session, "alert.none@bank.test")

    row = _alert(
        db_session,
        direction="above",
        threshold=Decimal("1"),
        notify_user_ids=[str(admitted.id), str(wrong_module.id), str(ungranted.id)],
    )
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)

    assert outcome.result == "breached"
    assert outcome.admitted_user_ids == (admitted.id,)
    assert set(outcome.withheld_user_ids) == {wrong_module.id, ungranted.id}
    event = outcome.event
    assert event is not None
    assert set(event.withheld_user_ids) == {str(wrong_module.id), str(ungranted.id)}


# --- transitions and idempotence ------------------------------------------------


def test_the_same_build_is_never_judged_twice(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """One evaluation per ``(alert, as_of, fingerprint)``: the notification cannot
    be sent twice by running the job twice."""

    row = _alert(db_session, direction="above", threshold=Decimal("1"))
    first = _evaluate(db_session)
    assert next(one for one in first.outcomes if one.alert_id == row.id).result == "breached"

    second = _evaluate(db_session)
    outcome = next(one for one in second.outcomes if one.alert_id == row.id)
    assert outcome.result == "already_recorded"
    assert outcome.event is None
    assert len(_events(db_session, row)) == 1


def test_a_book_that_stays_in_breach_notifies_once(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """A month in breach is one notification, not thirty."""

    row = _alert(db_session, direction="above", threshold=Decimal("1"))
    _prior_event(db_session, row, "breached")
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "unchanged"
    assert not outcome.notifies
    assert len(_events(db_session, row)) == 1  # only the prior one


def test_a_book_that_comes_back_within_the_threshold_clears(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    row = _alert(db_session, direction="below", threshold=Decimal("1"))
    _prior_event(db_session, row, "breached")
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "cleared"
    assert outcome.notification is not None
    assert outcome.notification.type == alerts.NOTIFICATION_TYPE_CLEARED


def test_a_failed_evaluation_does_not_reset_the_state(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """A week of authority failures must not read as a fresh breach afterwards.

    Prior state: breached, then an ``not_evaluated`` row. The figure is STILL
    past the line, so the correct answer is silence — if the unevaluated row had
    reset the state, this would notify a second time.
    """

    row = _alert(db_session, direction="above", threshold=Decimal("1"))
    _prior_event(db_session, row, "breached", days_ago=3)
    _prior_event(db_session, row, "not_evaluated", days_ago=2)
    evaluation = _evaluate(db_session)
    outcome = next(one for one in evaluation.outcomes if one.alert_id == row.id)
    assert outcome.result == "unchanged"


def test_an_inactive_alert_is_not_evaluated_at_all(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    row = _alert(db_session, direction="above", threshold=Decimal("1"), is_active=False)
    evaluation = _evaluate(db_session)
    assert all(one.alert_id != row.id for one in evaluation.outcomes)
    assert _events(db_session, row) == []


def test_a_date_with_no_mart_build_evaluates_nothing(db_session: Session, bank: Bank) -> None:
    """A threshold is about the bank's position, not about the absence of one."""

    row = _alert(db_session, direction="above", threshold=Decimal("1"))
    evaluation = alerts.evaluate_bank(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        as_of=date(2001, 1, 31),
    )
    assert evaluation.build_fingerprint is None
    assert evaluation.outcomes == ()
    assert _events(db_session, row) == []


# --- the enqueue seam -----------------------------------------------------------


def test_nothing_is_enqueued_while_the_feature_is_off(db_session: Session, bank: Bank) -> None:
    """Default off, and inert when off: the session is not touched at all.

    A job queued without ``risk-worker-bi`` deployed would sit ``queued`` for
    ever — the ``notification_email_mirror`` orphan this codebase already paid
    for (D-008).
    """

    assert (
        alerts.enqueue_evaluation(
            db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
        )
        is None
    )


@pytest.mark.usefixtures("_job_types_registered")
def test_one_evaluation_is_enqueued_per_bank_and_date(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_ALERTS_ENABLED", "1")
    get_settings.cache_clear()

    first = alerts.enqueue_evaluation(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
    )
    second = alerts.enqueue_evaluation(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
    )
    assert first is not None
    assert second is not None and second.id == first.id
    assert first.coalesce_key == alerts.coalesce_key_for(SAMPLE_BANK_ID, AS_OF)
    queued = list(db_session.scalars(select(Job).where(Job.job_type == alerts.JOB_TYPE)))
    assert len(queued) == 1


# --- the handler ----------------------------------------------------------------


def _job(db: Session, **overrides: Any) -> Job:
    values: dict[str, Any] = {
        "organization_id": ORG_1,
        "job_type": alerts.JOB_TYPE,
        "status": "running",
        "bank_id": SAMPLE_BANK_ID,
        "payload": {
            "organization_id": ORG_1,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": AS_OF.isoformat(),
            "builder_version": 1,
        },
    }
    values.update(overrides)
    row = Job(**values)
    db.add(row)
    db.flush()
    return row


def test_the_handler_is_inert_while_the_feature_is_off(db_session: Session, bank: Bank) -> None:
    """A job queued before the switch was pulled must not notify anybody."""

    job = _job(db_session)
    alert_job.run_bi_alert_evaluate(db_session, job)
    assert job.progress == {
        "status": "skipped",
        "reason": alert_job.SKIP_REASON_DISABLED,
    }


def test_the_handler_notifies_only_the_admitted_recipients(
    db_session: Session, bank: Bank, fingerprint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one write the service may not make, made from the service's decision.

    The in-app row is what the SMTP mirror later emails, so this is also where
    "a figure never reaches a recipient who could not have queried it" becomes a
    delivered artifact rather than an intention.
    """

    monkeypatch.setenv("BI_ALERTS_ENABLED", "1")
    get_settings.cache_clear()

    admitted = _user(db_session, "handler.admitted@bank.test")
    _grant(db_session, admitted.id)
    withheld = _user(db_session, "handler.withheld@bank.test")
    row = _alert(
        db_session,
        direction="above",
        threshold=Decimal("1"),
        notify_user_ids=[str(admitted.id), str(withheld.id)],
    )
    db_session.commit()

    job = _job(db_session)
    alert_job.run_bi_alert_evaluate(db_session, job)

    assert job.progress["notifications_emitted"] == 1
    emitted = list(
        db_session.scalars(
            select(Notification).where(Notification.type == alerts.NOTIFICATION_TYPE_BREACHED)
        )
    )
    assert [one.recipient_user_id for one in emitted] == [admitted.id]
    assert emitted[0].entity_id == str(row.id)
    # The figure is in the body the recipient reads, and only theirs exists.
    assert str(FIXTURE_LOANS_RC.normalize()) in emitted[0].body or "84850000" in emitted[0].body

    event = _events(db_session, row)[0]
    assert event.notified_user_ids == [str(admitted.id)]
    assert event.withheld_user_ids == [str(withheld.id)]


def test_the_handler_emits_nothing_for_an_unevaluated_alert(
    db_session: Session, bank: Bank, fingerprint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An internal condition is recorded, not announced: telling a bank manager
    that a grant is missing would train them to ignore alerts."""

    monkeypatch.setenv("BI_ALERTS_ENABLED", "1")
    get_settings.cache_clear()

    recipient = _user(db_session, "handler.quiet@bank.test")
    _grant(db_session, recipient.id)
    _alert(
        db_session,
        measure_id=NO_REGISTER_CODE,
        threshold_basis="governed_limit",
        threshold=None,
        notify_user_ids=[str(recipient.id)],
    )
    db_session.commit()

    job = _job(db_session)
    alert_job.run_bi_alert_evaluate(db_session, job)
    assert job.progress["notifications_emitted"] == 0
    assert (
        db_session.scalar(
            select(Notification).where(
                Notification.type.in_(
                    (alerts.NOTIFICATION_TYPE_BREACHED, alerts.NOTIFICATION_TYPE_CLEARED)
                )
            )
        )
        is None
    )


# --- a failed rebuild is not a state to judge ----------------------------------


def test_a_failed_rebuild_evaluates_no_alert_against_the_rows_it_rolled_back_to(
    db_session: Session, bank: Bank, fingerprint: str
) -> None:
    """Audit A360 H2's second-order effect, caught by T1b rather than by the audit.

    The builder rolls a failed rebuild back to the PREVIOUS rows, so figures are
    still there to be read. Before H2, ``build_fingerprint`` answered ``None`` for
    that state and ``evaluate_bank``'s ``fingerprint is None`` test skipped the
    date by accident. H2 made the fingerprint a digest — correct, because a stale
    serve must be attributable — and that silently turned the accident off: every
    alert would then be judged against a book the bank has already moved past, and
    recipients notified about it.

    A false breach is worse than silence for a threshold alert: it is acted on.
    So the date is skipped explicitly now, on `provenance.stale_dates`, which is
    the same fact the trust badge greys itself on.
    """

    row = _alert(db_session, direction="below", threshold=Decimal("1"))
    # It WOULD evaluate: same alert, same rows, before the build state changes.
    assert _evaluate(db_session).outcomes, "the fixture must have something to judge"

    db_session.execute(
        update(BiMartBuild)
        .where(
            BiMartBuild.organization_id == ORG_1,
            BiMartBuild.bank_id == bank.id,
            BiMartBuild.as_of_date == AS_OF,
        )
        .values(status="failed")
    )
    db_session.flush()

    evaluation = _evaluate(db_session)
    assert evaluation.outcomes == (), (
        "an alert was judged against rows whose latest build failed — the figure "
        "describes a book the bank has moved past"
    )
    assert _events(db_session, row) == [], "and nobody was notified about it"
