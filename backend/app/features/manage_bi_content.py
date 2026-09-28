"""Saved dashboards and calculated measures: the tenant-facing routes.

Mounted behind exactly the dependencies the BI read surface is mounted behind —
``BANK_ROUTE_DEPENDENCIES`` first, so a sibling tenant's ``BK-*`` is
``404 Bank not found.`` before anything else runs, then ``require_bi_enabled``,
so a deployment without BI answers 404 for its own banks too — and every route
resolves ``read_bi.require_bi_read``, which admits only an interactive tenant
human holding a current ``authv``: no machine key, no impersonated operator
(D-026), and no scalar-role check anywhere.

The order each route consults its guards in is the security property, and it is
the read surface's order with one step added for a document that has an owner:

1. the institution, the flag, the principal (the dependencies above);
2. the read budget, counted over ``bi_query_log`` like every other BI read;
3. **reachability**, which answers 404 — a dashboard this identity may not open
   does not exist for them, so a private dashboard cannot be enumerated by id;
4. **ownership**, which answers 403 — reachable, but only its owner may change
   it. Not an account administrator and not an Org Owner;
5. **authorization, per widget, for the reader** — never for the owner. A shared
   dashboard is a set of questions; the answers are always the reader's own.

What this module does NOT do is serve a figure. A dashboard read returns widget
specifications — the same resolved shape the certified packs return
(``BiPackWidgetRead``) — and the client then asks ``/bi/query`` for each granted
widget, where the same evaluator decides again. So a refusal here costs a reader
nothing but the widget, and a bug here cannot leak a number: there is none in the
response.

**Every request leaves exactly one row, and the refusals are the point.** The
read surface's rule is that ``packs`` and ``insights`` record one row per request
*including the refusals, because the budget is counted over these rows and a
probe loop must not be free* (``read_bi``'s module docstring). This module used
to record one row on one route — the successful dashboard read — and nothing on
any of the roughly two dozen paths that refuse. That made enumeration INSIDE a
tenant unmetered: the three refusals are distinguishable (404 for a document this
identity may not open, 403 for one they may open but do not own, 409 for a state
clash), so the responses answer "which ids exist, and which of them can I read",
and the rate limit that was supposed to bound the asking never counted a single
ask. :func:`_metered` now wraps every route body, so the row is a property of the
shape: a route cannot return or raise without writing one. The single exception is
the 429 itself — see :func:`_budget` for why an over-budget principal is not
charged again.

**A served dashboard whose every widget was refused is still an ``allowed``
row** (audit A9-09). ``decision`` is the authorization decision for the REQUEST
and means "was this read served"; the dashboard WAS served — its canvas, its
geometry, its count and its message — and the fields inside it that the reader
could not have are in ``denied_members``, which is the certified-pack convention
verbatim (``read_bi._pack_record``: a dashboard is a MIXED read by construction).
All-refused is the limit case of mixed, not a different kind of event: ``packs``
rows for a certified dashboard behave identically, and flipping this one would
make ``denied`` mean two things — "the request was refused" and "the request
succeeded and disclosed nothing" — so a reviewer counting refusals could no
longer tell a probe loop from a reader opening a dashboard they hold nothing on.
The discriminator for the second is already in the row and needs no new value:
``member_ids`` empty with ``denied_members`` non-empty.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.domain.bi.catalogue import CATALOGUE_VERSION, Catalogue, catalogue
from app.features.read_bi import PACK_MESSAGES, BiRead, BiReadAccess
from app.models import User
from app.models.bi_content import BiDashboard, BiDashboardVersion, BiMeasure
from app.schemas.authorization import SodDecisionRead, SodPolicyFindingRead
from app.schemas.bi import BiLayoutItem, BiPackWidget, BiPackWidgetRead
from app.schemas.bi_content import (
    BiCertificationBadge,
    BiDashboardCreateRequest,
    BiDashboardListRead,
    BiDashboardRead,
    BiDashboardShareListRead,
    BiDashboardShareRead,
    BiDashboardShareRequest,
    BiDashboardSpec,
    BiDashboardSummaryRead,
    BiDashboardUpdateRequest,
    BiDashboardVersionListRead,
    BiDashboardVersionRead,
    BiDashboardVisibility,
    BiMeasureCreateRequest,
    BiMeasureDecisionRead,
    BiMeasureDecisionRequest,
    BiMeasureFavourableDirection,
    BiMeasureListRead,
    BiMeasureProposalRequest,
    BiMeasureRead,
    BiMeasureState,
    BiMeasureUpdateRequest,
    BiMeasureValidationRead,
    BiMeasureValidationRequest,
    BiMeasureValueType,
)
from app.services import audit, grant_administration
from app.services.bi import content, query_log
from app.services.bi.authorization import UnknownMember

router = APIRouter(tags=["bi"])

#: ``audit_events.event_type`` for every mutation this module makes. A dashboard
#: is a document and a measure is a governed formula; both leave a trail naming
#: the actor, the object and what changed.
EVENT_DASHBOARD_CREATED = "bi.dashboard.created"
EVENT_DASHBOARD_UPDATED = "bi.dashboard.updated"
EVENT_DASHBOARD_DELETED = "bi.dashboard.deleted"
EVENT_DASHBOARD_SHARED = "bi.dashboard.shares_set"
EVENT_MEASURE_CREATED = "bi.measure.created"
EVENT_MEASURE_UPDATED = "bi.measure.updated"
EVENT_MEASURE_DELETED = "bi.measure.deleted"
EVENT_MEASURE_PROPOSED = "bi.measure.promotion_proposed"
EVENT_MEASURE_CERTIFIED = "bi.measure.certified"
EVENT_MEASURE_REJECTED = "bi.measure.promotion_rejected"

ENTITY_DASHBOARD = "bi_dashboard"
ENTITY_MEASURE = "bi_measure"

AsOf = Annotated[date, Query(description="The reporting date to resolve the dashboard for.")]


# --- metering ----------------------------------------------------------------------------
#
# The budget is COUNTED over ``bi_query_log`` rows, so a request that leaves no row
# costs its principal nothing. Every route here therefore runs inside
# :func:`_metered`, which writes exactly one row on the way out whatever the way
# out was — 200, 204, 404, 403, 409 or 422. Nothing in this file may write that row
# itself: one writer is what makes "one row per request" a property of the shape
# rather than of twenty-odd correct call sites.


def _budget(db: Session, access: BiReadAccess) -> None:
    """Refuse a principal that has already spent the window, and record NOTHING.

    The 429 is the one outcome that leaves no row, and it is deliberate: the
    budget is the count of rows in the window, so charging a refusal-for-being-
    over-budget would let a principal deepen their own deficit without limit and
    push their recovery out indefinitely. ``read_bi._require_budget`` is called
    before every ``_append`` on that surface for the same reason; this is the same
    rule stated in one place instead of by call order.
    """

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


#: The separator and the domain label the question material is joined with. The
#: label is what stops a row of this surface from colliding with a certified-pack
#: row of the same ``packs`` surface: both hash a date and an id, and two
#: different questions must not produce one digest.
_MATERIAL_SEPARATOR = "\x1f"
_QUESTION_DOMAIN = "bi_content"


def _question_digest(route: str, *parts: object) -> str:
    """A one-way digest of the question one request asked.

    Deliberately the same shape as ``read_bi._surface_digest``
    (``app/features/read_bi.py``, line 1639): a separator-joined material string
    and one SHA-256, so two identical questions hash alike and nothing in the
    column can be read back as a value. It is duplicated rather than imported
    because that helper is private to the read surface; merging the two is a
    refactor for whoever owns both files.

    **The requested id is never the only material.** A bare UUID's digest is the
    same 64 characters in every context, so one appearing in two rows would link
    them across surfaces and across tenants; the route label and the rest of the
    request are what stop that. What this does NOT do is hide the id from a reader
    who can already enumerate this institution's dashboard and measure ids — a
    digest over an enumerable space is a commitment, not a secret, and hiding it
    would need a keyed HMAC under a deployment pepper. That is the honest residue,
    and it is the right trade here: the enumerable ids live in ``bi_dashboards``
    and ``bi_measures`` in the same database as this log and under the same
    tenant policy, and the one thing the digest lets a reviewer do — see that a
    principal asked for an object they were refused, over and over — is exactly
    why the row is written at all.
    """

    material = _MATERIAL_SEPARATOR.join(
        (_QUESTION_DOMAIN, route, *("" if part is None else str(part) for part in parts))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_digest(payload: BaseModel) -> str:
    """The request body as material for :func:`_question_digest`.

    A validated model's own JSON, which is field-ordered by the model, so two
    equal requests produce one digest — ``query_log.query_digest`` says the same
    thing about a ``BiQuery``. A body is a question here: two saves of two
    different canvases are two questions, and a column that could not tell them
    apart would not mean what it says.
    """

    return hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Meter:
    """The one ``bi_query_log`` row a request will leave, as it becomes known.

    Only the members are open to the route: a request may discover that it served
    some figures and was refused others, and that pair is the whole reason an
    operator reads this table. Everything else about the row is decided by
    :func:`_metered`, including the decision — a route cannot mark its own refusal
    ``allowed`` by forgetting something.
    """

    query_hash: str
    served: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()

    def members(self, *, served: Sequence[str] = (), denied: Sequence[str] = ()) -> None:
        """Name the catalogue members this request served, and those it refused."""

        self.served = tuple(served)
        self.denied = tuple(denied)


def _refused_members(exc: BaseException) -> tuple[str, ...]:
    """The members a refusal already named to its caller, for the row.

    :func:`_denied` is the only refusal of this surface that names any, and it
    puts them in the response envelope. Reading them back out of it is what keeps
    the row and the answer in agreement: a second channel could disagree, and the
    one that a reviewer would then trust is the one nobody checked.
    """

    if not isinstance(exc, HTTPException) or not isinstance(exc.detail, dict):
        return ()
    named = exc.detail.get("denied_members")
    if not isinstance(named, list):
        return ()
    return tuple(str(member_id) for member_id in named)


def _row(access: BiReadAccess, meter: _Meter, *, decision: str) -> query_log.QueryRecord:
    """One row of this surface.

    ``surface`` is ``content.DASHBOARD_SURFACE`` for the measure routes as well as
    the dashboard ones: it is the single surface every authorization decision in
    this module is already made under, so the log and the evaluator name the same
    thing. ``row_count`` stays NULL because nothing here serves a figure — a
    dashboard read returns widget specifications and the client then asks
    ``/bi/query`` for each one, which writes its own row.
    """

    return query_log.QueryRecord(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        surface=content.DASHBOARD_SURFACE,
        query_hash=meter.query_hash,
        decision=decision,
        catalogue_version=CATALOGUE_VERSION,
        member_ids=meter.served,
        denied_members=meter.denied,
    )


def _append(db: Session, record: query_log.QueryRecord) -> None:
    """Record one decision and commit it. Never guarded.

    ``read_bi._append`` says the same thing and for the same reason: a read that
    cannot be recorded is not served, and the caller gets the failure rather than
    the data.
    """

    query_log.record(db, record)
    db.commit()


@contextmanager
def _metered(db: Session, access: BiReadAccess, route: str, *question: object) -> Iterator[_Meter]:
    """Meter one request, and leave exactly one ``bi_query_log`` row behind it.

    The budget is spent by ROWS, so a request that raised before writing one was
    free — and the refusals are precisely the requests a probe loop makes. Every
    route of this module runs inside this block for that reason: a dashboard id
    this identity may not open is 404, one they may open but do not own is 403 on a
    mutation, and a state clash is 409, so the responses tell an attacker which
    ids exist and which of them they can read. Metering does not remove that
    difference; it bounds how many times it can be asked.

    **The row survives the raise, and nothing else does.** A refusal can be raised
    with a mutation already staged — ``content.create_dashboard`` flushes before it
    refuses a duplicate — so the session is rolled back BEFORE the row is written:
    the commit that lands the meter must never carry a half-applied write, and a
    row that the raise rolled back would meter nothing.

    The decision is this function's alone. ``allowed`` means the request was
    SERVED, which is what ``bi_query_log.decision`` means everywhere (it is the
    authorization decision, never a description of the HTTP status); the members a
    served request was refused ride along in ``denied_members``, exactly as they do
    on a certified-pack row.
    """

    _budget(db, access)
    meter = _Meter(query_hash=_question_digest(route, *question))
    try:
        yield meter
    except BaseException as exc:
        db.rollback()
        meter.members(denied=_refused_members(exc) or meter.denied)
        _append(db, _row(access, meter, decision=query_log.DECISION_DENIED))
        raise
    _append(db, _row(access, meter, decision=query_log.DECISION_ALLOWED))


# --- refusals ----------------------------------------------------------------------------


def _denied(cat: Catalogue, exc: content.MembersDenied) -> HTTPException:
    """403 naming every refused member, in the read surface's own envelope.

    The same ``error_code``, message and fields ``/bi/query`` refuses with, so a
    client has one handler for "your access does not cover this" wherever it
    happens. The label is catalogue metadata, not tenant data, and naming it is
    what lets the message read as production copy rather than as a wire key.
    """

    labels: list[str] = []
    for member_id in exc.denied_members:
        try:
            labels.append(cat.member(member_id).label)
        except KeyError:
            labels.append(member_id)
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "bi_authorization_denied",
            "message": (
                "Your access does not cover every field this view needs. "
                "An Org Owner can grant the fields listed here."
            ),
            "denied_members": list(exc.denied_members),
            "denied_member_labels": labels,
            "reason": exc.reason,
        },
    )


def _not_found(exc: content.BiContentError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error_code": "bi_content_not_found", "message": str(exc)},
    )


def _forbidden(exc: content.NotTheOwner) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error_code": "bi_content_owner_only", "message": str(exc)},
    )


def _unprocessable(exc: content.ExpressionRefused) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error_code": "bi_measure_expression_refused",
            "message": str(exc),
            "position": exc.position,
        },
    )


def _conflict(code: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": code, "message": str(exc)},
    )


def _sod_blocked(exc: grant_administration.SodPolicyBlocked) -> HTTPException:
    """409 carrying the separation-of-duties verdict, in the platform's own shape."""

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "bi_measure_promotion_refused",
            "message": str(exc),
            "sod_decision": _sod_read(exc.decision).model_dump(mode="json"),
        },
    )


def _sod_read(decision: grant_administration.SodDecision) -> SodDecisionRead:
    return SodDecisionRead(
        outcome=decision.outcome.value,
        findings=[
            SodPolicyFindingRead(code=finding.code, message=finding.message)
            for finding in decision.findings
        ],
    )


# --- read models -------------------------------------------------------------------------


def _identities(db: Session, organization_id: str, user_ids: Sequence[UUID]) -> Mapping[UUID, User]:
    """The named identities of THIS tenant, for display names only."""

    wanted = set(user_ids)
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


def _stored_spec(version: BiDashboardVersion) -> BiDashboardSpec:
    """A stored canvas, re-validated on the way out.

    Re-validated rather than trusted: the row was written by this application,
    but a canvas that no longer satisfies the widget model — because a validator
    tightened, or because a row was written by hand — must refuse rather than
    reach a renderer as a shape nothing checked.
    """

    return BiDashboardSpec.model_validate(version.spec)


def _summary(
    dashboard: BiDashboard,
    *,
    widget_count: int,
    identities: Mapping[UUID, User],
    caller: UUID,
) -> BiDashboardSummaryRead:
    return BiDashboardSummaryRead(
        id=dashboard.id,
        title=dashboard.title,
        description=dashboard.description,
        owner_user_id=dashboard.owner_user_id,
        owner_display_name=_display_name(identities, dashboard.owner_user_id),
        owned_by_caller=dashboard.owner_user_id == caller,
        visibility=_visibility(dashboard.visibility),
        visibility_role=dashboard.visibility_role,
        badge=_badge(dashboard.badge),
        version=dashboard.current_version,
        widget_count=widget_count,
        source_pack=dashboard.source_pack,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


def _visibility(value: str) -> BiDashboardVisibility:
    """The stored value as its wire literal; anything else is a broken row."""

    if value not in ("private", "users", "role", "org"):  # pragma: no cover - CHECK constrained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This dashboard's sharing setting could not be read.",
        )
    return value


def _badge(value: str) -> BiCertificationBadge:
    if value not in ("platform_certified", "bank_certified", "personal"):  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This dashboard's certification could not be read.",
        )
    return value


def _widget_read(
    resolved: content.ResolvedWidget, layout_by_id: Mapping[str, BiLayoutItem]
) -> BiPackWidgetRead:
    """One widget as its reader gets it.

    A refused widget is built by NOT PASSING anything but its id and its
    geometry. That is deliberate and it is the whole protection: there is no
    branch here that could forget to clear a title, because a restricted widget
    is constructed from two fields and the model's remaining fields default to
    absent.
    """

    widget: BiPackWidget = resolved.widget
    layout = layout_by_id[widget.id]
    if not resolved.granted:
        return BiPackWidgetRead(id=widget.id, layout=layout, access="restricted")
    return BiPackWidgetRead(
        id=widget.id,
        layout=layout,
        access="granted",
        kind=widget.kind,
        title=widget.title,
        caption=widget.caption,
        query=resolved.query,
        panel=widget.panel,
        display=widget.display,
        needs_data=widget.needs_data,
        pending_capability=widget.pending_capability,
    )


def _dashboard_read(  # noqa: PLR0913 - one document, its reader, its date and its canvas
    dashboard: BiDashboard,
    spec: BiDashboardSpec,
    canvas: content.ResolvedCanvas,
    *,
    as_of: date,
    identities: Mapping[UUID, User],
    caller: UUID,
) -> BiDashboardRead:
    layout_by_id: Mapping[str, BiLayoutItem] = {item.i: item for item in spec.layout}
    widgets = [_widget_read(resolved, layout_by_id) for resolved in canvas.widgets]
    restricted = canvas.restricted_widgets
    readable = canvas.readable_widgets
    if readable and restricted == readable:
        message = PACK_MESSAGES["restricted"]
    elif restricted:
        message = PACK_MESSAGES["partial"]
    else:
        message = PACK_MESSAGES["granted"]
    return BiDashboardRead(
        id=dashboard.id,
        title=dashboard.title,
        description=dashboard.description,
        owner_user_id=dashboard.owner_user_id,
        owner_display_name=_display_name(identities, dashboard.owner_user_id),
        owned_by_caller=dashboard.owner_user_id == caller,
        visibility=_visibility(dashboard.visibility),
        visibility_role=dashboard.visibility_role,
        badge=_badge(dashboard.badge),
        version=dashboard.current_version,
        source_pack=dashboard.source_pack,
        as_of=as_of,
        access=canvas.access,
        message=message,
        widgets=widgets,
        restricted_widgets=restricted,
        readable_widgets=readable,
        catalogue_version=CATALOGUE_VERSION,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


def _measure_read(
    measure: BiMeasure,
    *,
    cat: Catalogue,
    identities: Mapping[UUID, User],
    caller: UUID,
) -> BiMeasureRead:
    """One measure for a caller who has already been shown to be allowed to read it."""

    members = list(measure.referenced_members)
    labels: list[str] = []
    for member_id in members:
        try:
            labels.append(cat.member(member_id).label)
        except KeyError:
            labels.append(member_id)
    return BiMeasureRead(
        id=measure.id,
        measure_key=measure.measure_key,
        label=measure.label,
        description=measure.description,
        expression=measure.expression,
        referenced_members=members,
        referenced_member_labels=labels,
        value_type=_value_type(measure.value_type),
        favourable_direction=_direction(measure.favourable_direction),
        state=_state(measure.state),
        badge=_badge(content.badge_for(measure)),
        owner_user_id=measure.owner_user_id,
        owner_display_name=_display_name(identities, measure.owner_user_id),
        owned_by_caller=measure.owner_user_id == caller,
        created_at=measure.created_at,
        updated_at=measure.updated_at,
        proposed_by_user_id=measure.proposed_by_user_id,
        proposed_by_display_name=_display_name(identities, measure.proposed_by_user_id),
        proposed_at=measure.proposed_at,
        proposal_reason=measure.proposal_reason,
        approved_by_user_id=measure.approved_by_user_id,
        approved_by_display_name=_display_name(identities, measure.approved_by_user_id),
        approved_at=measure.approved_at,
        approval_reason=measure.approval_reason,
        approved_expression=measure.approved_expression,
        # A hint for the review queue, never a licence: the route decides again,
        # and the database refuses a self-approval whatever a client believes.
        awaiting_caller_decision=(
            measure.state == "proposed" and measure.proposed_by_user_id != caller
        ),
    )


def _value_type(value: str) -> BiMeasureValueType:
    if value not in ("amount", "pct", "fraction", "index", "duration_years", "count"):
        raise HTTPException(  # pragma: no cover - CHECK constrained
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This measure's unit could not be read.",
        )
    return value


def _direction(value: str) -> BiMeasureFavourableDirection:
    if value not in ("higher_better", "lower_better", "magnitude_lower_better", "neutral"):
        raise HTTPException(  # pragma: no cover - CHECK constrained
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This measure's direction could not be read.",
        )
    return value


def _state(value: str) -> BiMeasureState:
    if value not in ("personal", "proposed", "bank_certified"):
        raise HTTPException(  # pragma: no cover - CHECK constrained
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This measure's review state could not be read.",
        )
    return value


# --- dashboards --------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/bi/dashboards",
    response_model=BiDashboardListRead,
    operation_id="listBiDashboards",
)
def list_bi_dashboards(bank_id: str, db: DbSession, access: BiRead) -> BiDashboardListRead:
    """Every saved dashboard of this institution this identity may open.

    No widget and no member id: a list is read by everyone a dashboard reaches,
    and the figures it names are only disclosed once they have been authorized —
    which happens when the dashboard is opened.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.list"):
        dashboards = content.reachable_dashboards(
            db,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            viewer_id=access.principal_user_id,
        )
        identities = _identities(
            db,
            access.ctx.organization_id,
            [dashboard.owner_user_id for dashboard in dashboards],
        )
        live = content.current_versions(db, dashboards)
        summaries = [
            _summary(
                dashboard,
                widget_count=_widget_count(live.get(dashboard.id)),
                identities=identities,
                caller=access.principal_user_id,
            )
            for dashboard in dashboards
        ]
        return BiDashboardListRead(dashboards=summaries)


def _widget_count(version: BiDashboardVersion | None) -> int:
    """How many widgets the live canvas holds, or zero when it cannot be read.

    A dashboard whose pointer names no row is a broken invariant; the LIST refuses
    to fail over one, because the remedy is to open (or delete) that dashboard and
    a list that 500s makes both impossible. Opening it raises.
    """

    return 0 if version is None else len(_stored_spec(version).widgets)


@router.post(
    "/banks/{bank_id}/bi/dashboards",
    response_model=BiDashboardSummaryRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBiDashboard",
)
def create_bi_dashboard(
    bank_id: str, payload: BiDashboardCreateRequest, db: DbSession, access: BiRead
) -> BiDashboardSummaryRead:
    """Save a dashboard: an authored canvas, or a copy of a certified pack.

    The author must hold every figure the canvas reads. Deny-by-default applied to
    writing as well as reading: a principal cannot persist a question they may not
    ask, which also keeps the refusal marker a property of SHARING rather than
    something an author can produce for themselves.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.create", _payload_digest(payload)):
        cat = catalogue()
        if payload.from_pack is not None:
            try:
                spec, pack_description = content.spec_from_pack(payload.from_pack)
            except content.PackNotAvailable as exc:
                raise _not_found(exc) from exc
            # The caller names their own copy; the pack's description stands in when
            # they said nothing, because a copy with no description is harder to tell
            # apart from the six others on the list than one carrying the original's.
            title, description = payload.title, payload.description or pack_description
        else:
            assert payload.spec is not None  # noqa: S101 - the request model refuses neither
            spec, title, description = payload.spec, payload.title, payload.description
        _authorized_canvas(db, access, cat, spec)
        try:
            dashboard, version = content.create_dashboard(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                owner_user_id=access.principal_user_id,
                title=title,
                description=description,
                visibility=payload.visibility,
                visibility_role=payload.visibility_role,
                spec=spec,
                source_pack=payload.from_pack,
            )
        except content.BiContentError as exc:
            raise _conflict("bi_dashboard_refused", exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_DASHBOARD_CREATED,
            entity_type=ENTITY_DASHBOARD,
            entity_id=dashboard.id,
            details={
                "bank_id": access.bank.id,
                "title": title,
                "visibility": payload.visibility,
                "visibility_role": payload.visibility_role,
                "source_pack": payload.from_pack,
                "widgets": len(spec.widgets),
                "spec_digest": version.spec_digest,
                "catalogue_version": CATALOGUE_VERSION,
            },
        )
        db.commit()
        db.refresh(dashboard)
        identities = _identities(db, access.ctx.organization_id, [dashboard.owner_user_id])
        return _summary(
            dashboard,
            widget_count=len(spec.widgets),
            identities=identities,
            caller=access.principal_user_id,
        )


@router.get(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}",
    response_model=BiDashboardRead,
    operation_id="getBiDashboard",
)
def get_bi_dashboard(  # noqa: PLR0913 - FastAPI injects db/access, the rest is the request
    bank_id: str,
    dashboard_id: UUID,
    db: DbSession,
    access: BiRead,
    as_of: AsOf,
) -> BiDashboardRead:
    """One saved dashboard, resolved for THIS reader and this reporting date.

    Every widget is authorized for the caller through the same ``authorize_query``
    decision ``/bi/query`` makes. The owner's authority is not consulted: a
    dashboard shared by a broadly-authorized principal to a narrow one resolves to
    refusal markers for the narrow reader, and the refusal carries its id and its
    place on the canvas and nothing else.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.read", dashboard_id, as_of) as meter:
        cat = catalogue()
        try:
            dashboard = content.load_dashboard(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                dashboard_id=dashboard_id,
                viewer_id=access.principal_user_id,
            )
        except content.DashboardNotFound as exc:
            raise _not_found(exc) from exc
        version = content.current_version(db, dashboard)
        spec = _stored_spec(version)
        authority = content.ViewerAuthority(
            db=db,
            ctx=access.ctx,
            bank=access.bank,
            cat=cat,
            surface=content.DASHBOARD_SURFACE,
        )
        canvas = content.resolve_widgets(spec, as_of=as_of, authority=authority)
        # The members this reader was refused go in the row and NOT in the response:
        # that is where an operator sees which grant is missing. The row itself is
        # written by ``_metered`` on the way out, so there is exactly one of it
        # whatever this request turned out to be.
        meter.members(served=canvas.served_members, denied=canvas.denied_members)
        identities = _identities(db, access.ctx.organization_id, [dashboard.owner_user_id])
        return _dashboard_read(
            dashboard,
            spec,
            canvas,
            as_of=as_of,
            identities=identities,
            caller=access.principal_user_id,
        )


@router.put(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}",
    response_model=BiDashboardSummaryRead,
    operation_id="updateBiDashboard",
)
def update_bi_dashboard(  # noqa: PLR0913 - FastAPI injects db/access, the rest is the request
    bank_id: str,
    dashboard_id: UUID,
    payload: BiDashboardUpdateRequest,
    db: DbSession,
    access: BiRead,
) -> BiDashboardSummaryRead:
    """Replace the canvas, appending a version. Owner only.

    The history is append-only, so an edit ADDS the new layout rather than
    overwriting the old one: a dashboard's history cannot be rewritten, only
    extended.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.update", dashboard_id, _payload_digest(payload)):
        cat = catalogue()
        try:
            dashboard = content.load_dashboard(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                dashboard_id=dashboard_id,
                viewer_id=access.principal_user_id,
            )
        except content.DashboardNotFound as exc:
            raise _not_found(exc) from exc
        try:
            content.require_owner(dashboard, actor_user_id=access.principal_user_id)
        except content.NotTheOwner as exc:
            raise _forbidden(exc) from exc
        _authorized_canvas(db, access, cat, payload.spec)
        try:
            version = content.update_dashboard(
                db,
                dashboard,
                actor_user_id=access.principal_user_id,
                title=payload.title,
                description=payload.description,
                visibility=payload.visibility,
                visibility_role=payload.visibility_role,
                spec=payload.spec,
                change_note=payload.change_note,
            )
        except content.NotTheOwner as exc:
            raise _forbidden(exc) from exc
        except content.BiContentError as exc:
            raise _conflict("bi_dashboard_refused", exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_DASHBOARD_UPDATED,
            entity_type=ENTITY_DASHBOARD,
            entity_id=dashboard.id,
            details={
                "bank_id": access.bank.id,
                "version": version.version,
                "title": payload.title,
                "visibility": payload.visibility,
                "visibility_role": payload.visibility_role,
                "change_note": payload.change_note,
                "widgets": len(payload.spec.widgets),
                "spec_digest": version.spec_digest,
                "catalogue_version": CATALOGUE_VERSION,
            },
        )
        db.commit()
        db.refresh(dashboard)
        identities = _identities(db, access.ctx.organization_id, [dashboard.owner_user_id])
        return _summary(
            dashboard,
            widget_count=len(payload.spec.widgets),
            identities=identities,
            caller=access.principal_user_id,
        )


@router.delete(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBiDashboard",
)
def delete_bi_dashboard(
    bank_id: str, dashboard_id: UUID, db: DbSession, access: BiRead
) -> Response:
    """Delete a dashboard, its history and its shares. Owner only."""

    _ = bank_id
    with _metered(db, access, "dashboards.delete", dashboard_id):
        try:
            dashboard = content.load_dashboard(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                dashboard_id=dashboard_id,
                viewer_id=access.principal_user_id,
            )
        except content.DashboardNotFound as exc:
            raise _not_found(exc) from exc
        try:
            content.require_owner(dashboard, actor_user_id=access.principal_user_id)
        except content.NotTheOwner as exc:
            raise _forbidden(exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_DASHBOARD_DELETED,
            entity_type=ENTITY_DASHBOARD,
            entity_id=dashboard.id,
            details={
                "bank_id": access.bank.id,
                "title": dashboard.title,
                "versions": dashboard.current_version,
            },
        )
        # A Core delete naming its table: the plane guard resolves a write's TARGET
        # statically, and the database's own ``ON DELETE CASCADE`` takes the versions
        # and the shares with it (no ORM relationship is configured, so this is what
        # ``db.delete(dashboard)`` did too).
        db.execute(
            delete(BiDashboard).where(
                BiDashboard.id == dashboard.id,
                BiDashboard.organization_id == access.ctx.organization_id,
                BiDashboard.bank_id == access.bank.id,
            )
        )
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}/versions",
    response_model=BiDashboardVersionListRead,
    operation_id="listBiDashboardVersions",
)
def list_bi_dashboard_versions(
    bank_id: str, dashboard_id: UUID, db: DbSession, access: BiRead
) -> BiDashboardVersionListRead:
    """A dashboard's history: who changed it, when, and what they said about it.

    Metadata only. An old canvas is not served here: a reader's access is decided
    against the CURRENT canvas, and showing an earlier one safely would mean
    authorizing every widget of every version.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.versions", dashboard_id):
        try:
            dashboard = content.load_dashboard(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                dashboard_id=dashboard_id,
                viewer_id=access.principal_user_id,
            )
        except content.DashboardNotFound as exc:
            raise _not_found(exc) from exc
        rows = content.versions(db, dashboard)
        identities = _identities(
            db, access.ctx.organization_id, [row.created_by_user_id for row in rows]
        )
        return BiDashboardVersionListRead(
            dashboard_id=dashboard.id,
            versions=[
                BiDashboardVersionRead(
                    version=row.version,
                    title=row.title,
                    description=row.description,
                    change_note=row.change_note,
                    widget_count=len(_stored_spec(row).widgets),
                    spec_digest=row.spec_digest,
                    created_by_user_id=row.created_by_user_id,
                    created_by_display_name=_display_name(identities, row.created_by_user_id),
                    created_at=row.created_at,
                    current=row.version == dashboard.current_version,
                )
                for row in rows
            ],
        )


@router.get(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}/shares",
    response_model=BiDashboardShareListRead,
    operation_id="listBiDashboardShares",
)
def list_bi_dashboard_shares(
    bank_id: str, dashboard_id: UUID, db: DbSession, access: BiRead
) -> BiDashboardShareListRead:
    """Who this dashboard is shared with. Owner only.

    Owner-only because the list is a membership disclosure: telling one reader who
    else can open a document is not part of being able to open it.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.shares.read", dashboard_id):
        dashboard = _owned_dashboard(db, access, dashboard_id)
        return BiDashboardShareListRead(
            shares=[
                BiDashboardShareRead(
                    user_id=user.id,
                    display_name=user.display_name,
                    email=user.email,
                    shared_at=share.created_at,
                )
                for share, user in content.shares(db, dashboard)
            ]
        )


@router.put(
    "/banks/{bank_id}/bi/dashboards/{dashboard_id}/shares",
    response_model=BiDashboardShareListRead,
    operation_id="setBiDashboardShares",
)
def set_bi_dashboard_shares(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    dashboard_id: UUID,
    payload: BiDashboardShareRequest,
    db: DbSession,
    access: BiRead,
) -> BiDashboardShareListRead:
    """Replace the set of identities this dashboard reaches. Owner only.

    Reachability, not authority: each of these identities is re-authorized widget
    by widget every time they open it, so naming someone here can never show them
    a figure their own bindings do not cover.
    """

    _ = bank_id
    with _metered(db, access, "dashboards.shares.set", dashboard_id, _payload_digest(payload)):
        dashboard = _owned_dashboard(db, access, dashboard_id)
        try:
            added, removed = content.set_shares(
                db,
                dashboard,
                actor_user_id=access.principal_user_id,
                user_ids=payload.user_ids,
            )
        except content.NotTheOwner as exc:
            raise _forbidden(exc) from exc
        except content.BiContentError as exc:
            raise _conflict("bi_dashboard_share_refused", exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_DASHBOARD_SHARED,
            entity_type=ENTITY_DASHBOARD,
            entity_id=dashboard.id,
            details={
                "bank_id": access.bank.id,
                "added": [str(user_id) for user_id in added],
                "removed": [str(user_id) for user_id in removed],
                "reaches": len(payload.user_ids),
            },
        )
        db.commit()
        return BiDashboardShareListRead(
            shares=[
                BiDashboardShareRead(
                    user_id=user.id,
                    display_name=user.display_name,
                    email=user.email,
                    shared_at=share.created_at,
                )
                for share, user in content.shares(db, dashboard)
            ]
        )


def _authorized_canvas(
    db: Session, access: BiReadAccess, cat: Catalogue, spec: BiDashboardSpec
) -> None:
    """The access decision for a whole canvas, then the shape rules.

    ``UnknownMember`` has to be caught here. The authorization walk resolves every
    id, and a PERSONAL calculated measure is deliberately not resolvable — only
    certified ones load, so a draft cannot be named to anyone. That is right, and
    it meant an author saving a canvas that referenced their OWN uncertified
    formula got a **500** rather than a refusal: the exception escaped the route
    entirely (audit A9-04's neighbour, found while testing its fix).

    The shape rules are asked FIRST in that case, because they carry the honest
    answer — "certify it before putting it on a saved dashboard" — where a bare
    unknown-member message would send the author looking for a typo. If the shape
    rules are satisfied, the id really is unknown, and naming it discloses nothing
    because the caller supplied it.
    """

    try:
        content.authorize_canvas(
            db, access.ctx, access.bank, spec, surface=content.DASHBOARD_SURFACE
        )
    except content.MembersDenied as exc:
        raise _denied(cat, exc) from exc
    except UnknownMember as exc:
        _shaped(db, access, spec)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "bi_dashboard_unknown_member",
                "message": (
                    f"{exc.member_id} is not a figure this dashboard can show. "
                    "Check the name, or certify the calculated measure first."
                ),
            },
        ) from exc
    _shaped(db, access, spec)


def _shaped(db: Session, access: BiReadAccess, spec: BiDashboardSpec) -> None:
    """422 for a widget the query engine would refuse, AFTER the access decision.

    After, because a shape message names members: telling a principal that one
    figure cannot be broken down by another would name both to someone who may
    have been refused either.

    The institution's certified measure keys MUST be supplied. That argument
    defaults to empty so a forgetful caller refuses every calculated measure
    rather than admitting one unchecked, which is the right default and is exactly
    why omitting it here was quiet: a bank-certified measure was refused on every
    saved canvas, with copy telling the author to certify what was already
    certified (audit A9-04).
    """

    try:
        content.check_canvas_shape(
            spec,
            certified_measures=content.certified_measure_keys(db, access.ctx, access.bank),
        )
    except content.CanvasRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "bi_dashboard_widget_refused", "message": str(exc)},
        ) from exc


def _owned_dashboard(db: Session, access: BiReadAccess, dashboard_id: UUID) -> BiDashboard:
    """Reachable first (404), then owned (403). In that order, always."""

    try:
        dashboard = content.load_dashboard(
            db,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            dashboard_id=dashboard_id,
            viewer_id=access.principal_user_id,
        )
    except content.DashboardNotFound as exc:
        raise _not_found(exc) from exc
    try:
        content.require_owner(dashboard, actor_user_id=access.principal_user_id)
    except content.NotTheOwner as exc:
        raise _forbidden(exc) from exc
    return dashboard


# --- calculated measures -----------------------------------------------------------------


@router.post(
    "/banks/{bank_id}/bi/measures/validation",
    response_model=BiMeasureValidationRead,
    operation_id="validateBiMeasureExpression",
)
def validate_bi_measure_expression(
    bank_id: str, payload: BiMeasureValidationRequest, db: DbSession, access: BiRead
) -> BiMeasureValidationRead:
    """Check a formula without saving it. The server's verdict is the only one.

    A refusal carries the named message and the position it happened at, never an
    echo of the caller's text; a formula naming a figure this caller may not read
    is refused with those figures listed, which is the same answer saving it would
    give.
    """

    _ = bank_id
    with _metered(db, access, "measures.validate", _payload_digest(payload)) as meter:
        try:
            compiled = content.compile_expression(
                db,
                access.ctx,
                access.bank,
                payload.expression,
                surface=content.DASHBOARD_SURFACE,
            )
        except content.ExpressionRefused as exc:
            return BiMeasureValidationRead(valid=False, message=str(exc), position=exc.position)
        except content.MembersDenied as exc:
            # A verdict, not a refusal: the caller asked whether this formula is
            # theirs to use and was ANSWERED, so the row stays ``allowed`` and
            # carries the members — the same mixed-read convention a dashboard row
            # follows. ``_metered`` cannot see this one, because no exception
            # leaves the route, so the route names them itself.
            meter.members(denied=exc.denied_members)
            return BiMeasureValidationRead(
                valid=False,
                message=(
                    "Your access does not cover every figure this formula uses. "
                    "An Org Owner can grant the ones listed here."
                ),
                denied_members=list(exc.denied_members),
            )
        return BiMeasureValidationRead(
            valid=True,
            message=content.EXPRESSION_ACCEPTED,
            referenced_members=list(compiled.referenced_members),
            referenced_member_labels=list(compiled.labels),
        )


@router.get(
    "/banks/{bank_id}/bi/measures",
    response_model=BiMeasureListRead,
    operation_id="listBiMeasures",
)
def list_bi_measures(bank_id: str, db: DbSession, access: BiRead) -> BiMeasureListRead:
    """Every calculated measure this identity may read.

    Their own drafts, plus the institution's certified ones — and only those whose
    figures their access covers. A measure they may not compute is ABSENT rather
    than named: its label is authored text that can describe the very figure they
    were refused.
    """

    _ = bank_id
    with _metered(db, access, "measures.list"):
        cat = catalogue()
        measures = content.readable_measures(
            db,
            access.ctx,
            access.bank,
            viewer_id=access.principal_user_id,
            surface=content.DASHBOARD_SURFACE,
        )
        identities = _identities(
            db,
            access.ctx.organization_id,
            [
                user_id
                for measure in measures
                for user_id in (
                    measure.owner_user_id,
                    measure.proposed_by_user_id,
                    measure.approved_by_user_id,
                )
                if user_id is not None
            ],
        )
        return BiMeasureListRead(
            measures=[
                _measure_read(
                    measure, cat=cat, identities=identities, caller=access.principal_user_id
                )
                for measure in measures
            ],
            available_in_queries=True,
            message=content.MEASURES_QUERYABLE,
        )


@router.post(
    "/banks/{bank_id}/bi/measures",
    response_model=BiMeasureRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBiMeasure",
)
def create_bi_measure(
    bank_id: str, payload: BiMeasureCreateRequest, db: DbSession, access: BiRead
) -> BiMeasureRead:
    """Save a personal calculated measure.

    The formula is parsed HERE and the figures it names come from that parse, not
    from the request: a client sends text and nothing else about what the text
    means. Every figure it names is then authorized for the author.
    """

    _ = bank_id
    with _metered(db, access, "measures.create", _payload_digest(payload)):
        cat = catalogue()
        compiled = _compiled(db, access, payload.expression)
        try:
            measure = content.create_measure(
                db,
                organization_id=access.ctx.organization_id,
                bank_id=access.bank.id,
                owner_user_id=access.principal_user_id,
                measure_key=payload.measure_key,
                label=payload.label,
                description=payload.description,
                compiled=compiled,
                value_type=payload.value_type,
                favourable_direction=payload.favourable_direction,
            )
        except content.MeasureKeyUnavailable as exc:
            raise _conflict("bi_measure_key_unavailable", exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_MEASURE_CREATED,
            entity_type=ENTITY_MEASURE,
            entity_id=measure.id,
            details={
                "bank_id": access.bank.id,
                "measure_key": measure.measure_key,
                "expression_digest": measure.expression_digest,
                "referenced_members": list(compiled.referenced_members),
                "catalogue_version": CATALOGUE_VERSION,
            },
        )
        db.commit()
        db.refresh(measure)
        identities = _identities(db, access.ctx.organization_id, [measure.owner_user_id])
        return _measure_read(
            measure, cat=cat, identities=identities, caller=access.principal_user_id
        )


@router.get(
    "/banks/{bank_id}/bi/measures/{measure_id}",
    response_model=BiMeasureRead,
    operation_id="getBiMeasure",
)
def get_bi_measure(bank_id: str, measure_id: UUID, db: DbSession, access: BiRead) -> BiMeasureRead:
    """One calculated measure, if this identity may read it."""

    _ = bank_id
    with _metered(db, access, "measures.read", measure_id):
        cat = catalogue()
        measure = _readable_measure(db, access, measure_id)
        identities = _identities(
            db,
            access.ctx.organization_id,
            [
                user_id
                for user_id in (
                    measure.owner_user_id,
                    measure.proposed_by_user_id,
                    measure.approved_by_user_id,
                )
                if user_id is not None
            ],
        )
        return _measure_read(
            measure, cat=cat, identities=identities, caller=access.principal_user_id
        )


@router.put(
    "/banks/{bank_id}/bi/measures/{measure_id}",
    response_model=BiMeasureRead,
    operation_id="updateBiMeasure",
)
def update_bi_measure(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    measure_id: UUID,
    payload: BiMeasureUpdateRequest,
    db: DbSession,
    access: BiRead,
) -> BiMeasureRead:
    """Edit a calculated measure. Owner only, and a changed formula drops certification.

    An approver approved a formula, not a name: the moment the text moves, the
    approval and the proposal behind it are cleared and the measure is personal
    again. The database refuses the alternative outright.
    """

    _ = bank_id
    with _metered(db, access, "measures.update", measure_id, _payload_digest(payload)):
        cat = catalogue()
        measure = _readable_measure(db, access, measure_id)
        if measure.owner_user_id != access.principal_user_id:
            raise _forbidden(content.NotTheOwner("calculated measure"))
        compiled = _compiled(db, access, payload.expression)
        was_certified = measure.state == "bank_certified"
        content.update_measure(
            db,
            measure,
            actor_user_id=access.principal_user_id,
            label=payload.label,
            description=payload.description,
            compiled=compiled,
            value_type=payload.value_type,
            favourable_direction=payload.favourable_direction,
        )
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_MEASURE_UPDATED,
            entity_type=ENTITY_MEASURE,
            entity_id=measure.id,
            details={
                "bank_id": access.bank.id,
                "measure_key": measure.measure_key,
                "expression_digest": measure.expression_digest,
                "referenced_members": list(compiled.referenced_members),
                "certification_cleared": was_certified and measure.state != "bank_certified",
            },
        )
        db.commit()
        db.refresh(measure)
        identities = _identities(db, access.ctx.organization_id, [measure.owner_user_id])
        return _measure_read(
            measure, cat=cat, identities=identities, caller=access.principal_user_id
        )


@router.delete(
    "/banks/{bank_id}/bi/measures/{measure_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBiMeasure",
)
def delete_bi_measure(bank_id: str, measure_id: UUID, db: DbSession, access: BiRead) -> Response:
    """Delete a calculated measure. Owner only."""

    _ = bank_id
    with _metered(db, access, "measures.delete", measure_id):
        measure = _readable_measure(db, access, measure_id)
        if measure.owner_user_id != access.principal_user_id:
            raise _forbidden(content.NotTheOwner("calculated measure"))
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_MEASURE_DELETED,
            entity_type=ENTITY_MEASURE,
            entity_id=measure.id,
            details={
                "bank_id": access.bank.id,
                "measure_key": measure.measure_key,
                "state": measure.state,
            },
        )
        db.execute(
            delete(BiMeasure).where(
                BiMeasure.id == measure.id,
                BiMeasure.organization_id == access.ctx.organization_id,
                BiMeasure.bank_id == access.bank.id,
            )
        )
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/banks/{bank_id}/bi/measures/{measure_id}/proposal",
    response_model=BiMeasureRead,
    operation_id="proposeBiMeasurePromotion",
)
def propose_bi_measure_promotion(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    measure_id: UUID,
    payload: BiMeasureProposalRequest,
    db: DbSession,
    access: BiRead,
) -> BiMeasureRead:
    """The maker's half of a promotion: put a personal measure up for certification."""

    _ = bank_id
    with _metered(db, access, "measures.propose", measure_id, _payload_digest(payload)):
        cat = catalogue()
        measure = _readable_measure(db, access, measure_id)
        try:
            content.propose_measure(
                db, measure, actor_user_id=access.principal_user_id, reason=payload.reason
            )
        except content.NotTheOwner as exc:
            raise _forbidden(exc) from exc
        except content.MeasureStateConflict as exc:
            raise _conflict("bi_measure_state_conflict", exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=EVENT_MEASURE_PROPOSED,
            entity_type=ENTITY_MEASURE,
            entity_id=measure.id,
            details={
                "bank_id": access.bank.id,
                "measure_key": measure.measure_key,
                "expression_digest": measure.expression_digest,
                "reason": payload.reason,
            },
        )
        db.commit()
        db.refresh(measure)
        identities = _identities(
            db,
            access.ctx.organization_id,
            [measure.owner_user_id, access.principal_user_id],
        )
        return _measure_read(
            measure, cat=cat, identities=identities, caller=access.principal_user_id
        )


@router.post(
    "/banks/{bank_id}/bi/measures/{measure_id}/decision",
    response_model=BiMeasureDecisionRead,
    operation_id="decideBiMeasurePromotion",
)
def decide_bi_measure_promotion(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    measure_id: UUID,
    payload: BiMeasureDecisionRequest,
    db: DbSession,
    access: BiRead,
) -> BiMeasureDecisionRead:
    """The checker's half: certify the measure for the institution, or send it back.

    Proposer ≠ approver, through the platform's own separation-of-duties
    machinery; the formula must be the one the checker read; and approving needs
    APPROVAL authority over every figure the formula names, not merely the
    authority to view them.
    """

    _ = bank_id
    with _metered(db, access, "measures.decide", measure_id, _payload_digest(payload)):
        cat = catalogue()
        measure = _readable_measure(db, access, measure_id)
        try:
            _, sod = content.decide_promotion(
                db,
                access.ctx,
                access.bank,
                measure,
                actor_user_id=access.principal_user_id,
                decision=payload.decision,
                reason=payload.reason,
                expression_digest_reviewed=payload.expression_digest,
                surface=content.DASHBOARD_SURFACE,
            )
        except grant_administration.SodPolicyBlocked as exc:
            raise _sod_blocked(exc) from exc
        except content.MeasureStateConflict as exc:
            raise _conflict("bi_measure_state_conflict", exc) from exc
        except content.ExpressionMoved as exc:
            raise _conflict("bi_measure_expression_moved", exc) from exc
        except content.MembersDenied as exc:
            raise _denied(cat, exc) from exc
        except content.ExpressionRefused as exc:
            raise _unprocessable(exc) from exc
        audit.record_event(
            db,
            access.ctx,
            event_type=(
                EVENT_MEASURE_CERTIFIED if payload.decision == "approve" else EVENT_MEASURE_REJECTED
            ),
            entity_type=ENTITY_MEASURE,
            entity_id=measure.id,
            details={
                "bank_id": access.bank.id,
                "measure_key": measure.measure_key,
                "decision": payload.decision,
                "reason": payload.reason,
                "reviewed_expression_digest": payload.expression_digest,
                "approved_expression_digest": measure.approved_expression_digest,
                "sod_outcome": sod.outcome.value,
                "sod_findings": [finding.code for finding in sod.findings],
            },
        )
        db.commit()
        db.refresh(measure)
        identities = _identities(
            db,
            access.ctx.organization_id,
            [
                user_id
                for user_id in (
                    measure.owner_user_id,
                    measure.proposed_by_user_id,
                    measure.approved_by_user_id,
                    access.principal_user_id,
                )
                if user_id is not None
            ],
        )
        return BiMeasureDecisionRead(
            measure=_measure_read(
                measure, cat=cat, identities=identities, caller=access.principal_user_id
            ),
            sod_decision=_sod_read(sod),
        )


def _compiled(db: Session, access: BiReadAccess, expression: str) -> content.CompiledExpression:
    """Parse and authorize a formula, or refuse the request."""

    try:
        return content.compile_expression(
            db, access.ctx, access.bank, expression, surface=content.DASHBOARD_SURFACE
        )
    except content.ExpressionRefused as exc:
        raise _unprocessable(exc) from exc
    except content.MembersDenied as exc:
        raise _denied(catalogue(), exc) from exc


def _readable_measure(db: Session, access: BiReadAccess, measure_id: UUID) -> BiMeasure:
    """One measure of this institution this identity may read, or 404.

    A measure whose figures the caller's access does not cover answers exactly as
    one that does not exist: naming it would disclose the figure behind it.
    """

    try:
        measure = content.load_measure(
            db,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            measure_id=measure_id,
        )
    except content.MeasureNotFound as exc:
        raise _not_found(exc) from exc
    own = measure.owner_user_id == access.principal_user_id
    if not own and measure.state not in content.SHARED_MEASURE_STATES:
        raise _not_found(content.MeasureNotFound("No such calculated measure."))
    if not content.readable(
        db, access.ctx, access.bank, measure, surface=content.DASHBOARD_SURFACE
    ):
        raise _not_found(content.MeasureNotFound("No such calculated measure."))
    return measure
