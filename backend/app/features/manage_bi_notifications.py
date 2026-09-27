"""Threshold alerts and scheduled report subscriptions: the tenant-facing routes.

The engines behind these routes were complete before any of them existed
(``app/services/bi/alerts.py`` evaluates after a mart build,
``app/services/bi/subscriptions.py`` renders one artifact per recipient under
that recipient's own authority), and nothing could create either object. This
module is the product: list, create, change, stop, delete, and read what
happened.

Mounted behind exactly the dependencies the other BI surfaces are mounted
behind — ``BANK_ROUTE_DEPENDENCIES`` first, so a sibling tenant's ``BK-*`` is
``404 Bank not found.`` before anything else runs, then ``require_bi_enabled``,
so a deployment without BI answers 404 for its own banks too — and every route
resolves ``read_bi.require_bi_read``: an interactive tenant human with a current
``authv``, no machine key, no impersonated operator, and no scalar-role check
anywhere.

The order each route consults its guards in IS the security property, and it is
``manage_bi_content.py``'s order:

1. the institution, the flag, the principal (the dependencies above);
2. the read budget, counted over ``bi_query_log`` like every other BI read;
3. **reachability**, which answers 404 — an alert nobody named this identity on
   does not exist for them, so it cannot be enumerated by id;
4. **ownership**, which answers 403 — reachable, but only its owner may change
   it. Not an account administrator, not an Org Owner;
5. **authorization for the CALLER** — never for the owner — over the figure the
   alert judges or the figures the subscription reports.

Three properties are particular to this surface and easy to lose:

**Creating one of these confers nothing.** A subscription's stored row holds a
name, a query, a cadence, an artifact format, an owner id and a list of
recipient ids. It holds no binding id, no permission, no authorization version
and no rendered figure, because each delivery is authorized and rendered as its
own recipient at send time. So the route must resolve the AUTHOR's access in
order to refuse an author who may not ask the question — and must then store
nothing about that decision. ``tests/api/test_bi_notification_routes.py``
asserts the stored row's columns against that list.

**A distribution list is a membership disclosure.** Naming who else receives a
report is not part of receiving it, so ``recipients`` is populated for the owner
alone and a reachable non-owner sees only their own identity — the rule
``manage_bi_content.py`` applies to a dashboard's shares, for the same reason.
Delivery history is owner-only outright: it names people and what each of them
was or was not sent.

**A verdict is a figure.** An alert event carries the observed value and the line
it was judged against, so the events read is refused unless the caller's own
bindings cover the alert's measure. An owner whose grant was withdrawn keeps the
alert — they must be able to stop it — and loses the verdict, and the list says
so in production copy rather than showing a blank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.authorization import Permission
from app.db.base import utc_now
from app.domain.bi.catalogue import CATALOGUE_VERSION, Catalogue, MeasureDef, catalogue
from app.features.read_bi import BiRead, BiReadAccess
from app.models import Bank, User
from app.models.bi_notifications import (
    BiAlert,
    BiAlertEvent,
    BiSubscription,
    BiSubscriptionDelivery,
)
from app.schemas.bi import BiFilter, BiQuery, BiTime
from app.schemas.bi_notifications import (
    BiAlertEventListRead,
    BiAlertEventRead,
    BiAlertListRead,
    BiAlertRead,
    BiAlertState,
    BiAlertThresholdBasis,
    BiAlertUpsert,
    BiDeliveryMode,
    BiDeliveryStatus,
    BiDeliveryTrigger,
    BiNotificationDeactivateRequest,
    BiNotificationRecipient,
    BiSubscriptionCadence,
    BiSubscriptionDeliveryListRead,
    BiSubscriptionDeliveryRead,
    BiSubscriptionFormat,
    BiSubscriptionListRead,
    BiSubscriptionRead,
    BiSubscriptionUpsert,
)
from app.services import audit, jurisdictions
from app.services.bi import query_log, subscriptions
from app.services.bi.authorization import UnknownMember, authorize_query, query_members
from app.services.bi.exports import policy
from app.services.bi.insights.statements import render_value

router = APIRouter(tags=["bi"])

#: ``audit_events.event_type`` for every mutation here. An alert and a
#: subscription are both instructions the platform will act on without anyone
#: present, so each one leaves a trail naming the actor, the object and the
#: reason the actor gave.
EVENT_ALERT_CREATED = "bi.alert.created"
EVENT_ALERT_UPDATED = "bi.alert.updated"
EVENT_ALERT_DEACTIVATED = "bi.alert.deactivated"
EVENT_ALERT_DELETED = "bi.alert.deleted"
EVENT_SUBSCRIPTION_CREATED = "bi.subscription.created"
EVENT_SUBSCRIPTION_UPDATED = "bi.subscription.updated"
EVENT_SUBSCRIPTION_DEACTIVATED = "bi.subscription.deactivated"
EVENT_SUBSCRIPTION_DELETED = "bi.subscription.deleted"

ENTITY_ALERT = "bi_alert"
ENTITY_SUBSCRIPTION = "bi_subscription"

#: The telemetry surfaces ``authorize_query`` reports these decisions under.
#: Neither is a ``bi_query_log`` surface — that vocabulary is closed and has no
#: value for either — which is why the audit trail for a configuration change is
#: the ``audit_events`` row and the trail for a verdict is ``bi_alert_events``.
ALERT_SURFACE = "alert"
SUBSCRIPTION_SURFACE = "subscription"

#: How many rows a history read returns at most. A history is something a person
#: reads, so the page is a screenful rather than everything ever recorded.
HISTORY_LIMIT_DEFAULT = 50
HISTORY_LIMIT_MAX = 200

HistoryLimit = Annotated[
    int,
    Query(ge=1, le=HISTORY_LIMIT_MAX, description="How many of the most recent rows to return."),
]

#: Production copy for an alert row's newest verdict, or for its absence. Each
#: sentence states a fact rather than a code, and none of them states a figure —
#: the figure lives on the event, which is authorized separately.
NEVER_EVALUATED = (
    "This alert has not been judged yet. It is evaluated each time the "
    "institution's figures are rebuilt for a reporting date."
)
VERDICT_WITHHELD = (
    "Your access does not cover the figure this alert watches, so its verdict is "
    "not shown. An Org Owner can grant it."
)
ALERT_INACTIVE = "This alert is stopped, so it is no longer being judged."

#: How each alert state reads on a row.
_STATE_COPY: Mapping[str, str] = {
    "breached": "Past its threshold",
    "cleared": "Back within its threshold",
    "not_evaluated": "Could not be judged",
}

#: Why an evaluation produced no verdict. ``models.bi_notifications``' own
#: vocabulary, each value as a sentence a reader can act on. Deny-by-default:
#: an unknown reason falls back to the generic sentence rather than to the code.
_EVENT_REASON_COPY: Mapping[str, str] = {
    "authorization_revoked": (
        "The figure could not be read under this alert's owner's access, so no "
        "verdict was reached. The owner's grant has to be restored."
    ),
    "no_governed_limit": (
        "No governed limit was in force for this figure on this date, so there "
        "was no line to judge it against."
    ),
    "no_figure": (
        "The institution's figures held nothing for this reporting date, so there "
        "was nothing to judge."
    ),
    "unknown_member": (
        "The figure this alert watches is no longer published, so it can no "
        "longer be judged. Point the alert at a figure that is."
    ),
    "query_refused": (
        "The platform would not answer the question behind this alert on this "
        "date, so no verdict was reached."
    ),
    "data_scope_unsupported": (
        "This alert judges the institution as a whole, and its owner's access "
        "covers only part of it, so no verdict was reached."
    ),
}
_EVENT_REASON_FALLBACK = "No verdict was reached for this reporting date."

#: How each delivery status reads. A refused delivery is NOT an error and must
#: not read as one: a recipient whose access does not cover the figures was
#: correctly sent nothing.
_DELIVERY_STATUS_COPY: Mapping[str, str] = {
    "pending": "Being prepared",
    "sent": "Sent",
    "denied": "Not sent: this recipient's access does not cover the figures",
    "no_data": "Not sent: there were no figures for this reporting date",
    "failed": "Could not be sent",
}

#: Why a delivery ended as it did — ``services.bi.subscriptions``' own reasons,
#: as sentences. Only the ones a reader can act on say what to do.
_DELIVERY_REASON_COPY: Mapping[str, str] = {
    "already_delivered": "This recipient had already received this run.",
    "recipient_inactive": "This person's account is no longer active.",
    "data_scope_unsupported": (
        "This report covers the institution as a whole and this recipient's "
        "access covers only part of it."
    ),
    "disclosure_class_changed": (
        "The report turned out to identify individual records, so it was not attached."
    ),
    "no_figures_for_date": "The institution's figures held nothing for this reporting date.",
    "no_build_for_institution": "The institution's figures had not been built yet.",
    "unsupported_time_window": "This report's reporting window can no longer be filled in.",
    "recipient_cap_exceeded": (
        "This subscription names more recipients than one run may deliver to."
    ),
    "attachment_over_size_cap": (
        "The report was too large to attach, so a sign-in link was sent instead."
    ),
    "relay_unavailable": "The mail relay did not accept the message.",
    "relay_not_configured": "This deployment has no mail relay configured.",
    "subscription_inactive": "This subscription was stopped before the run.",
    "unsupported_artifact_format": "This report's file format is no longer offered.",
}

#: What a subscription WOULD do with its content, as a sentence the composer and
#: the list both show. The classifier decides it from the catalogue's own
#: sensitivity declarations, before a single row is read.
DELIVERY_NOTE_ATTACHED = (
    "Each recipient is emailed this report as a file, prepared under their own access."
)
DELIVERY_NOTE_LINK = (
    "This report identifies individual records, so it is never attached. Each "
    "recipient is emailed a sign-in link instead."
)

AsOf = Annotated[
    date | None,
    Query(description="The reporting date to resolve a governed limit against, when there is one."),
]


# --- refusals ----------------------------------------------------------------------------


def _budget(db: Session, access: BiReadAccess) -> None:
    """Meter this principal against the window every BI read is metered by."""

    budget = query_log.budget_for(
        db,
        organization_id=access.ctx.organization_id,
        principal_user_id=access.principal_user_id,
    )
    if budget.exceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": "bi_rate_limited",
                "message": (
                    "Too many analytics requests in the last minute. Wait a moment and try again."
                ),
            },
            headers={"Retry-After": str(budget.retry_after_seconds)},
        )


def _alert_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error_code": "bi_alert_not_found", "message": "No such alert."},
    )


def _subscription_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error_code": "bi_subscription_not_found",
            "message": "No such scheduled report.",
        },
    )


def _owner_only(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "bi_notification_owner_only",
            "message": f"Only the person who created this {what} can change it.",
        },
    )


def _denied(cat: Catalogue, denied_members: Sequence[str], reason: str) -> HTTPException:
    """403 naming every refused figure, in the read surface's own envelope.

    The same ``error_code``, message and fields ``/bi/query`` refuses with, so a
    client has one handler for "your access does not cover this" wherever it
    happens. The label is catalogue metadata, not tenant data.
    """

    labels: list[str] = []
    for member_id in denied_members:
        try:
            labels.append(cat.member(member_id).label)
        except KeyError:
            labels.append(member_id)
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "bi_authorization_denied",
            "message": (
                "Your access does not cover every figure this needs. "
                "An Org Owner can grant the figures listed here."
            ),
            "denied_members": list(denied_members),
            "denied_member_labels": labels,
            "reason": reason,
        },
    )


def _unprocessable(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error_code": error_code, "message": message},
    )


def _conflict(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message},
    )


# --- the caller's authority, asked once per distinct question ----------------------------


@dataclass
class _Authority:
    """One caller's ``authorize_query`` verdict per distinct question, cached.

    A list read asks about many objects and several of them ask about the same
    figure. ``authorize_query`` already evaluates per ``(module, sensitivity)``
    pair, so the cache here only avoids re-walking the catalogue — it never
    stands in for a decision, and it is scoped to one request and one principal
    because it is built inside the route.
    """

    db: Session
    access: BiReadAccess
    cat: Catalogue
    surface: str
    _verdicts: dict[tuple[str, ...], tuple[bool, tuple[str, ...], str]]

    @classmethod
    def build(
        cls, db: Session, access: BiReadAccess, cat: Catalogue, *, surface: str
    ) -> _Authority:
        return cls(db=db, access=access, cat=cat, surface=surface, _verdicts={})

    def decide(
        self, query: BiQuery, *, permission: Permission = Permission.VIEW
    ) -> tuple[bool, tuple[str, ...], str]:
        """``(allowed, denied member ids, reason)`` for this caller on ``query``.

        An id the catalogue no longer knows is a REFUSAL rather than an
        exception: a stored object may outlive a member, and a list that raised
        500 over one retired figure would hide every other object beside it.
        """

        try:
            members = tuple(member.id for member in query_members(self.cat, query))
        except UnknownMember as exc:
            return False, (str(exc.member_id),), "unknown_member"
        key = (self.surface, permission.value, *members)
        cached = self._verdicts.get(key)
        if cached is not None:
            return cached
        decision = authorize_query(
            self.db,
            self.access.ctx,
            self.access.bank,
            self.cat,
            query,
            permission=permission,
            surface=self.surface,
        )
        verdict = (decision.allowed, decision.denied_members, decision.reason)
        self._verdicts[key] = verdict
        return verdict


# --- identities --------------------------------------------------------------------------


def _identities(db: Session, organization_id: str, user_ids: Sequence[UUID]) -> Mapping[UUID, User]:
    """The named identities of THIS tenant, for display only."""

    wanted = {user_id for user_id in user_ids}
    if not wanted:
        return {}
    rows = db.scalars(
        select(User).where(User.organization_id == organization_id, User.id.in_(wanted))
    )
    return {row.id: row for row in rows}


def _display_name(identities: Mapping[UUID, User], user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    user = identities.get(user_id)
    return None if user is None else user.display_name


def _stored_ids(raw: Sequence[str]) -> list[UUID]:
    """The stored JSON list as identifiers, skipping anything unreadable.

    The list is a JSON column, so the composite user foreign key does not protect
    it (the report that built these tables says so). A value that is not an
    identifier is therefore possible in principle and must not make a read fail:
    it is dropped here and refused at delivery time, where the recipient is
    looked up.
    """

    ids: list[UUID] = []
    for value in raw:
        try:
            ids.append(UUID(str(value)))
        except (AttributeError, TypeError, ValueError):
            continue
    return ids


def _recipients(
    identities: Mapping[UUID, User], user_ids: Sequence[UUID]
) -> list[BiNotificationRecipient]:
    """The named list, in the order it was stored, dropping ids with no identity.

    An id with no row is dropped rather than shown as a blank: it names nobody
    this tenant holds, and the delivery path already records that refusal.
    """

    named: list[BiNotificationRecipient] = []
    for user_id in user_ids:
        user = identities.get(user_id)
        if user is None:
            continue
        named.append(
            BiNotificationRecipient(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                is_active=user.is_active,
            )
        )
    return named


def _resolve_recipients(
    db: Session,
    access: BiReadAccess,
    *,
    user_ids: Sequence[UUID],
    emails: Sequence[str],
    what: str,
) -> list[UUID]:
    """Every named recipient as an identity of THIS organization, or a refusal.

    Both ways of naming a person resolve here, against the same organization
    scope, and BOTH are refused the same way: an identifier or an address that
    does not name an active identity of this tenant refuses the whole request
    rather than being dropped. Dropping one would create a subscription that
    quietly delivers to fewer people than its author believes it does.

    The cap is applied AFTER resolution, because an address and an identifier may
    name the same person and the cap counts people. It is the service's own
    ``MAX_RECIPIENTS``, which is what bounds one delivery run.
    """

    organization_id = access.ctx.organization_id
    resolved: dict[UUID, None] = {}
    missing_ids: list[str] = []
    for user_id in user_ids:
        row = db.scalar(
            select(User).where(User.organization_id == organization_id, User.id == user_id)
        )
        if row is None or not row.is_active:
            missing_ids.append(str(user_id))
            continue
        resolved[row.id] = None
    missing_emails: list[str] = []
    for address in emails:
        row = db.scalar(
            select(User).where(
                User.organization_id == organization_id,
                func.lower(User.email) == address,
            )
        )
        if row is None or not row.is_active:
            missing_emails.append(address)
            continue
        resolved[row.id] = None
    if missing_ids or missing_emails:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "bi_notification_recipient_unknown",
                "message": (
                    f"Everyone on a {what}'s list has to be an active user of this "
                    "organization. These are not, so nothing was saved."
                ),
                "unknown_emails": missing_emails,
                "unknown_user_ids": missing_ids,
            },
        )
    if len(resolved) > subscriptions.MAX_RECIPIENTS:
        raise _unprocessable(
            "bi_notification_recipient_cap_exceeded",
            (
                f"A {what} may name at most {subscriptions.MAX_RECIPIENTS} people. "
                f"This one names {len(resolved)}."
            ),
        )
    return list(resolved)


# --- alerts: shape and authority ---------------------------------------------------------


def _measure_of(cat: Catalogue, measure_id: str) -> MeasureDef:
    """The catalogue measure an alert watches, or a named refusal.

    A dimension is not a figure, and neither is an id the catalogue does not
    know. Both refuse before anything is written: an alert on something that
    cannot produce a number is an alert that can never be judged.
    """

    try:
        member = cat.member(measure_id)
    except KeyError as exc:
        raise _unprocessable(
            "bi_alert_measure_unknown",
            "That figure is not one this institution publishes, so no alert can watch it.",
        ) from exc
    if not isinstance(member, MeasureDef):
        raise _unprocessable(
            "bi_alert_measure_unknown",
            f"{member.label} is something to group or filter by, not a figure to watch.",
        )
    return member


def _stored_alert_query(alert: BiAlert) -> BiQuery | None:
    """The question a STORED alert asks, or ``None`` when its row cannot be read.

    The stored filters ride along, and that is the security property rather than
    a detail: a filter is a read (``authorize_query`` walks it exactly as it walks
    a projection), so deciding on the bare measure would authorize a BROADER
    question than the alert asks and would admit a reader to "exposure to X" on
    the strength of being allowed the exposure total.

    A filter payload the wire model no longer accepts yields ``None``, which every
    caller reads as a refusal. Refusing is right: an unreadable question is not one
    to serve a verdict for, and the alert stays listable so its owner can stop it.
    """

    try:
        filters = [BiFilter.model_validate(payload) for payload in alert.filters]
    except ValidationError:
        return None
    return _alert_query(alert.measure_id, filters, utc_now().date())


def _alert_query(alert_measure_id: str, filters: Sequence[BiFilter], as_of: date) -> BiQuery:
    """The single-figure question an alert is about.

    No dimensions: an alert is a threshold on one figure for the institution, and
    that is the same query ``services/bi/alerts.py`` evaluates. ``as_of`` does not
    change which members the question touches, so any date answers the
    authorization question identically — it is supplied because the wire model
    requires a window, not because the decision depends on it.
    """

    return BiQuery(measures=[alert_measure_id], filters=list(filters), time=BiTime(as_of=as_of))


def _require_threshold_pairing(payload: BiAlertUpsert, measure: MeasureDef) -> None:
    """A threshold is stated or governed, and it is never inferred.

    The pairing is also a CHECK constraint and also a request-model validator.
    This is the third statement of it and the one that answers with a named error
    rather than an integrity error, so the refusal is something a surface can act
    on. The first branch is unreachable while the request model stands; it is
    kept because the database's refusal would be a 500, and it is exercised
    directly by ``tests/services/bi/test_bi_notification_rules.py``.

    The second branch is reachable and is the one the request model cannot make:
    whether the catalogue declares a governed source for this figure at all. A
    governed basis on a figure with no register behind it is an alert that can
    only ever record "there was no line to judge it against".
    """

    stated = payload.threshold_basis == "stated"
    if stated != (payload.threshold is not None):
        raise _unprocessable(
            "bi_alert_threshold_mismatch",
            (
                "An alert is judged either against a number you state or against the "
                "limit already governed for the figure — not both and not neither."
            ),
        )
    if not stated and measure.thresholds_source is None:
        raise _unprocessable(
            "bi_alert_governed_limit_unavailable",
            (
                f"No limit is governed for {measure.label}, so this alert has nothing to "
                "judge it against. State a number instead."
            ),
        )


def _require_sliceable(cat: Catalogue, measure: MeasureDef, filters: Sequence[BiFilter]) -> None:
    """Every filter must name a field this figure can be narrowed by.

    The compiler's own rule (a measure is only correct at the grains its own
    table carries), applied before the row is written rather than every time the
    alert is evaluated: an alert the engine would refuse for ever is not
    something to persist. Checked AFTER the authorization walk, because the
    message names both members.
    """

    for predicate in filters:
        try:
            member = cat.member(predicate.member)
        except KeyError as exc:
            raise _unprocessable(
                "bi_alert_filter_unknown",
                "One of this alert's conditions names a field this institution does not publish.",
            ) from exc
        if isinstance(member, MeasureDef):
            raise _unprocessable(
                "bi_alert_filter_unknown",
                f"{member.label} is a figure, not something to narrow by.",
            )
        if predicate.member not in measure.allowed_dimensions:
            raise _unprocessable(
                "bi_alert_filter_unknown",
                f"{measure.label} cannot be narrowed by {member.label}.",
            )


def _require_answerable_query(cat: Catalogue, query: BiQuery) -> None:
    """Refuse a subscription whose question the compiler would always refuse.

    The same two rules ``content.check_canvas_shape`` applies to a saved
    dashboard's widget, applied to one query: a figure is not something to group
    by, and every field a question is sliced by must be one that every figure in
    it can be sliced by. Written out here rather than reused because that helper
    takes a dashboard canvas; the rules it mirrors are named in its docstring.
    """

    measures: list[MeasureDef] = []
    for member_id in query.measures:
        member = cat.member(member_id)
        if not isinstance(member, MeasureDef):
            raise _unprocessable(
                "bi_subscription_query_refused",
                f"{member.label} is something to group or filter by, not a figure to report.",
            )
        measures.append(member)
    sliced: list[str] = [
        *query.dimensions,
        *(predicate.member for predicate in query.filters),
    ]
    if query.pivot is not None:
        sliced.append(query.pivot.dimension)
    if query.top_n is not None:
        sliced.append(query.top_n.dimension)
    for dimension_id in sliced:
        dimension = cat.member(dimension_id)
        if isinstance(dimension, MeasureDef):
            raise _unprocessable(
                "bi_subscription_query_refused",
                f"{dimension.label} is a figure, not something to group or narrow by.",
            )
        for measure in measures:
            if dimension_id not in measure.allowed_dimensions:
                raise _unprocessable(
                    "bi_subscription_query_refused",
                    f"{measure.label} cannot be broken down by {dimension.label}.",
                )


# --- reachability ------------------------------------------------------------------------


def _named(raw: Sequence[str], user_id: UUID) -> bool:
    return any(str(value) == str(user_id) for value in raw)


def _load_alert(db: Session, access: BiReadAccess, alert_id: UUID) -> BiAlert:
    """One alert of THIS institution this identity may reach, or 404.

    Scoped at the query by organization AND institution: two banks of one
    organization share an RLS tenant, so organization scoping alone cannot
    isolate their child objects. Reachability sits on top and answers the same
    404 a missing row answers, so an alert this identity has nothing to do with
    cannot be enumerated by id.

    Two identities reach an alert: its owner, and anyone it is addressed to.
    Whether the caller may see its VERDICT is a separate decision, taken by each
    route that would serve one.
    """

    alert = db.scalar(
        select(BiAlert).where(
            BiAlert.id == alert_id,
            BiAlert.organization_id == access.ctx.organization_id,
            BiAlert.bank_id == access.bank.id,
        )
    )
    if alert is None:
        raise _alert_not_found()
    if alert.owner_user_id == access.principal_user_id:
        return alert
    if _named(alert.notify_user_ids, access.principal_user_id):
        return alert
    raise _alert_not_found()


def _owned_alert(db: Session, access: BiReadAccess, alert_id: UUID) -> BiAlert:
    """Reachable first (404), then owned (403). In that order, always."""

    alert = _load_alert(db, access, alert_id)
    if alert.owner_user_id != access.principal_user_id:
        raise _owner_only("alert")
    return alert


def _load_subscription(db: Session, access: BiReadAccess, subscription_id: UUID) -> BiSubscription:
    """One subscription of THIS institution this identity may reach, or 404."""

    subscription = db.scalar(
        select(BiSubscription).where(
            BiSubscription.id == subscription_id,
            BiSubscription.organization_id == access.ctx.organization_id,
            BiSubscription.bank_id == access.bank.id,
        )
    )
    if subscription is None:
        raise _subscription_not_found()
    if subscription.owner_user_id == access.principal_user_id:
        return subscription
    if _named(subscription.recipient_user_ids, access.principal_user_id):
        return subscription
    raise _subscription_not_found()


def _owned_subscription(db: Session, access: BiReadAccess, subscription_id: UUID) -> BiSubscription:
    """Reachable first (404), then owned (403). In that order, always."""

    subscription = _load_subscription(db, access, subscription_id)
    if subscription.owner_user_id != access.principal_user_id:
        raise _owner_only("scheduled report")
    return subscription


# --- read models -------------------------------------------------------------------------


def _state(value: str) -> BiAlertState:
    if value not in ("breached", "cleared", "not_evaluated"):  # pragma: no cover - CHECK
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This alert's verdict could not be read.",
        )
    return value


def _basis(value: str) -> BiAlertThresholdBasis:
    if value not in ("stated", "governed_limit"):  # pragma: no cover - CHECK constrained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This alert's threshold could not be read.",
        )
    return value


def _cadence(value: str) -> BiSubscriptionCadence:
    if value not in ("daily", "weekly", "monthly", "on_new_data"):  # pragma: no cover - CHECK
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This scheduled report's schedule could not be read.",
        )
    return value


def _format(value: str) -> BiSubscriptionFormat:
    if value not in ("csv", "xlsx", "pdf"):  # pragma: no cover - CHECK constrained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This scheduled report's file format could not be read.",
        )
    return value


def _delivery_status(value: str) -> BiDeliveryStatus:
    if value not in ("pending", "sent", "denied", "no_data", "failed"):  # pragma: no cover - CHECK
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This delivery's outcome could not be read.",
        )
    return value


def _delivery_trigger(value: str) -> BiDeliveryTrigger:
    if value not in ("schedule", "new_data"):  # pragma: no cover - CHECK constrained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This delivery's trigger could not be read.",
        )
    return value


def _delivery_mode(value: str | None) -> BiDeliveryMode | None:
    if value is None:
        return None
    if value not in ("attachment", "link"):  # pragma: no cover - CHECK constrained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This delivery's form could not be read.",
        )
    return value


def _disclosure(value: str | None) -> policy.ExportClass | None:
    if value is None:
        return None
    return policy.RECORD_LEVEL if value == policy.RECORD_LEVEL else policy.SUMMARY


def _latest_event(db: Session, alert: BiAlert) -> BiAlertEvent | None:
    """The newest recorded verdict for one alert, whatever its state."""

    return db.scalar(
        select(BiAlertEvent)
        .where(
            BiAlertEvent.organization_id == alert.organization_id,
            BiAlertEvent.alert_id == alert.id,
        )
        .order_by(BiAlertEvent.as_of_date.desc(), BiAlertEvent.evaluated_at.desc())
        .limit(1)
    )


def _alert_detail(alert: BiAlert, event: BiAlertEvent | None, *, visible: bool) -> str:
    """One sentence for the row: the newest verdict, or why there is none."""

    if not visible:
        return VERDICT_WITHHELD
    if event is None:
        return ALERT_INACTIVE if not alert.is_active else NEVER_EVALUATED
    if event.state == "not_evaluated":
        reason = event.reason or ""
        return _EVENT_REASON_COPY.get(reason, _EVENT_REASON_FALLBACK)
    return f"{_STATE_COPY[event.state]} as at {event.as_of_date.isoformat()}."


def _event_detail(event: BiAlertEvent, measure: MeasureDef | None) -> str:
    """One recorded transition, as a sentence that states its own figures.

    Both figures are rendered by ``insights/statements.render_value`` in the
    measure's own value type, so an amount reads in the reporting currency the
    institution's jurisdiction resolves and a percentage carries its own sign.
    Nothing is rounded. A measure the catalogue no longer knows loses the unit,
    not the sentence.
    """

    if event.state == "not_evaluated":
        return _EVENT_REASON_COPY.get(event.reason or "", _EVENT_REASON_FALLBACK)
    observed, line = event.observed_value, event.threshold_value
    if observed is None or line is None:  # pragma: no cover - CHECK constrained
        return _EVENT_REASON_FALLBACK
    figure = _rendered(observed, measure)
    threshold = _rendered(line, measure)
    verdict = _STATE_COPY[event.state].lower()
    attribution = (
        f" The limit came from {event.limit_source}." if event.limit_source is not None else ""
    )
    return (
        f"{figure} as at {event.as_of_date.isoformat()}, against a threshold of "
        f"{threshold} — {verdict}.{attribution}"
    )


def _rendered(value: Decimal, measure: MeasureDef | None) -> str:
    return render_value(value, measure.value_type) if measure is not None else f"{value}"


def _alert_read(  # noqa: PLR0913 - one alert, its reader, its label and its verdict
    alert: BiAlert,
    *,
    measure: MeasureDef | None,
    identities: Mapping[UUID, User],
    caller: UUID,
    event: BiAlertEvent | None,
    verdict_visible: bool,
) -> BiAlertRead:
    own = alert.owner_user_id == caller
    stored = _stored_ids(alert.notify_user_ids)
    # The distribution list is the owner's to see. A reachable non-owner is on
    # it, so they are told that and nothing more: naming the rest would disclose
    # who else the institution tells about this figure.
    visible_ids = stored if own else [caller]
    return BiAlertRead(
        id=alert.id,
        bank_id=alert.bank_id,
        name=alert.name,
        measure_id=alert.measure_id,
        measure_label=measure.label if measure is not None else alert.measure_id,
        filters=[BiFilter.model_validate(payload) for payload in alert.filters],
        direction="above" if alert.direction == "above" else "below",
        threshold_basis=_basis(alert.threshold_basis),
        threshold=alert.threshold,
        owner_user_id=alert.owner_user_id,
        owner_display_name=_display_name(identities, alert.owner_user_id),
        owned_by_caller=own,
        notify_user_ids=visible_ids,
        recipients=_recipients(identities, stored) if own else [],
        is_active=alert.is_active,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        latest_state=_state(event.state) if event is not None and verdict_visible else None,
        latest_as_of=event.as_of_date if event is not None and verdict_visible else None,
        latest_detail=_alert_detail(alert, event, visible=verdict_visible),
    )


def _time_zone_name(db: Session, bank: Bank) -> str:
    """The institution's own zone, by the SAME rule the delivery scan applies.

    Jurisdiction is data (``banks.jurisdiction_code`` → ``jurisdictions.timezone``),
    so "07:30" means half past seven where the bank is. A registry row with no
    zone, or one naming a zone this system does not hold, falls back to UTC —
    the only jurisdiction-neutral choice — exactly as
    ``services/bi/subscriptions.py`` does when it decides when to run. The two
    are pinned together by ``tests/services/bi/test_bi_notification_rules.py``,
    because a surface that displayed one zone while the scan used another would
    be worse than showing none.
    """

    jurisdiction = jurisdictions.get_jurisdiction(db, bank)
    name = jurisdiction.timezone if jurisdiction is not None else None
    if not name:
        return "UTC"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return name


def _subscription_read(  # noqa: PLR0913 - one subscription and everything it states
    subscription: BiSubscription,
    *,
    query: BiQuery,
    disclosure: policy.ExportClass,
    time_zone: str,
    identities: Mapping[UUID, User],
    caller: UUID,
) -> BiSubscriptionRead:
    own = subscription.owner_user_id == caller
    stored = _stored_ids(subscription.recipient_user_ids)
    visible_ids = stored if own else [caller]
    return BiSubscriptionRead(
        id=subscription.id,
        bank_id=subscription.bank_id,
        name=subscription.name,
        query=query,
        artifact_format=_format(subscription.artifact_format),
        cadence=_cadence(subscription.cadence),
        hour=subscription.hour,
        minute=subscription.minute,
        day_of_week=subscription.day_of_week,
        day_of_month=subscription.day_of_month,
        time_zone=time_zone,
        owner_user_id=subscription.owner_user_id,
        owner_display_name=_display_name(identities, subscription.owner_user_id),
        owned_by_caller=own,
        recipient_user_ids=visible_ids,
        recipients=_recipients(identities, stored) if own else [],
        is_active=subscription.is_active,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        disclosure_class=disclosure,
        delivery_note=(
            DELIVERY_NOTE_LINK if disclosure == policy.RECORD_LEVEL else DELIVERY_NOTE_ATTACHED
        ),
    )


def _delivery_read(
    delivery: BiSubscriptionDelivery, identities: Mapping[UUID, User]
) -> BiSubscriptionDeliveryRead:
    """One recipient's copy of one run, stated honestly.

    A refused delivery is a first-class outcome, not a hidden error: the reason
    is a sentence about the recipient's access, and no figure, member label or
    row count accompanies it, because none was ever produced.
    """

    recipient = identities.get(delivery.recipient_user_id)
    status_copy = _DELIVERY_STATUS_COPY.get(delivery.status, "Outcome recorded")
    reason_copy = _DELIVERY_REASON_COPY.get(delivery.reason or "")
    detail = f"{status_copy}. {reason_copy}" if reason_copy else f"{status_copy}."
    return BiSubscriptionDeliveryRead(
        id=delivery.id,
        subscription_id=delivery.subscription_id,
        recipient_user_id=delivery.recipient_user_id,
        scheduled_for=delivery.scheduled_for,
        trigger=_delivery_trigger(delivery.trigger),
        status=_delivery_status(delivery.status),
        as_of_date=delivery.as_of_date,
        disclosure_class=_disclosure(delivery.disclosure_class),
        delivery_mode=_delivery_mode(delivery.delivery_mode),
        artifact_format=_format(delivery.artifact_format) if delivery.artifact_format else None,
        artifact_size_bytes=delivery.artifact_size_bytes,
        row_count=delivery.row_count,
        reason=delivery.reason,
        detail=detail,
        sent_at=delivery.sent_at,
        recipient_email=recipient.email if recipient is not None else None,
        recipient_display_name=recipient.display_name if recipient is not None else None,
    )


# --- alerts ------------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/bi/alerts",
    response_model=BiAlertListRead,
    operation_id="listBiAlerts",
)
def list_bi_alerts(bank_id: str, db: DbSession, access: BiRead) -> BiAlertListRead:
    """Every alert of this institution this identity owns or is addressed to.

    An alert whose figure this caller's access does not cover is not dropped from
    the list — its OWNER has to be able to see and stop it — but its verdict is,
    and the row says why. A caller who is neither the owner nor addressed sees
    nothing, because they reach nothing.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    rows = db.scalars(
        select(BiAlert)
        .where(
            BiAlert.organization_id == access.ctx.organization_id,
            BiAlert.bank_id == access.bank.id,
        )
        .order_by(BiAlert.is_active.desc(), BiAlert.updated_at.desc(), BiAlert.id)
    )
    reachable = [
        alert
        for alert in rows
        if alert.owner_user_id == access.principal_user_id
        or _named(alert.notify_user_ids, access.principal_user_id)
    ]
    identities = _identities(
        db,
        access.ctx.organization_id,
        [
            user_id
            for alert in reachable
            for user_id in (alert.owner_user_id, *_stored_ids(alert.notify_user_ids))
        ],
    )
    measures = {measure.id: measure for measure in cat.measures()}
    alerts: list[BiAlertRead] = []
    for alert in reachable:
        measure = measures.get(alert.measure_id)
        asked = _stored_alert_query(alert)
        allowed = asked is not None and authority.decide(asked)[0]
        alerts.append(
            _alert_read(
                alert,
                measure=measure,
                identities=identities,
                caller=access.principal_user_id,
                event=_latest_event(db, alert),
                verdict_visible=allowed,
            )
        )
    return BiAlertListRead(alerts=alerts)


@router.post(
    "/banks/{bank_id}/bi/alerts",
    response_model=BiAlertRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBiAlert",
)
def create_bi_alert(
    bank_id: str, payload: BiAlertUpsert, db: DbSession, access: BiRead
) -> BiAlertRead:
    """Watch one figure against one line. The caller becomes its owner.

    Deny-by-default applied to writing as well as reading: an author who may not
    ask what this figure is may not instruct the platform to watch it either.
    Being authorized HERE confers nothing on the alert — every recipient is
    re-authorized when a verdict is reached, and the owner is re-authorized at
    every evaluation, so a withdrawn grant stops the figure rather than only the
    interactive page.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    measure = _measure_of(cat, payload.measure_id)
    _require_threshold_pairing(payload, measure)
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    allowed, denied, reason = authority.decide(
        _alert_query(payload.measure_id, payload.filters, utc_now().date())
    )
    if not allowed:
        raise _denied(cat, denied, reason)
    _require_sliceable(cat, measure, payload.filters)
    notify = _resolve_recipients(
        db,
        access,
        user_ids=payload.notify_user_ids,
        emails=payload.notify_emails,
        what="alert",
    )
    alert = BiAlert(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        name=payload.name,
        measure_id=payload.measure_id,
        filters=[predicate.model_dump(mode="json") for predicate in payload.filters],
        direction=payload.direction,
        threshold_basis=payload.threshold_basis,
        threshold=payload.threshold,
        owner_user_id=access.principal_user_id,
        notify_user_ids=[str(user_id) for user_id in notify],
        is_active=payload.is_active,
    )
    db.add(alert)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "bi_alert_name_taken",
            "This institution already has an alert with that name.",
        ) from exc
    _audit_alert(db, access, alert, event_type=EVENT_ALERT_CREATED, reason=payload.reason)
    db.commit()
    db.refresh(alert)
    return _alert_read(
        alert,
        measure=measure,
        identities=_identities(db, access.ctx.organization_id, [alert.owner_user_id, *notify]),
        caller=access.principal_user_id,
        event=None,
        verdict_visible=True,
    )


@router.get(
    "/banks/{bank_id}/bi/alerts/{alert_id}",
    response_model=BiAlertRead,
    operation_id="getBiAlert",
)
def get_bi_alert(bank_id: str, alert_id: UUID, db: DbSession, access: BiRead) -> BiAlertRead:
    """One alert, if this identity owns it or is addressed by it."""

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    alert = _load_alert(db, access, alert_id)
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    asked = _stored_alert_query(alert)
    allowed = asked is not None and authority.decide(asked)[0]
    measures = {measure.id: measure for measure in cat.measures()}
    stored = _stored_ids(alert.notify_user_ids)
    return _alert_read(
        alert,
        measure=measures.get(alert.measure_id),
        identities=_identities(db, access.ctx.organization_id, [alert.owner_user_id, *stored]),
        caller=access.principal_user_id,
        event=_latest_event(db, alert),
        verdict_visible=allowed,
    )


@router.put(
    "/banks/{bank_id}/bi/alerts/{alert_id}",
    response_model=BiAlertRead,
    operation_id="updateBiAlert",
)
def update_bi_alert(  # noqa: PLR0913 - FastAPI injects db/access, the rest is the request
    bank_id: str,
    alert_id: UUID,
    payload: BiAlertUpsert,
    db: DbSession,
    access: BiRead,
) -> BiAlertRead:
    """Replace an alert's definition. Owner only, and the author is re-authorized.

    Re-authorized on every edit, not only at creation: an owner whose grant was
    withdrawn may stop or delete their alert, and may not point it at a figure
    they can no longer read.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    alert = _owned_alert(db, access, alert_id)
    measure = _measure_of(cat, payload.measure_id)
    _require_threshold_pairing(payload, measure)
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    allowed, denied, reason = authority.decide(
        _alert_query(payload.measure_id, payload.filters, utc_now().date())
    )
    if not allowed:
        raise _denied(cat, denied, reason)
    _require_sliceable(cat, measure, payload.filters)
    notify = _resolve_recipients(
        db,
        access,
        user_ids=payload.notify_user_ids,
        emails=payload.notify_emails,
        what="alert",
    )
    alert.name = payload.name
    alert.measure_id = payload.measure_id
    alert.filters = [predicate.model_dump(mode="json") for predicate in payload.filters]
    alert.direction = payload.direction
    alert.threshold_basis = payload.threshold_basis
    alert.threshold = payload.threshold
    alert.notify_user_ids = [str(user_id) for user_id in notify]
    alert.is_active = payload.is_active
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "bi_alert_name_taken",
            "This institution already has an alert with that name.",
        ) from exc
    _audit_alert(db, access, alert, event_type=EVENT_ALERT_UPDATED, reason=payload.reason)
    db.commit()
    db.refresh(alert)
    return _alert_read(
        alert,
        measure=measure,
        identities=_identities(db, access.ctx.organization_id, [alert.owner_user_id, *notify]),
        caller=access.principal_user_id,
        event=_latest_event(db, alert),
        verdict_visible=True,
    )


@router.post(
    "/banks/{bank_id}/bi/alerts/{alert_id}/deactivation",
    response_model=BiAlertRead,
    operation_id="deactivateBiAlert",
)
def deactivate_bi_alert(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    alert_id: UUID,
    payload: BiNotificationDeactivateRequest,
    db: DbSession,
    access: BiRead,
) -> BiAlertRead:
    """Stop judging this figure, keeping the alert and its history. Owner only.

    Separate from deleting it, and separate from an edit, because stopping an
    alert is the act somebody takes in a hurry: it needs no figure authority, no
    valid threshold and no reachable measure — only the owner and a reason.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    alert = _owned_alert(db, access, alert_id)
    alert.is_active = False
    db.flush()
    _audit_alert(db, access, alert, event_type=EVENT_ALERT_DEACTIVATED, reason=payload.reason)
    db.commit()
    db.refresh(alert)
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    asked = _stored_alert_query(alert)
    allowed = asked is not None and authority.decide(asked)[0]
    measures = {measure.id: measure for measure in cat.measures()}
    stored = _stored_ids(alert.notify_user_ids)
    return _alert_read(
        alert,
        measure=measures.get(alert.measure_id),
        identities=_identities(db, access.ctx.organization_id, [alert.owner_user_id, *stored]),
        caller=access.principal_user_id,
        event=_latest_event(db, alert),
        verdict_visible=allowed,
    )


@router.delete(
    "/banks/{bank_id}/bi/alerts/{alert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBiAlert",
)
def delete_bi_alert(bank_id: str, alert_id: UUID, db: DbSession, access: BiRead) -> Response:
    """Delete an alert and every verdict it recorded. Owner only."""

    _ = bank_id
    _budget(db, access)
    alert = _owned_alert(db, access, alert_id)
    _audit_alert(db, access, alert, event_type=EVENT_ALERT_DELETED, reason=None)
    # Core deletes naming their tables: the BI plane guard resolves a write's
    # target statically, and the events are removed explicitly because no ORM
    # relationship is configured and the events carry the recorded figures.
    db.execute(
        delete(BiAlertEvent).where(
            BiAlertEvent.organization_id == access.ctx.organization_id,
            BiAlertEvent.alert_id == alert.id,
        )
    )
    db.execute(
        delete(BiAlert).where(
            BiAlert.id == alert.id,
            BiAlert.organization_id == access.ctx.organization_id,
            BiAlert.bank_id == access.bank.id,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/banks/{bank_id}/bi/alerts/{alert_id}/events",
    response_model=BiAlertEventListRead,
    operation_id="listBiAlertEvents",
)
def list_bi_alert_events(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    alert_id: UUID,
    db: DbSession,
    access: BiRead,
    limit: HistoryLimit = HISTORY_LIMIT_DEFAULT,
) -> BiAlertEventListRead:
    """What this alert has recorded, newest first.

    Every row states the figure it was judged on, so this read is refused unless
    the caller's own bindings cover the alert's figure. That includes the owner:
    an alert outlives a grant, and a verdict is the figure in all but name.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    alert = _load_alert(db, access, alert_id)
    authority = _Authority.build(db, access, cat, surface=ALERT_SURFACE)
    asked = _stored_alert_query(alert)
    if asked is None:
        raise _conflict(
            "bi_alert_unreadable",
            "This alert's conditions can no longer be read, so no verdict can be shown for "
            "it. Change it or delete it.",
        )
    allowed, denied, reason = authority.decide(asked)
    if not allowed:
        raise _denied(cat, denied, reason)
    measures = {measure.id: measure for measure in cat.measures()}
    measure = measures.get(alert.measure_id)
    rows = db.scalars(
        select(BiAlertEvent)
        .where(
            BiAlertEvent.organization_id == access.ctx.organization_id,
            BiAlertEvent.alert_id == alert.id,
        )
        .order_by(BiAlertEvent.as_of_date.desc(), BiAlertEvent.evaluated_at.desc())
        .limit(limit)
    )
    return BiAlertEventListRead(
        alert_id=alert.id,
        events=[
            BiAlertEventRead(
                id=event.id,
                alert_id=event.alert_id,
                as_of_date=event.as_of_date,
                state=_state(event.state),
                observed_value=event.observed_value,
                threshold_value=event.threshold_value,
                threshold_basis=_basis(event.threshold_basis),
                limit_source=event.limit_source,
                reason=event.reason,
                detail=_event_detail(event, measure),
                evaluated_at=event.evaluated_at,
            )
            for event in rows
        ],
    )


def _audit_alert(
    db: Session,
    access: BiReadAccess,
    alert: BiAlert,
    *,
    event_type: str,
    reason: str | None,
) -> None:
    """Who changed which alert, what it now says, and why they said so.

    The threshold is recorded as text rather than as a number so the trail is
    exact whatever a JSON encoder would do to a ``Decimal``, and the recipient
    COUNT is recorded rather than the list: the audit trail is read by people who
    are not the owner, and the owner-only rule on a distribution list holds there
    too.
    """

    audit.record_event(
        db,
        access.ctx,
        event_type=event_type,
        entity_type=ENTITY_ALERT,
        entity_id=alert.id,
        details={
            "bank_id": access.bank.id,
            "name": alert.name,
            "measure_id": alert.measure_id,
            "direction": alert.direction,
            "threshold_basis": alert.threshold_basis,
            "threshold": None if alert.threshold is None else str(alert.threshold),
            "filters": len(alert.filters),
            "notifies": len(alert.notify_user_ids),
            "is_active": alert.is_active,
            "reason": reason,
            "catalogue_version": CATALOGUE_VERSION,
        },
    )


# --- subscriptions -----------------------------------------------------------------------


def _stored_query(subscription: BiSubscription) -> BiQuery:
    """A stored question, re-validated on the way out.

    Re-validated rather than trusted, for ``manage_bi_content.py``'s reason: the
    row was written by this application, but a question that no longer satisfies
    the wire model — because a validator tightened, or because a row was written
    by hand — must refuse rather than reach a renderer as a shape nothing checked.
    """

    try:
        return BiQuery.model_validate(subscription.query)
    except ValidationError as exc:
        # A named refusal rather than a 500, and one its owner can act on:
        # ``DELETE`` never reads the stored question, so the report can always be
        # removed even when it can no longer be described.
        raise _conflict(
            "bi_subscription_query_unreadable",
            "This report's question can no longer be read, so it cannot be shown or sent. "
            "Delete it and write it again.",
        ) from exc


def _disclosure_of(cat: Catalogue, query: BiQuery) -> policy.ExportClass:
    """What this question's content IS, from the catalogue's own declarations.

    The one classifier the governed exports use, so a subscription and an
    interactive export cannot disagree about whether a report may be attached.
    An id the catalogue no longer knows is record-level, which is the refusing
    answer: nothing is attached and every recipient is sent a link.
    """

    try:
        return policy.classify(query_members(cat, query))
    except UnknownMember:
        return policy.RECORD_LEVEL


@router.get(
    "/banks/{bank_id}/bi/subscriptions",
    response_model=BiSubscriptionListRead,
    operation_id="listBiSubscriptions",
)
def list_bi_subscriptions(bank_id: str, db: DbSession, access: BiRead) -> BiSubscriptionListRead:
    """Every scheduled report of this institution this identity owns or receives.

    No figure and no delivery: a list names reports, not their contents, and each
    delivery is prepared under its own recipient's access when the run happens.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    time_zone = _time_zone_name(db, access.bank)
    rows = db.scalars(
        select(BiSubscription)
        .where(
            BiSubscription.organization_id == access.ctx.organization_id,
            BiSubscription.bank_id == access.bank.id,
        )
        .order_by(
            BiSubscription.is_active.desc(), BiSubscription.updated_at.desc(), BiSubscription.id
        )
    )
    reachable = [
        subscription
        for subscription in rows
        if subscription.owner_user_id == access.principal_user_id
        or _named(subscription.recipient_user_ids, access.principal_user_id)
    ]
    identities = _identities(
        db,
        access.ctx.organization_id,
        [
            user_id
            for subscription in reachable
            for user_id in (
                subscription.owner_user_id,
                *_stored_ids(subscription.recipient_user_ids),
            )
        ],
    )
    out: list[BiSubscriptionRead] = []
    for subscription in reachable:
        query = _stored_query(subscription)
        out.append(
            _subscription_read(
                subscription,
                query=query,
                disclosure=_disclosure_of(cat, query),
                time_zone=time_zone,
                identities=identities,
                caller=access.principal_user_id,
            )
        )
    return BiSubscriptionListRead(subscriptions=out)


@router.post(
    "/banks/{bank_id}/bi/subscriptions",
    response_model=BiSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBiSubscription",
)
def create_bi_subscription(
    bank_id: str, payload: BiSubscriptionUpsert, db: DbSession, access: BiRead
) -> BiSubscriptionRead:
    """Mail one report on a schedule. The caller becomes its owner.

    The author must hold every figure the report reads — a principal cannot
    instruct the platform to send a question they may not ask. What is STORED
    carries no trace of that decision: no binding id, no permission, no
    authorization version. Every delivery is authorized, compiled and rendered as
    its own recipient at send time, and a recipient the evaluator refuses is sent
    nothing at all.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    authority = _Authority.build(db, access, cat, surface=SUBSCRIPTION_SURFACE)
    disclosure = _disclosure_of(cat, payload.query)
    # A record-level report is never attached, so the author needs only VIEW for
    # it; an attachable one needs the full export sentence. The same rule the
    # delivery path applies per recipient, applied here to the author.
    for permission in policy.permissions_for(disclosure):
        allowed, denied, reason = authority.decide(payload.query, permission=permission)
        if not allowed:
            raise _denied(cat, denied, reason)
    _require_answerable_query(cat, payload.query)
    recipients = _resolve_recipients(
        db,
        access,
        user_ids=payload.recipient_user_ids,
        emails=payload.recipient_emails,
        what="scheduled report",
    )
    subscription = BiSubscription(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        name=payload.name,
        owner_user_id=access.principal_user_id,
        query=payload.query.model_dump(mode="json"),
        artifact_format=payload.artifact_format,
        cadence=payload.cadence,
        hour=payload.hour,
        minute=payload.minute,
        day_of_week=payload.day_of_week,
        day_of_month=payload.day_of_month,
        recipient_user_ids=[str(user_id) for user_id in recipients],
        is_active=payload.is_active,
    )
    db.add(subscription)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "bi_subscription_name_taken",
            "This institution already has a scheduled report with that name.",
        ) from exc
    _audit_subscription(
        db,
        access,
        subscription,
        event_type=EVENT_SUBSCRIPTION_CREATED,
        disclosure=disclosure,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(subscription)
    return _subscription_read(
        subscription,
        query=payload.query,
        disclosure=disclosure,
        time_zone=_time_zone_name(db, access.bank),
        identities=_identities(
            db, access.ctx.organization_id, [subscription.owner_user_id, *recipients]
        ),
        caller=access.principal_user_id,
    )


@router.get(
    "/banks/{bank_id}/bi/subscriptions/{subscription_id}",
    response_model=BiSubscriptionRead,
    operation_id="getBiSubscription",
)
def get_bi_subscription(
    bank_id: str, subscription_id: UUID, db: DbSession, access: BiRead
) -> BiSubscriptionRead:
    """One scheduled report, if this identity owns it or receives it."""

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    subscription = _load_subscription(db, access, subscription_id)
    query = _stored_query(subscription)
    stored = _stored_ids(subscription.recipient_user_ids)
    return _subscription_read(
        subscription,
        query=query,
        disclosure=_disclosure_of(cat, query),
        time_zone=_time_zone_name(db, access.bank),
        identities=_identities(
            db, access.ctx.organization_id, [subscription.owner_user_id, *stored]
        ),
        caller=access.principal_user_id,
    )


@router.put(
    "/banks/{bank_id}/bi/subscriptions/{subscription_id}",
    response_model=BiSubscriptionRead,
    operation_id="updateBiSubscription",
)
def update_bi_subscription(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    subscription_id: UUID,
    payload: BiSubscriptionUpsert,
    db: DbSession,
    access: BiRead,
) -> BiSubscriptionRead:
    """Replace a scheduled report's definition. Owner only."""

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    subscription = _owned_subscription(db, access, subscription_id)
    authority = _Authority.build(db, access, cat, surface=SUBSCRIPTION_SURFACE)
    disclosure = _disclosure_of(cat, payload.query)
    for permission in policy.permissions_for(disclosure):
        allowed, denied, reason = authority.decide(payload.query, permission=permission)
        if not allowed:
            raise _denied(cat, denied, reason)
    _require_answerable_query(cat, payload.query)
    recipients = _resolve_recipients(
        db,
        access,
        user_ids=payload.recipient_user_ids,
        emails=payload.recipient_emails,
        what="scheduled report",
    )
    subscription.name = payload.name
    subscription.query = payload.query.model_dump(mode="json")
    subscription.artifact_format = payload.artifact_format
    subscription.cadence = payload.cadence
    subscription.hour = payload.hour
    subscription.minute = payload.minute
    subscription.day_of_week = payload.day_of_week
    subscription.day_of_month = payload.day_of_month
    subscription.recipient_user_ids = [str(user_id) for user_id in recipients]
    subscription.is_active = payload.is_active
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "bi_subscription_name_taken",
            "This institution already has a scheduled report with that name.",
        ) from exc
    _audit_subscription(
        db,
        access,
        subscription,
        event_type=EVENT_SUBSCRIPTION_UPDATED,
        disclosure=disclosure,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(subscription)
    return _subscription_read(
        subscription,
        query=payload.query,
        disclosure=disclosure,
        time_zone=_time_zone_name(db, access.bank),
        identities=_identities(
            db, access.ctx.organization_id, [subscription.owner_user_id, *recipients]
        ),
        caller=access.principal_user_id,
    )


@router.post(
    "/banks/{bank_id}/bi/subscriptions/{subscription_id}/deactivation",
    response_model=BiSubscriptionRead,
    operation_id="deactivateBiSubscription",
)
def deactivate_bi_subscription(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    subscription_id: UUID,
    payload: BiNotificationDeactivateRequest,
    db: DbSession,
    access: BiRead,
) -> BiSubscriptionRead:
    """Stop sending this report, keeping it and its delivery history. Owner only.

    Needs no figure authority: stopping the platform from mailing something is
    the act somebody takes in a hurry, and an owner whose grant has been
    withdrawn is exactly who needs it to work.
    """

    _ = bank_id
    _budget(db, access)
    cat = catalogue()
    subscription = _owned_subscription(db, access, subscription_id)
    subscription.is_active = False
    db.flush()
    query = _stored_query(subscription)
    disclosure = _disclosure_of(cat, query)
    _audit_subscription(
        db,
        access,
        subscription,
        event_type=EVENT_SUBSCRIPTION_DEACTIVATED,
        disclosure=disclosure,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(subscription)
    stored = _stored_ids(subscription.recipient_user_ids)
    return _subscription_read(
        subscription,
        query=query,
        disclosure=disclosure,
        time_zone=_time_zone_name(db, access.bank),
        identities=_identities(
            db, access.ctx.organization_id, [subscription.owner_user_id, *stored]
        ),
        caller=access.principal_user_id,
    )


@router.delete(
    "/banks/{bank_id}/bi/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBiSubscription",
)
def delete_bi_subscription(
    bank_id: str, subscription_id: UUID, db: DbSession, access: BiRead
) -> Response:
    """Delete a scheduled report and its delivery history. Owner only."""

    _ = bank_id
    _budget(db, access)
    subscription = _owned_subscription(db, access, subscription_id)
    _audit_subscription(
        db,
        access,
        subscription,
        event_type=EVENT_SUBSCRIPTION_DELETED,
        disclosure=None,
        reason=None,
    )
    db.execute(
        delete(BiSubscriptionDelivery).where(
            BiSubscriptionDelivery.organization_id == access.ctx.organization_id,
            BiSubscriptionDelivery.subscription_id == subscription.id,
        )
    )
    db.execute(
        delete(BiSubscription).where(
            BiSubscription.id == subscription.id,
            BiSubscription.organization_id == access.ctx.organization_id,
            BiSubscription.bank_id == access.bank.id,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/banks/{bank_id}/bi/subscriptions/{subscription_id}/deliveries",
    response_model=BiSubscriptionDeliveryListRead,
    operation_id="listBiSubscriptionDeliveries",
)
def list_bi_subscription_deliveries(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    subscription_id: UUID,
    db: DbSession,
    access: BiRead,
    limit: HistoryLimit = HISTORY_LIMIT_DEFAULT,
) -> BiSubscriptionDeliveryListRead:
    """What each recipient was sent, newest run first. Owner only.

    Owner-only because it is a membership disclosure twice over: it names who is
    on the list, and it names which of them the evaluator refused. A refusal is
    reported as the honest outcome it is — nobody's access failed, a report
    simply was not that person's to receive.

    It carries no figure at all: a row count and a file size are facts about a
    file, not about the book, and the artifact itself never existed for a refused
    recipient.
    """

    _ = bank_id
    _budget(db, access)
    subscription = _owned_subscription(db, access, subscription_id)
    rows = list(
        db.scalars(
            select(BiSubscriptionDelivery)
            .where(
                BiSubscriptionDelivery.organization_id == access.ctx.organization_id,
                BiSubscriptionDelivery.subscription_id == subscription.id,
            )
            .order_by(
                BiSubscriptionDelivery.scheduled_for.desc(),
                BiSubscriptionDelivery.created_at.desc(),
            )
            .limit(limit)
        )
    )
    identities = _identities(
        db, access.ctx.organization_id, [delivery.recipient_user_id for delivery in rows]
    )
    return BiSubscriptionDeliveryListRead(
        subscription_id=subscription.id,
        deliveries=[_delivery_read(delivery, identities) for delivery in rows],
    )


def _audit_subscription(  # noqa: PLR0913 - one mutation and everything it states
    db: Session,
    access: BiReadAccess,
    subscription: BiSubscription,
    *,
    event_type: str,
    disclosure: policy.ExportClass | None,
    reason: str | None,
) -> None:
    """Who changed which scheduled report, what it now says, and why.

    The recipient COUNT rather than the list, for the reason ``_audit_alert``
    gives; and the disclosure class, because whether a report leaves the platform
    as a file or as a link is the fact an auditor is looking for.
    """

    audit.record_event(
        db,
        access.ctx,
        event_type=event_type,
        entity_type=ENTITY_SUBSCRIPTION,
        entity_id=subscription.id,
        details={
            "bank_id": access.bank.id,
            "name": subscription.name,
            "cadence": subscription.cadence,
            "hour": subscription.hour,
            "minute": subscription.minute,
            "day_of_week": subscription.day_of_week,
            "day_of_month": subscription.day_of_month,
            "artifact_format": subscription.artifact_format,
            "disclosure_class": disclosure,
            "recipients": len(subscription.recipient_user_ids),
            "is_active": subscription.is_active,
            "reason": reason,
            "catalogue_version": CATALOGUE_VERSION,
        },
    )


__all__ = [
    "DELIVERY_NOTE_ATTACHED",
    "DELIVERY_NOTE_LINK",
    "ENTITY_ALERT",
    "ENTITY_SUBSCRIPTION",
    "EVENT_ALERT_CREATED",
    "EVENT_ALERT_DEACTIVATED",
    "EVENT_ALERT_DELETED",
    "EVENT_ALERT_UPDATED",
    "EVENT_SUBSCRIPTION_CREATED",
    "EVENT_SUBSCRIPTION_DEACTIVATED",
    "EVENT_SUBSCRIPTION_DELETED",
    "EVENT_SUBSCRIPTION_UPDATED",
    "router",
]
