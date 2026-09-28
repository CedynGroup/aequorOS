"""Deny-by-default authorization for ONE BI query (S3–S9, S18–S19; D-026, D-028).

A BI query is not a route: one request can read twenty-five measures over nine
dimensions drawn from six modules at three sensitivities, so the authorization
sentence cannot be declared on the handler the way ``/fx/dashboard`` declares
FX/aggregated/view. It is declared on the CATALOGUE — every member carries its
own ``module`` and ``sensitivity`` (D-028) — and this module turns the query
into the set of sentences it needs, requires every one of them, and returns a
result the caller renders as data or as a 403 with no rows.

Five rules, each of which has been a leak somewhere in this industry:

1. **A filter counts exactly as a projection does.** "Total exposure WHERE
   counterparty name = 'X'" reveals that obligor as surely as grouping by the
   name would. Filters, the Top-N dimension, the pivot axis, every sort key and
   every member a measure is COMPOSED from (a ratio's numerator, denominator or
   weight; a concentration measure's ``over`` dimension) are walked with the
   same weight as a requested measure, and a hierarchy id is walked as all of
   its levels — drilling from counterparty type into a named counterparty
   crosses from ``aggregated`` into ``restricted`` and is evaluated as such.
2. **Sensitivity is exact, never a ladder.** A ``confidential`` binding does
   not satisfy an ``aggregated`` member; the evaluator matches
   ``all``-or-exact on both module and sensitivity
   (``app/core/authorization.py``) and BI adds no ordering of its own. That is
   why each distinct ``(module, sensitivity)`` pair is a separate evaluation.
3. **Only an interactive human principal may read BI** (D-026). A machine key
   and an operator acting as examiner are refused before any pair is
   evaluated: every served query writes ``bi_query_log``, and both of those
   credentials are defined by what they may NOT persist. The Phase 4 feed route
   has its own machine dependency; this function never authorizes one.
4. **Anything unrecognised denies.** A member whose module or sensitivity is
   not a platform value, a module with no entitlement slug, an evaluator that
   fails: each is a denial with a named reason, never a fallback to allow.
5. **WHICH SLICE is part of the decision, and a figure about the whole bank is
   refused to a part of it** (S18, D-029). The matched bindings are reduced to
   one ``BiDataScope`` through ``services.authorization.effective_data_scope``,
   and a scoped principal asking for a ``grain="institution"`` measure — or for
   a portfolio measure whose fact carries no branch key — is DENIED rather than
   quietly served a slice, because a capital ratio computed over one branch is a
   wrong number wearing a right name. Every denied decision carries
   ``NO_INSTITUTION_DATA``, so a caller that forgets to check ``allowed`` cannot
   read a refusal as institution-wide authority.

Designation is NOT authority (D-022): an ``advisory_only`` or
``supervisory_monitoring`` measure is authorized exactly like a filed one and
is merely badged differently by the UI. Entitlement IS separate: the tenant's
licence class must carry the member's module in its institution-type
``default_modules``, the same gate ``require_module_access`` applies to the
routers (``app/api/deps.py``); it is a licence-class question, not a grant, so
it is checked per module rather than per pair.

What the ROUTE still owns (this module writes nothing, and reads only the
binding, licence-class and certified-measure rows its own decision is made of):
resolving the institution through ``resolve_tenant_bank`` so a sibling
tenant's ``BK-*`` is 404 before any of this runs (S1/S3), raising the 403,
writing the ``bi_query_log`` row from ``member_ids`` / ``denied_members``, and
building the ETag from the query, the build fingerprint, the principal, the
matched binding ids, the resolved data scope and ``authv`` (S19).

The declared scope this module returns becomes ROWS in
``app/services/bi/data_scope.py``, which is where a region turns into the branch
codes the mart holds and into the one unremovable filter the compiler ANDs in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.core.observability import authorization_denied
from app.db.base import Base
from app.domain.bi.authority import AUTHORIZATION_MODULE, ENTITLEMENT_SLUG
from app.domain.bi.catalogue import Catalogue, HierarchyDef, MeasureDef, MemberDef
from app.domain.bi.catalogue import UnknownMember as CatalogueUnknownMember
from app.domain.bi.catalogue.measures import ENTITLEMENT_BY_MODULE
from app.models import Bank
from app.schemas.bi import BiQuery
from app.services import authorization as account_authorization
from app.services import institution_types, scoped_authorization
from app.services.bi.errors import UnknownMember

#: Authorization module value → the ``institution_types.default_modules`` slug
#: that entitles a licence class to it. Derived from the catalogue's own two
#: maps rather than restated: ``AUTHORIZATION_MODULE``/``ENTITLEMENT_SLUG``
#: cover every engine module and ``ENTITLEMENT_BY_MODULE`` the portfolio ones
#: (it adds ``risk``, which no engine produces). A module absent from here is
#: a denial, so a future catalogue module cannot arrive unentitled and open.
ENTITLEMENT_SLUGS: Mapping[str, str] = MappingProxyType(
    {
        **{AUTHORIZATION_MODULE[key]: ENTITLEMENT_SLUG[key] for key in AUTHORIZATION_MODULE},
        **ENTITLEMENT_BY_MODULE,
    }
)

#: Reasons, as stable strings for telemetry and the query log. The human one
#: is deliberately the platform's existing label (``scoped_authorization``
#: emits it for the same principals) so one telemetry filter catches BI too.
REASON_ALLOWED = "allowed"
REASON_HUMAN_REQUIRED = "human_scoped_binding_required"
REASON_NOT_ENTITLED = "module_not_entitled"
REASON_SCOPE_UNRECOGNIZED = "member_scope_unrecognized"
REASON_EVALUATION_FAILED = "binding_evaluation_failed"
#: A figure the catalogue declares at ``grain="institution"`` — a capital ratio,
#: a liquidity ratio, anything the engines compute for the bank as a whole —
#: asked for by a principal whose bindings cover part of the book. Refused, never
#: narrowed: an institution ratio computed over one branch is a wrong number
#: wearing a right name (S18).
REASON_INSTITUTION_GRAIN = "institution_grain_requires_whole_institution"
#: A portfolio-grain figure whose FACT carries no branch key, so the row cannot
#: be attributed to a branch at all. ``bi_fact_target`` is the live example: a
#: target row is stated for the whole bank (``scope_dimension='bank_wide'``), so
#: serving it beside one branch's actual would produce an attainment of a branch
#: against the institution's budget.
REASON_BANK_WIDE_FIGURE = "bank_wide_figure_requires_whole_institution"
#: Every pair allowed, yet no binding id came back — so nothing authorized the
#: read and there is no scope to serve it under. Unreachable through the pair
#: loop (an allowed pair always names its bindings) and refused rather than
#: assumed, because the assumption would be "the whole institution".
REASON_NO_AUTHORIZING_BINDING = "no_authorizing_binding"
#: Two permissions of one conjunctive read (``view`` AND ``export``) were
#: satisfied by bindings whose scopes are narrow and DIFFERENT. There is no
#: ordering between two branch sets, so the read is refused rather than served
#: under a guess. Raised by ``read_bi._merged_decision``, not by this function,
#: which only ever evaluates one permission.
REASON_DATA_SCOPE_CONFLICT = "data_scope_conflict"

#: Mirrors ``services.authorization.DataScopeKind``: the three storable column
#: values plus the two a REDUCTION can produce and no row may carry.
DataScopeKind = Literal["all", "branch", "region", "mixed", "none"]


@dataclass(frozen=True, slots=True)
class BiDataScope:
    """Which slice of the institution the matched bindings admit (D-029).

    The DECLARED scope, mirroring ``services.authorization.EffectiveDataScope``
    field for field: ``authorize_query`` reduces ``matching_binding_ids`` through
    that one authority and carries the answer here. Declared, not resolved — a
    ``region`` names regions, and turning a region into the branch codes the mart
    actually holds is ``app/services/bi/data_scope.py``'s job, because the
    account plane must not read ``bi_*`` tables.

    Two kinds cannot be stored on a binding row and only ever describe a union:
    ``mixed`` (branch rows and region rows together) and ``none`` (nothing
    authorized the read). ``none`` is deliberately not ``all``: a reader who
    forgets to check ``allowed`` falls into the scoped path, where it serves no
    rows, rather than into the whole book.
    """

    kind: DataScopeKind = "all"
    #: Declared branch codes, sorted and de-duplicated.
    branches: tuple[str, ...] = ()
    #: Declared region names, sorted and de-duplicated.
    regions: tuple[str, ...] = ()

    @property
    def whole_institution(self) -> bool:
        return self.kind == "all"

    @property
    def serves_nothing(self) -> bool:
        return self.kind == "none"


#: The scope of a principal holding at least one institution-wide sentence.
ALL_INSTITUTION_DATA = BiDataScope()
#: The scope of a query nothing authorized. Every DENIED decision carries this,
#: so ``decision.data_scope.whole_institution`` can never be read as authority.
NO_INSTITUTION_DATA = BiDataScope(kind="none")


def bi_data_scope(scope: account_authorization.EffectiveDataScope) -> BiDataScope:
    """The account plane's reduction, as the BI decision carries it.

    A conversion rather than a re-export so the BI decision object stays free of
    an account-plane type, and so the two shapes are pinned together by a test
    rather than by a coincidence of imports.
    """

    return BiDataScope(kind=scope.kind, branches=scope.branches, regions=scope.regions)


#: The column a fact carries when its rows can be attributed to one branch, and
#: the key the compiler joins ``bi_dim_branch`` on
#: (``compiler._DIM_JOIN_KEYS``; ``tests/services/bi/test_data_scope.py`` pins
#: the two together so the restatement cannot drift).
BRANCH_FACT_KEY = "branch_code"


def branch_attributable(table_name: str) -> bool:
    """Whether every row of this fact belongs to exactly one branch.

    Read off the mapped table rather than listed, so a fact added without a
    branch key is refused to a scoped principal on the day it lands instead of
    on the day somebody remembers a list. An unknown table name is not
    attributable: deny-by-default applies to the question too.
    """

    table = Base.metadata.tables.get(table_name)
    return table is not None and BRANCH_FACT_KEY in table.c


@dataclass(frozen=True, slots=True)
class BiAuthorization:
    """The decision for one query: what may be served, and what may not.

    ``denied_members`` carries member IDS only — never a filter value, never a
    row, never SQL (S26). It is the complete set, not the first failure, so the
    UI can name every grant the principal is missing in one message.
    """

    allowed: bool
    denied_members: tuple[str, ...]
    matching_binding_ids: tuple[UUID, ...]
    data_scope: BiDataScope
    #: Why, for telemetry and ``bi_query_log``; ``"allowed"`` when it is.
    reason: str = REASON_ALLOWED
    #: Every member the query touches, expansions included, in query order.
    #: The query log records these whatever the decision was.
    member_ids: tuple[str, ...] = field(default_factory=tuple)


def query_members(cat: Catalogue, q: BiQuery) -> tuple[MemberDef, ...]:
    """Every catalogue member the query touches, directly or indirectly.

    Walks measures, dimensions, filters, the Top-N dimension, the pivot axis
    and the sort keys; expands a hierarchy id into its levels and a measure
    into the measures it is composed from and the dimension it concentrates
    over, transitively. De-duplicated, in first-seen order. An id the catalogue
    does not know raises :class:`UnknownMember` (422) — the same refusal the
    compiler makes, so the two agree whichever runs first.
    """

    hierarchies: dict[str, HierarchyDef] = {h.id: h for h in cat.hierarchies()}
    found: dict[str, MemberDef] = {}

    def visit(member_id: str) -> None:
        hierarchy = hierarchies.get(member_id)
        if hierarchy is not None:
            for level in hierarchy.levels:
                visit(level)
            return
        if member_id in found:
            return
        try:
            member = cat.member(member_id)
        except CatalogueUnknownMember as exc:
            raise UnknownMember(member_id) from exc
        # Recorded BEFORE recursing, so a self-referential composition
        # terminates instead of exhausting the stack.
        found[member_id] = member
        if not isinstance(member, MeasureDef):
            return
        for reference in (member.numerator, member.denominator, member.weight, member.over):
            if reference is not None:
                visit(reference)

    for measure_id in q.measures:
        visit(measure_id)
    for dimension_id in q.dimensions:
        visit(dimension_id)
    for predicate in q.filters:
        visit(predicate.member)
    if q.top_n is not None:
        visit(q.top_n.dimension)
    if q.pivot is not None:
        visit(q.pivot.dimension)
    for sort in q.sort:
        visit(sort.member)
    return tuple(found.values())


def scope_pairs(members: tuple[MemberDef, ...]) -> Mapping[tuple[str, str], tuple[str, ...]]:
    """The distinct ``(module, sensitivity)`` pairs and the members needing each.

    This is the unit of evaluation: the evaluator is called once per pair, so a
    twenty-five-measure query over three modules at one sensitivity costs three
    calls, not twenty-five.
    """

    pairs: dict[tuple[str, str], list[str]] = {}
    for member in members:
        pairs.setdefault((member.module, member.sensitivity), []).append(member.id)
    return MappingProxyType({pair: tuple(ids) for pair, ids in pairs.items()})


def _interactive_human(ctx: TenantContext) -> bool:
    """Whether the principal is a tenant human holding a current app token.

    The first two conditions are exactly what ``evaluate_bank_permission``
    tests; the last two name the two credentials D-026 refuses explicitly, so a
    future context shape that carried an ``authv`` for a key or an
    impersonation session would still be denied here.
    """

    return (
        ctx.actor_user_id is not None
        and ctx.authorization_version is not None
        and ctx.integration_key_id is None
        and ctx.impersonation_context is None
    )


def _platform_scope(module: str, sensitivity: str) -> tuple[Module, Sensitivity] | None:
    """The catalogue's declared strings as evaluator values, or ``None``."""

    try:
        return Module(module), Sensitivity(sensitivity)
    except ValueError:
        return None


def branch_readable(member: MemberDef) -> bool:
    """Whether a SCOPED principal may be served this member at all.

    True for every dimension — a dimension is a way of slicing, not a figure — and
    for a measure that is both portfolio-grain and carried on a fact with a branch
    key. Public because the catalogue route and the certified-pack resolver must
    hide exactly what the query path will refuse, rather than advertise a figure
    and then 403 it.
    """

    if not isinstance(member, MeasureDef):
        return True
    return member.grain != "institution" and branch_attributable(member.table)


def institution_grain_measures(members: Sequence[MemberDef]) -> tuple[str, ...]:
    """The ids of every measure the catalogue declares for the whole institution."""

    return tuple(
        member.id
        for member in members
        if isinstance(member, MeasureDef) and member.grain == "institution"
    )


def bank_wide_measures(members: Sequence[MemberDef]) -> tuple[str, ...]:
    """Portfolio-grain measures whose fact cannot be attributed to a branch."""

    return tuple(
        member.id
        for member in members
        if isinstance(member, MeasureDef)
        and member.grain != "institution"
        and not branch_attributable(member.table)
    )


def _unattributable_measures(members: Sequence[MemberDef]) -> tuple[tuple[str, ...], str]:
    """Every measure a SCOPED principal may not be served, and the primary reason.

    Two different defects, one refusal. A ``grain="institution"`` measure is a
    figure ABOUT the whole bank: narrowing it to a branch would return a smaller
    number under the same name. A portfolio-grain measure on a fact with no
    branch key is a figure the platform cannot attribute at all — the bank-wide
    target rows are the live case — so a branch filter would either refuse at
    compile time or, worse, be dropped.

    Both ids sets are returned together, because what the Org Owner needs is
    every figure the grant would have to widen for, not the first one; the reason
    names the class the denial is primarily about, so the query log and the
    telemetry stay one stable string per rule.
    """

    grain = institution_grain_measures(members)
    bank_wide = bank_wide_measures(members)
    if not grain and not bank_wide:
        return (), REASON_ALLOWED
    reason = REASON_INSTITUTION_GRAIN if grain else REASON_BANK_WIDE_FIGURE
    return (*grain, *bank_wide), reason


def authorize_query(  # noqa: PLR0913 - the complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    cat: Catalogue,
    q: BiQuery,
    *,
    permission: Permission = Permission.VIEW,
    surface: str,
) -> BiAuthorization:
    """Require every sentence the query needs, for one already-resolved bank.

    ``bank`` must come from the tenant-scoped resolver, so a cross-tenant id is
    404 before this runs; a bank of this tenant the principal holds no
    institution coverage for is a denial here (S3). ``surface`` is the
    ``bi_query_log`` surface (``query``, ``grid``, ``drill``, ``explain``,
    ``export``, ``feed``) and reaches the shared decision telemetry as
    ``bi_<surface>``.

    Raises :class:`UnknownMember` (422) for an id the catalogue does not know;
    propagates ``InstitutionTypeUnresolved`` (409) when the institution's
    licence class does not resolve, because that is a configuration failure the
    platform reports as such everywhere else rather than a denied grant.
    Everything else is a returned decision; nothing raises 403.
    """

    # A calculated measure is authorized as the FIGURES ITS TEXT NAMES, never as
    # itself. A formula is a bank's own arithmetic over catalogue members, so the
    # sentence a reader must hold is the union of the sentences those members
    # need; there is no separate grant for a formula and there must not be one, or
    # a measure would become a way to reach a figure through a name nobody
    # evaluated. The expansion re-parses the APPROVED text server-side every time
    # and never reads the stored member column, so doctoring that column cannot
    # widen the walk, and an id it cannot resolve is left exactly as it arrived so
    # it still refuses as unknown.
    #
    # Imported inside the call because the compiler imports this module: the
    # authorization decision is the lower layer and must not depend on the query
    # builder at module scope.
    from app.services.bi.compiler import (  # noqa: PLC0415 - one-way dependency at run time
        expand_calculated_measures,
    )

    walked = expand_calculated_measures(
        db, cat, q, organization_id=ctx.organization_id, bank_id=bank.id
    )
    members = query_members(cat, walked)
    member_ids = tuple(member.id for member in members)
    telemetry_surface = f"bi_{surface}"

    if not _interactive_human(ctx):
        authorization_denied(
            reason=REASON_HUMAN_REQUIRED,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            permission=permission.value,
            members=len(member_ids),
        )
        return BiAuthorization(
            allowed=False,
            denied_members=member_ids,
            matching_binding_ids=(),
            data_scope=NO_INSTITUTION_DATA,
            reason=REASON_HUMAN_REQUIRED,
            member_ids=member_ids,
        )

    entitled = frozenset(institution_types.get_type(db, bank).default_modules)
    denied: list[str] = []
    reasons: list[str] = []
    matched: dict[UUID, None] = {}
    #: The ids that authorized EACH pair, kept apart. Unioning them before
    #: reducing was audit finding A10-01: ``reduce_data_scope``'s "any binding of
    #: kind ``all`` wins" is sound only WITHIN one resource's matches, and a pair
    #: is a resource. Across pairs, an ``all`` binding that authorized
    #: ``risk``/``aggregated`` silently discarded the branch restriction that
    #: applied to the credit pair — and because 32 of the catalogue's 66
    #: dimensions are ``risk``/``aggregated``, the reader triggered the widening
    #: themselves just by breaking a figure down by date.
    per_pair: list[tuple[UUID, ...]] = []

    def deny(member_ids_for_pair: tuple[str, ...], reason: str) -> None:
        denied.extend(member_ids_for_pair)
        reasons.append(reason)

    for (module_value, sensitivity_value), pair_members in scope_pairs(members).items():
        scope = _platform_scope(module_value, sensitivity_value)
        slug = ENTITLEMENT_SLUGS.get(module_value)
        if scope is None or slug is None:
            authorization_denied(
                reason=REASON_SCOPE_UNRECOGNIZED,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                module=module_value,
                sensitivity=sensitivity_value,
                surface=telemetry_surface,
            )
            deny(pair_members, REASON_SCOPE_UNRECOGNIZED)
            continue
        if slug not in entitled:
            authorization_denied(
                reason=REASON_NOT_ENTITLED,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                module=slug,
                surface=telemetry_surface,
            )
            deny(pair_members, REASON_NOT_ENTITLED)
            continue
        module, sensitivity = scope
        decision = scoped_authorization.evaluate_bank_permission(
            db,
            ctx,
            bank,
            permission=permission,
            module=module,
            sensitivity=sensitivity,
            surface=telemetry_surface,
        )
        if decision is None:
            deny(pair_members, REASON_EVALUATION_FAILED)
            continue
        if not decision.allowed:
            deny(pair_members, decision.reason)
            continue
        matched.update(dict.fromkeys(decision.matching_binding_ids))
        per_pair.append(tuple(decision.matching_binding_ids))

    if denied:
        return BiAuthorization(
            allowed=False,
            denied_members=tuple(denied),
            # A denied query is served nothing, so no binding authorized it and
            # there is no ETag to key on the rows that matched other pairs.
            matching_binding_ids=(),
            # NOT ``all``: a caller that forgets to check ``allowed`` must fall
            # into the scoped path, where an empty value set serves no rows,
            # rather than into the whole institution.
            data_scope=NO_INSTITUTION_DATA,
            reason=reasons[0],
            member_ids=member_ids,
        )

    # Every pair allowed. WHICH SLICE follows from the same bindings that allowed
    # it, reduced by the one authority (``services.authorization``) rather than
    # by a second reading of the columns here. The id sets are selectors: the
    # loader re-reads the rows, scoped to this organization and to an active,
    # in-window status, so a stale id contributes nothing.
    #
    # Reduced PER PAIR and then combined narrowest-wins (A10-01). The asymmetry
    # is deliberate and worth stating, because the two rules look contradictory
    # side by side: WITHIN one resource the widest binding wins, because bindings
    # OR and narrowing them would revoke authority the Org Owner granted; ACROSS
    # resources the narrowest wins, because the query needs every pair at once
    # and serving it means reading all of them — so the answer can only be the
    # intersection of what each pair admits. Two irreconcilable narrow slices are
    # refused rather than intersected: ``branch ["B1"]`` on one pair and
    # ``region ["North"]`` on another is a sentence the platform cannot answer
    # honestly, and guessing which the reader meant would be inventing authority.
    # ``read_bi._merged_decision`` applies the identical rule across PERMISSIONS.
    grants = account_authorization.load_effective_grants(
        db, organization_id=ctx.organization_id, binding_ids=tuple(matched)
    )
    pair_scopes = [
        bi_data_scope(
            account_authorization.reduce_data_scope(
                [grants[binding_id] for binding_id in ids if binding_id in grants]
            )
        )
        for ids in per_pair
    ]
    narrow = {scope for scope in pair_scopes if not scope.whole_institution}
    if len(narrow) > 1:
        authorization_denied(
            reason=REASON_DATA_SCOPE_CONFLICT,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            permission=permission.value,
        )
        return BiAuthorization(
            allowed=False,
            denied_members=member_ids,
            matching_binding_ids=(),
            data_scope=NO_INSTITUTION_DATA,
            reason=REASON_DATA_SCOPE_CONFLICT,
            member_ids=member_ids,
        )
    data_scope = next(iter(narrow), ALL_INSTITUTION_DATA if pair_scopes else NO_INSTITUTION_DATA)
    if data_scope.serves_nothing:
        authorization_denied(
            reason=REASON_NO_AUTHORIZING_BINDING,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            permission=permission.value,
        )
        return BiAuthorization(
            allowed=False,
            denied_members=member_ids,
            matching_binding_ids=(),
            data_scope=NO_INSTITUTION_DATA,
            reason=REASON_NO_AUTHORIZING_BINDING,
            member_ids=member_ids,
        )
    if not data_scope.whole_institution:
        unattributable, scope_reason = _unattributable_measures(members)
        if unattributable:
            authorization_denied(
                reason=scope_reason,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                surface=telemetry_surface,
                permission=permission.value,
                members=len(unattributable),
            )
            return BiAuthorization(
                allowed=False,
                denied_members=unattributable,
                matching_binding_ids=(),
                data_scope=NO_INSTITUTION_DATA,
                reason=scope_reason,
                member_ids=member_ids,
            )
    return BiAuthorization(
        allowed=True,
        denied_members=(),
        matching_binding_ids=tuple(matched),
        data_scope=data_scope,
        reason=REASON_ALLOWED,
        member_ids=member_ids,
    )
