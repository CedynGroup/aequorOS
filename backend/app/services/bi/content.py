"""Saved dashboards and calculated measures: the rules, away from the routes.

Four properties live here, and each is the reason a sentence in ``docs/bi.md``
§Phase 3 is not merely a feature:

1. **Sharing never shares data.** :func:`resolve_widgets` re-authorizes EVERY
   widget for whoever is reading, through the same
   ``app/services/bi/authorization.py::authorize_query`` decision the read routes
   make. The owner's authority is not an input to it — the function is not even
   given the owner — so a dashboard built by a broadly-authorized principal and
   shared to a narrow one resolves to refusal markers for the narrow reader. A
   share grants REACHABILITY (:func:`reaches`), never authority.
2. **Only the owner may edit or delete.** :func:`require_owner` compares
   ``owner_user_id`` and nothing else. An account administrator and an Org Owner
   are refused like anyone else: the document is that person's working note, and
   there is deliberately no administrative override path to add later by
   accident.
3. **A formula is compiled on the server.** :func:`compile_expression` parses the
   text (``app/domain/bi/expr.py``), resolves every id it names against the
   catalogue, refuses anything that is not a figure, and puts the resulting
   member set through the authorization walk. A client never sends a member list,
   a module or a sensitivity, so there is nothing for a hostile formula to
   under-report: the ids come from ``expr.referenced_members``, whose walk is
   structural. The same text is re-parsed by the same walk everywhere it matters
   — here, on every read (:func:`_readable`), at certification, and in the
   compiler when the formula is actually evaluated — and the stored
   ``referenced_members`` column is never the authority for any of them.
4. **A promotion is maker-checker, judged by the platform's own policy.**
   :func:`decide_promotion` reuses ``grant_administration``'s ``SodDecision`` /
   ``SodOutcome`` / ``SodFinding`` / ``SodPolicyBlocked`` rather than inventing a
   second proposer-approver check, freezes the approved formula on the row, and
   refuses a decision taken against a formula that has since moved.

Nothing in this module writes a canonical, regulatory or live table, and nothing
reads a figure: the marts are never queried here. The tables it owns are
``app/models/bi_content.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    BindingStatus,
    ConditionCheck,
    ConditionKind,
    InstitutionScope,
    Module,
    Permission,
    RoleBundle,
    Sensitivity,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi import expr
from app.domain.bi.catalogue import CATALOGUE_VERSION, Catalogue, MeasureDef, MemberDef, catalogue
from app.domain.bi.catalogue import UnknownMember as CatalogueUnknownMember
from app.domain.bi.packs import PackError
from app.domain.bi.packs import pack as certified_pack
from app.models import AuthorizationBinding, Bank, User
from app.models.bi_content import (
    BiDashboard,
    BiDashboardShare,
    BiDashboardVersion,
    BiMeasure,
)
from app.schemas.bi import BiPackWidget, BiTime
from app.schemas.bi import BiQuery as BiQuerySchema
from app.schemas.bi_content import BiDashboardSpec
from app.services import authorization as authorization_service
from app.services import grant_administration, scoped_authorization
from app.services.bi.authorization import authorize_query, query_members, scope_pairs
from app.services.bi.compiler import expand_calculated_measures
from app.services.bi.errors import UnknownMember

#: The ``bi_query_log`` surface a dashboard read is recorded under.
#:
#: It is ``packs`` because that value already means "a dashboard resolved for a
#: reader, and the fields that reader was refused inside it" — which is exactly
#: what a saved-dashboard render is, and what an operator needs the row for. A
#: value of its own (``dashboards``) would be truer, and it needs the vocabulary
#: in ``app/models/bi.py`` widened plus a migration, both of which belong to
#: other owners; recording the read under a surface that names a different SHAPE
#: of dashboard is a smaller inaccuracy than not recording a read that discloses
#: which grants a viewer is missing. The ``query_hash`` names the dashboard and
#: its version, so the two are never confused in the log.
DASHBOARD_SURFACE = "packs"

#: The states in which a calculated measure belongs to the INSTITUTION rather
#: than to one person: a proposal, because a checker who cannot see it cannot
#: review it, and a certification, because that is what certifying it did. A
#: personal draft is its author's alone.
SHARED_MEASURE_STATES: tuple[str, ...] = ("proposed", "bank_certified")

#: How the measure states map to the badge a surface shows.
_BADGE_BY_STATE: Mapping[str, str] = {
    "personal": "personal",
    "proposed": "personal",
    "bank_certified": "bank_certified",
}

#: Production copy for a formula the server accepted.
EXPRESSION_ACCEPTED = "This formula is valid."

#: Production copy for what a reader may do with a calculated measure.
#:
#: This said "not yet" while the compiler could evaluate a certified formula and
#: no read route could reach that arm: ``authorize_query`` resolved every measure
#: id against the STATIC catalogue, so a calculated id was refused as unknown
#: before a query was ever compiled. A surface offering the field while the route
#: refused it would have been the worse lie, so the sentence stayed honest and the
#: flag stayed false.
#:
#: The hook landed (``authorize_query`` now walks
#: ``compiler.expand_calculated_measures``), and the route-level proof is
#: ``tests/api/test_bi_content_routes.py::
#: test_a_certified_measure_can_be_charted_through_the_query_route``. A formula is
#: authorized as the FIGURES ITS TEXT NAMES, so being able to chart one grants a
#: reader nothing they did not already hold.
MEASURES_QUERYABLE = (
    "Calculated measures can be written, reviewed and certified here. Once certified they can be "
    "added to a chart or a grid."
)


class BiContentError(Exception):
    """A requested dashboard or measure operation cannot be applied."""


class DashboardNotFound(BiContentError):
    """No dashboard of this institution that this identity may reach.

    One refusal for "there is no such dashboard" and for "you may not reach that
    one": a reachability rule that answered differently would let anyone
    enumerate every private dashboard in the institution by identifier.
    """


class MeasureNotFound(BiContentError):
    """No calculated measure of this institution that this identity may read."""


class NotTheOwner(BiContentError):
    """Reachable, but only its owner may change it."""

    def __init__(self, what: str) -> None:
        super().__init__(f"Only the person who created this {what} can change or delete it.")


class MembersDenied(BiContentError):
    """The caller's access does not cover every figure the request names."""

    def __init__(self, denied_members: Sequence[str], *, reason: str) -> None:
        super().__init__("Your access does not cover every field this request names.")
        self.denied_members = tuple(denied_members)
        self.reason = reason


class ExpressionRefused(BiContentError):
    """The formula is not accepted, with the position it failed at.

    ``message`` is fixed copy plus, at most, a value the language itself made
    well-formed (a figure id, a function name); it never echoes the caller's
    text. ``position`` counts characters from 1, the way an editor does.
    """

    def __init__(self, message: str, *, position: int | None = None) -> None:
        super().__init__(message)
        self.position = position


class MeasureKeyUnavailable(BiContentError):
    """The requested measure id is taken, or names a platform figure."""


class MeasureStateConflict(BiContentError):
    """The measure is not in the state this step requires."""


class ExpressionMoved(BiContentError):
    """The formula changed after the checker read it."""

    def __init__(self) -> None:
        super().__init__(
            "The formula changed after it was sent for review, so it has to be reviewed again."
        )


class PackNotAvailable(BiContentError):
    """No certified dashboard of that name shipped in this build."""


class CanvasRefused(BiContentError):
    """A widget asks a question the query engine cannot answer.

    Checked at SAVE time so a person does not build a dashboard, save it, open it
    and find a widget reading an error. The compiler remains the authority
    (``app/services/bi/compiler.py``); this is a pre-check over the same catalogue
    rule, and the direction it can be wrong in is safe — a canvas that passes here
    and is refused there shows a query error, never a figure.
    """


# --- digests -----------------------------------------------------------------------------


def spec_digest(spec: BiDashboardSpec) -> str:
    """A value-based digest of a canvas.

    Value-based like every other digest in this platform: the material is the
    validated model's own JSON with its keys sorted, so two equal canvases hash
    alike whatever order a client sent the fields in, and nothing volatile (an
    id, a timestamp, a version number) is in it.
    """

    material = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def expression_digest(source: str) -> str:
    """A digest of a formula's SOURCE TEXT, exactly as the author wrote it.

    Deliberately not a digest of the parsed tree: a checker approves the text a
    person can read, and two spellings that happen to parse alike are two things
    to review.
    """

    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def dashboard_digest(dashboard: BiDashboard, *, as_of: date) -> str:
    """The ``bi_query_log.query_hash`` for one dashboard read.

    A dashboard read is not a ``BiQuery``, so ``query_log.query_digest`` cannot
    state it. The question being asked is "this dashboard, at this version, for
    this date", and the digest names exactly that — one way, with nothing in it
    that can be read back as a value.
    """

    material = "\x1f".join(
        (
            DASHBOARD_SURFACE,
            str(dashboard.id),
            str(dashboard.current_version),
            as_of.isoformat(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --- reachability (never authority) ------------------------------------------------------


def covering_role_bundles(
    db: Session, *, organization_id: str, user_id: UUID, bank_id: str
) -> frozenset[str]:
    """The role bundles this identity holds over this institution, if any.

    The one rule both the ``role`` and the ``org`` visibilities are built on, and
    it reads the stored bindings rather than ``users.role``: a scalar role has not
    been authority anywhere in this platform since the authorization foundation
    landed, and a reachability rule that consulted one would resurrect it.

    Coverage only — no module, no sensitivity and no permission enter it. Whether
    the reader may see a FIGURE is decided per widget by ``authorize_query``,
    every time the dashboard is opened. An empty set means this identity has no
    institution coverage at all, and therefore reaches nothing here but its own
    documents.
    """

    bundles: set[str] = set()
    for binding in db.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.principal_user_id == user_id,
            AuthorizationBinding.status == BindingStatus.ACTIVE.value,
        )
    ):
        if not authorization_service.binding_is_effective(binding):
            continue
        covers = (
            binding.institution_scope == InstitutionScope.ORGANIZATION.value
            or binding.institution_id == bank_id
        )
        if covers:
            bundles.add(binding.role_bundle)
    return frozenset(bundles)


def reaches(db: Session, dashboard: BiDashboard, *, viewer_id: UUID) -> bool:
    """Whether ``viewer_id`` may OPEN this dashboard. Not whether they may read it.

    Four rules, one per visibility, and ``org`` cannot cross a tenant: the row is
    tenant-scoped and RLS-forced, and the identity being tested was resolved from
    the same tenant's token.
    """

    if dashboard.owner_user_id == viewer_id:
        return True
    if dashboard.visibility == "private":
        return False
    if dashboard.visibility == "users":
        return (
            db.scalar(
                select(BiDashboardShare.id).where(
                    BiDashboardShare.organization_id == dashboard.organization_id,
                    BiDashboardShare.dashboard_id == dashboard.id,
                    BiDashboardShare.grantee_user_id == viewer_id,
                )
            )
            is not None
        )
    bundles = covering_role_bundles(
        db,
        organization_id=dashboard.organization_id,
        user_id=viewer_id,
        bank_id=dashboard.bank_id,
    )
    if dashboard.visibility == "role":
        return dashboard.visibility_role in bundles
    return bool(bundles)


def require_owner(dashboard: BiDashboard, *, actor_user_id: UUID) -> None:
    """The whole of rule 1: the owner, and no one else.

    No administrator branch, no Org Owner branch, no "unless the owner is
    deactivated" branch. Each of those is the one a future reader would take as
    licence to add another.
    """

    if dashboard.owner_user_id != actor_user_id:
        raise NotTheOwner("dashboard")


# --- per-viewer widget authorization -----------------------------------------------------


@dataclass
class ViewerAuthority:
    """One reader's verdict per ``(module, sensitivity)`` pair, built as it goes.

    The cache exists because ``authorize_query`` already decides per PAIR: one
    call over a widget's own query returns the verdict for every pair that widget
    touches, so a twenty-four-widget canvas drawn from three pairs costs one
    evaluation and not twenty-four. It caches VERDICTS, never rows, and it is
    built for one reader and thrown away with the request — there is no place for
    one reader's answer to be served to another.
    """

    db: Session
    ctx: TenantContext
    bank: Bank
    cat: Catalogue
    surface: str
    permissions: tuple[Permission, ...] = (Permission.VIEW,)
    _verdicts: dict[tuple[str, str], bool] = field(default_factory=dict)
    #: Every pair that was asked about, allowed or not, for the response's counts.
    _reason: str = ""

    @property
    def reason(self) -> str:
        """Why the last refusal happened, for the decision telemetry."""

        return self._reason

    def refused_members(self, query: BiQuerySchema) -> tuple[str, ...]:
        """The member ids of ``query`` this reader may not see, in query order.

        Empty means the reader holds every sentence the query needs. Raises
        ``UnknownMember`` (the compiler's own refusal) for an id the catalogue
        does not know, so a stored canvas naming a retired member fails loudly
        rather than rendering as a silent gap.

        A widget may name one of the institution's CERTIFIED calculated measures,
        which is not a catalogue member and has no ``(module, sensitivity)`` of its
        own: what it needs is a sentence for every FIGURE its formula names. The
        query is therefore expanded first
        (``compiler.expand_calculated_measures``) — from the server's own parse of
        the certified text — and both the member walk and the evaluation see the
        expanded form, so a formula cannot be a way round a missing binding.
        """

        expanded = expand_calculated_measures(
            self.db,
            self.cat,
            query,
            organization_id=self.ctx.organization_id,
            bank_id=self.bank.id,
        )
        members = query_members(self.cat, expanded)
        pairs = scope_pairs(members)
        unknown = [pair for pair in pairs if pair not in self._verdicts]
        if unknown:
            self._evaluate(expanded, pairs)
        return tuple(
            member.id
            for member in members
            if not self._verdicts.get((member.module, member.sensitivity), False)
        )

    def served_members(self, query: BiQuerySchema) -> tuple[MemberDef, ...]:
        """Every catalogue figure a granted widget actually reads.

        The same expansion :meth:`refused_members` authorizes over, so what the
        append-only log records as SERVED is the set that was authorized rather than
        the set the request happened to name.
        """

        return query_members(
            self.cat,
            expand_calculated_measures(
                self.db,
                self.cat,
                query,
                organization_id=self.ctx.organization_id,
                bank_id=self.bank.id,
            ),
        )

    def _evaluate(
        self, query: BiQuerySchema, pairs: Mapping[tuple[str, str], tuple[str, ...]]
    ) -> None:
        """Ask the read path's own decision function, once, about this query.

        ``authorize_query`` evaluates EVERY pair the query touches and returns
        every denial, so one call settles every pair here — including the ones
        this widget shares with the next. Each permission in ``permissions`` is
        required: a pair is allowed only if no pass denied it, which is the same
        conjunction the export surface applies for ``view`` + ``export``.
        """

        denied: set[str] = set()
        for permission in self.permissions:
            decision = authorize_query(
                self.db,
                self.ctx,
                self.bank,
                self.cat,
                query,
                permission=permission,
                surface=self.surface,
            )
            if decision.allowed:
                continue
            denied.update(decision.denied_members)
            if not self._reason:
                self._reason = decision.reason
        for pair, member_ids in pairs.items():
            allowed = not any(member_id in denied for member_id in member_ids)
            # Never widen a verdict already taken: a pair denied under one
            # permission stays denied.
            self._verdicts[pair] = self._verdicts.get(pair, True) and allowed


@dataclass(frozen=True, slots=True)
class ResolvedWidget:
    """One widget as a reader gets it: granted with its query, or refused."""

    widget: BiPackWidget
    query: BiQuerySchema | None
    granted: bool


@dataclass(frozen=True, slots=True)
class ResolvedCanvas:
    """A canvas resolved for one reader and one reporting date."""

    widgets: tuple[ResolvedWidget, ...]
    #: Member ids this reader was refused. They reach the append-only log, where
    #: an operator can see which grant is missing, and NEVER the response.
    denied_members: tuple[str, ...]
    #: Member ids actually served, for the same log row.
    served_members: tuple[str, ...]

    @property
    def readable_widgets(self) -> int:
        """How many widgets could have shown a figure (a query, not a gap)."""

        return sum(1 for resolved in self.widgets if resolved.widget.query is not None)

    @property
    def restricted_widgets(self) -> int:
        return sum(1 for resolved in self.widgets if not resolved.granted)

    @property
    def access(self) -> Literal["granted", "restricted"]:
        """``restricted`` only when every figure-bearing widget was refused."""

        readable = self.readable_widgets
        if readable and self.restricted_widgets == readable:
            return "restricted"
        return "granted"


def resolve_widgets(
    spec: BiDashboardSpec, *, as_of: date, authority: ViewerAuthority
) -> ResolvedCanvas:
    """Resolve every widget for the date, and authorize every one for the READER.

    The owner is not a parameter of this function and cannot be: a shared
    dashboard is a set of questions, and the answers are always the reader's own.
    A refused widget is dropped to its geometry by the caller building the
    response model — everything but the layout is omitted by not being passed.
    """

    resolved: list[ResolvedWidget] = []
    denied: list[str] = []
    served: list[str] = []
    for widget in spec.widgets:
        query = None if widget.query is None else widget.query.for_period(as_of)
        if query is None:
            # A panel or a named gap: it carries no query of its own, and the
            # surface it embeds authorizes itself when the client fetches it.
            resolved.append(ResolvedWidget(widget=widget, query=None, granted=True))
            continue
        refused = authority.refused_members(query)
        if refused:
            denied.extend(member_id for member_id in refused if member_id not in denied)
            resolved.append(ResolvedWidget(widget=widget, query=None, granted=False))
            continue
        served.extend(
            member.id for member in authority.served_members(query) if member.id not in served
        )
        resolved.append(ResolvedWidget(widget=widget, query=query, granted=True))
    return ResolvedCanvas(
        widgets=tuple(resolved), denied_members=tuple(denied), served_members=tuple(served)
    )


def check_canvas_shape(  # noqa: PLR0912 - one branch per named refusal
    spec: BiDashboardSpec, *, certified_measures: frozenset[str] = frozenset()
) -> None:
    """Refuse a widget the query engine would refuse, naming the widget.

    Three rules, all the compiler's own
    (``compiler.py``: every sliceable dimension must be allowed by every measure
    the query reads):

    * a measure position must hold a measure and a dimension position a dimension
      — a figure is not something to group by, and the reverse;
    * every dimension a widget slices by (grouped, filtered, pivoted or Top-N'd)
      must appear in the ``allowed_dimensions`` of every measure it reads, because
      a measure is only correct at the grains its own table carries;
    * a CALCULATED measure on a saved canvas must be one the institution has
      CERTIFIED. A saved dashboard is a document other people open, and a personal
      formula on one turns a share into a way to make someone else compute the
      owner's arithmetic under the owner's label. ``certified_measures`` is the
      institution's certified keys and defaults to EMPTY, so a caller that does
      not supply them refuses every calculated measure rather than admitting one
      unchecked. The compiler applies the same rule again when the widget runs.

    Called AFTER the authorization walk on purpose: a shape message names members,
    and naming one to a principal who was refused it would be a disclosure.
    """

    cat = catalogue()
    for widget in spec.widgets:
        if widget.query is None:
            continue
        measures: list[MeasureDef] = []
        for member_id in widget.query.measures:
            if member_id not in cat:
                if member_id in certified_measures:
                    # A certified calculated measure. Its own figures decide which
                    # breakdowns it allows, and the compiler checks them against
                    # the formula it re-parses; this pre-check has nothing truer to
                    # say about it than that it exists and is certified.
                    continue
                raise CanvasRefused(
                    f"{widget.title}: {member_id} is not a figure this institution has "
                    "certified. Certify the calculated measure before putting it on a "
                    "saved dashboard."
                )
            member = cat.member(member_id)
            if not isinstance(member, MeasureDef):
                raise CanvasRefused(
                    f"{widget.title}: {member.label} is something to group or filter by, "
                    "not a figure to show."
                )
            measures.append(member)
        sliced: list[str] = [
            *widget.query.dimensions,
            *(predicate.member for predicate in widget.query.filters),
        ]
        if widget.query.pivot is not None:
            sliced.append(widget.query.pivot.dimension)
        if widget.query.top_n is not None:
            sliced.append(widget.query.top_n.dimension)
        for dimension_id in sliced:
            if dimension_id not in cat:
                raise CanvasRefused(
                    f"{widget.title}: {dimension_id} is not something this platform can "
                    "group or filter by."
                )
            dimension = cat.member(dimension_id)
            if isinstance(dimension, MeasureDef):
                raise CanvasRefused(
                    f"{widget.title}: {dimension.label} is a figure, not something to group "
                    "or filter by."
                )
            for measure in measures:
                if dimension_id not in measure.allowed_dimensions:
                    raise CanvasRefused(
                        f"{widget.title}: {measure.label} cannot be broken down by "
                        f"{dimension.label}."
                    )


def authorize_canvas(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    spec: BiDashboardSpec,
    *,
    surface: str,
) -> None:
    """Require the AUTHOR to hold every figure the canvas they are saving reads.

    Deny-by-default applied to writing as well as reading: a principal cannot
    persist a question they may not ask. It is not what protects a viewer — that
    is :func:`resolve_widgets`, every time the dashboard is opened — it is what
    keeps the refusal marker a property of SHARING rather than something an
    author can also produce for themselves, and it stops a canvas being composed
    around figures its author has never been granted.

    The date is today's: a saved canvas names no date (its widgets carry relative
    windows), and the authorization sentence a member needs does not depend on
    one.
    """

    authority = ViewerAuthority(db=db, ctx=ctx, bank=bank, cat=catalogue(), surface=surface)
    refused: list[str] = []
    for widget in spec.widgets:
        if widget.query is None:
            continue
        query = widget.query.for_period(utc_now().date())
        refused.extend(
            member_id for member_id in authority.refused_members(query) if member_id not in refused
        )
    if refused:
        raise MembersDenied(refused, reason=authority.reason or "member_scope_unrecognized")


# --- dashboards --------------------------------------------------------------------------


def spec_from_pack(pack_id: str) -> tuple[BiDashboardSpec, str]:
    """A certified pack's widgets and layout, copied verbatim, plus its description.

    The widgets come from the FILE the server holds, never from the request: a
    copy of a certified dashboard has to be a copy, or a client could present an
    edited canvas as one. A pack is never edited in place — this is the only
    editing path there is, and what it produces is the copier's own ``personal``
    dashboard.
    """

    try:
        pack = certified_pack(pack_id)
    except (PackError, KeyError) as exc:
        raise PackNotAvailable(
            "No certified dashboard of that name is published in this deployment."
        ) from exc
    spec = BiDashboardSpec(widgets=list(pack.widgets), layout=list(pack.layout))
    return spec, pack.description


def create_dashboard(  # noqa: PLR0913 - one complete document, stated explicitly
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    owner_user_id: UUID,
    title: str,
    description: str,
    visibility: str,
    visibility_role: str | None,
    spec: BiDashboardSpec,
    source_pack: str | None,
) -> tuple[BiDashboard, BiDashboardVersion]:
    """Save a dashboard and its first version. The caller commits."""

    _validate_visibility(visibility, visibility_role)
    now = utc_now()
    dashboard = BiDashboard(
        organization_id=organization_id,
        bank_id=bank_id,
        owner_user_id=owner_user_id,
        title=title,
        description=description,
        visibility=visibility,
        visibility_role=visibility_role,
        # A saved dashboard is its author's. "Platform-certified" belongs to a
        # pack file, and a bank's own certification has no governance path yet
        # (see the report accompanying this module), so a copy of a certified
        # pack is ``personal`` too — which is the honest badge for a document
        # anyone may now edit.
        badge="personal",
        current_version=1,
        source_pack=source_pack,
        created_at=now,
        updated_at=now,
    )
    db.add(dashboard)
    db.flush()
    version = _append_version(
        db,
        dashboard,
        version=1,
        title=title,
        description=description,
        spec=spec,
        change_note="",
        actor_user_id=owner_user_id,
        now=now,
    )
    return dashboard, version


def update_dashboard(  # noqa: PLR0913 - one complete document, stated explicitly
    db: Session,
    dashboard: BiDashboard,
    *,
    actor_user_id: UUID,
    title: str,
    description: str,
    visibility: str,
    visibility_role: str | None,
    spec: BiDashboardSpec,
    change_note: str,
) -> BiDashboardVersion:
    """Append a version and point the dashboard at it. Owner only.

    Every save appends, including one that only moves a widget: the history is
    what the dashboard looked like over time, and a "no material change" rule
    would be this module deciding which of the owner's edits counted.
    """

    require_owner(dashboard, actor_user_id=actor_user_id)
    _validate_visibility(visibility, visibility_role)
    now = utc_now()
    dashboard.current_version += 1
    dashboard.title = title
    dashboard.description = description
    dashboard.visibility = visibility
    dashboard.visibility_role = visibility_role
    dashboard.updated_at = now
    if visibility != "users":
        # A share row that no visibility reads is a permission nobody can see:
        # narrowing to ``private`` has to remove the names, not leave them ready
        # to take effect again the next time the owner widens it.
        db.execute(
            delete(BiDashboardShare).where(
                BiDashboardShare.organization_id == dashboard.organization_id,
                BiDashboardShare.dashboard_id == dashboard.id,
            )
        )
    return _append_version(
        db,
        dashboard,
        version=dashboard.current_version,
        title=title,
        description=description,
        spec=spec,
        change_note=change_note,
        actor_user_id=actor_user_id,
        now=now,
    )


def _append_version(  # noqa: PLR0913 - one row, stated explicitly
    db: Session,
    dashboard: BiDashboard,
    *,
    version: int,
    title: str,
    description: str,
    spec: BiDashboardSpec,
    change_note: str,
    actor_user_id: UUID,
    now: datetime,
) -> BiDashboardVersion:
    row = BiDashboardVersion(
        organization_id=dashboard.organization_id,
        bank_id=dashboard.bank_id,
        dashboard_id=dashboard.id,
        version=version,
        title=title,
        description=description,
        spec=spec.model_dump(mode="json"),
        spec_digest=spec_digest(spec),
        change_note=change_note,
        created_by_user_id=actor_user_id,
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _validate_visibility(visibility: str, visibility_role: str | None) -> None:
    """A ``role`` visibility must name a role the platform actually issues."""

    if visibility != "role":
        return
    if visibility_role not in {bundle.value for bundle in RoleBundle}:
        raise BiContentError("That is not a role this organization can hold.")


def current_version(db: Session, dashboard: BiDashboard) -> BiDashboardVersion:
    """The version being rendered today.

    Raises ``BiContentError`` when the pointer names no row: that is a broken
    invariant rather than a user error, and serving an older version instead
    would show a canvas nobody chose.
    """

    row = db.scalar(
        select(BiDashboardVersion).where(
            BiDashboardVersion.organization_id == dashboard.organization_id,
            BiDashboardVersion.dashboard_id == dashboard.id,
            BiDashboardVersion.version == dashboard.current_version,
        )
    )
    if row is None:
        raise BiContentError("This dashboard's current layout could not be read.")
    return row


def current_versions(
    db: Session, dashboards: Sequence[BiDashboard]
) -> Mapping[UUID, BiDashboardVersion]:
    """The live version of each dashboard, in one query.

    The list surface needs one number off each canvas (how many widgets it holds),
    and asking per row would make the list cost a query per dashboard. The join is
    on the pointer itself, so this cannot return a version no dashboard is
    pointing at.
    """

    if not dashboards:
        return {}
    ids = [dashboard.id for dashboard in dashboards]
    rows = db.scalars(
        select(BiDashboardVersion)
        .join(
            BiDashboard,
            (BiDashboard.id == BiDashboardVersion.dashboard_id)
            & (BiDashboard.organization_id == BiDashboardVersion.organization_id)
            & (BiDashboard.current_version == BiDashboardVersion.version),
        )
        .where(BiDashboard.id.in_(ids))
    )
    return {row.dashboard_id: row for row in rows}


def versions(db: Session, dashboard: BiDashboard) -> tuple[BiDashboardVersion, ...]:
    """Every version, newest first."""

    return tuple(
        db.scalars(
            select(BiDashboardVersion)
            .where(
                BiDashboardVersion.organization_id == dashboard.organization_id,
                BiDashboardVersion.dashboard_id == dashboard.id,
            )
            .order_by(BiDashboardVersion.version.desc())
        )
    )


def load_dashboard(
    db: Session, *, organization_id: str, bank_id: str, dashboard_id: UUID, viewer_id: UUID
) -> BiDashboard:
    """One dashboard of THIS institution that ``viewer_id`` may reach, or a refusal.

    Scoped at the query by organization AND institution: two banks of one
    organization share an RLS tenant, so organization scoping alone cannot
    isolate their child objects (the by-id rule in ``AGENTS.md``). Reachability is
    applied on top, and a dashboard the viewer may not reach is
    :class:`DashboardNotFound` — the same answer as one that does not exist.
    """

    dashboard = db.scalar(
        select(BiDashboard).where(
            BiDashboard.id == dashboard_id,
            BiDashboard.organization_id == organization_id,
            BiDashboard.bank_id == bank_id,
        )
    )
    if dashboard is None or not reaches(db, dashboard, viewer_id=viewer_id):
        raise DashboardNotFound("No such dashboard.")
    return dashboard


def reachable_dashboards(
    db: Session, *, organization_id: str, bank_id: str, viewer_id: UUID
) -> tuple[BiDashboard, ...]:
    """Every dashboard of this institution this identity may open, newest first."""

    rows = tuple(
        db.scalars(
            select(BiDashboard)
            .where(
                BiDashboard.organization_id == organization_id,
                BiDashboard.bank_id == bank_id,
            )
            .order_by(BiDashboard.updated_at.desc(), BiDashboard.id)
        )
    )
    # The same four rules as :func:`reaches`, asked once for the whole list
    # instead of once per row: a reachability rule that cost a query per
    # dashboard would make the list slower the more private documents an
    # institution held, which is the opposite of what it should cost.
    shared_ids = set(
        db.scalars(
            select(BiDashboardShare.dashboard_id).where(
                BiDashboardShare.organization_id == organization_id,
                BiDashboardShare.grantee_user_id == viewer_id,
            )
        )
    )
    bundles = covering_role_bundles(
        db, organization_id=organization_id, user_id=viewer_id, bank_id=bank_id
    )

    def reachable(row: BiDashboard) -> bool:
        if row.owner_user_id == viewer_id:
            return True
        if row.visibility == "users":
            return row.id in shared_ids
        if row.visibility == "role":
            return row.visibility_role in bundles
        if row.visibility == "org":
            return bool(bundles)
        return False

    return tuple(row for row in rows if reachable(row))


def set_shares(
    db: Session,
    dashboard: BiDashboard,
    *,
    actor_user_id: UUID,
    user_ids: Sequence[UUID],
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """Replace the named grantees. Owner only. Returns (added, removed).

    Every grantee must be an identity of the same tenant — the composite foreign
    key enforces it in the database and this check names it, so the refusal reads
    as a message rather than as a constraint violation. The owner is never a
    grantee: they reach their own dashboard by owning it.
    """

    require_owner(dashboard, actor_user_id=actor_user_id)
    if dashboard.visibility != "users":
        raise BiContentError(
            "This dashboard is not shared with named people, so there is no list to set."
        )
    requested = {user_id for user_id in user_ids if user_id != dashboard.owner_user_id}
    if requested:
        known = set(
            db.scalars(
                select(User.id).where(
                    User.organization_id == dashboard.organization_id,
                    User.id.in_(requested),
                )
            )
        )
        if unknown := requested - known:
            raise BiContentError(
                f"{len(unknown)} of the people named are not members of this organization."
            )
    existing = set(
        db.scalars(
            select(BiDashboardShare.grantee_user_id).where(
                BiDashboardShare.organization_id == dashboard.organization_id,
                BiDashboardShare.dashboard_id == dashboard.id,
            )
        )
    )
    removed = tuple(sorted(existing - requested, key=str))
    added = tuple(sorted(requested - existing, key=str))
    if removed:
        # A Core delete naming its table, rather than ``db.delete(row)`` over a
        # collection: the plane guard resolves a write's TARGET statically
        # (``tests/architecture/test_bi_plane_boundary.py``), and a target it
        # cannot read is a write it cannot vouch for.
        db.execute(
            delete(BiDashboardShare).where(
                BiDashboardShare.organization_id == dashboard.organization_id,
                BiDashboardShare.dashboard_id == dashboard.id,
                BiDashboardShare.grantee_user_id.in_(removed),
            )
        )
    now = utc_now()
    for grantee_id in added:
        db.add(
            BiDashboardShare(
                organization_id=dashboard.organization_id,
                bank_id=dashboard.bank_id,
                dashboard_id=dashboard.id,
                grantee_user_id=grantee_id,
                shared_by_user_id=actor_user_id,
                created_at=now,
            )
        )
    db.flush()
    return added, removed


def shares(db: Session, dashboard: BiDashboard) -> tuple[tuple[BiDashboardShare, User], ...]:
    """The named grantees with their identities, for the owner's share list."""

    rows = db.execute(
        select(BiDashboardShare, User)
        .join(
            User,
            (User.id == BiDashboardShare.grantee_user_id)
            & (User.organization_id == BiDashboardShare.organization_id),
        )
        .where(
            BiDashboardShare.organization_id == dashboard.organization_id,
            BiDashboardShare.dashboard_id == dashboard.id,
        )
        .order_by(BiDashboardShare.created_at, BiDashboardShare.id)
    ).all()
    return tuple((share, user) for share, user in rows)


# --- calculated measures -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    """A formula the server accepted, and the figures it names."""

    source: str
    digest: str
    referenced_members: tuple[str, ...]
    members: tuple[MeasureDef, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(member.label for member in self.members)


def _parse(source: str, *, retention_days: int | None) -> expr.Expr:
    """The one place a formula becomes structure. Never on a client.

    ``ExpressionError.message`` is production copy by contract and its
    ``position`` is a 0-based offset; the wire model counts from 1, the way an
    editor does.

    ``retention_days`` decides whether the reach of a period comparison is checked
    (D-196) and has no default, because the two callers want opposite things and
    neither may get it by omission:

    * the WRITE path passes the deployment's window, so a formula reaching past
      the history the marts hold is refused where its author can shorten it;
    * an AUTHORIZATION walk passes ``None``, because which figures a stored
      formula names — and therefore who may read it — must not change when a
      deployment shortens its retention window. A measure that vanished from its
      own author's list on a configuration change would be the wrong answer to
      the right question, and the compiler refuses the read anyway.
    """

    try:
        return expr.parse(source, retention_days=retention_days)
    except expr.ExpressionError as error:
        raise ExpressionRefused(error.message, position=error.position + 1) from error


def _retention_days() -> int:
    """How long the deployment keeps daily history (``BI_DAILY_RETENTION_DAYS``)."""

    return get_settings().bi.daily_retention_days


def compile_expression(
    db: Session, ctx: TenantContext, bank: Bank, source: str, *, surface: str
) -> CompiledExpression:
    """Parse a formula on the SERVER, resolve what it names, and authorize it.

    The three steps are in this order for a reason:

    1. **parse**, so the ids come from ``expr.referenced_members`` — a structural
       walk of the tree — and never from the request. A client that sent a member
       list would be handing the authorization walk its own input;
    2. **resolve**, so every id is a catalogue FIGURE. A dimension is refused by
       name (a formula computes with numbers, and ``[m:branch.code]`` is not
       one), a calculated measure is refused by name (nesting one measure in
       another is not supported), and an unknown id is refused as unknown;
    3. **authorize**, so a formula cannot reference a figure its author may not
       see. This is the step a hostile formula exists to slip past, which is why
       the ids it is given are the ones step 1 derived.
    """

    tree = _parse(source, retention_days=_retention_days())
    referenced = expr.referenced_members(tree)
    if not referenced:
        raise ExpressionRefused("A calculated measure has to name at least one figure.")
    cat = catalogue()
    stored = set(
        db.scalars(
            select(BiMeasure.measure_key).where(
                BiMeasure.organization_id == ctx.organization_id,
                BiMeasure.bank_id == bank.id,
                BiMeasure.measure_key.in_(referenced),
            )
        )
    )
    members: list[MeasureDef] = []
    for member_id in referenced:
        if member_id in stored:
            raise ExpressionRefused(
                f"{member_id} is a calculated measure. One calculated measure cannot be "
                "written in terms of another."
            )
        member = _catalogue_measure(cat, member_id)
        members.append(member)
    _authorize_members(db, ctx, bank, referenced, surface=surface)
    return CompiledExpression(
        source=source,
        digest=expression_digest(source),
        referenced_members=tuple(referenced),
        members=tuple(members),
    )


def _catalogue_measure(cat: Catalogue, member_id: str) -> MeasureDef:
    try:
        member: MemberDef = cat.member(member_id)
    except CatalogueUnknownMember as exc:
        raise ExpressionRefused(f"There is no figure called {member_id}.") from exc
    if not isinstance(member, MeasureDef):
        raise ExpressionRefused(
            f"{member.label} is something to group or filter by, not a figure to calculate with."
        )
    return member


def _authorize_members(  # noqa: PLR0913 - the complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    member_ids: Sequence[str],
    *,
    surface: str,
    permissions: tuple[Permission, ...] = (Permission.VIEW,),
) -> None:
    """The authorization walk for a formula: every figure it names, or a refusal.

    Expressed as a ``BiQuery`` over exactly those members and put to
    ``authorize_query``, so a calculated measure is judged by the same function
    and the same rules as the query that would read the same figures — including
    exact sensitivity matching, entitlement, and the human-principal refusal.
    """

    authority = ViewerAuthority(
        db=db,
        ctx=ctx,
        bank=bank,
        cat=catalogue(),
        surface=surface,
        permissions=permissions,
    )
    probe = BiQuerySchema(measures=list(member_ids), time=BiTime(as_of=utc_now().date()))
    refused = authority.refused_members(probe)
    if refused:
        raise MembersDenied(refused, reason=authority.reason or "member_scope_unrecognized")


def create_measure(  # noqa: PLR0913 - one complete measure, stated explicitly
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    owner_user_id: UUID,
    measure_key: str,
    label: str,
    description: str,
    compiled: CompiledExpression,
    value_type: str,
    favourable_direction: str,
) -> BiMeasure:
    """Store a personal calculated measure. The caller commits."""

    _require_key_available(
        db, organization_id=organization_id, bank_id=bank_id, measure_key=measure_key
    )
    now = utc_now()
    measure = BiMeasure(
        organization_id=organization_id,
        bank_id=bank_id,
        measure_key=measure_key,
        owner_user_id=owner_user_id,
        label=label,
        description=description,
        expression=compiled.source,
        expression_digest=compiled.digest,
        referenced_members=list(compiled.referenced_members),
        value_type=value_type,
        favourable_direction=favourable_direction,
        state="personal",
        created_at=now,
        updated_at=now,
    )
    db.add(measure)
    db.flush()
    return measure


def _require_key_available(
    db: Session, *, organization_id: str, bank_id: str, measure_key: str
) -> None:
    """A measure id must be free in this institution and unknown to the catalogue.

    Shadowing a catalogue member would make one id mean two figures, and a
    formula or a query naming it would then read whichever the resolver happened
    to consult first.
    """

    try:
        catalogue().member(measure_key)
    except CatalogueUnknownMember:
        pass
    else:
        raise MeasureKeyUnavailable(
            f"{measure_key} is the name of a figure the platform already provides."
        )
    taken = db.scalar(
        select(BiMeasure.id).where(
            BiMeasure.organization_id == organization_id,
            BiMeasure.bank_id == bank_id,
            BiMeasure.measure_key == measure_key,
        )
    )
    if taken is not None:
        raise MeasureKeyUnavailable(
            f"{measure_key} is already used by another calculated measure here."
        )


def update_measure(  # noqa: PLR0913 - one complete measure, stated explicitly
    db: Session,
    measure: BiMeasure,
    *,
    actor_user_id: UUID,
    label: str,
    description: str,
    compiled: CompiledExpression,
    value_type: str,
    favourable_direction: str,
) -> BiMeasure:
    """Edit a measure. Owner only, and a changed formula drops any certification.

    An approver approved a FORMULA, not a name: once the text moves, the
    certification and the proposal that led to it are cleared and the measure is
    personal again. The database refuses the alternative — a certified row whose
    ``approved_expression_digest`` differs from its ``expression_digest`` cannot
    be stored (``ck_bi_measures_certified_matches_expression``) — so this is not
    a convention that a future path could forget.
    """

    if measure.owner_user_id != actor_user_id:
        raise NotTheOwner("calculated measure")
    measure.label = label
    measure.description = description
    measure.value_type = value_type
    measure.favourable_direction = favourable_direction
    if compiled.digest != measure.expression_digest:
        measure.expression = compiled.source
        measure.expression_digest = compiled.digest
        measure.referenced_members = list(compiled.referenced_members)
        _clear_promotion(measure)
    measure.updated_at = utc_now()
    db.flush()
    return measure


def _clear_promotion(measure: BiMeasure) -> None:
    measure.state = "personal"
    measure.proposed_by_user_id = None
    measure.proposed_at = None
    measure.proposed_expression_digest = None
    measure.proposal_reason = None
    measure.approved_by_user_id = None
    measure.approved_at = None
    measure.approved_expression = None
    measure.approved_expression_digest = None
    measure.approval_reason = None


def propose_measure(
    db: Session, measure: BiMeasure, *, actor_user_id: UUID, reason: str
) -> BiMeasure:
    """The maker's half of the promotion: put a personal measure up for certification.

    Owner only. A measure already proposed or certified is a conflict rather than
    a silent re-proposal, so the checker is never looking at a proposal that was
    replaced under them.
    """

    if measure.owner_user_id != actor_user_id:
        raise NotTheOwner("calculated measure")
    if measure.state != "personal":
        raise MeasureStateConflict("This measure has already been sent for review or certified.")
    measure.state = "proposed"
    measure.proposed_by_user_id = actor_user_id
    measure.proposed_at = utc_now()
    measure.proposed_expression_digest = measure.expression_digest
    measure.proposal_reason = reason
    measure.updated_at = utc_now()
    db.flush()
    return measure


def promotion_sod_decision(
    *, proposed_by_user_id: UUID | None, approver_user_id: UUID
) -> grant_administration.SodDecision:
    """The separation-of-duties verdict for one promotion.

    Expressed in the platform's own policy types
    (``app/services/grant_administration.py``) rather than as a boolean, because
    this is the same control that governs every other authority decision here and
    the surfaces that display it already read this shape. The finding code is the
    one the tenant-facing SoD vocabulary uses for a maker/checker collision, and
    the outcome is a BLOCK: there is no runtime condition that could catch a
    self-certified measure later, so the separation is enforced where it can be —
    at the decision.
    """

    if proposed_by_user_id is not None and proposed_by_user_id == approver_user_id:
        return grant_administration.SodDecision(
            grant_administration.SodOutcome.BLOCK,
            (
                grant_administration.SodFinding(
                    code="maker_checker_runtime_condition_required",
                    message=(
                        "A person cannot certify a calculated measure they proposed. "
                        "Someone else has to review it."
                    ),
                ),
            ),
        )
    return grant_administration.SodDecision(grant_administration.SodOutcome.ALLOW)


def _authorize_certification(  # noqa: PLR0913 - the complete checker sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    member_ids: Sequence[str],
    *,
    proposer_user_id: UUID | None,
    actor_user_id: UUID,
    surface: str,
) -> None:
    """Require the checker to hold APPROVAL authority over every figure, in context.

    Two passes, and the second one cannot go through ``authorize_query``:

    * ``view`` over every figure, through the query path's own decision function,
      so entitlement, exact sensitivity matching and the deny-by-default on
      anything unrecognised are the read surface's;
    * ``approve`` over every ``(module, sensitivity)`` pair the figures need,
      through ``scoped_authorization.evaluate_bank_permission`` — because
      ``Permission.APPROVE`` names MAKER/CHECKER as required runtime context
      (``app/services/authorization.py::_REQUIRED_RUNTIME_CONDITIONS``), and a
      caller that does not establish it is DENIED by the evaluator. That is the
      platform's design and it is right: whoever approves must be named against
      whoever prepared. A promotion knows both, so the condition is supplied
      truthfully here and the evaluator enforces the separation a second time,
      independently of :func:`promotion_sod_decision`.
    """

    _authorize_members(db, ctx, bank, member_ids, surface=surface)
    distinct = proposer_user_id is None or proposer_user_id != actor_user_id
    conditions = (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "the measure's checker is not its proposer"
                if distinct
                else "a calculated measure's proposer cannot certify it"
            ),
        ),
    )
    members = query_members(
        catalogue(),
        BiQuerySchema(measures=list(member_ids), time=BiTime(as_of=utc_now().date())),
    )
    denied: list[str] = []
    reason = ""
    for (module_value, sensitivity_value), pair_members in scope_pairs(members).items():
        try:
            module, sensitivity = Module(module_value), Sensitivity(sensitivity_value)
        except ValueError:
            denied.extend(pair_members)
            reason = reason or "member_scope_unrecognized"
            continue
        decision = scoped_authorization.evaluate_bank_permission(
            db,
            ctx,
            bank,
            permission=Permission.APPROVE,
            module=module,
            sensitivity=sensitivity,
            surface=surface,
            conditions=conditions,
        )
        if decision is None or not decision.allowed:
            denied.extend(pair_members)
            reason = reason or (
                "binding_evaluation_failed" if decision is None else decision.reason
            )
    if denied:
        raise MembersDenied(denied, reason=reason or "binding_evaluation_failed")


def decide_promotion(  # noqa: PLR0913 - the complete checker sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    measure: BiMeasure,
    *,
    actor_user_id: UUID,
    decision: Literal["approve", "reject"],
    reason: str,
    expression_digest_reviewed: str,
    surface: str,
) -> tuple[BiMeasure, grant_administration.SodDecision]:
    """The checker's half: certify the measure for the institution, or send it back.

    Four refusals, in this order, and none of them is skippable:

    1. the measure must be ``proposed``;
    2. the formula must be the one that was reviewed — a decision taken against a
       version that has since moved is not a decision about what would be
       certified (:class:`ExpressionMoved`);
    3. proposer ≠ approver, through
       :func:`promotion_sod_decision`, raising the platform's own
       ``SodPolicyBlocked``;
    4. the approver's access must cover APPROVING every figure the formula names,
       not merely viewing them. Certifying a measure for the whole institution is
       an approval, so it needs approval authority over the same sentences the
       figures need — the conjunction the export surface already applies for
       ``view`` + ``export``.
    """

    if measure.state != "proposed":
        raise MeasureStateConflict("This measure is not waiting for a review.")
    if measure.expression_digest != expression_digest_reviewed:
        raise ExpressionMoved()
    sod = promotion_sod_decision(
        proposed_by_user_id=measure.proposed_by_user_id, approver_user_id=actor_user_id
    )
    if sod.outcome is grant_administration.SodOutcome.BLOCK:
        raise grant_administration.SodPolicyBlocked(sod)
    _authorize_certification(
        db,
        ctx,
        bank,
        # ``None``: this parse establishes WHICH FIGURES the checker must hold
        # authority over. A retention window that has since been shortened must not
        # be able to block a rejection, and the compiler applies that bound itself
        # before the measure can ever be read.
        tuple(expr.referenced_members(_parse(measure.expression, retention_days=None))),
        proposer_user_id=measure.proposed_by_user_id,
        actor_user_id=actor_user_id,
        surface=surface,
    )
    now = utc_now()
    if decision == "reject":
        # Back to its author's hands, with the proposal cleared: a rejected
        # measure is personal again, and the reason travels on the audit event
        # rather than being left on the row as a state nobody can act on.
        _clear_promotion(measure)
        measure.updated_at = now
        db.flush()
        return measure, sod
    measure.state = "bank_certified"
    measure.approved_by_user_id = actor_user_id
    measure.approved_at = now
    measure.approved_expression = measure.expression
    measure.approved_expression_digest = measure.expression_digest
    measure.approval_reason = reason
    measure.updated_at = now
    db.flush()
    return measure, sod


def load_measure(db: Session, *, organization_id: str, bank_id: str, measure_id: UUID) -> BiMeasure:
    """One measure of THIS institution, scoped at the query.

    Institution as well as organization: two banks of one organization share an
    RLS tenant, so organization scoping alone would let one bank's identity read
    the other's measure by id.
    """

    measure = db.scalar(
        select(BiMeasure).where(
            BiMeasure.id == measure_id,
            BiMeasure.organization_id == organization_id,
            BiMeasure.bank_id == bank_id,
        )
    )
    if measure is None:
        raise MeasureNotFound("No such calculated measure.")
    return measure


def certified_measure_keys(db: Session, ctx: TenantContext, bank: Bank) -> frozenset[str]:
    """Every measure key this institution has CERTIFIED, for the canvas rule.

    ``check_canvas_shape`` refuses a calculated measure on a saved dashboard
    unless it is certified, and its ``certified_measures`` argument defaults to
    EMPTY so a caller that forgets it refuses everything rather than admitting
    one unchecked. That default is right, and it made a real defect quiet: the
    only production caller omitted the argument, so a bank-certified measure was
    refused on every saved canvas with production copy telling the author to
    certify what was already certified (audit A9-04).

    Deliberately NOT filtered by reader: this is the institution's certification
    state, not an access question. Whether the author may READ the figures the
    formula names is decided separately, and earlier, by ``authorize_canvas``.
    """

    rows = db.scalars(
        select(BiMeasure.measure_key).where(
            BiMeasure.organization_id == ctx.organization_id,
            BiMeasure.bank_id == bank.id,
            BiMeasure.state == "bank_certified",
        )
    )
    return frozenset(str(key) for key in rows)


def readable_measures(
    db: Session, ctx: TenantContext, bank: Bank, *, viewer_id: UUID, surface: str
) -> tuple[BiMeasure, ...]:
    """Every measure this identity may read: their own, and the certified ones.

    A measure whose figures the reader's access does not cover is ABSENT rather
    than listed as restricted. A formula names catalogue members, and its label
    is authored text that can describe one ("exposure to the largest three
    obligors"), so naming it to someone refused those figures is the disclosure
    the read surface refuses everywhere else. There is correspondingly no
    restricted variant of the measure read model.
    """

    rows = db.scalars(
        select(BiMeasure)
        .where(
            BiMeasure.organization_id == ctx.organization_id,
            BiMeasure.bank_id == bank.id,
        )
        .order_by(BiMeasure.label, BiMeasure.id)
    )
    authority = ViewerAuthority(db=db, ctx=ctx, bank=bank, cat=catalogue(), surface=surface)
    visible: list[BiMeasure] = []
    for measure in rows:
        if measure.owner_user_id != viewer_id and measure.state not in SHARED_MEASURE_STATES:
            # Someone else's personal draft is theirs alone. A proposal is
            # visible because a checker has to be able to read what they are
            # being asked to certify, and a certified measure because it is the
            # institution's vocabulary by then.
            continue
        if not _readable(authority, measure):
            continue
        visible.append(measure)
    return tuple(visible)


def readable(
    db: Session, ctx: TenantContext, bank: Bank, measure: BiMeasure, *, surface: str
) -> bool:
    """Whether this identity's access covers every figure ``measure`` names."""

    authority = ViewerAuthority(db=db, ctx=ctx, bank=bank, cat=catalogue(), surface=surface)
    return _readable(authority, measure)


def _readable(authority: ViewerAuthority, measure: BiMeasure) -> bool:
    """Re-derived from the stored TEXT, never from ``referenced_members``.

    The stored list is a recorded copy of the server's own walk, kept so the row
    is auditable. Authorizing from it would make a cached column the authority
    for a read, and a cache that fell behind the formula it summarises is a
    figure read without a binding.
    """

    try:
        member_ids = expr.referenced_members(expr.parse(measure.expression, retention_days=None))
    except expr.ExpressionError:
        # A stored formula that no longer parses is not readable by anyone: the
        # measure's own figures cannot be established, so neither can the
        # sentences they need.
        return False
    if not member_ids:
        return False
    probe = BiQuerySchema(measures=list(member_ids), time=BiTime(as_of=utc_now().date()))
    try:
        return not authority.refused_members(probe)
    except UnknownMember:
        # A formula naming a member the catalogue has since retired cannot be
        # authorized, so it is readable by nobody — the refusing reading, and the
        # one that keeps a stale stored id from rendering as a figure.
        return False


def badge_for(measure: BiMeasure) -> str:
    """The certification badge a surface shows for this measure."""

    return _BADGE_BY_STATE[measure.state]


def catalogue_version() -> str:
    """The catalogue the member ids in a stored canvas were written against."""

    return CATALOGUE_VERSION
