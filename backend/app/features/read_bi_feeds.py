"""The Power BI Stage B feed: ONE route a report server pulls (docs/bi.md §Phase 4).

``GET /api/v1/banks/{bank_id}/bi/feeds/{dataset}`` serves one curated dataset as
NDJSON or CSV, streamed, with a cursor that says what is new. It is the only BI
surface a MACHINE principal may reach, and the only one with no row cap, so the
order its guards run in carries the whole security property:

1. **The institution, before anything else.** Mounted on
   ``BANK_ROUTE_DEPENDENCIES``, so a ``BK-*`` belonging to another tenant is
   ``404 Bank not found.`` before a credential is evaluated, a dataset is
   resolved or a log row is written.
2. **Then the deployment flag** (``require_bi_enabled``, reused from
   ``read_bi``): a deployment without BI answers 404 here exactly as it does on
   every other BI path. There is no separate feed flag today — see the module
   note at the bottom of this docstring.
3. **Then the credential.** :func:`require_bi_feed` admits ONLY an
   ``aeq_live_…`` integration key issued for THIS institution, and it locks that
   key, which serializes the pull with revocation and refreshes ``last_used_at``.
   A key naming a sibling institution of the same tenant is ``404``, the same
   answer the push routes give, because which institutions a credential covers is
   not something a credential may enumerate. A human app token is ``403``: a feed
   is a machine surface even for a principal who could run the same query
   interactively.
4. **Then the dataset.** An unregistered name is ``404`` with no list. The
   registry is public documentation (``docs/API_INTEGRATION.md`` §8), so the
   existence of a name is not a secret — what must never leak is the registry
   ITSELF in a refusal, and a reader of this route learns exactly one bit.
5. **Then the cursor**, validated rather than parsed permissively: a malformed
   token is ``422``, never "start from the beginning", because silently
   re-sending a bank's whole history in response to a typo is a denial of service
   dressed as helpfulness.
6. **Then authorization**, over every ``(module, sensitivity)`` pair the dataset
   touches, with the binding's data scope becoming an unremovable branch filter
   and an institution-grain dataset requiring scope ``all``
   (``services/bi/feeds/authorization.py``).
7. **Then the slices**, read from ``bi_mart_builds`` only, so a refused pull
   never reaches a figure.

**Every pull is recorded, exactly once, whatever the outcome.** Two rows, and
they are different records on purpose:

* one ``bi_query_log`` row — the BI READ record, under surface ``feed``, with the
  principal, the member ids, the allow/deny decision, the row count and the
  duration. This is the table the whole BI plane's reads are reviewed through.
* one ``audit_events`` row — the CREDENTIAL USE record, which is what names the
  dataset, the cursor in and the cursor out, the reporting dates served and the
  slice the credential covered. ``bi_query_log`` has no column for any of those
  and must not grow one: its ``query_hash`` is a one-way digest by design.

The volume is deliberate and worth stating: a report server refreshing four
times an hour over three datasets writes about 290 audit rows a day per
institution. That is the price of being able to answer "what did that credential
take, and when" — which is the first question anyone asks about a machine
credential, and the question a Cyber & Information Security Directive review
asks about an outbound data flow.

**Why the streamed half owns its own session.** A ``StreamingResponse`` body runs
AFTER the request's dependency-provided session has been closed (FastAPI exits
``yield`` dependencies before sending the response), so the generator opens its
own tenant-scoped session, reads every page through it, writes both records and
commits. That is also what lets the row count be the number of rows that actually
LEFT the platform: a client that disconnects halfway is recorded as having
received halfway, which is more useful than a count promised in advance.

**No dedicated feed kill-switch yet.** The surface is gated by ``BI_ENABLED``,
by the ``INTEGRATION_KEY_ROUTES`` allow-list in ``app/api/deps.py``, and by the
fact that a ``bi_reader`` credential has to be issued before anything can
authenticate. A ``BI_FEED_ENABLED`` flag in ``BiSettings`` would let an operator
close the outbound flow without closing the bank's own dashboards; it is named
in this track's report as a one-line follow-up rather than added here, because
``app/core/config.py`` belongs to nobody in this phase and a silent edit to it
would be the kind of change nobody reviews.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.api.deps import DbSession, Tenant, TenantBank, TenantContext
from app.core.authorization import PrincipalType
from app.core.config import get_settings
from app.core.observability import cross_tenant_attempt
from app.db.session import get_sessionmaker
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.identity import public as integration_keys
from app.identity.public import Bank
from app.services.audit import record_event
from app.services.bi import feeds, query_log
from app.services.bi.errors import BiQueryError
from app.services.bi.feeds import authorization as feed_authorization
from app.services.bi.feeds import cursor as feed_cursor
from app.services.bi.feeds import datasets as feed_datasets
from app.services.bi.feeds import render, runner

router = APIRouter(tags=["bi"])

#: The templated path, and the ``(method, path)`` tuple ``app/api/deps.py``'s
#: ``INTEGRATION_KEY_ROUTES`` must name for an integration key to be admitted
#: here at all. Exported so the allow-list entry and this route cannot drift:
#: the authentication boundary refuses a key on any route it does not name, and
#: without the entry this surface answers 401 to the only credential it accepts.
FEED_ROUTE_PATH: Final = "/api/v1/banks/{bank_id}/bi/feeds/{dataset}"
FEED_INTEGRATION_KEY_ROUTE: Final[tuple[str, str]] = ("GET", FEED_ROUTE_PATH)

#: Error codes the route answers with. Stable strings: a report server's own
#: alerting keys on them.
ERROR_MACHINE_REQUIRED: Final = "bi_feed_machine_credential_required"
ERROR_AUTHORIZATION_DENIED: Final = "bi_feed_authorization_denied"
ERROR_INVALID_CURSOR: Final = "bi_feed_invalid_cursor"
ERROR_SHAPE_CHANGED: Final = "bi_feed_shape_changed"

#: Audit event vocabulary. One event type for a served pull and one for a refused
#: one, so a reviewer can filter either without reading details.
EVENT_PULLED: Final = "bi_feed.pulled"
EVENT_REFUSED: Final = "bi_feed.refused"
ENTITY_TYPE: Final = "bi_feed"

_DIGEST_SEPARATOR: Final = "\x1f"


@dataclass(frozen=True, slots=True)
class BiFeedAccess:
    """An admitted machine puller, with the values every step below needs.

    ``bank_id`` is carried as a plain string beside the row: the streamed half of
    the pull runs after the request's session has been closed, and reading an
    identifier off a detached ORM instance is the kind of thing that works until
    the day something expires it.
    """

    ctx: TenantContext
    bank: Bank
    bank_id: str
    principal_user_id: UUID
    integration_key_id: UUID


def require_bi_feed(db: DbSession, ctx: Tenant, bank: TenantBank) -> BiFeedAccess:
    """Admit only a bank-scoped integration key issued for THIS institution.

    Refuses in the two shapes the platform already uses for the same two facts:
    ``403`` when the credential is not a machine key at all (a human token, an
    impersonated operator session), and ``404 Bank not found.`` when the key
    names a different institution — the same answer ``resolve_tenant_bank`` gives
    for another tenant's bank, so a credential cannot map the estate.
    """

    if bank is None:
        # Every feed path carries ``{bank_id}``, so the resolver cannot return
        # None here; refuse rather than narrow with an assertion.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    if ctx.integration_key_id is None or ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ERROR_MACHINE_REQUIRED,
                "message": (
                    "The analytics feed is served only to an integration key issued "
                    "for this institution."
                ),
            },
        )
    if ctx.integration_key_bank_id != bank.id:
        cross_tenant_attempt(
            reason="integration_key_bank_mismatch",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            credential_bank_id=ctx.integration_key_bank_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    if not feed_authorization.machine_credential(ctx, bank):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ERROR_MACHINE_REQUIRED,
                "message": (
                    "The analytics feed is served only to an integration key issued "
                    "for this institution."
                ),
            },
        )
    # Locking the credential serializes this pull with revocation and refreshes
    # ``last_used_at``; the write is committed below, before anything streams.
    key = integration_keys.lock_authenticated_key(db, ctx)
    return BiFeedAccess(
        ctx=ctx,
        bank=bank,
        bank_id=bank.id,
        principal_user_id=ctx.actor_user_id,
        integration_key_id=key.id,
    )


BiFeed = Annotated[BiFeedAccess, Depends(require_bi_feed)]


def _digest(*parts: object) -> str:
    """A one-way digest of what was asked, for ``bi_query_log.query_hash``.

    The dataset, the cursor and the format, hashed — never stored as values, for
    the same reason no filter value ever reaches that table. Two identical pulls
    hash alike, which is what lets a reviewer tell a polling loop from a walk.
    """

    material = _DIGEST_SEPARATOR.join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record(  # noqa: PLR0913 - one log row, spelled out
    db: Session,
    access: BiFeedAccess,
    *,
    query_hash: str,
    decision: str,
    member_ids: tuple[str, ...],
    denied_members: tuple[str, ...] = (),
    row_count: int | None = None,
    duration_ms: int | None = None,
    build_fingerprint: str | None = None,
) -> None:
    """Append the one ``bi_query_log`` row this pull is, and commit it."""

    query_log.record(
        db,
        query_log.QueryRecord(
            organization_id=access.ctx.organization_id,
            bank_id=access.bank_id,
            principal_user_id=access.principal_user_id,
            surface=feeds.SURFACE_FEED,
            query_hash=query_hash,
            decision=decision,
            catalogue_version=CATALOGUE_VERSION,
            member_ids=member_ids,
            denied_members=denied_members,
            row_count=row_count,
            duration_ms=duration_ms,
            build_fingerprint=build_fingerprint,
            principal_type=PrincipalType.MACHINE.value,
        ),
    )
    db.commit()


def _refuse(  # noqa: PLR0913 - one refusal, its record and its audit row
    db: Session,
    access: BiFeedAccess,
    *,
    query_hash: str,
    dataset_id: str,
    reason: str,
    member_ids: tuple[str, ...] = (),
    denied_members: tuple[str, ...] = (),
    detail: dict[str, object],
    status_code: int,
) -> HTTPException:
    """Record the refusal in both places, then return the exception to raise."""

    record_event(
        db,
        access.ctx,
        event_type=EVENT_REFUSED,
        entity_type=ENTITY_TYPE,
        entity_id=access.bank_id,
        details={
            "bank_id": access.bank_id,
            "dataset": dataset_id,
            "reason": reason,
            "integration_key_id": str(access.integration_key_id),
            "denied_members": list(denied_members),
        },
    )
    _record(
        db,
        access,
        query_hash=query_hash,
        decision=query_log.DECISION_DENIED,
        member_ids=member_ids,
        denied_members=denied_members,
    )
    return HTTPException(status_code=status_code, detail=detail)


@router.get(
    "/banks/{bank_id}/bi/feeds/{dataset}",
    operation_id="pullBiFeed",
    summary="Pull one curated analytics dataset",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "The dataset, streamed as NDJSON or CSV.",
            "content": {"application/x-ndjson": {}, "text/csv": {}},
        }
    },
)
def pull_bi_feed(  # noqa: PLR0913 - FastAPI injects db/access and the two query values
    bank_id: str,
    dataset: str,
    db: DbSession,
    access: BiFeed,
    fmt: Annotated[render.FeedFormat, Query(alias="format")] = "ndjson",
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> StreamingResponse:
    """Serve everything in ``dataset`` that is newer than ``cursor``.

    The response carries the cursor to use next
    (``X-Bi-Feed-Next-Cursor``), the reporting dates it covered and the slice of
    the institution the credential is authorized over. The consumer's one
    obligation is **replace by reporting date**: delete its own rows for every
    date in ``X-Bi-Feed-Reporting-Dates``, then insert the payload. That is what
    makes a restatement land, and it is why re-delivery is harmless.
    """

    _ = bank_id  # the institution is the router's dependency
    query_hash = _digest(feeds.SURFACE_FEED, dataset, cursor, fmt)

    try:
        entry = feed_datasets.dataset(dataset)
    except feed_datasets.UnknownDataset as exc:
        raise _refuse(
            db,
            access,
            query_hash=query_hash,
            dataset_id=dataset,
            reason="unknown_dataset",
            detail={"error_code": "not_found", "message": "Not found."},
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    requested: feed_cursor.FeedCursor | None = None
    if cursor is not None:
        try:
            requested = feed_cursor.decode(cursor)
        except feed_cursor.InvalidCursor as exc:
            raise _refuse(
                db,
                access,
                query_hash=query_hash,
                dataset_id=entry.id,
                reason=ERROR_INVALID_CURSOR,
                detail={
                    "error_code": ERROR_INVALID_CURSOR,
                    "message": (
                        "The cursor is not one this feed issued. Omit it to resynchronise "
                        "from the beginning."
                    ),
                },
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from exc

    cat = catalogue()
    decision = feed_authorization.authorize_feed(db, access.ctx, access.bank, cat, entry)
    if not decision.allowed:
        raise _refuse(
            db,
            access,
            query_hash=query_hash,
            dataset_id=entry.id,
            reason=decision.reason,
            member_ids=decision.member_ids,
            denied_members=decision.denied_members,
            detail={
                "error_code": ERROR_AUTHORIZATION_DENIED,
                "message": (
                    "This credential is not authorized for the figures in this dataset. "
                    "An Org Owner can widen or reissue it."
                ),
                "denied_members": list(decision.denied_members),
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    selection = feed_cursor.select_slices(
        db,
        organization_id=access.ctx.organization_id,
        bank_id=access.bank_id,
        entry=entry,
        cursor=requested,
    )
    shape_date = selection.slices[0].as_of if selection.slices else feed_datasets.SHAPE_DATE
    try:
        columns = runner.columns_for(
            db,
            cat=cat,
            entry=entry,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank_id,
            injected=decision.injected,
            as_of=shape_date,
        )
    except BiQueryError as exc:
        raise _refuse(
            db,
            access,
            query_hash=query_hash,
            dataset_id=entry.id,
            reason=exc.code,
            member_ids=decision.member_ids,
            detail={"error_code": exc.code, "message": exc.message},
            status_code=status.HTTP_409_CONFLICT,
        ) from exc

    block = runner.build_provenance(
        db,
        bank=access.bank,
        entry=entry,
        columns=columns,
        selection=selection,
        requested_cursor=cursor,
        data_scope_label=decision.scope_label,
    )
    # The credential's ``last_used_at`` and nothing else: committed before the
    # body starts, because the request session does not survive into the stream.
    db.commit()

    return StreamingResponse(
        _stream(
            access,
            entry=entry,
            decision=decision,
            selection=selection,
            block=block,
            fmt=fmt,
            query_hash=query_hash,
        ),
        media_type=render.MEDIA_TYPES[fmt],
        headers=block.headers(),
    )


class _RowCounter:
    """Counts the rows that actually reach the wire, for the two records below."""

    def __init__(self) -> None:
        self.count = 0

    def wrap(self, rows: Iterator[tuple[Any, ...]]) -> Iterator[tuple[Any, ...]]:
        for row in rows:
            self.count += 1
            yield row


def _stream(  # noqa: PLR0913 - the streamed half's own inputs, all explicit
    access: BiFeedAccess,
    *,
    entry: feed_datasets.FeedDataset,
    decision: feed_authorization.FeedAuthorization,
    selection: feed_cursor.SliceSelection,
    block: runner.FeedProvenance,
    fmt: render.FeedFormat,
    query_hash: str,
) -> Iterator[bytes]:
    """The payload, then the two records of what left the platform.

    Owns its own session: the request's was closed before this generator ran.
    Both records are written in ``finally`` and describe what actually reached
    the wire, so a client that disconnected halfway — or a statement the server
    cancelled — is recorded as having served what it served rather than as having
    served everything.
    """

    settings = get_settings().bi
    session = get_sessionmaker()()
    session.info["organization_id"] = access.ctx.organization_id
    counter = _RowCounter()
    started = perf_counter()
    try:
        rows = runner.iter_rows(
            session,
            cat=catalogue(),
            entry=entry,
            slices=selection.slices,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank_id,
            injected=decision.injected,
            timeout_ms=settings.export_timeout_ms,
            columns=block.columns,
        )
        yield from render.iter_bytes(counter.wrap(rows), block.columns, fmt)
    finally:
        try:
            _finalise(
                session,
                access,
                entry=entry,
                selection=selection,
                block=block,
                fmt=fmt,
                query_hash=query_hash,
                rows=counter.count,
                elapsed_ms=int((perf_counter() - started) * 1000),
            )
        finally:
            session.close()


def _finalise(  # noqa: PLR0913 - one served pull, recorded in both places
    session: Session,
    access: BiFeedAccess,
    *,
    entry: feed_datasets.FeedDataset,
    selection: feed_cursor.SliceSelection,
    block: runner.FeedProvenance,
    fmt: render.FeedFormat,
    query_hash: str,
    rows: int,
    elapsed_ms: int,
) -> None:
    """The ``bi_query_log`` row and the ``audit_events`` row for a served pull."""

    next_cursor = selection.next_cursor
    record_event(
        session,
        access.ctx,
        event_type=EVENT_PULLED,
        entity_type=ENTITY_TYPE,
        entity_id=access.bank_id,
        details={
            "bank_id": access.bank_id,
            "dataset": entry.id,
            "format": fmt,
            "cursor": block.requested_cursor,
            "next_cursor": next_cursor.encode() if next_cursor is not None else None,
            "reporting_dates": [slice_.as_of.isoformat() for slice_ in selection.slices],
            "more_available": selection.more_available,
            "row_count": rows,
            "data_scope": block.data_scope_label,
            "build_fingerprint": block.build_fingerprint,
            "catalogue_version": CATALOGUE_VERSION,
            "integration_key_id": str(access.integration_key_id),
        },
    )
    _record(
        session,
        access,
        query_hash=query_hash,
        decision=query_log.DECISION_ALLOWED,
        member_ids=block.dataset.member_ids,
        row_count=rows,
        duration_ms=elapsed_ms,
        build_fingerprint=block.build_fingerprint,
    )


__all__ = [
    "ENTITY_TYPE",
    "ERROR_AUTHORIZATION_DENIED",
    "ERROR_INVALID_CURSOR",
    "ERROR_MACHINE_REQUIRED",
    "ERROR_SHAPE_CHANGED",
    "EVENT_PULLED",
    "EVENT_REFUSED",
    "FEED_INTEGRATION_KEY_ROUTE",
    "FEED_ROUTE_PATH",
    "BiFeedAccess",
    "pull_bi_feed",
    "require_bi_feed",
    "router",
]
