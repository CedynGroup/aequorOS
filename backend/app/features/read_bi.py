"""The BI read surface: eight routes under ``/api/v1/banks/{bank_id}/bi/``.

``catalogue`` says what this caller may ask, ``query`` answers one question,
``grid`` and ``drill`` page an answer for the grid, ``explain`` says where a
figure came from, ``packs`` resolves a certified dashboard for a reader and a
reporting date, and ``insights`` says what the platform is prepared to state
about that date. Everything they need is built elsewhere: the catalogue
declares the members (``app/domain/bi/catalogue``), the compiler turns a
``BiQuery`` into one read-only statement (``app/services/bi/compiler.py``), the
authorization module decides it (``app/services/bi/authorization.py``), and the
builder wrote the marts, the pack files are parsed at import
(``app/domain/bi/packs``) and the insights layer decides what may be said
(``app/services/bi/insights``). What this module owns is the ORDER those are
consulted in, and it is the order that carries the security properties:

1. **The institution, before anything else.** Every route is mounted with
   ``BANK_ROUTE_DEPENDENCIES``, so a ``BK-*`` belonging to another tenant is
   ``404 Bank not found.`` before a permission is evaluated, a member is
   resolved or a log row is written (S1).
2. **Then the deployment flag.** ``BI_ENABLED`` is checked by a router-level
   dependency rather than at import time, because the mount decision would
   otherwise be frozen at the first ``get_settings()`` call for the life of the
   process — including for the test suite, which pins the flag per test. With
   the flag off every BI route answers 404: the feature is not "forbidden", it
   is not there. The route table still carries the routes, which is what lets
   the impersonation sweep (``tests/api/test_impersonation_boundary.py``) and
   the cross-tenant sweep see them at all.
3. **Then the principal.** :func:`require_bi_read` (D-027) admits only an
   interactive tenant human holding a current ``authv``: no machine key, no
   impersonated operator (D-026), and deliberately no scalar-role check —
   authority is the stored binding, never ``users.role``.
4. **Then the read budget** (D-010), counted over ``bi_query_log`` because a
   per-process bucket is wrong behind four uvicorn workers
   (``app/services/bi/query_log.py``).
5. **Then authorization, BEFORE the compiler.** A pivot query makes the
   compiler probe the pivot dimension's distinct values, which READS data — so
   nothing may be compiled until every member the query touches has been
   allowed. A denial is 403 naming the denied member ids and serves no rows.
6. **Then the ETag**, over the query digest, the mart build fingerprint, the
   catalogue version, the principal, its ``authv`` and the bindings that
   matched (S19). ``authv`` moves on every authority change and the fingerprint
   on every rebuild, so a stale cache cannot survive either. ``If-None-Match``
   is honoured with 304 — for POST too: the dashboard's own fetch layer sends
   it, and the ETag is what makes a re-render free.
7. **Then compile, execute under the server-side guards, and record.**

**Why the log is written before the response and never swallowed.** Every read
that reaches a logged surface lands in ``bi_query_log`` in the request
that made it — allowed, denied, refused as malformed, cancelled, or answered
304 — and the row is committed before the handler returns. The table is
append-only, so a row cannot be written first and completed later; the row
therefore goes in once everything it states is known. Nothing here catches an
exception from that write: a read that cannot be recorded is not served, and the
caller gets the failure rather than the data. ``decision`` says whether the read
was SERVED (``allowed``) or refused (``denied``); an authority denial is the
subset whose ``denied_members`` is non-empty, and a malformed request is a
``denied`` row naming no member at all — the unknown id is client bytes and is
never stored. ``row_count IS NULL`` is how a row says no rows were served.

``catalogue`` writes NO row: C1's ``surface`` CHECK did not name it, and
recording it under ``query`` or ``explain`` would put an event in an append-only
audit table that did not happen. It is still subject to the budget; it does not
refill it, so a principal polling only it is bounded by nothing but the process.
(The vocabulary also still admits ``trust``, the retired reconciliation-verdict
route's value — see ``QUERY_LOG_SURFACES``; nothing writes it.) ``packs`` and
``insights`` DO have surfaces of their own, so both record one row per request —
including the refusals, because the budget is counted over these rows and a
probe loop must not be free.

**Two conventions the last two routes add.** A ``packs`` row may carry
``denied_members`` on an ``allowed`` decision: a dashboard is a MIXED read by
construction, the reader WAS served it, and the fields they were refused inside
it are exactly what an operator needs in order to write the grant. And the
``query_hash`` of a ``packs`` or ``insights`` row is a digest of the SURFACE, the
date and the pack (:func:`_surface_digest`) rather than of a ``BiQuery``, because
for those two the question is not a query — the column keeps its meaning (two
identical questions hash alike) and still holds no value that can be read back.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Tenant, TenantBank, TenantContext
from app.core.authorization import Permission
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi.catalogue import (
    CATALOGUE_VERSION,
    Catalogue,
    DimensionDef,
    MeasureDef,
    MemberDef,
    catalogue,
)
from app.domain.bi.packs import PackError
from app.domain.bi.packs import pack as certified_pack
from app.domain.bi.packs import packs as certified_packs
from app.models import Bank
from app.models.bi import BiFactEngineMetric, BiMartBuild
from app.schemas.bi import (
    BI_MAX_MEASURES,
    BiBuildRead,
    BiCatalogueDimensionRead,
    BiCatalogueHierarchyRead,
    BiCatalogueMeasureRead,
    BiCatalogueRead,
    BiCatalogueValueRead,
    BiDataScopeRead,
    BiExplainComponentRead,
    BiExplainEngineRead,
    BiExplainRead,
    BiExplainRequest,
    BiFilter,
    BiGridPageRead,
    BiGridRowRead,
    BiInsightRead,
    BiInsightsRead,
    BiLayoutItem,
    BiPackAccess,
    BiPackListRead,
    BiPackRead,
    BiPackSpec,
    BiPackWidget,
    BiPackWidgetRead,
    BiPagedQueryRequest,
    BiQuery,
    BiQueryResult,
    BiResultColumn,
    BiTime,
)
from app.services import institution_types
from app.services.bi import data_scope, grid_adapter, provenance, query_log
from app.services.bi.authorization import (
    BiAuthorization,
    BiDataScope,
    authorize_query,
    authorize_query_conjunctive,
    branch_readable,
    query_members,
    scope_pairs,
)
from app.services.bi.compiler import ColumnSpec, CompiledQuery, compile_query
from app.services.bi.errors import BiQueryError
from app.services.bi.execution import QueryResult, execute
from app.services.bi.insights import assemble as assemble_insights
from app.services.bi.insights import (
    default_compare_to,
    engine_measure_applies,
    engine_regime,
)
from app.services.bi.insights.statements import Insight

router = APIRouter(tags=["bi"])

SURFACE_QUERY = "query"
SURFACE_GRID = "grid"
SURFACE_DRILL = "drill"
SURFACE_EXPLAIN = "explain"
#: A governed export. Its own surface so the log distinguishes a figure that was
#: LOOKED AT from one that left the platform as a file (D-065).
SURFACE_EXPORT = "export"
#: A certified dashboard resolved for a reader, and the statements the platform
#: will make about a reporting date. Both are logged: a pack read discloses which
#: fields a reader was refused inside a dashboard they opened, and an insights
#: read runs real statements over the marts.
SURFACE_PACKS = "packs"
SURFACE_INSIGHTS = "insights"
#: Not a ``bi_query_log`` surface — C1's ``surface`` CHECK names the data
#: surfaces and this is not one. Carried so the decision telemetry and the ETags
#: can name it (see the module docstring on the catalogue row).
SURFACE_CATALOGUE = "catalogue"

#: D-028: a member that identifies ONE record is ``confidential``. A drill is
#: the record-level surface, so it must name at least one of them — otherwise it
#: is an aggregate query on the wrong route, and the authority it would be
#: served under is not the record-level one.
RECORD_LEVEL_SENSITIVITY = "confidential"

_HEADER_SEPARATOR = "\x1f"


# --- dependencies -----------------------------------------------------------------------


def require_bi_enabled() -> None:
    """404 unless ``BI_ENABLED`` is set in THIS deployment.

    Read per request, not at import: ``get_settings()`` is cached for the life
    of the process, so an import-time mount would freeze the decision before a
    deployment (or a test) could state it. A deployment without BI answers every
    BI path exactly as it answers a path that does not exist.
    """

    if not get_settings().bi.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


@dataclass(frozen=True, slots=True)
class BiReadAccess:
    """An admitted BI reader, with the two identity values every route needs.

    ``principal_user_id`` and ``authorization_version`` are narrowed here, once,
    so no handler has to re-test what the dependency already refused.
    """

    ctx: TenantContext
    bank: Bank
    principal_user_id: UUID
    authorization_version: int


def require_bi_read(ctx: Tenant, bank: TenantBank) -> BiReadAccess:
    """Admit only an interactive tenant human with a current token (D-026, D-027).

    Named in ``deps.MUTATION_ROLE_DEPENDENCY_NAMES`` so the route-table sweep
    credits the POST reads as guarded: a ``BiQuery`` does not fit in a query
    string, and a POST resolving only ``Tenant`` is what that sweep convicts.

    It checks no scalar role on purpose. Every BI member carries its own
    ``(module, sensitivity)`` and is evaluated against the caller's stored
    bindings; a tenant ``examiner`` or ``viewer`` with the right binding reads,
    and an ``admin`` without one does not.
    """

    if ctx.impersonation_context is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_human_principal_required",
                "message": "Business intelligence is not available in an impersonated session.",
            },
        )
    if ctx.integration_key_id is not None:
        # Unreachable through the router (an integration key is refused at
        # authentication for any route outside the push set) and kept as the
        # second, local statement of D-026: a machine principal never reads BI.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_human_principal_required",
                "message": "Business intelligence is not available to an integration key.",
            },
        )
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_human_principal_required",
                "message": "Business intelligence requires an active scoped binding.",
            },
        )
    if bank is None:
        # Every BI path carries ``{bank_id}``, so the resolver cannot return
        # None here; refuse rather than narrow with an assertion.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return BiReadAccess(
        ctx=ctx,
        bank=bank,
        principal_user_id=ctx.actor_user_id,
        authorization_version=ctx.authorization_version,
    )


BiRead = Annotated[BiReadAccess, Depends(require_bi_read)]
IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]


# --- ETag -------------------------------------------------------------------------------


def _etag(*parts: object) -> str:
    """A strong ETag over the exact inputs that can change the answer."""

    material = _HEADER_SEPARATOR.join("" if part is None else str(part) for part in parts)
    return f'"{hashlib.sha256(material.encode("utf-8")).hexdigest()}"'


def _is_fresh(if_none_match: str | None, etag: str) -> bool:
    """Whether the caller already holds this exact representation."""

    if not if_none_match:
        return False
    candidates = [candidate.strip() for candidate in if_none_match.split(",")]
    return any(candidate == "*" or candidate.removeprefix("W/") == etag for candidate in candidates)


def _not_modified(etag: str) -> Response:
    return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=_cache_headers(etag))


def _cache_headers(etag: str) -> dict[str, str]:
    """``private`` so no shared cache stores one tenant's figures."""

    return {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}


class _NotModified(Exception):  # noqa: N818 - a control-flow signal, not an error
    """The caller's ``If-None-Match`` already names this representation."""

    def __init__(self, etag: str) -> None:
        super().__init__(etag)
        self.etag = etag


# --- the window, the build and the badge ------------------------------------------------


def _data_window(time: BiTime) -> tuple[date, date]:
    """Every date the query can read, comparison window included (``provenance``)."""

    return provenance.data_window(time)


def _build_fingerprint(
    db: Session, organization_id: str, bank_id: str, window: tuple[date, date]
) -> str | None:
    """The fingerprint of the mart state the window was read from (``provenance``)."""

    return provenance.build_fingerprint(
        db, organization_id=organization_id, bank_id=bank_id, window=window
    )


def _query_error(exc: BiQueryError) -> HTTPException:
    """A compiler / execution refusal as its HTTP shape, with no statement in it."""

    return HTTPException(
        status_code=exc.status_code,
        detail={"error_code": exc.code, "message": exc.message, "members": list(exc.members)},
    )


def _denied(cat: Catalogue, decision: BiAuthorization) -> HTTPException:
    """403 naming every denied member, with labels the caller cannot look up.

    A denied member is absent from the caller's own catalogue, so the id alone
    would leave the UI printing a wire key. The label is catalogue metadata, not
    tenant data, and naming it is what lets the message read as production copy.
    """

    labels: list[str] = []
    for member_id in decision.denied_members:
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
            "denied_members": list(decision.denied_members),
            "denied_member_labels": labels,
            "reason": decision.reason,
        },
    )


def _append(db: Session, record: query_log.QueryRecord) -> None:
    """Record one decision and commit it. Never guarded: see the module docstring."""

    query_log.record(db, record)
    db.commit()


def _require_budget(db: Session, access: BiReadAccess) -> None:
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


@dataclass(frozen=True, slots=True)
class _Authorized:
    """An allowed query, and everything the answer and the log row need."""

    decision: BiAuthorization
    window: tuple[date, date]
    build_fingerprint: str | None
    etag: str
    #: The log row with everything known before the statement ran.
    record: query_log.QueryRecord
    #: The decision's declared scope, resolved against THIS institution's branch
    #: dimension. Resolved once, here, because it enters the ETag as well as the
    #: statement: re-resolving at compile time would read the same rows twice and
    #: could disagree with the representation the ETag names.
    scope: data_scope.ResolvedDataScope = data_scope.WHOLE_INSTITUTION

    @property
    def injected_filters(self) -> tuple[BiFilter, ...]:
        """The caller's data scope as filters the request cannot remove (S18)."""

        return self.scope.filters


def _merged_decision(  # noqa: PLR0913 - the complete authorization sentence
    db: Session,
    access: BiReadAccess,
    cat: Catalogue,
    query: BiQuery,
    *,
    surface: str,
    permissions: Sequence[Permission],
) -> BiAuthorization:
    """Require EVERY permission in ``permissions`` over the query, as one decision.

    A thin binding of the admitted reader to
    :func:`~app.services.bi.authorization.authorize_query_conjunctive`, which
    owns the rule: allowed only if every pass allowed, denied members unioned,
    the reason from the first refusal, and the SCOPE combined identical-or-refuse
    across the passes — ``all`` yields to a narrow pass, two identical narrow
    passes serve that slice, two DIFFERENT narrow passes (``{B1}`` beside
    ``{B2}``, which have no ordering) refuse as ``data_scope_conflict`` rather
    than being guessed between or intersected. An EMPTY ``permissions`` is
    refused too: a read that asked for no sentence was authorized by nothing,
    and the deny-by-default answer is the only honest one (audit A360-1 found
    this function returning the whole institution for that case).

    Kept as a named seam so the routes read as "one decision"; the same helper
    renders a subscription as its recipient, so the two cannot drift.
    """

    return authorize_query_conjunctive(
        db,
        access.ctx,
        access.bank,
        cat,
        query,
        permissions=permissions,
        surface=surface,
    )


def _authorize(  # noqa: PLR0913 - the guarded pipeline's own inputs, all explicit
    db: Session,
    access: BiReadAccess,
    query: BiQuery,
    *,
    surface: str,
    if_none_match: str | None,
    permissions: Sequence[Permission] = (Permission.VIEW,),
) -> _Authorized:
    """Meter, authorize, fingerprint and revalidate — before anything compiles.

    Raises 403 on a denial, :class:`_NotModified` when the caller's ETag still
    holds, and logs both. Returns only on an allowed, non-cached read.
    """

    cat = catalogue()
    _require_budget(db, access)
    attempt = query_log.QueryRecord(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        surface=surface,
        query_hash=query_log.query_digest(query),
        decision=query_log.DECISION_ALLOWED,
        catalogue_version=CATALOGUE_VERSION,
    )
    try:
        decision = _merged_decision(
            db, access, cat, query, surface=surface, permissions=permissions
        )
    except BiQueryError as exc:
        # An id the catalogue does not know. No decision was reached, so the row
        # names no member and no denial — but the ATTEMPT is recorded, because the
        # budget is counted over these rows and a stream of malformed queries is
        # otherwise the one unmetered way to make this plane work (audit A6-06).
        # The unknown id itself is never stored: it is client bytes.
        _append(db, replace(attempt, decision=query_log.DECISION_DENIED))
        raise _query_error(exc) from exc
    window = _data_window(query.time)
    fingerprint = _build_fingerprint(db, access.ctx.organization_id, access.bank.id, window)
    # Resolved BEFORE the ETag and only for an allowed decision: a denied decision
    # carries ``kind="none"``, whose resolution refuses on purpose.
    scope = (
        data_scope.resolve(
            db,
            decision.data_scope,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
        )
        if decision.allowed
        else data_scope.ResolvedDataScope(kind="none")
    )
    etag = _etag(
        surface,
        ",".join(permission.value for permission in permissions),
        attempt.query_hash,
        fingerprint,
        CATALOGUE_VERSION,
        access.principal_user_id,
        access.authorization_version,
        # The RESOLVED scope, not just the bindings that named it: a region grant
        # covers whatever the region holds now, so a branch ingested into it
        # changes this answer without changing a binding id or ``authv``.
        scope.fingerprint,
        *sorted(str(binding_id) for binding_id in decision.matching_binding_ids),
    )
    record = replace(attempt, member_ids=decision.member_ids, build_fingerprint=fingerprint)
    if not decision.allowed:
        _append(
            db,
            replace(
                record,
                decision=query_log.DECISION_DENIED,
                denied_members=decision.denied_members,
                build_fingerprint=None,
            ),
        )
        raise _denied(cat, decision)
    if _is_fresh(if_none_match, etag):
        _append(db, record)
        raise _NotModified(etag)
    return _Authorized(
        decision=decision,
        window=window,
        build_fingerprint=fingerprint,
        etag=etag,
        record=record,
        scope=scope,
    )


def _injected_filters(authorized: _Authorized) -> tuple[BiFilter, ...]:
    """The caller's data scope as filters the request cannot remove (S18, D-029).

    Exactly one filter for a scoped principal and none for an institution-wide
    one, resolved by ``app/services/bi/data_scope.py``. It is unremovable because
    it is handed to ``compile_query`` beside the ``BiQuery`` rather than inside
    it: the client's own predicates cannot reach it, and the compiler ANDs the two
    — a request naming a branch outside the grant is served the intersection,
    which is nothing.
    """

    return authorized.injected_filters


def _scope_read(scope: data_scope.ResolvedDataScope) -> BiDataScopeRead:
    """The resolved slice, as the answer's own statement of what it covers.

    Always emitted, ``all`` included, so a client never has to treat "absent" and
    "the whole institution" as the same thing — a stale client that never learned
    about scopes would otherwise read a narrowed answer as institution-wide
    (audit A10-12).

    ``unresolved_regions`` is filled only in the case this layer can actually
    determine: every declared region resolving to NO branch at all. A partially
    resolved set is not derivable from ``ResolvedDataScope``, which keeps the union
    of codes rather than the per-region mapping, and guessing which region was the
    empty one would be inventing evidence. The credit plane computes the exact set
    because it resolves the register itself; this says less rather than more.
    """

    if scope.kind == "none":
        # A refused read has no slice to describe; every caller builds this read
        # model for an ALLOWED decision only. Refused as the platform's own
        # invariant violation rather than as the read model's validation error.
        raise data_scope.DataScopeServesNothing("A refused read has no data scope to describe.")
    unresolved: list[str] = []
    if scope.declared_regions and not scope.branch_codes:
        unresolved = list(scope.declared_regions)
    return BiDataScopeRead(
        kind=scope.kind,
        branches=list(scope.branch_codes),
        regions=list(scope.declared_regions),
        unresolved_regions=unresolved,
    )


def _logged_members(decision: BiAuthorization, compiled: CompiledQuery) -> tuple[str, ...]:
    """Every member the decision covered, plus the scope members that applied."""

    return (
        *decision.member_ids,
        *(
            member_id
            for member_id in compiled.injected_member_ids
            if member_id not in decision.member_ids
        ),
    )


def _compile(
    db: Session, access: BiReadAccess, query: BiQuery, authorized: _Authorized
) -> CompiledQuery:
    """Compile an authorized query, recording a refusal as a served-nothing read."""

    try:
        return compile_query(
            db,
            catalogue(),
            query,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            injected_filters=_injected_filters(authorized),
        )
    except BiQueryError as exc:
        _append(db, replace(authorized.record, row_count=None, duration_ms=None))
        raise _query_error(exc) from exc


def _run(  # noqa: PLR0913 - the guarded pipeline's own inputs, all explicit
    db: Session,
    access: BiReadAccess,
    query: BiQuery,
    authorized: _Authorized,
    *,
    row_cap: int,
    timeout_ms: int,
) -> tuple[CompiledQuery, QueryResult]:
    """Compile, execute under the guards, and record the outcome either way."""

    compiled = _compile(db, access, query, authorized)
    try:
        result = execute(db, compiled, timeout_ms=timeout_ms, row_cap=row_cap)
    except BiQueryError as exc:
        _append(
            db,
            replace(
                authorized.record,
                member_ids=_logged_members(authorized.decision, compiled),
                row_count=None,
                duration_ms=None,
            ),
        )
        raise _query_error(exc) from exc
    _append(
        db,
        replace(
            authorized.record,
            member_ids=_logged_members(authorized.decision, compiled),
            row_count=len(result.rows),
            duration_ms=result.elapsed_ms,
        ),
    )
    return compiled, result


def _columns(specs: Sequence[ColumnSpec]) -> list[BiResultColumn]:
    return [
        BiResultColumn(
            id=spec.id,
            label=spec.label,
            kind=spec.kind,
            format=spec.format,
            member_id=spec.member_id,
            role=spec.role,
            pivot_value=spec.pivot_value,
        )
        for spec in specs
    ]


# --- the pipeline, named for the surfaces that reuse it ----------------------------------
#
# ``app/features/export_bi.py`` and ``app/features/ask_bi.py`` run the SAME guarded
# steps: budget, authorize, fingerprint, log, refuse. They must not copy them — a
# second implementation of "may this principal read this query" is how an export comes
# to return a member its caller could not have queried interactively, which is the one
# rule ``docs/bi.md`` §Exports states as a rule, and it is the rule §Phase 5 restates
# for a question the model wrote. These aliases are the seam: public names for the
# steps above, so the reuse reads as reuse rather than as reaching into another
# module's internals.
#
# ``visible_pairs`` / ``visible_member`` are here for the natural-language surface,
# which must hand the model the caller's OWN catalogue. Reimplementing that filter
# would be the same defect in a different place: the model would then be offered a
# member the query path refuses, and the refusal would arrive after the reader had
# been shown the figure's name.

Authorized = _Authorized
authorize = _authorize
append_query_log = _append
query_error = _query_error
injected_filters = _injected_filters
data_window = _data_window
require_budget = _require_budget
run_query = _run
result_columns = _columns
scope_read = _scope_read


# --- the catalogue this caller may query -------------------------------------------------


def _chunks(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _representative(cat: Catalogue, member_ids: Sequence[str]) -> str:
    """One member that stands for its ``(module, sensitivity)`` pair.

    Preferably one that is not COMPOSED from other measures: authorization walks
    a ratio into its numerator and denominator, which may sit in other modules,
    and a representative that drags another pair in would report its own pair as
    denied when the dragged one is. That direction is safe (a member is hidden
    that could have been shown) but it is avoidable, so it is avoided.
    """

    for member_id in member_ids:
        member = cat.member(member_id)
        if not isinstance(member, MeasureDef):
            return member_id
        if not any((member.numerator, member.denominator, member.weight, member.over)):
            return member_id
    return member_ids[0]


@dataclass(frozen=True, slots=True)
class _Visibility:
    """What one caller may be SHOWN: pair authority, narrowed by their slice."""

    pairs: Mapping[tuple[str, str], bool]
    #: False when the caller's bindings cover part of the institution, in which
    #: case a figure the platform cannot attribute to a branch is not visible
    #: however its pair was decided — the query path refuses exactly those, and
    #: the catalogue must not advertise what the query path refuses.
    whole_institution: bool = True

    def allows(self, member: MemberDef) -> bool:
        if not self.pairs.get((member.module, member.sensitivity), False):
            return False
        return self.whole_institution or branch_readable(member)


def _probe(
    db: Session, access: BiReadAccess, cat: Catalogue, ids: Sequence[str], *, surface: str
) -> tuple[set[str], list[BiDataScope]]:
    """Put a set of representative ids to the decision function, chunk by chunk."""

    denied: set[str] = set()
    scopes: list[BiDataScope] = []
    for chunk in _chunks(ids, BI_MAX_MEASURES):
        probe = BiQuery(measures=list(chunk), time=BiTime(as_of=utc_now().date()))
        decision = authorize_query(db, access.ctx, access.bank, cat, probe, surface=surface)
        denied.update(decision.denied_members)
        if decision.allowed:
            scopes.append(decision.data_scope)
    return denied, scopes


def _visible_pairs(
    db: Session, access: BiReadAccess, cat: Catalogue, *, surface: str = SURFACE_CATALOGUE
) -> _Visibility:
    """Which ``(module, sensitivity)`` pairs this caller holds, by asking the
    query path's own decision function.

    The catalogue must never advertise a member the query would refuse, so the
    filter cannot be a second implementation of the rules in
    ``app/services/bi/authorization.py`` — entitlement, exact sensitivity
    matching, the human-principal refusal and the deny-by-default on anything
    unrecognised. Instead ONE representative member per distinct pair is put to
    :func:`authorize_query`, which groups by pair anyway: twelve pairs cost one
    call and the answer is, by construction, the answer the query path gives.

    ``surface`` names the caller for the decision telemetry. The packs surface
    reuses this function for the same reason: a pack widget is granted or refused
    by ``(module, sensitivity)``, which is the unit ``authorize_query`` decides
    in, so asking once per widget would give the same answer at twenty-four times
    the cost.

    **A SECOND pass for a scoped caller, and why it is not optional.** The
    preferred representative is a measure composed from nothing, which in four of
    the twelve pairs is an ``grain="institution"`` engine figure — and a scoped
    caller is refused those, so the first pass would mark the whole pair invisible
    and take the pair's branch-readable portfolio measures down with it. The second
    pass re-asks those pairs with a branch-readable representative, so a pair is
    marked invisible only for a genuine AUTHORITY reason and the per-member slice
    rule is applied by :meth:`_Visibility.allows` where it belongs.
    """

    pairs = scope_pairs(cat.members())
    by_representative = {
        _representative(cat, member_ids): pair for pair, member_ids in pairs.items()
    }
    denied, scopes = _probe(db, access, cat, list(by_representative), surface=surface)
    allowed = {pair: member_id not in denied for member_id, pair in by_representative.items()}
    if _whole_institution(scopes):
        return _Visibility(pairs=allowed, whole_institution=True)
    # NOT ``all(...)`` over a possibly EMPTY list: a scoped reader's preferred
    # representatives are institution-grain in four of the twelve pairs, so the
    # first pass can come back wholly denied — and a vacuous ``all(())`` would
    # then read as "institution-wide", skip the retry AND stop ``allows`` from
    # narrowing per member. An unknown scope has to fall through to the retry.
    retry = {
        _representative(cat, readable): pair
        for pair, member_ids in pairs.items()
        if not allowed[pair]
        and (readable := tuple(m for m in member_ids if branch_readable(cat.member(m))))
    }
    if retry:
        retried, retry_scopes = _probe(db, access, cat, list(retry), surface=surface)
        for member_id, pair in retry.items():
            allowed[pair] = member_id not in retried
        scopes = [*scopes, *retry_scopes]
    return _Visibility(pairs=allowed, whole_institution=_whole_institution(scopes))


def _whole_institution(scopes: Sequence[BiDataScope]) -> bool:
    """Whether every scope that ALLOWED something covers the whole institution.

    ``False`` for an empty list on purpose: no allowed decision means no scope was
    established, and assuming the widest one is the fail-open this phase closes.
    Where the list is empty because the reader holds nothing, every pair is
    invisible anyway and the answer does not matter.
    """

    return bool(scopes) and all(scope.whole_institution for scope in scopes)


def _visible(member: MemberDef, allowed: _Visibility) -> bool:
    return allowed.allows(member)


def _measure_read(measure: MeasureDef) -> BiCatalogueMeasureRead:
    rule = measure.engine_rule
    return BiCatalogueMeasureRead(
        id=measure.id,
        label=measure.label,
        description=measure.description,
        module=measure.module,
        sensitivity=measure.sensitivity,
        measure_kind=measure.measure_kind,
        aggregation=measure.aggregation,
        time_behaviour=measure.time_behaviour,
        value_type=measure.value_type,
        grain=measure.grain,
        allowed_dimensions=list(measure.allowed_dimensions),
        favourable_direction=measure.favourable_direction,
        thresholds_source=measure.thresholds_source,
        advisory_designation=measure.advisory_designation,
        certified=measure.certified,
        engine_metric_id=None if rule is None else rule.metric_id,
        engine_tier=None if rule is None else rule.tier,
    )


def _dimension_read(dimension: DimensionDef) -> BiCatalogueDimensionRead:
    return BiCatalogueDimensionRead(
        id=dimension.id,
        label=dimension.label,
        description=dimension.description,
        module=dimension.module,
        sensitivity=dimension.sensitivity,
        value_type=dimension.value_type,
        values=[
            BiCatalogueValueRead(code=value.code, label=value.label) for value in dimension.values
        ],
    )


# The visibility half of the seam, named here because ``_Visibility`` and the two
# functions it belongs to are defined below the block above.

Visibility = _Visibility
visible_pairs = _visible_pairs
visible_member = _visible


# --- routes -----------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/bi/catalogue",
    response_model=BiCatalogueRead,
    operation_id="getBiCatalogue",
)
def get_bi_catalogue(
    bank_id: str,
    db: DbSession,
    access: BiRead,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BiCatalogueRead | Response:
    """Every measure, dimension and drill path THIS caller may query."""

    _ = bank_id  # resolved by the router's dependency
    cat = catalogue()
    _require_budget(db, access)
    allowed = _visible_pairs(db, access, cat)
    measures = [m for m in cat.measures() if _visible(m, allowed)]
    dimensions = [d for d in cat.dimensions() if _visible(d, allowed)]
    visible_dimension_ids = {d.id for d in dimensions}
    hierarchies: list[BiCatalogueHierarchyRead] = []
    for hierarchy in cat.hierarchies():
        levels = [level for level in hierarchy.levels if level in visible_dimension_ids]
        if levels:
            hierarchies.append(
                BiCatalogueHierarchyRead(id=hierarchy.id, label=hierarchy.label, levels=levels)
            )
    etag = _etag(
        SURFACE_CATALOGUE,
        cat.version,
        access.bank.id,
        access.principal_user_id,
        access.authorization_version,
        # The pair authority AND the slice: a scoped reader is shown fewer members
        # of the same pairs, so an institution-wide representation must not revalidate
        # for them (nor theirs for an institution-wide reader).
        allowed.whole_institution,
        *sorted(
            f"{module}/{sensitivity}" for (module, sensitivity), ok in allowed.pairs.items() if ok
        ),
    )
    if _is_fresh(if_none_match, etag):
        return _not_modified(etag)
    response.headers.update(_cache_headers(etag))
    return BiCatalogueRead(
        version=cat.version,
        measures=[_measure_read(measure) for measure in measures],
        dimensions=[_dimension_read(dimension) for dimension in dimensions],
        hierarchies=hierarchies,
        withheld_members=len(cat.members()) - len(measures) - len(dimensions),
    )


@router.post(
    "/banks/{bank_id}/bi/query",
    response_model=BiQueryResult,
    operation_id="runBiQuery",
)
def run_bi_query(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    query: BiQuery,
    db: DbSession,
    access: BiRead,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BiQueryResult | Response:
    """Answer one catalogue query for one institution, under the UI row cap."""

    _ = bank_id
    settings = get_settings().bi
    try:
        authorized = _authorize(
            db, access, query, surface=SURFACE_QUERY, if_none_match=if_none_match
        )
    except _NotModified as fresh:
        return _not_modified(fresh.etag)
    _, result = _run(
        db,
        access,
        query,
        authorized,
        row_cap=settings.ui_row_cap,
        timeout_ms=settings.interactive_timeout_ms,
    )
    response.headers.update(_cache_headers(authorized.etag))
    return BiQueryResult(
        columns=_columns(result.columns),
        rows=[list(row) for row in result.rows],
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
        used_aggregate=result.used_aggregate,
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
        data_scope=_scope_read(authorized.scope),
    )


def _paged(
    db: Session,
    access: BiReadAccess,
    request: BiPagedQueryRequest,
    *,
    surface: str,
    if_none_match: str | None,
) -> tuple[_Authorized, BiGridPageRead]:
    """One capped page of rows, shaped for the grid (D-030)."""

    settings = get_settings().bi
    offset, limit = grid_adapter.page_bounds(
        request.start_row, request.end_row, cap=settings.grid_page_cap
    )
    query = request.query.model_copy(update={"limit": limit, "offset": offset})
    authorized = _authorize(db, access, query, surface=surface, if_none_match=if_none_match)
    _, result = _run(
        db,
        access,
        query,
        authorized,
        row_cap=settings.grid_page_cap,
        timeout_ms=settings.interactive_timeout_ms,
    )
    page = grid_adapter.to_grid(result, start_row=offset)
    return authorized, BiGridPageRead(
        columns=_columns(page.columns),
        rows=[
            BiGridRowRead(values=row.values, level=row.level, subtotal=row.subtotal)
            for row in page.rows
        ],
        dimension_count=page.dimension_count,
        start_row=page.start_row,
        last_row=page.last_row,
        truncated=page.truncated,
        elapsed_ms=page.elapsed_ms,
        used_aggregate=page.used_aggregate,
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
    )


@router.post(
    "/banks/{bank_id}/bi/grid",
    response_model=BiGridPageRead,
    operation_id="runBiGridQuery",
)
def run_bi_grid_query(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    request: BiPagedQueryRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BiGridPageRead | Response:
    """One page of grouped, pivoted or plain rows for AG Grid Community."""

    _ = bank_id
    try:
        authorized, page = _paged(
            db, access, request, surface=SURFACE_GRID, if_none_match=if_none_match
        )
    except _NotModified as fresh:
        return _not_modified(fresh.etag)
    response.headers.update(_cache_headers(authorized.etag))
    return page


@router.post(
    "/banks/{bank_id}/bi/drill",
    response_model=BiGridPageRead,
    operation_id="runBiDrillQuery",
)
def run_bi_drill_query(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    request: BiPagedQueryRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BiGridPageRead | Response:
    """The records behind a figure: one capped page at the record grain.

    A drill must name at least one record-level member (D-028) — a position
    reference, a position id, an employer. That is what distinguishes it from an
    aggregate query, and those members are the ones whose sensitivity requires
    the record-level sentence, so the authority the page is served under is the
    record-level one.
    """

    _ = bank_id
    _require_record_level(request.query)
    try:
        authorized, page = _paged(
            db, access, request, surface=SURFACE_DRILL, if_none_match=if_none_match
        )
    except _NotModified as fresh:
        return _not_modified(fresh.etag)
    response.headers.update(_cache_headers(authorized.etag))
    return page


def _require_record_level(query: BiQuery) -> None:
    cat = catalogue()
    named = [*query.measures, *query.dimensions, *(f.member for f in query.filters)]
    for member_id in named:
        try:
            member = cat.member(member_id)
        except KeyError:
            # An id the catalogue does not know is the compiler's 422 to raise,
            # with its own message; it is not a missing record field.
            continue
        if member.sensitivity == RECORD_LEVEL_SENSITIVITY:
            return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error_code": "bi_drill_needs_a_record_field",
            "message": (
                "A drill-through has to name a record-level field, such as the position reference."
            ),
        },
    )


def _engine_read(
    db: Session, access: BiReadAccess, measure: MeasureDef, window: tuple[date, date]
) -> BiExplainEngineRead | None:
    """The engine row a certified figure was copied from, as copied."""

    rule = measure.engine_rule
    if rule is None:
        return None
    regime = engine_regime(measure)
    if regime is None:
        return BiExplainEngineRead(metric_id=rule.metric_id, module=rule.module, tier=rule.tier)
    row = db.scalars(
        select(BiFactEngineMetric)
        .where(
            BiFactEngineMetric.organization_id == access.ctx.organization_id,
            BiFactEngineMetric.bank_id == access.bank.id,
            BiFactEngineMetric.as_of_date <= window[1],
            BiFactEngineMetric.module == rule.module,
            BiFactEngineMetric.metric_id == rule.metric_id,
            BiFactEngineMetric.tier == rule.tier,
            BiFactEngineMetric.regime == regime,
        )
        .order_by(BiFactEngineMetric.as_of_date.desc())
        .limit(1)
    ).first()
    if row is None:
        # Authorized and well-formed, but the builder has copied no such row for
        # this window: say so by omission rather than inventing a provenance.
        return BiExplainEngineRead(
            metric_id=rule.metric_id, module=rule.module, tier=rule.tier, regime=regime
        )
    return BiExplainEngineRead(
        metric_id=rule.metric_id,
        module=rule.module,
        tier=rule.tier,
        regime=row.regime,
        as_of=row.as_of_date,
        value=row.value,
        unit=row.unit,
        input_hash=row.input_hash,
        engine_version=row.engine_version,
        pipeline_state=row.pipeline_state,
        status=row.status,
        advisory_designation=row.advisory_designation,
        computed_at=row.computed_at,
        run_id=row.run_id,
        reporting_period_id=row.reporting_period_id,
    )


_ComponentRole = Literal["numerator", "denominator", "weight", "over"]


def _components(cat: Catalogue, measure: MeasureDef) -> list[BiExplainComponentRead]:
    """The measures and dimension a composed figure is built from, named."""

    parts: tuple[tuple[_ComponentRole, str | None], ...] = (
        ("numerator", measure.numerator),
        ("denominator", measure.denominator),
        ("weight", measure.weight),
        ("over", measure.over),
    )
    return [
        BiExplainComponentRead(role=role, member_id=member_id, label=cat.member(member_id).label)
        for role, member_id in parts
        if member_id is not None
    ]


@router.post(
    "/banks/{bank_id}/bi/explain",
    response_model=BiExplainRead,
    operation_id="explainBiMeasure",
)
def explain_bi_measure(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    request: BiExplainRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BiExplainRead | Response:
    """Where one figure in a query came from — definition, source, checks.

    It compiles the query (which is how the source table and whether the daily
    aggregate answered it are known) and deliberately never executes it: the
    figure itself came from ``query``. No statement text is returned.
    """

    _ = bank_id
    cat = catalogue()
    if request.measure not in request.query.measures:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "bi_explain_measure_not_in_query",
                "message": "Explain one of the measures the view asked for.",
            },
        )
    try:
        authorized = _authorize(
            db, access, request.query, surface=SURFACE_EXPLAIN, if_none_match=if_none_match
        )
    except _NotModified as fresh:
        return _not_modified(fresh.etag)
    compiled = _compile(db, access, request.query, authorized)
    _append(
        db, replace(authorized.record, member_ids=_logged_members(authorized.decision, compiled))
    )
    measure = cat.measure(request.measure)
    time = request.query.time
    response.headers.update(_cache_headers(authorized.etag))
    return BiExplainRead(
        measure=_measure_read(measure),
        components=_components(cat, measure),
        source_table=compiled.fact_table,
        used_aggregate=compiled.used_aggregate,
        fx_rule=measure.fx_rule,
        as_of=time.as_of,
        window_start=None if time.range is None else time.range.start,
        window_end=None if time.range is None else time.range.end,
        compare_to=time.compare_to,
        engine=_engine_read(db, access, measure, authorized.window),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
    )


#: Production copy for each thing the pack surface can tell a reader. A pack
#: whose every figure was refused must never read as a dashboard with nothing to
#: show, so the pack-level sentence is always present and always says which it is.
PACK_MESSAGES: Mapping[str, str] = {
    "granted": "Your access covers every figure this dashboard reads.",
    "partial": (
        "Your access does not cover some of the figures this dashboard reads, so they are "
        "not shown. An organization owner can grant them."
    ),
    "restricted": (
        "Your access does not cover any of the figures this dashboard reads, so none of "
        "them are shown. An organization owner can grant them."
    ),
}


def _pack_engine_reach(
    cat: Catalogue, spec: BiPackSpec, *, institution_class: str, capital_regime: str
) -> tuple[int, int]:
    """How many of the pack's engine measures are THIS institution's, and how many it names.

    Counted over the measures the widget queries name directly: an engine
    measure's regime is the whole question, and a ratio's components are engine
    measures in their own right when they are engine measures at all.

    A member id the catalogue no longer carries counts towards the total and
    never towards the applicable count, so the pack fails closed and is not
    served. That is a start-up-level defect — ``tests/domain/bi/test_packs.py``
    resolves every member of every pack through the catalogue — and withholding
    the dashboard is the right run-time answer to it, rather than a 500 in front
    of a board.
    """

    applicable = 0
    total = 0
    for widget in spec.widgets:
        if widget.query is None:
            continue
        for member_id in widget.query.measures:
            try:
                member = cat.member(member_id)
            except KeyError:
                total += 1
                continue
            if not isinstance(member, MeasureDef) or member.engine_rule is None:
                continue
            total += 1
            if engine_measure_applies(
                member, institution_class=institution_class, capital_regime=capital_regime
            ):
                applicable += 1
    return applicable, total


def _servable_packs(db: Session, bank: Bank, cat: Catalogue) -> tuple[BiPackSpec, ...]:
    """Every certified pack this institution's licence class may be shown.

    Two conditions, and the first is D-070:

    1. the pack SET must be certified for this class at all — at least one pack
       has to name an engine authority that resolves for it. A class for which no
       pack does is shown no dashboards, because inferring that the five packs
       naming no engine measure are therefore correct for it would be exactly the
       "show it a bank's dashboard" the decision refuses;
    2. the individual pack must name no engine authority belonging to another
       class.

    Fail-closed through ``institution_types.get_type``: an institution whose
    licence class does not resolve raises (409) rather than being treated as a
    bank.
    """

    institution_type = institution_types.get_type(db, bank)
    klass = institution_type.institution_class
    regime = institution_type.capital_regime
    reach = {
        spec.id: _pack_engine_reach(cat, spec, institution_class=klass, capital_regime=regime)
        for spec in certified_packs()
    }
    if not any(applicable for applicable, _ in reach.values()):
        return ()
    return tuple(spec for spec in certified_packs() if reach[spec.id][0] == reach[spec.id][1])


def _pack_class_refusal(db: Session, bank: Bank) -> HTTPException:
    """404 for a licence class with no certified dashboard set (D-070)."""

    label = institution_types.get_type(db, bank).display_name
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error_code": "bi_packs_not_published_for_institution_class",
            "message": (
                f"No certified dashboard has been published for a {label} yet. The "
                "dashboards that exist are built on figures produced under a different "
                "prudential regime, so not one of them would have a value to show."
            ),
        },
    )


def _pack_widget_read(  # noqa: PLR0913 - one widget, its place, its date and its reader
    cat: Catalogue,
    widget: BiPackWidget,
    layout: BiLayoutItem,
    *,
    as_of: date,
    allowed: _Visibility,
    denied: list[str],
) -> BiPackWidgetRead:
    """One widget resolved for the date, or the refusal that replaced it.

    ``denied`` accumulates the member ids the reader was refused: they reach the
    append-only log, where an operator can see which grant is missing, and they
    do NOT reach the response.
    """

    query = None if widget.query is None else widget.query.for_period(as_of)
    if query is not None:
        refused = [
            member.id for member in query_members(cat, query) if not _visible(member, allowed)
        ]
        if refused:
            denied.extend(member_id for member_id in refused if member_id not in denied)
            # Everything but the geometry is dropped HERE, by not being passed.
            return BiPackWidgetRead(id=widget.id, layout=layout, access="restricted")
    return BiPackWidgetRead(
        id=widget.id,
        layout=layout,
        access="granted",
        kind=widget.kind,
        title=widget.title,
        caption=widget.caption,
        query=query,
        panel=widget.panel,
        display=widget.display,
        needs_data=widget.needs_data,
        pending_capability=widget.pending_capability,
    )


def _pack_read(  # noqa: PLR0913 - one pack, its date, its reader and the two logs
    cat: Catalogue,
    spec: BiPackSpec,
    *,
    as_of: date,
    allowed: _Visibility,
    denied: list[str],
    served: list[str],
) -> BiPackRead:
    """One certified dashboard, resolved for one reader and one reporting date."""

    layouts = {item.i: item for item in spec.layout}
    widgets = [
        _pack_widget_read(
            cat, widget, layouts[widget.id], as_of=as_of, allowed=allowed, denied=denied
        )
        for widget in spec.widgets
    ]
    readable = sum(1 for widget in spec.widgets if widget.query is not None)
    restricted = sum(1 for widget in widgets if widget.access == "restricted")
    for widget, resolved in zip(spec.widgets, widgets, strict=True):
        if resolved.access != "granted" or widget.query is None or resolved.query is None:
            continue
        served.extend(
            member.id for member in query_members(cat, resolved.query) if member.id not in served
        )
    access: BiPackAccess
    if readable and restricted == readable:
        access, message = "restricted", PACK_MESSAGES["restricted"]
    elif restricted:
        access, message = "granted", PACK_MESSAGES["partial"]
    else:
        access, message = "granted", PACK_MESSAGES["granted"]
    return BiPackRead(
        id=spec.id,
        title=spec.title,
        description=spec.description,
        audience=spec.audience,
        version=spec.version,
        as_of=as_of,
        access=access,
        message=message,
        widgets=widgets,
        restricted_widgets=restricted,
        readable_widgets=readable,
        catalogue_version=CATALOGUE_VERSION,
    )


def _surface_digest(surface: str, *parts: object) -> str:
    """A one-way digest of a read that is not a ``BiQuery``.

    ``bi_query_log.query_hash`` holds "which question was asked", and for a pack
    or an insights read the question is the surface, the date and the pack — not a
    ``BiQuery``, so ``query_log.query_digest`` cannot state it. The material is
    the same shape and the column keeps its meaning: two identical questions hash
    alike, and nothing in it can be read back as a value.
    """

    material = _HEADER_SEPARATOR.join(
        (surface, *("" if part is None else str(part) for part in parts))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _pack_record(
    access: BiReadAccess,
    *,
    query_hash: str,
    decision: str,
    served: Sequence[str] = (),
    denied: Sequence[str] = (),
) -> query_log.QueryRecord:
    """The ``packs`` log row.

    ``row_count`` stays NULL because a pack read serves a SPEC and no figures —
    the client then asks ``/bi/query`` for each widget, and each of those writes
    its own row. ``denied_members`` is populated even on an ``allowed`` row, and
    this is the one surface where that is right: a dashboard is a MIXED read by
    construction, the reader was served it, and the fields they were refused
    inside it are exactly what an operator needs in order to write the grant.
    """

    return query_log.QueryRecord(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        surface=SURFACE_PACKS,
        query_hash=query_hash,
        decision=decision,
        catalogue_version=CATALOGUE_VERSION,
        member_ids=tuple(served),
        denied_members=tuple(denied),
    )


@router.get(
    "/banks/{bank_id}/bi/packs",
    response_model=BiPackListRead,
    operation_id="listBiPacks",
)
def list_bi_packs(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    db: DbSession,
    access: BiRead,
    response: Response,
    as_of: Annotated[date, Query(description="The reporting date to resolve the packs for.")],
    if_none_match: IfNoneMatch = None,
) -> BiPackListRead | Response:
    """Every certified dashboard this institution and reader may open."""

    _ = bank_id
    cat = catalogue()
    _require_budget(db, access)
    specs = _servable_packs(db, access.bank, cat)
    query_hash = _surface_digest(SURFACE_PACKS, as_of.isoformat(), *(spec.id for spec in specs))
    if not specs:
        _append(db, _pack_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED))
        raise _pack_class_refusal(db, access.bank)
    allowed = _visible_pairs(db, access, cat, surface=SURFACE_PACKS)
    etag = _etag(
        SURFACE_PACKS,
        cat.version,
        as_of.isoformat(),
        access.bank.id,
        access.principal_user_id,
        access.authorization_version,
        *(f"{spec.id}@{spec.version}" for spec in specs),
        # The pair authority AND the slice: a scoped reader is shown fewer members
        # of the same pairs, so an institution-wide representation must not revalidate
        # for them (nor theirs for an institution-wide reader).
        allowed.whole_institution,
        *sorted(
            f"{module}/{sensitivity}" for (module, sensitivity), ok in allowed.pairs.items() if ok
        ),
    )
    denied: list[str] = []
    served: list[str] = []
    try:
        reads = [
            _pack_read(cat, spec, as_of=as_of, allowed=allowed, denied=denied, served=served)
            for spec in specs
        ]
    except BiQueryError as exc:
        _append(db, _pack_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED))
        raise _query_error(exc) from exc
    _append(
        db,
        _pack_record(
            access,
            query_hash=query_hash,
            decision=query_log.DECISION_ALLOWED,
            served=served,
            denied=denied,
        ),
    )
    if _is_fresh(if_none_match, etag):
        return _not_modified(etag)
    response.headers.update(_cache_headers(etag))
    return BiPackListRead(as_of=as_of, packs=reads, catalogue_version=CATALOGUE_VERSION)


@router.get(
    "/banks/{bank_id}/bi/packs/{pack}",
    response_model=BiPackRead,
    operation_id="getBiPack",
)
def get_bi_pack(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    pack: str,
    db: DbSession,
    access: BiRead,
    response: Response,
    as_of: Annotated[date, Query(description="The reporting date to resolve the pack for.")],
    if_none_match: IfNoneMatch = None,
) -> BiPackRead | Response:
    """One certified dashboard, resolved for this institution, date and reader.

    ``pack`` is a CLOSED platform catalogue key, not an object identifier: it
    names a file that shipped in this build and is validated against
    ``app.domain.bi.packs`` before anything else is read. There is no tenant
    object behind it and therefore no cross-tenant dimension to it — the
    institution in the path is the only tenant reference, and the router's own
    dependency has already resolved it.
    """

    _ = bank_id
    cat = catalogue()
    _require_budget(db, access)
    query_hash = _surface_digest(SURFACE_PACKS, as_of.isoformat(), pack)
    specs = {spec.id: spec for spec in _servable_packs(db, access.bank, cat)}
    spec = specs.get(pack)
    if spec is None:
        _append(db, _pack_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED))
        raise _pack_refusal(db, access.bank, pack)
    allowed = _visible_pairs(db, access, cat, surface=SURFACE_PACKS)
    etag = _etag(
        SURFACE_PACKS,
        cat.version,
        as_of.isoformat(),
        access.bank.id,
        access.principal_user_id,
        access.authorization_version,
        f"{spec.id}@{spec.version}",
        # The pair authority AND the slice: a scoped reader is shown fewer members
        # of the same pairs, so an institution-wide representation must not revalidate
        # for them (nor theirs for an institution-wide reader).
        allowed.whole_institution,
        *sorted(
            f"{module}/{sensitivity}" for (module, sensitivity), ok in allowed.pairs.items() if ok
        ),
    )
    denied: list[str] = []
    served: list[str] = []
    try:
        read = _pack_read(cat, spec, as_of=as_of, allowed=allowed, denied=denied, served=served)
    except BiQueryError as exc:
        _append(db, _pack_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED))
        raise _query_error(exc) from exc
    _append(
        db,
        _pack_record(
            access,
            query_hash=query_hash,
            decision=query_log.DECISION_ALLOWED,
            served=served,
            denied=denied,
        ),
    )
    if _is_fresh(if_none_match, etag):
        return _not_modified(etag)
    response.headers.update(_cache_headers(etag))
    return read


def _pack_refusal(db: Session, bank: Bank, pack: str) -> HTTPException:
    """404 for a pack this institution cannot be shown, or that does not exist.

    Two reasons, two codes, and neither says anything about another institution:
    a key the build does not carry is simply not found, and a key that IS a pack
    but is certified for another licence class says so, because "no dashboard
    exists for your licence class" is the honest and actionable sentence.
    """

    try:
        certified_pack(pack)
    except PackError:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "bi_pack_not_found",
                "message": "There is no such dashboard.",
            },
        )
    return _pack_class_refusal(db, bank)


# --- insights ------------------------------------------------------------------------------
#
# ``app/services/bi/insights`` decides what may be SAID; ``insights/assemble.py``
# reads the figures to say it about. This route is the thin seam between them: it
# meters the read, revalidates the caller's cache, asks the assembler for one
# institution and one date, records the decision, and shapes the statements for
# the wire. It computes no figure and judges no movement — both would be a second
# opinion about what a measure IS.
#
# **Nothing here fabricates an empty answer.** A date whose marts hold nothing
# produces data-gap statements or none at all, and ``measures_read`` is on the
# response beside the list so the client can tell "nothing stands out" from
# "nothing has been computed". The dashboard's ``InsightStrip`` renders the
# second as a sentence the platform did not make if it is handed the first.


def _insight_read(insight: Insight) -> BiInsightRead:
    """One statement as the wire carries it.

    Field for field the same shape as ``statements.py::Insight``, which the
    dashboard's ``components/bi/types.ts::BiInsight`` mirrors in camelCase. The
    headline and detail are rendered server-side and are carried as given: a
    browser that re-rounded a capital ratio would change what the sentence says.
    """

    return BiInsightRead(
        id=insight.id,
        rule_id=insight.rule_id,
        statement_class=insight.statement_class,
        headline=insight.headline,
        detail=insight.detail,
        as_of=insight.as_of,
        measure_ids=list(insight.measure_ids),
        evidence=list(insight.evidence),
        favourability=insight.favourability,
        emphasis=insight.emphasis,
        qualifiers=list(insight.qualifiers),
        certified=insight.certified,
        advisory_designation=insight.advisory,
    )


def _insights_record(  # noqa: PLR0913 - one row, spelled out
    access: BiReadAccess,
    *,
    query_hash: str,
    decision: str,
    served: Sequence[str] = (),
    denied: Sequence[str] = (),
    row_count: int | None = None,
    duration_ms: int | None = None,
    build_fingerprint: str | None = None,
) -> query_log.QueryRecord:
    """The ``insights`` log row. ONE per request, whatever it took to answer."""

    return query_log.QueryRecord(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        surface=SURFACE_INSIGHTS,
        query_hash=query_hash,
        decision=decision,
        catalogue_version=CATALOGUE_VERSION,
        member_ids=tuple(served),
        denied_members=tuple(denied),
        row_count=row_count,
        duration_ms=duration_ms,
        build_fingerprint=build_fingerprint,
    )


@router.get(
    "/banks/{bank_id}/bi/insights",
    response_model=BiInsightsRead,
    operation_id="getBiInsights",
)
def get_bi_insights(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    db: DbSession,
    access: BiRead,
    response: Response,
    as_of: Annotated[date, Query(description="The reporting date to report on.")],
    compare_to: Annotated[
        date | None,
        Query(description="The earlier period to measure movements against."),
    ] = None,
    if_none_match: IfNoneMatch = None,
) -> BiInsightsRead | Response:
    """What the platform is prepared to say about one institution at one date.

    An empty list is a real answer. It is not the same answer as "nothing has
    been computed", which is why ``measures_read`` is reported beside it.
    """

    _ = bank_id
    settings = get_settings().bi
    cat = catalogue()
    _require_budget(db, access)
    prior = compare_to or default_compare_to(as_of)
    query_hash = _surface_digest(SURFACE_INSIGHTS, as_of.isoformat(), prior.isoformat())
    if prior >= as_of:
        _append(
            db, _insights_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "bi_insights_comparison_not_earlier",
                "message": "The period compared against has to be earlier than the reporting date.",
            },
        )
    window = _data_window(BiTime(as_of=as_of, compare_to=prior))
    fingerprint = _build_fingerprint(db, access.ctx.organization_id, access.bank.id, window)
    etag = _etag(
        SURFACE_INSIGHTS,
        cat.version,
        as_of.isoformat(),
        prior.isoformat(),
        fingerprint,
        access.bank.id,
        access.principal_user_id,
        access.authorization_version,
    )
    if _is_fresh(if_none_match, etag):
        # The statements were not run, so no rows were served; the row still goes
        # in, because the budget is counted over these rows (audit A6-06).
        _append(
            db,
            _insights_record(
                access,
                query_hash=query_hash,
                decision=query_log.DECISION_ALLOWED,
                build_fingerprint=fingerprint,
            ),
        )
        return _not_modified(etag)
    started = perf_counter()
    try:
        assembled = assemble_insights(
            db,
            ctx=access.ctx,
            bank=access.bank,
            cat=cat,
            as_of=as_of,
            compare_to=prior,
            surface=SURFACE_INSIGHTS,
            row_cap=settings.ui_row_cap,
            timeout_ms=settings.interactive_timeout_ms,
        )
    except BiQueryError as exc:
        _append(
            db, _insights_record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED)
        )
        raise _query_error(exc) from exc
    elapsed_ms = int((perf_counter() - started) * 1000)
    insight_set = assembled.insight_set
    # Nothing read AND something withheld is a refused read, not a quiet one.
    # Answering 200 with an empty list would hand a strip that renders "nothing
    # stands out for this reporting date" a statement about the bank's figures,
    # made to a reader who was shown none of them. So it refuses — and the refusal
    # names NO measure: the reader is not the operator writing the grant, and the
    # withheld member ids go to the append-only log instead.
    served_nothing = assembled.measures_read == 0 and assembled.measures_withheld > 0
    _append(
        db,
        _insights_record(
            access,
            query_hash=query_hash,
            decision=(query_log.DECISION_DENIED if served_nothing else query_log.DECISION_ALLOWED),
            served=assembled.member_ids,
            denied=assembled.denied_members,
            row_count=None if served_nothing else len(insight_set.insights),
            duration_ms=elapsed_ms,
            build_fingerprint=assembled.build_fingerprint,
        ),
    )
    if served_nothing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_insights_authorization_denied",
                "message": (
                    "Your access does not cover any of the figures this summary is built "
                    "from, so nothing is reported. An organization owner can grant them."
                ),
            },
        )
    response.headers.update(_cache_headers(etag))
    return BiInsightsRead(
        as_of=as_of,
        compare_to=assembled.compare_to,
        insights=[_insight_read(insight) for insight in insight_set.insights],
        fact_sheet_hash=insight_set.fact_sheet_hash,
        truncated=insight_set.truncated,
        measures_read=assembled.measures_read,
        measures_withheld=assembled.measures_withheld,
        builds=_builds_for(db, access.ctx.organization_id, access.bank.id, as_of),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=assembled.build_fingerprint,
    )


def _builds_for(db: Session, organization_id: str, bank_id: str, as_of: date) -> list[BiBuildRead]:
    """Every ``bi_mart_builds`` record for the date, one per scope, in scope order.

    Freshness only: which scopes were built, whether each build succeeded and when
    it finished. No verdict rides here — BI says nothing about whether its figures
    agree with the returns the platform files.
    """

    builds = db.scalars(
        select(BiMartBuild)
        .where(
            BiMartBuild.organization_id == organization_id,
            BiMartBuild.bank_id == bank_id,
            BiMartBuild.as_of_date == as_of,
        )
        .order_by(BiMartBuild.scope)
    ).all()
    return [
        BiBuildRead(
            scope=build.scope,
            status=build.status,
            fingerprint=build.fingerprint,
            finished_at=build.finished_at,
            row_counts=dict(build.row_counts or {}),
        )
        for build in builds
    ]
