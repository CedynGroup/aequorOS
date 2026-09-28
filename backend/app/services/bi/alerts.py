"""Threshold alerts: evaluated after a mart build, judged against a stated line.

``docs/bi.md`` §Phase 3: *``bi_alerts``, evaluated after ``bi_mart_refresh``,
delivered via notifications and email.* Requirement P3-A1.

Five rules, and each one is the answer to a way this feature goes wrong:

1. **The threshold is PASSED IN, never inferred** (D-173). An alert is either
   ``stated`` — the bank's own number, on the row — or ``governed_limit``, in
   which case the number comes from ``app/services/bi/limits.py``, the one door
   to a register, which REFUSES rather than converting when a register value and
   a measure are in different units. Nothing here reads a threshold off data,
   and there is no "n standard deviations" or "x % worse than last month" mode:
   a line derived from the series it judges is not a limit.
2. **Evaluation is an authorized read, under a NAMED principal** (D-174). The
   alert's owner is re-authorized through the same ``authorize_query`` the
   ``/bi/query`` route makes, at their authority as it stands NOW — minutes or
   months pass between creating an alert and evaluating it, and a revoked grant
   must stop the figure, not merely the interactive page. An unauthorized owner
   yields ``not_evaluated``, never a silent skip and never a figure.
3. **Who is TOLD is decided per recipient** (D-175). The alert's distribution
   list is not a grant: each recipient's own authority is evaluated before the
   figure reaches them, and the event row records both who was told and who was
   withheld. This is the same rule as a subscription delivery, for the same
   reason — the alert is created by one person and read by others.
4. **A row is written on a TRANSITION only** (D-176). A book that stays in
   breach for a month is one notification, not thirty. The state a transition is
   measured against is the last ``breached``/``cleared`` event; a
   ``not_evaluated`` row never resets it, so a transient failure cannot
   manufacture a second breach notification.
5. **Re-evaluating the same data does nothing** (D-177). ``(alert, as_of,
   build_fingerprint)`` is unique, and the fingerprint identifies the mart state
   the figure came from, so a reclaimed job, a replayed queue row and a second
   build of the same unchanged slice all converge on the row that already
   exists.

**What this module does NOT do.** It writes no ``notifications`` row and no
``audit_events`` row: ``app/services/bi`` writes ``bi_*`` tables and nothing else
(the plane guard), so the in-app notification is emitted by
``app/jobs/bi_alerts.py`` from the outcome returned here — exactly as
``app/jobs/bi_export.py`` owns the audit event for the export service. The
notification COPY is composed here, because it states figures and the rules for
stating a figure honestly live in the BI plane.

**The data scope (Phase 4).** An alert is judged over its OWNER's slice: their
declared scope is resolved to branch codes and passed to :func:`_observed_value`
as the same unremovable filter the read routes inject, so a branch manager's
alert on gross loans is about their branches and nobody else's.

Two consequences worth stating, because both look like bugs and are not.

*The threshold does not mean the same thing over a slice, and that is the bank's
call to make.* A ``stated`` threshold is a number the owner typed while looking
at their own figures, so judging it against those same figures is exactly right.
A ``governed_limit`` is a REGISTER value — a regulatory or board limit stated for
the institution — and a branch's share of the book will sit under it almost by
construction, so such an alert is close to unfireable for a scoped owner. It is
still evaluated rather than refused: the platform's job here is to compare the
number the bank named against the figure the owner may see, not to decide that
their grant makes their own alert pointless. What it must never do is judge an
institution limit against a branch figure while LABELLING it institution-wide,
and it cannot: a ``grain="institution"`` measure is refused to a scoped principal
by ``authorize_query`` before any threshold is looked up.

*A recipient is admitted only when their slice is the owner's slice.* The event
row holds ONE observed figure (and is unique on ``(alert, as_of,
build_fingerprint)``), so the notification carries the owner's number. Sending it
to someone whose grant covers a different slice would state a figure they may not
see — or, if their slice is wider, a figure that is not theirs under a name that
says it is. ``_recipient_decisions`` therefore compares the recipient's declared
scope with the owner's and withholds on any difference, which reduces to today's
behaviour when both are institution-wide. Per-recipient VERDICTS would need a
per-recipient event row; that is named in the task report, not faked here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Permission
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi.catalogue import Catalogue, MeasureDef, catalogue
from app.models import Bank, Job, User
from app.models.bi_notifications import BiAlert, BiAlertEvent
from app.schemas.bi import BiFilter, BiQuery, BiTime
from app.services import job_queue
from app.services.bi import data_scope, limits, provenance
from app.services.bi.authorization import BiDataScope, authorize_query
from app.services.bi.compiler import compile_query
from app.services.bi.errors import BiQueryError
from app.services.bi.execution import execute
from app.services.bi.insights.statements import render_value
from app.services.bi.versions import BUILDER_VERSION

#: The job type this seam enqueues. A literal here, not an import from
#: ``app.jobs``, for the reason ``bi/enqueue.py`` states: the enqueue side never
#: imports the handler side. ``job_queue.enqueue`` validates it.
JOB_TYPE = "bi_alert_evaluate"

#: ``jobs.entity_type``: the institution, so the operator board finds a tenant's
#: alert evaluations the way it finds their mart builds.
ENTITY_TYPE = "bank"

#: The telemetry surface ``authorize_query`` reports under (``bi_alert``). It is
#: NOT a ``bi_query_log`` surface: that table's vocabulary is closed
#: (``models.bi.QUERY_LOG_SURFACES``) and has no ``alert`` value, so an
#: evaluation's audit trail is the ``bi_alert_events`` row, which is strictly
#: richer — it carries the member ids, the decision, the fingerprint AND the
#: verdict. Adding the vocabulary is named in the task report.
AUTHORIZATION_SURFACE = "alert"

#: How an evaluation ended. The first three are ``bi_alert_events.state`` values
#: and mean a row was written; the last two mean no row and no notification.
AlertResult = Literal["breached", "cleared", "not_evaluated", "unchanged", "already_recorded"]

#: Notification type strings (``notifications.type``), emitted by the job.
NOTIFICATION_TYPE_BREACHED = "bi.alert.breached"
NOTIFICATION_TYPE_CLEARED = "bi.alert.cleared"

#: How a breach and a clearance read. ``warning`` rather than ``critical``: a
#: threshold the bank set for itself is not the platform declaring an incident,
#: and reserving ``critical`` for the regulatory deadline scan keeps that
#: severity meaningful.
SEVERITY_BREACHED = "warning"
SEVERITY_CLEARED = "info"

#: Why an alert produced no verdict — ``models.bi_notifications``' own
#: vocabulary, named here so the reasons this module can produce are visible in
#: one place.
REASON_AUTHORIZATION_REVOKED = "authorization_revoked"
REASON_NO_GOVERNED_LIMIT = "no_governed_limit"
REASON_NO_FIGURE = "no_figure"
REASON_UNKNOWN_MEMBER = "unknown_member"
REASON_QUERY_REFUSED = "query_refused"
#: Kept in the vocabulary after Phase 4 made a scoped alert evaluable, because it
#: still has one live cause — a grant covering more branches than one ``IN`` list
#: may carry (``data_scope.DataScopeUnservable``) — and because historical rows
#: written under the Phase 1 refusal must keep meaning what they said.
REASON_DATA_SCOPE_UNSUPPORTED = "data_scope_unsupported"


# --- the enqueue seam -----------------------------------------------------------------------


def coalesce_key_for(bank_id: str, as_of: date) -> str:
    """``bi-alerts:{bank}:{as_of}`` — one queued evaluation per bank and date."""

    return f"bi-alerts:{bank_id}:{as_of.isoformat()}"


def enqueue_evaluation(
    db: Session, *, organization_id: str, bank_id: str, as_of: date
) -> Job | None:
    """Queue one alert evaluation for ``(bank, as_of)``; ``None`` when disabled.

    Called from the ``bi_mart_refresh`` handler after a SUCCEEDED build, which is
    the only place that knows a slice's figures have actually moved: the builder
    skips an unchanged fingerprint, so a rebuild that changed nothing enqueues
    nothing here either. Flushes, never commits — the handler's transaction owns
    the commit, so the evaluation lands with the build that asked for it.

    ``BI_ALERTS_ENABLED`` is checked FIRST and the session is not touched when it
    is off, so a deployment without the ``bi`` worker lane cannot orphan a job it
    could never claim (D-008).
    """

    if not get_settings().bi.alerts_enabled:
        return None
    return job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        bank_id=bank_id,
        payload={
            "organization_id": organization_id,
            "bank_id": bank_id,
            "as_of_date": as_of.isoformat(),
            "builder_version": BUILDER_VERSION,
        },
        coalesce_key=coalesce_key_for(bank_id, as_of),
        entity_type=ENTITY_TYPE,
        entity_id=bank_id,
    )


# --- what an evaluation produces ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NotificationCopy:
    """The in-app notification for one transition, as the job will emit it.

    Composed here rather than in the handler because it states figures, and the
    rules for stating a figure — the value in its own recorded precision, in the
    unit the measure declares, never rounded for readability — are BI's
    (``insights/statements.render_value``).
    """

    type: str
    severity: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    """One alert's evaluation: the verdict, the row and who may be told."""

    alert_id: UUID
    alert_name: str
    measure_id: str
    result: AlertResult
    #: The row written, or ``None`` for ``unchanged`` / ``already_recorded``.
    event: BiAlertEvent | None
    observed_value: Decimal | None
    threshold_value: Decimal | None
    reason: str | None
    #: Recipients whose OWN authority admits the measure, and those it does not.
    admitted_user_ids: tuple[UUID, ...]
    withheld_user_ids: tuple[UUID, ...]
    #: Present only for a transition, and only then is anyone notified.
    notification: NotificationCopy | None

    @property
    def notifies(self) -> bool:
        return self.notification is not None and bool(self.admitted_user_ids)


@dataclass(frozen=True, slots=True)
class BankAlertEvaluation:
    """Every active alert of one bank, evaluated against one mart build."""

    organization_id: str
    bank_id: str
    as_of: date
    #: ``None`` when the bank has no successful build for the date at all, in
    #: which case nothing was evaluated: there is no state to judge.
    build_fingerprint: str | None
    outcomes: tuple[AlertOutcome, ...]

    @property
    def transitions(self) -> tuple[AlertOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.notifies)

    def progress(self) -> dict[str, Any]:
        """A queue-readable summary. Names alerts by id, never a figure."""

        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.result] = counts.get(outcome.result, 0) + 1
        return {
            "as_of_date": self.as_of.isoformat(),
            "build_fingerprint": self.build_fingerprint,
            "alerts_evaluated": len(self.outcomes),
            "results": counts,
            "notified": [str(outcome.alert_id) for outcome in self.transitions],
        }


# --- evaluation -----------------------------------------------------------------------------


def active_alerts(db: Session, *, organization_id: str, bank_id: str) -> list[BiAlert]:
    """This bank's active alerts, in a stable order."""

    return list(
        db.scalars(
            select(BiAlert)
            .where(
                BiAlert.organization_id == organization_id,
                BiAlert.bank_id == bank_id,
                BiAlert.is_active.is_(True),
            )
            .order_by(BiAlert.created_at, BiAlert.id)
        )
    )


def evaluate_bank(
    db: Session, *, organization_id: str, bank_id: str, as_of: date
) -> BankAlertEvaluation:
    """Evaluate every active alert of one bank against the ``as_of`` mart build.

    Writes ``bi_alert_events`` (and nothing else) and flushes; the caller commits
    after emitting the notifications the outcomes name, so an event that claims
    somebody was told and the row that told them land together.
    """

    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        raise AlertEvaluationError(f"Bank {bank_id} not found for organization.")

    fingerprint = provenance.build_fingerprint(
        db, organization_id=organization_id, bank_id=bank_id, window=(as_of, as_of)
    )
    alerts = active_alerts(db, organization_id=organization_id, bank_id=bank_id)
    if fingerprint is None or not alerts:
        # Nothing built for the date means there is no state to judge — an alert
        # is about the bank's position, not about the absence of one.
        return BankAlertEvaluation(
            organization_id=organization_id,
            bank_id=bank_id,
            as_of=as_of,
            build_fingerprint=fingerprint,
            outcomes=(),
        )

    cat = catalogue()
    measures = _measures_for(cat, alerts)
    resolver = limits.LimitResolver.load(db, bank, as_of_dates=[as_of])
    outcomes = tuple(
        _evaluate_one(
            db,
            bank=bank,
            cat=cat,
            alert=alert,
            measure=measures.get(alert.measure_id),
            as_of=as_of,
            fingerprint=fingerprint,
            resolver=resolver,
        )
        for alert in alerts
    )
    db.flush()
    return BankAlertEvaluation(
        organization_id=organization_id,
        bank_id=bank_id,
        as_of=as_of,
        build_fingerprint=fingerprint,
        outcomes=outcomes,
    )


class AlertEvaluationError(Exception):
    """An evaluation that cannot run: a bank the tenant does not have."""


def _measures_for(cat: Catalogue, alerts: list[BiAlert]) -> dict[str, MeasureDef]:
    """The catalogue measure behind each alert; missing ids are simply absent."""

    wanted = {alert.measure_id for alert in alerts}
    found: dict[str, MeasureDef] = {}
    for measure in cat.measures():
        if measure.id in wanted:
            found[measure.id] = measure
    return found


def _query_for(alert: BiAlert, as_of: date) -> BiQuery:
    """The single-figure query this alert is about, for ``as_of``.

    No dimensions: an alert is a threshold on one figure for the institution.
    The stored filters ride along and are walked by ``authorize_query`` exactly
    as a projection is, so "exposure where counterparty = X" is authorized as a
    statement about X.
    """

    return BiQuery(
        measures=[alert.measure_id],
        filters=[BiFilter.model_validate(payload) for payload in alert.filters],
        time=BiTime(as_of=as_of),
    )


def _evaluate_one(  # noqa: PLR0913, PLR0911 - one alert, and every way its verdict can end
    db: Session,
    *,
    bank: Bank,
    cat: Catalogue,
    alert: BiAlert,
    measure: MeasureDef | None,
    as_of: date,
    fingerprint: str,
    resolver: limits.LimitResolver,
) -> AlertOutcome:
    """Judge one alert, write its event when the state changed, and say who may hear."""

    if _already_recorded(db, alert, as_of, fingerprint):
        return _no_row(alert, "already_recorded")
    if measure is None:
        return _record_not_evaluated(db, alert, as_of, fingerprint, REASON_UNKNOWN_MEMBER)

    owner = db.scalar(
        select(User).where(
            User.id == alert.owner_user_id, User.organization_id == alert.organization_id
        )
    )
    if owner is None or not owner.is_active:
        return _record_not_evaluated(db, alert, as_of, fingerprint, REASON_AUTHORIZATION_REVOKED)

    query = _query_for(alert, as_of)
    try:
        decision = authorize_query(
            db,
            _context_for(owner),
            bank,
            cat,
            query,
            permission=Permission.VIEW,
            surface=AUTHORIZATION_SURFACE,
        )
    except BiQueryError:
        return _record_not_evaluated(db, alert, as_of, fingerprint, REASON_UNKNOWN_MEMBER)
    if not decision.allowed:
        return _record_not_evaluated(db, alert, as_of, fingerprint, REASON_AUTHORIZATION_REVOKED)
    # The owner's slice, resolved against this institution's branches. An
    # institution-grain measure never reaches here under a scoped grant —
    # ``authorize_query`` denies it — so what this filter narrows is a portfolio
    # figure, which is a figure a branch HAS.
    try:
        scope = data_scope.resolve(
            db,
            decision.data_scope,
            organization_id=alert.organization_id,
            bank_id=alert.bank_id,
        )
        scope_filters = scope.filters
    except BiQueryError:
        return _record_not_evaluated(db, alert, as_of, fingerprint, REASON_DATA_SCOPE_UNSUPPORTED)

    threshold, limit_source, absence = _threshold_for(alert, measure, resolver, as_of)
    if threshold is None:
        return _record_not_evaluated(
            db, alert, as_of, fingerprint, absence or REASON_NO_GOVERNED_LIMIT
        )

    observed, refusal = _observed_value(
        db, cat, query, bank=bank, measure=measure, injected_filters=scope_filters
    )
    if observed is None:
        return _record_not_evaluated(db, alert, as_of, fingerprint, refusal or REASON_NO_FIGURE)

    breached = observed > threshold if alert.direction == "above" else observed < threshold
    previous = _previous_state(db, alert)
    if breached and previous != "breached":
        state = "breached"
    elif not breached and previous == "breached":
        state = "cleared"
    else:
        return _no_row(alert, "unchanged", observed=observed, threshold=threshold)

    admitted, withheld = _recipient_decisions(
        db, bank, cat, query, alert, owner_scope=decision.data_scope
    )
    event = BiAlertEvent(
        organization_id=alert.organization_id,
        bank_id=alert.bank_id,
        alert_id=alert.id,
        as_of_date=as_of,
        build_fingerprint=fingerprint,
        state=state,
        observed_value=observed,
        threshold_value=threshold,
        threshold_basis=alert.threshold_basis,
        limit_source=limit_source,
        reason=None,
        # The scope's own members ride along, exactly as ``bi_query_log`` records
        # them, so the event says the figure was narrowed — while carrying no
        # branch CODE, which is a filter value and is never stored.
        member_ids=list(
            dict.fromkeys(
                (*decision.member_ids, *(predicate.member for predicate in scope_filters))
            )
        ),
        notified_user_ids=[],
        withheld_user_ids=[str(user_id) for user_id in withheld],
        evaluated_at=utc_now(),
        builder_version=BUILDER_VERSION,
    )
    db.add(event)
    return AlertOutcome(
        alert_id=alert.id,
        alert_name=alert.name,
        measure_id=alert.measure_id,
        result=state,
        event=event,
        observed_value=observed,
        threshold_value=threshold,
        reason=None,
        admitted_user_ids=admitted,
        withheld_user_ids=withheld,
        notification=_notification_copy(
            alert, measure, state=state, observed=observed, threshold=threshold, as_of=as_of
        ),
    )


def _context_for(user: User) -> TenantContext:
    """The principal as the evaluator sees them, at their CURRENT authority."""

    return TenantContext(
        organization_id=user.organization_id,
        actor_user_id=user.id,
        authorization_version=user.authorization_version,
    )


def _already_recorded(db: Session, alert: BiAlert, as_of: date, fingerprint: str) -> bool:
    """Whether this exact data has already been judged for this alert."""

    return (
        db.scalar(
            select(BiAlertEvent.id)
            .where(
                BiAlertEvent.organization_id == alert.organization_id,
                BiAlertEvent.alert_id == alert.id,
                BiAlertEvent.as_of_date == as_of,
                BiAlertEvent.build_fingerprint == fingerprint,
            )
            .limit(1)
        )
        is not None
    )


def _previous_state(db: Session, alert: BiAlert) -> str | None:
    """The last VERDICT this alert reached; ``not_evaluated`` rows are skipped.

    Skipping them is the point: a week of authority failures must not make the
    next successful evaluation look like a fresh breach.
    """

    return db.scalar(
        select(BiAlertEvent.state)
        .where(
            BiAlertEvent.organization_id == alert.organization_id,
            BiAlertEvent.alert_id == alert.id,
            BiAlertEvent.state != "not_evaluated",
        )
        .order_by(BiAlertEvent.as_of_date.desc(), BiAlertEvent.evaluated_at.desc())
        .limit(1)
    )


def _threshold_for(
    alert: BiAlert, measure: MeasureDef, resolver: limits.LimitResolver, as_of: date
) -> tuple[Decimal | None, str | None, str | None]:
    """The line this alert is judged against: ``(value, attribution, absence)``.

    ``stated`` is the bank's own number and needs no register at all.
    ``governed_limit`` goes through the limit resolver, which is the ONLY door to
    a register in the BI plane and which refuses rather than converting when the
    register's unit and the measure's do not reconcile — in which case there is
    no line and the alert is unevaluated.
    """

    if alert.threshold_basis == "stated":
        return alert.threshold, None, None
    outcome = resolver.for_measure(measure, as_of=as_of)
    if limits.is_governed(outcome):
        return outcome.value, outcome.source, None
    return None, None, REASON_NO_GOVERNED_LIMIT


def _observed_value(  # noqa: PLR0913 - one figure and every input it is read from
    db: Session,
    cat: Catalogue,
    query: BiQuery,
    *,
    bank: Bank,
    measure: MeasureDef,
    injected_filters: tuple[BiFilter, ...] = (),
) -> tuple[Decimal | None, str | None]:
    """``(figure, refusal)`` — exactly one of the two is set.

    A refusal and an absent figure are DIFFERENT facts and are reported as such:
    ``query_refused`` means the platform would not answer (a timeout, a member
    the compiler will not serve at this grain), ``no_figure`` means it answered
    and the mart holds nothing for the date. Neither is ever zero — substituting
    zero would report a breach of every floor the bank has.
    """

    settings = get_settings().bi
    try:
        compiled = compile_query(
            db,
            cat,
            query,
            organization_id=bank.organization_id,
            bank_id=bank.id,
            injected_filters=injected_filters,
        )
        result = execute(
            db, compiled, timeout_ms=settings.interactive_timeout_ms, row_cap=_SINGLE_ROW_CAP
        )
    except BiQueryError:
        return None, REASON_QUERY_REFUSED
    # A no-dimension single-measure query answers in exactly one row; asking for
    # two is how "exactly one" is checked rather than assumed.
    if len(result.rows) != 1:
        return None, REASON_NO_FIGURE
    position = next(
        (
            index
            for index, spec in enumerate(result.columns)
            if spec.member_id == measure.id and spec.role is None
        ),
        None,
    )
    if position is None:
        return None, REASON_NO_FIGURE
    value = _as_decimal(result.rows[0][position])
    if value is None:
        return None, REASON_NO_FIGURE
    return value, None


#: One row is the whole answer to a no-dimension single-measure query; asking for
#: two is how "exactly one" is checked rather than assumed.
_SINGLE_ROW_CAP = 2


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        # Through ``str`` so a driver that hands back a float does not import its
        # binary representation into a threshold comparison.
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _recipient_decisions(  # noqa: PLR0913 - the split and every input it is made from
    db: Session,
    bank: Bank,
    cat: Catalogue,
    query: BiQuery,
    alert: BiAlert,
    *,
    owner_scope: BiDataScope,
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """Split the distribution list by each recipient's OWN authority and slice.

    The alert's owner may see the figure; that says nothing about the people the
    alert is addressed to. Each one is evaluated through the same
    ``authorize_query`` the read routes make, so a notification cannot carry a
    number its reader could not have asked for.

    And it cannot carry a number about a DIFFERENT slice than the reader's own.
    The event row holds one observed figure, computed over the owner's scope, so a
    recipient whose declared scope differs is withheld: narrower, and the figure
    states more of the book than they may see; wider, and the figure is not the
    one its name promises them. Comparing the DECLARED scopes is deliberate —
    equal declarations resolve to equal branch sets in the same transaction, and
    comparing resolutions would cost one query per recipient to reach the same
    answer. When both are institution-wide this is exactly the old rule.
    """

    admitted: list[UUID] = []
    withheld: list[UUID] = []
    for raw in alert.notify_user_ids:
        user_id = _as_uuid(raw)
        if user_id is None:
            continue
        recipient = db.scalar(
            select(User).where(User.id == user_id, User.organization_id == alert.organization_id)
        )
        if recipient is None or not recipient.is_active:
            withheld.append(user_id)
            continue
        try:
            decision = authorize_query(
                db,
                _context_for(recipient),
                bank,
                cat,
                query,
                permission=Permission.VIEW,
                surface=AUTHORIZATION_SURFACE,
            )
        except BiQueryError:
            withheld.append(user_id)
            continue
        if decision.allowed and decision.data_scope == owner_scope:
            admitted.append(user_id)
        else:
            withheld.append(user_id)
    return tuple(admitted), tuple(withheld)


def _as_uuid(raw: object) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (AttributeError, TypeError, ValueError):
        return None


def _no_row(
    alert: BiAlert,
    result: AlertResult,
    *,
    observed: Decimal | None = None,
    threshold: Decimal | None = None,
) -> AlertOutcome:
    return AlertOutcome(
        alert_id=alert.id,
        alert_name=alert.name,
        measure_id=alert.measure_id,
        result=result,
        event=None,
        observed_value=observed,
        threshold_value=threshold,
        reason=None,
        admitted_user_ids=(),
        withheld_user_ids=(),
        notification=None,
    )


def _record_not_evaluated(
    db: Session, alert: BiAlert, as_of: date, fingerprint: str, reason: str
) -> AlertOutcome:
    """Record that this alert could NOT be judged, and why. Nobody is notified.

    An unevaluated alert is an internal condition — a withdrawn grant, a
    register with no row, a measure the catalogue dropped — and telling a bank
    manager about it would train them to ignore alerts. It is recorded for the
    owner's alert history and the operator board instead.
    """

    event = BiAlertEvent(
        organization_id=alert.organization_id,
        bank_id=alert.bank_id,
        alert_id=alert.id,
        as_of_date=as_of,
        build_fingerprint=fingerprint,
        state="not_evaluated",
        observed_value=None,
        threshold_value=None,
        threshold_basis=alert.threshold_basis,
        limit_source=None,
        reason=reason,
        member_ids=[],
        notified_user_ids=[],
        withheld_user_ids=[],
        evaluated_at=utc_now(),
        builder_version=BUILDER_VERSION,
    )
    db.add(event)
    return AlertOutcome(
        alert_id=alert.id,
        alert_name=alert.name,
        measure_id=alert.measure_id,
        result="not_evaluated",
        event=event,
        observed_value=None,
        threshold_value=None,
        reason=reason,
        admitted_user_ids=(),
        withheld_user_ids=(),
        notification=None,
    )


def _notification_copy(  # noqa: PLR0913 - one sentence and everything it states
    alert: BiAlert,
    measure: MeasureDef,
    *,
    state: str,
    observed: Decimal,
    threshold: Decimal,
    as_of: date,
) -> NotificationCopy:
    """Production copy for one transition. Names no currency and no regulator.

    Both figures are rendered by ``insights/statements.render_value`` in the
    measure's own value type, so an amount reads as "in the reporting currency"
    — which the institution's jurisdiction resolves — and a percentage carries
    its own sign. Nothing is rounded: rounding a capital ratio to shorten a
    sentence changes what the sentence says.
    """

    figure = render_value(observed, measure.value_type)
    line = render_value(threshold, measure.value_type)
    side = "above" if alert.direction == "above" else "below"
    if state == "breached":
        return NotificationCopy(
            type=NOTIFICATION_TYPE_BREACHED,
            severity=SEVERITY_BREACHED,
            title=f"{alert.name}: threshold reached",
            body=(
                f"{measure.label} is {figure} as at {as_of.isoformat()}, which is {side} "
                f"the threshold of {line} set for this alert. Open Insights to see what "
                f"moved."
            ),
        )
    return NotificationCopy(
        type=NOTIFICATION_TYPE_CLEARED,
        severity=SEVERITY_CLEARED,
        title=f"{alert.name}: back within threshold",
        body=(
            f"{measure.label} is {figure} as at {as_of.isoformat()} and is no longer {side} "
            f"the threshold of {line} set for this alert."
        ),
    )


__all__ = [
    "AUTHORIZATION_SURFACE",
    "ENTITY_TYPE",
    "JOB_TYPE",
    "NOTIFICATION_TYPE_BREACHED",
    "NOTIFICATION_TYPE_CLEARED",
    "SEVERITY_BREACHED",
    "SEVERITY_CLEARED",
    "AlertEvaluationError",
    "AlertOutcome",
    "AlertResult",
    "BankAlertEvaluation",
    "NotificationCopy",
    "active_alerts",
    "coalesce_key_for",
    "enqueue_evaluation",
    "evaluate_bank",
]
