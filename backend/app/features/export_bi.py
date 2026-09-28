"""Governed exports: ``POST /banks/{id}/bi/export`` and its collection route.

An export is the only BI surface that hands the bank's figures to something the
platform will never see again, so the order this module consults its guards in
is the security property — exactly as it is for the read surface, and by reusing
the read surface's own pipeline rather than a second copy of it:

1. **the institution, then the flag, then the principal.** The router mounts
   this with ``BANK_ROUTE_DEPENDENCIES`` and ``require_bi_enabled``, and the
   routes resolve ``read_bi.require_bi_read`` — so a sibling tenant's ``BK-*``
   is 404, a deployment without BI answers 404, and a machine key or an
   impersonated operator never reaches an export (D-026).
2. **the disclosure class, read from the query.** ``exports.policy`` classifies
   the members the query touches: a summary needs ``view``, a record-level or
   confidential export needs ``view`` AND ``export``. The client sends no flag
   about this and could not be believed if it did.
3. **the same decision the read routes make**, through
   ``read_bi._authorize``, with the elevated permission set. That reuse is what
   guarantees the property ``docs/bi.md`` states as a rule: *an export must never
   be able to return a member the caller could not query interactively.* There
   is one evaluator, one budget, one denial shape and one ``bi_query_log`` row.
4. **the row cap decides the delivery.** Under ``BI_EXPORT_ASYNC_THRESHOLD_ROWS``
   the artifact is rendered inline and streamed back. Over it, the request
   becomes a ``bi_export`` job in the ``bi`` worker lane which writes to the
   storage temp tier; the owner collects it from the second route as a
   short-lived presigned link.

**How "over the threshold" is known without a second query.** The statement runs
once, asking the executor for the threshold as its row cap; the executor always
fetches ``cap + 1`` rows to detect truncation, so an over-threshold export is
identified by the same read that would have served an under-threshold one. No
``COUNT`` and no double execution in the common case.

**The log.** One ``bi_query_log`` row per request, with ``surface="export"``:
the rows served inline, or ``row_count`` NULL when the request was queued
instead — which is what that column already means ("a read that served no
rows"). When the job later renders the file it writes its own row, because that
render is a second, separately authorized read that happened minutes later under
possibly different authority. Both paths also write ``audit_events``, which
``docs/bi.md`` requires for every export.

**The collection route is owner-only.** A presigned GET carries no identity, so
whoever holds the link holds the file. The route therefore mints one only for
the principal named in the job's own payload; for anyone else the job does not
exist.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.api.deps import DbSession
from app.core.config import get_settings
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.features import read_bi
from app.features.read_bi import Authorized, BiRead, BiReadAccess
from app.models import Job
from app.schemas.bi import (
    BiExportClass,
    BiExportFormat,
    BiExportRead,
    BiExportRequest,
    BiExportState,
    BiQuery,
    BiTrustBadge,
)
from app.services import audit
from app.services.bi import exports, query_log
from app.services.bi.authorization import query_members
from app.services.bi.errors import BiQueryError
from app.services.bi.exports import jobs as export_jobs
from app.services.bi.exports import policy, runner
from app.storage import StorageError
from app.storage.client import StorageClient

router = APIRouter(tags=["bi"])

#: ``audit_events.event_type`` for an export served in the request itself, and
#: for one queued for the worker. Both are recorded before the response is
#: built, in the same transaction as the query-log row.
EVENT_DELIVERED = "bi.export.delivered"
EVENT_QUEUED = "bi.export.queued"

#: ``audit_events.entity_type``. An inline export has no queue row, so the
#: institution is what both kinds are filed against; the details name the rest.
ENTITY_TYPE = "bi_export"

#: Production copy for each state of an export, shown as-is.
STATE_MESSAGES: dict[str, str] = {
    "ready": "Your export is ready to download.",
    "queued": (
        "This export is larger than we serve in one request, so it is being prepared in the "
        "background. Check back shortly for the download link."
    ),
    "running": "Your export is being prepared. Check back shortly for the download link.",
    "failed": (
        "The export could not be produced. Try a narrower window or fewer fields, "
        "and contact support if it keeps failing."
    ),
    "denied": (
        "Your access no longer covers every field this export needs, so it was not produced. "
        "An Org Owner can grant the missing fields."
    ),
}


def storage_client() -> StorageClient:
    """The object store, resolved per request so a test can override the dependency."""

    from app.storage.factory import get_storage_client  # noqa: PLC0415 - lazy, like ingestion

    return get_storage_client()


Storage = Annotated[StorageClient, Depends(storage_client)]


# --- the classification ---------------------------------------------------------------------


def _classify(query: BiQuery) -> policy.ExportClass:
    """Which disclosure class this query is, from the catalogue's own declarations.

    Raised refusals are the compiler's own (an unknown member id is a 422 with
    its message), so a malformed query fails the same way here as everywhere
    else rather than being classified as something.
    """

    return policy.classify(query_members(catalogue(), query))


def _trust_badge(db: Session, access: BiReadAccess, query: BiQuery) -> BiTrustBadge:
    window = read_bi.data_window(query.time)
    return read_bi.trust_badge(db, access.ctx.organization_id, access.bank.id, window)


# --- the export ------------------------------------------------------------------------------


@router.post(
    "/banks/{bank_id}/bi/export",
    operation_id="runBiExport",
    responses={
        200: {"description": "The artifact itself.", "content": {"application/octet-stream": {}}},
        202: {"model": BiExportRead, "description": "Queued for the worker."},
    },
)
def run_bi_export(  # noqa: PLR0913 - FastAPI injects db/access/storage
    bank_id: str,
    request: BiExportRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
) -> Response:
    """Export one catalogue query as CSV, a workbook or a PDF.

    Answers with the file when it fits in one request, and with
    :class:`~app.schemas.bi.BiExportRead` and ``202`` when it does not.
    """

    _ = bank_id, response  # the institution is the router's dependency
    settings = get_settings().bi
    fmt = request.format
    query = request.query

    try:
        export_class = _classify(query)
    except BiQueryError as exc:
        raise read_bi.query_error(exc) from exc

    authorized = read_bi.authorize(
        db,
        access,
        query,
        surface=read_bi.SURFACE_EXPORT,
        if_none_match=None,
        permissions=policy.permissions_for(export_class),
    )

    probe_cap = min(settings.export_async_threshold_rows, settings.export_row_cap)
    try:
        run = runner.run_query(
            db,
            cat=catalogue(),
            query=query,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            injected_filters=read_bi.injected_filters(authorized),
            row_cap=probe_cap,
            timeout_ms=settings.export_timeout_ms,
        )
    except BiQueryError as exc:
        read_bi.append_query_log(db, replace(authorized.record, row_count=None, duration_ms=None))
        raise read_bi.query_error(exc) from exc

    # Fail closed on the COMPILED member set: compilation can add members the
    # decision did not cover (a data-scope filter), and a widened set must not be
    # served under the authority that covered the narrower one.
    if policy.classify_ids(catalogue(), run.compiled.member_ids) != export_class:
        read_bi.append_query_log(
            db,
            replace(
                authorized.record,
                decision=query_log.DECISION_DENIED,
                denied_members=run.compiled.member_ids,
                row_count=None,
                build_fingerprint=None,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "bi_export_disclosure_class_changed",
                "message": (
                    "This export needs more access than the fields you named suggested. "
                    "An Org Owner can grant the missing fields."
                ),
            },
        )

    if run.truncated and probe_cap < settings.export_row_cap:
        return _queue(db, access, query, fmt=fmt, export_class=export_class, authorized=authorized)
    return _inline(
        db, access, query, run=run, fmt=fmt, export_class=export_class, authorized=authorized
    )


def _inline(  # noqa: PLR0913 - one delivery, spelled out
    db: Session,
    access: BiReadAccess,
    query: BiQuery,
    *,
    run: runner.ExportRun,
    fmt: BiExportFormat,
    export_class: policy.ExportClass,
    authorized: Authorized,
) -> Response:
    """Render the artifact and stream it back, logging and auditing it first."""

    ctx_block = runner.build_context(
        db,
        bank=access.bank,
        query=query,
        cat=catalogue(),
        data_scope=authorized.scope,
        export_class=export_class,
        user_label=runner.principal_label(db, access.ctx.organization_id, access.principal_user_id),
        window=authorized.window,
    )
    table = runner.to_table(run, ctx_block)
    filename = exports.filename_for(fmt, bank_id=access.bank.id, as_of_label=ctx_block.as_of_label)
    audit.record_event(
        db,
        access.ctx,
        event_type=EVENT_DELIVERED,
        entity_type=ENTITY_TYPE,
        entity_id=access.bank.id,
        details={
            "bank_id": access.bank.id,
            "format": fmt,
            "export_class": export_class,
            "delivery": "inline",
            "query_hash": authorized.record.query_hash,
            "member_ids": list(run.compiled.member_ids),
            "row_count": run.row_count,
            "truncated": run.truncated,
            "catalogue_version": CATALOGUE_VERSION,
            "build_fingerprint": ctx_block.build_fingerprint,
            "trust": ctx_block.trust_status,
        },
    )
    read_bi.append_query_log(
        db,
        replace(
            authorized.record,
            member_ids=run.compiled.member_ids,
            row_count=run.row_count,
            duration_ms=run.result.elapsed_ms,
        ),
    )
    return StreamingResponse(
        exports.iter_bytes(fmt, table, ctx_block),
        media_type=exports.MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Bi-Export-Class": export_class,
            "X-Bi-Catalogue-Version": CATALOGUE_VERSION,
        },
    )


def _queue(  # noqa: PLR0913 - one delivery, spelled out
    db: Session,
    access: BiReadAccess,
    query: BiQuery,
    *,
    fmt: BiExportFormat,
    export_class: policy.ExportClass,
    authorized: Authorized,
) -> Response:
    """Hand the export to the ``bi`` lane and answer 202 with the job to poll."""

    job = export_jobs.enqueue_export(
        db,
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        query=query,
        fmt=fmt,
        export_class=export_class,
        reason="over_inline_row_threshold",
    )
    audit.record_event(
        db,
        access.ctx,
        event_type=EVENT_QUEUED,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        details={
            "bank_id": access.bank.id,
            "format": fmt,
            "export_class": export_class,
            "delivery": "asynchronous",
            "query_hash": authorized.record.query_hash,
            "catalogue_version": CATALOGUE_VERSION,
        },
    )
    # ``row_count`` stays NULL: this request served no rows. The job writes its
    # own row when it renders, for the read that actually leaves the platform.
    read_bi.append_query_log(
        db, replace(authorized.record, member_ids=authorized.decision.member_ids)
    )
    payload = BiExportRead(
        job_id=job.id,
        state="queued",
        format=fmt,
        export_class=cast("BiExportClass", export_class),
        message=STATE_MESSAGES["queued"],
        trust=_trust_badge(db, access, query),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
    )
    return Response(
        content=payload.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
        headers={"Cache-Control": "private, no-store"},
    )


# --- collecting a queued export ---------------------------------------------------------------

#: ``jobs.status`` → the export state a caller sees.
_JOB_STATES: dict[str, BiExportState] = {
    "queued": "queued",
    "running": "running",
    "failed": "failed",
}


@router.get(
    "/banks/{bank_id}/bi/exports/{job_id}",
    response_model=BiExportRead,
    operation_id="getBiExport",
)
def get_bi_export(  # noqa: PLR0913 - FastAPI injects db/access/storage
    bank_id: str,
    job_id: UUID,
    db: DbSession,
    access: BiRead,
    storage: Storage,
) -> BiExportRead:
    """One queued export, and a short-lived download link once it is ready.

    404 for anyone but the principal who asked for it: a presigned GET is a
    bearer credential, so the owner check IS the access control on the finished
    file, and the existence of another person's export is not this caller's
    business either.
    """

    _ = bank_id
    job = db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.organization_id == access.ctx.organization_id,
            Job.job_type == export_jobs.JOB_TYPE,
            Job.bank_id == access.bank.id,
        )
    )
    owner = str((job.payload or {}).get("principal_user_id")) if job is not None else None
    if job is None or owner != str(access.principal_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found.")

    progress = job.progress or {}
    fmt = cast("BiExportFormat", (job.payload or {}).get("format", "csv"))
    export_class = cast(
        "BiExportClass", (job.payload or {}).get("export_class", policy.RECORD_LEVEL)
    )
    reported = str(progress.get("status") or "")
    if job.status == "succeeded" and reported == export_jobs.STATUS_DENIED:
        state: BiExportState = "denied"
    elif job.status == "succeeded" and reported == export_jobs.STATUS_SUCCEEDED:
        state = "ready"
    elif job.status == "succeeded":
        # Skipped by the run-time kill-switch, or by the version rule. There is
        # no file and there will not be one from this row; say so rather than
        # leaving the caller polling a job that has finished.
        state = "failed"
    else:
        state = _JOB_STATES.get(job.status, "running")

    base = BiExportRead(
        job_id=job.id,
        state=state,
        format=fmt,
        export_class=export_class,
        message=STATE_MESSAGES[state],
        catalogue_version=str((job.payload or {}).get("catalogue_version") or CATALOGUE_VERSION),
    )
    if state != "ready":
        return base
    object_path = str(progress.get("object_path") or "")
    try:
        link = export_jobs.download_url(storage, bank=access.bank, object_path=object_path)
    except (StorageError, export_jobs.BiExportJobError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "bi_export_unavailable",
                "message": (
                    "The export is ready but the document store did not answer. "
                    "Try again in a moment."
                ),
            },
        ) from exc
    return base.model_copy(
        update={
            "filename": progress.get("filename"),
            "media_type": progress.get("media_type"),
            "row_count": progress.get("row_count"),
            "truncated": bool(progress.get("truncated")),
            "size_bytes": progress.get("size_bytes"),
            "checksum_sha256": progress.get("checksum_sha256"),
            "download_url": link,
            "download_expires_in_seconds": export_jobs.DOWNLOAD_EXPIRY_SECONDS,
        }
    )


__all__ = [
    "ENTITY_TYPE",
    "EVENT_DELIVERED",
    "EVENT_QUEUED",
    "STATE_MESSAGES",
    "get_bi_export",
    "router",
    "run_bi_export",
    "storage_client",
]
