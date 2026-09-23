"""The BI read surface: six routes under ``/api/v1/banks/{bank_id}/bi/``.

``catalogue`` says what this caller may ask, ``query`` answers one question,
``grid`` and ``drill`` page an answer for the grid, ``explain`` says where a
figure came from, and ``trust`` says whether the figures reconcile to what the
platform already files. Everything they need is built elsewhere: the catalogue
declares the members (``app/domain/bi/catalogue``), the compiler turns a
``BiQuery`` into one read-only statement (``app/services/bi/compiler.py``), the
authorization module decides it (``app/services/bi/authorization.py``), and the
builder wrote the marts. What this module owns is the ORDER those are consulted
in, and it is the order that carries the security properties:

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
that reaches the four logged surfaces lands in ``bi_query_log`` in the request
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

``catalogue`` and ``trust`` write NO row: C1's ``surface`` CHECK names only the
six data surfaces, and recording either under ``query`` or ``explain`` would put
an event in an append-only audit table that did not happen. Both are still
authorized (trust over every member its checks disclose — audit A6-01) and both
are subject to the budget; neither refills it, so a principal polling only those
two is bounded by nothing but the process. Closing that needs the vocabulary
widened in ``app/models/bi.py`` + migration ``202609220066`` (C1's files), which
is named as a follow-up rather than done here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Tenant, TenantBank, TenantContext
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
from app.domain.bi.catalogue.engine import engine_measure_id
from app.models import Bank
from app.models.bi import (
    RECONCILIATION_STATUSES,
    BiFactEngineMetric,
    BiMartBuild,
    BiReconciliationResult,
)
from app.schemas.bi import (
    BI_MAX_MEASURES,
    BiBuildRead,
    BiCatalogueDimensionRead,
    BiCatalogueHierarchyRead,
    BiCatalogueMeasureRead,
    BiCatalogueRead,
    BiCatalogueValueRead,
    BiExplainComponentRead,
    BiExplainEngineRead,
    BiExplainRead,
    BiExplainRequest,
    BiFilter,
    BiGridPageRead,
    BiGridRowRead,
    BiPagedQueryRequest,
    BiQuery,
    BiQueryResult,
    BiResultColumn,
    BiTime,
    BiTrustBadge,
    BiTrustCheckRead,
    BiTrustRead,
    BiTrustStatus,
)
from app.services.bi import grid_adapter, query_log, reconciliation
from app.services.bi.authorization import BiAuthorization, authorize_query, scope_pairs
from app.services.bi.compiler import ColumnSpec, CompiledQuery, compile_query
from app.services.bi.errors import BiQueryError
from app.services.bi.execution import QueryResult, execute

router = APIRouter(tags=["bi"])

SURFACE_QUERY = "query"
SURFACE_GRID = "grid"
SURFACE_DRILL = "drill"
SURFACE_EXPLAIN = "explain"
#: Not ``bi_query_log`` surfaces — C1's ``surface`` CHECK names the six data
#: surfaces and neither of these is one. Carried so the decision telemetry and
#: the ETags can name them (see the module docstring on the trust row).
SURFACE_CATALOGUE = "catalogue"
SURFACE_TRUST = "trust"

#: Production copy for each reconciliation check, in the units a banker reads.
#: Neutral by construction: no currency, no regulator, no form number (the
#: return family differs per jurisdiction, the check does not).
CHECK_LABELS: Mapping[str, str] = {
    "R1": "Non-performing loans agree with the credit engine",
    "R2": "Loan balances agree with the balance sheet",
    "R3": "Deposit balances agree with the balance sheet",
    "R4": "Income-statement lines agree with the regulatory return",
    "R5": "Every position in the book reached the analytics tables",
    "R6": "Every balance is stated in the reporting currency",
    "R7": "Every balance is attributed to a known branch",
    "R8": "The analytics tables are as current as the live figures",
    "R9": "The balance sheet balances",
    "R10": "Arrears ageing is complete",
}

#: What each reconciliation check DISCLOSES, named as the catalogue member whose
#: sentence already governs that same figure (audit A6-01).
#:
#: A trust payload is not a query — it is a status, an ``lhs``, an ``rhs``, a
#: ``difference`` and a ``detail`` per check — which is exactly why it read as
#: metadata and was served to anyone. It is not metadata: R2's ``lhs`` IS the
#: institution's total loans, the same number ``loans.balance_rc`` serves; R3's
#: is its total deposits; R9's is total assets and funding; R7's ``detail``
#: carries real branch codes. So the sentence this route needs is the union of
#: the sentences those members need, and it is expressed AS those members so the
#: pairs come from the catalogue (D-028) rather than from a literal here.
#:
#: Every check is mapped, and the route requires EVERY pair, because the badge's
#: verdict is one statement about the whole book: a principal who may not see the
#: deposit total may not be told that the deposit reconciliation failed either.
CHECK_DISCLOSURES: Mapping[str, str] = {
    "R1": "loans.npl_exposure_rc",
    "R2": "loans.balance_rc",
    "R3": "deposits.balance_rc",
    "R4": "gl_account.pl_line",
    "R5": "positions.count",
    "R6": "positions.unconverted_count",
    "R7": "branch.code",
    "R8": "time.date",
    "R9": "positions.balance_rc",
    "R10": "loan.dpd_band",
}

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
    """Every date the query can read, comparison window included.

    Mirrors the compiler's own ``_windows`` (pinned by a test): a single as-of
    is one day, a range is itself, and a comparison adds the prior date or an
    equal-length prior window. The badge and the fingerprint must cover the
    prior period too — a comparison reads it.
    """

    if time.as_of is not None:
        if time.compare_to is None:
            return time.as_of, time.as_of
        return min(time.as_of, time.compare_to), max(time.as_of, time.compare_to)
    assert time.range is not None  # noqa: S101 - BiTime validates exactly one window
    start, end = time.range.start, time.range.end
    if time.compare_to is None:
        return start, end
    prior_start = time.compare_to - (end - start)
    return min(start, prior_start), max(end, time.compare_to)


def _build_fingerprint(
    db: Session, organization_id: str, bank_id: str, window: tuple[date, date]
) -> str | None:
    """The fingerprint of the mart state the window was read from.

    One successful build stamps every scope of a date with the SAME value-based
    fingerprint, so the common case — one date, fully built — returns that value
    verbatim and an ETag can be compared against the builder's own record. A
    window over several dates, or a date whose scopes did not all succeed, has
    no single fingerprint: those return one deterministic digest over the build
    state instead, which changes whenever any part of it does. Nothing built at
    all returns ``None``, and the ETag then rests on the catalogue version and
    the principal.
    """

    rows = db.execute(
        select(
            BiMartBuild.as_of_date, BiMartBuild.scope, BiMartBuild.status, BiMartBuild.fingerprint
        )
        .where(
            BiMartBuild.organization_id == organization_id,
            BiMartBuild.bank_id == bank_id,
            BiMartBuild.as_of_date >= window[0],
            BiMartBuild.as_of_date <= window[1],
        )
        .order_by(BiMartBuild.as_of_date, BiMartBuild.scope)
    ).all()
    if not rows:
        return None
    fingerprints = {row.fingerprint for row in rows if row.status == "succeeded"}
    if not fingerprints:
        return None
    if len(fingerprints) == 1 and all(row.status == "succeeded" for row in rows):
        return fingerprints.pop()
    material = _HEADER_SEPARATOR.join(
        f"{row.as_of_date.isoformat()}:{row.scope}:{row.status}:{row.fingerprint}" for row in rows
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _stored_checks(
    db: Session, organization_id: str, bank_id: str, window: tuple[date, date]
) -> list[BiReconciliationResult]:
    return list(
        db.scalars(
            select(BiReconciliationResult)
            .where(
                BiReconciliationResult.organization_id == organization_id,
                BiReconciliationResult.bank_id == bank_id,
                BiReconciliationResult.as_of_date >= window[0],
                BiReconciliationResult.as_of_date <= window[1],
            )
            .order_by(BiReconciliationResult.as_of_date, BiReconciliationResult.check_id)
        )
    )


def _trust_badge(
    db: Session, organization_id: str, bank_id: str, window: tuple[date, date]
) -> BiTrustBadge:
    """The verdict for everything the window covers; a missing check is grey.

    For a single date this is ``reconciliation.trust_for`` (pinned by a test).
    For a window it is the worst verdict in it — a badge may understate
    confidence, never overstate it — which is also why a date in the window with
    no stored result greys the whole badge rather than being skipped.
    """

    rows = _stored_checks(db, organization_id, bank_id, window)
    by_date: dict[date, dict[str, str]] = {}
    for row in rows:
        by_date.setdefault(row.as_of_date, {})[row.check_id] = row.status
    if not by_date:
        return BiTrustBadge(status=_trust_status(reconciliation.GREY), failing_checks=[])
    overalls: list[str] = []
    failing: set[str] = set()
    for stored in by_date.values():
        statuses = {
            check_id: stored.get(check_id, reconciliation.GREY)
            for check_id in reconciliation.STORABLE_CHECK_IDS
        }
        overalls.append(reconciliation.overall_trust(statuses.values()))
        failing.update(
            check_id
            for check_id, value in statuses.items()
            if value in {reconciliation.RED, reconciliation.AMBER}
        )
    return BiTrustBadge(
        status=_trust_status(reconciliation.overall_trust(overalls)),
        failing_checks=sorted(failing),
    )


def _check_read(check_id: str, row: BiReconciliationResult | None) -> BiTrustCheckRead:
    if row is None:
        return BiTrustCheckRead(
            check_id=check_id,
            label=CHECK_LABELS.get(check_id, check_id),
            status=_trust_status(reconciliation.GREY),
            detail={"reason": "not_assessed"},
        )
    return BiTrustCheckRead(
        check_id=check_id,
        label=CHECK_LABELS.get(check_id, check_id),
        status=_trust_status(row.status),
        lhs=row.lhs,
        rhs=row.rhs,
        difference=row.difference,
        tolerance=row.tolerance,
        detail=dict(row.detail or {}),
        evaluated_at=row.evaluated_at,
    )


def _trust_status(value: str) -> BiTrustStatus:
    """The stored status, or grey for a value the badge vocabulary does not know."""

    if value in RECONCILIATION_STATUSES:
        return cast("BiTrustStatus", value)
    return cast("BiTrustStatus", reconciliation.GREY)


# --- the guarded pipeline ---------------------------------------------------------------


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


def _authorize(
    db: Session,
    access: BiReadAccess,
    query: BiQuery,
    *,
    surface: str,
    if_none_match: str | None,
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
        decision = authorize_query(db, access.ctx, access.bank, cat, query, surface=surface)
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
    etag = _etag(
        surface,
        attempt.query_hash,
        fingerprint,
        CATALOGUE_VERSION,
        access.principal_user_id,
        access.authorization_version,
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
    if not decision.data_scope.whole_institution:
        # Fail closed: a principal scoped to part of the institution needs the
        # injected filters Phase 4 derives from the matched bindings, and
        # serving the whole book instead would be the leak S18 names.
        _append(
            db,
            replace(
                record,
                decision=query_log.DECISION_DENIED,
                denied_members=decision.member_ids,
                build_fingerprint=None,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_data_scope_unsupported",
                "message": (
                    "Your access covers part of this institution, "
                    "which this view cannot narrow to yet."
                ),
            },
        )
    if _is_fresh(if_none_match, etag):
        _append(db, record)
        raise _NotModified(etag)
    return _Authorized(
        decision=decision, window=window, build_fingerprint=fingerprint, etag=etag, record=record
    )


def _injected_filters(decision: BiAuthorization) -> tuple[BiFilter, ...]:
    """The caller's data scope as filters the request cannot remove (S18, D-029).

    Phase 1 has no branch or region scope to derive — ``authorization_bindings``
    carries no data-scope columns yet — so an authorized principal reads the
    whole institution and there is nothing to inject. ``_authorize`` has already
    refused anything else, so this cannot silently serve a scoped principal the
    whole book.
    """

    assert decision.data_scope.whole_institution  # noqa: S101 - refused in _authorize
    return ()


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
            injected_filters=_injected_filters(authorized.decision),
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


def _visible_pairs(
    db: Session, access: BiReadAccess, cat: Catalogue
) -> dict[tuple[str, str], bool]:
    """Which ``(module, sensitivity)`` pairs this caller holds, by asking the
    query path's own decision function.

    The catalogue must never advertise a member the query would refuse, so the
    filter cannot be a second implementation of the rules in
    ``app/services/bi/authorization.py`` — entitlement, exact sensitivity
    matching, the human-principal refusal and the deny-by-default on anything
    unrecognised. Instead ONE representative member per distinct pair is put to
    :func:`authorize_query`, which groups by pair anyway: twelve pairs cost one
    call and the answer is, by construction, the answer the query path gives.
    """

    pairs = scope_pairs(cat.members())
    by_representative = {
        _representative(cat, member_ids): pair for pair, member_ids in pairs.items()
    }
    ids = list(by_representative)
    allowed: dict[tuple[str, str], bool] = {}
    for chunk in _chunks(ids, BI_MAX_MEASURES):
        probe = BiQuery(measures=list(chunk), time=BiTime(as_of=utc_now().date()))
        decision = authorize_query(
            db, access.ctx, access.bank, cat, probe, surface=SURFACE_CATALOGUE
        )
        denied = set(decision.denied_members)
        for member_id in chunk:
            allowed[by_representative[member_id]] = member_id not in denied
    return allowed


def _visible(member: MemberDef, allowed: Mapping[tuple[str, str], bool]) -> bool:
    return allowed.get((member.module, member.sensitivity), False)


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
        reconciliation_checks=list(measure.reconciliation_checks),
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
        *sorted(f"{module}/{sensitivity}" for (module, sensitivity), ok in allowed.items() if ok),
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
        trust=_trust_badge(db, access.ctx.organization_id, access.bank.id, authorized.window),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
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
        trust=_trust_badge(db, access.ctx.organization_id, access.bank.id, authorized.window),
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


def _engine_regime(measure: MeasureDef) -> str | None:
    """The regime encoded in an engine measure id, or ``None`` if it is not one.

    The id is composed by ``engine_measure_id``; this inverts it and checks the
    round trip against that same composer, so the convention has one owner.
    """

    rule = measure.engine_rule
    if rule is None:
        return None
    prefix = f"engine.{rule.metric_id}."
    suffix = f".{rule.tier}"
    if not (measure.id.startswith(prefix) and measure.id.endswith(suffix)):
        return None
    regime = measure.id[len(prefix) : -len(suffix)]
    if not regime or engine_measure_id(rule.metric_id, regime, rule.tier) != measure.id:
        return None
    return regime


def _engine_read(
    db: Session, access: BiReadAccess, measure: MeasureDef, window: tuple[date, date]
) -> BiExplainEngineRead | None:
    """The engine row a certified figure was copied from, as copied."""

    rule = measure.engine_rule
    if rule is None:
        return None
    regime = _engine_regime(measure)
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
        reconciliation_blocked=row.reconciliation_blocked,
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
    rows = {
        row.check_id: row
        for row in _stored_checks(db, access.ctx.organization_id, access.bank.id, authorized.window)
    }
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
        checks=[
            _check_read(check_id, rows.get(check_id)) for check_id in measure.reconciliation_checks
        ],
        trust=_trust_badge(db, access.ctx.organization_id, access.bank.id, authorized.window),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
    )


def trust_probe(as_of: date) -> BiQuery:
    """The members ``GET trust`` discloses, as one query for the decision function.

    Public because the authorization matrix runs it as a shape: the trust route's
    sentence must be exercised by the same table every other surface is, not by a
    second copy of the member list.
    """

    cat = catalogue()
    measures = sorted(
        member_id
        for member_id in set(CHECK_DISCLOSURES.values())
        if isinstance(cat.member(member_id), MeasureDef)
    )
    dimensions = sorted(
        member_id
        for member_id in set(CHECK_DISCLOSURES.values())
        if not isinstance(cat.member(member_id), MeasureDef)
    )
    return BiQuery(measures=measures, dimensions=dimensions, time=BiTime(as_of=as_of))


def _authorize_trust(db: Session, access: BiReadAccess, as_of: date) -> BiAuthorization:
    """Require every sentence the reconciliation payload discloses, or 403.

    No ``bi_query_log`` row is written: C1's ``surface`` CHECK has no ``trust``
    value, and recording this read under ``query`` or ``explain`` would put an
    event in an append-only audit table that did not happen. The decision still
    reaches the shared authorization telemetry (``authorization_denied``,
    surface ``bi_trust``). Widening the vocabulary is a two-line change to
    ``app/models/bi.py`` and migration ``202609220066`` — C1's files — after
    which this route logs and refills the budget like the others; until then it
    is subject to the budget and contributes nothing to it.
    """

    cat = catalogue()
    decision = authorize_query(
        db, access.ctx, access.bank, cat, trust_probe(as_of), surface=SURFACE_TRUST
    )
    if not decision.allowed:
        raise _denied(cat, decision)
    if not decision.data_scope.whole_institution:
        # The badge is one verdict over the whole institution; a principal scoped
        # to part of it cannot be told what the whole reconciles to (S18).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_data_scope_unsupported",
                "message": (
                    "Your access covers part of this institution, "
                    "which this view cannot narrow to yet."
                ),
            },
        )
    return decision


@router.get(
    "/banks/{bank_id}/bi/trust",
    response_model=BiTrustRead,
    operation_id="getBiTrust",
)
def get_bi_trust(  # noqa: PLR0913 - FastAPI injects db/access/response/header
    bank_id: str,
    db: DbSession,
    access: BiRead,
    response: Response,
    as_of: Annotated[date, Query(description="The reporting date to report on.")],
    if_none_match: IfNoneMatch = None,
) -> BiTrustRead | Response:
    """Every reconciliation check for one (institution, date), with its evidence.

    A check with no stored result is reported as "not assessed" — never as a
    pass — so a badge can never read green over a check that did not run.

    The payload is figures, not metadata (audit A6-01), so it is authorized like
    every other data route: the caller must hold the sentence of every member
    :data:`CHECK_DISCLOSURES` names, for THIS institution. A principal with
    coverage on another institution of the same tenant, or with no binding at
    all, gets 403 and no body — ``resolve_tenant_bank`` scopes by organization
    only, and organization is not institution.
    """

    _ = bank_id
    window = (as_of, as_of)
    _require_budget(db, access)
    decision = _authorize_trust(db, access, as_of)
    fingerprint = _build_fingerprint(db, access.ctx.organization_id, access.bank.id, window)
    etag = _etag(
        SURFACE_TRUST,
        as_of.isoformat(),
        fingerprint,
        CATALOGUE_VERSION,
        access.bank.id,
        access.principal_user_id,
        access.authorization_version,
        *sorted(str(binding_id) for binding_id in decision.matching_binding_ids),
    )
    if _is_fresh(if_none_match, etag):
        return _not_modified(etag)
    rows = {
        row.check_id: row
        for row in _stored_checks(db, access.ctx.organization_id, access.bank.id, window)
    }
    builds = db.scalars(
        select(BiMartBuild)
        .where(
            BiMartBuild.organization_id == access.ctx.organization_id,
            BiMartBuild.bank_id == access.bank.id,
            BiMartBuild.as_of_date == as_of,
        )
        .order_by(BiMartBuild.scope)
    ).all()
    response.headers.update(_cache_headers(etag))
    return BiTrustRead(
        as_of=as_of,
        status=_trust_badge(db, access.ctx.organization_id, access.bank.id, window).status,
        checks=[
            _check_read(check_id, rows.get(check_id))
            for check_id in reconciliation.STORABLE_CHECK_IDS
        ],
        builds=[
            BiBuildRead(
                scope=build.scope,
                status=build.status,
                fingerprint=build.fingerprint,
                finished_at=build.finished_at,
                row_counts=dict(build.row_counts or {}),
            )
            for build in builds
        ],
        build_fingerprint=fingerprint,
    )
