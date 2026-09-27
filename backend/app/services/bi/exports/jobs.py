"""The asynchronous export: the ``bi_export`` job's payload, run and artifact.

``docs/bi.md`` §Exports: *async exports over 10k rows: job ``bi_export`` writes
to the storage temp tier and returns a presigned GET link.* The threshold is
``BI_EXPORT_ASYNC_THRESHOLD_ROWS`` (10 000 by default) and the cap above it is
``BI_EXPORT_ROW_CAP`` (100 000).

**The job re-authorizes.** It does not inherit the request's decision. Minutes
pass between the enqueue and the render, and in those minutes a grant can be
revoked — ``authorization.invalidate_user_authorization`` bumps ``authv`` and
kills the sessions, but a queued row is not a session. So the handler rebuilds
the principal from ``users`` as it stands NOW, re-runs ``authorize_query`` over
the same query, and refuses if the answer has changed. A refusal is a
``succeeded`` job carrying ``status: "denied"``: the queue's retry cannot fix an
authority decision, and failing the row would bury the reason under three
attempts. The file is never written in that case.

**The link is not stored.** The job records the object path; the presigned URL
is minted when the owner asks for it, from the route, with its own short expiry.
A URL persisted in ``jobs.progress`` would be a bearer credential sitting in a
table that outlives it.

**Only the requester may collect it.** A presigned GET carries no identity at
all, so the route that mints one compares ``payload["principal_user_id"]``
against the caller and answers 404 otherwise. That comparison is the whole of
the access control on the finished artifact, which is why the payload names the
principal rather than relying on ``jobs.organization_id``.

**The payload carries the query, which can carry filter VALUES.** That is
unavoidable — the job has to re-run it — and it is why nothing here writes those
values to ``bi_query_log``, which is the log a reviewer reads and is deliberately
value-free. ``jobs`` is tenant-scoped like every other queue row.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.models import Bank, Job, User
from app.schemas.bi import BiQuery
from app.services import job_queue
from app.services.bi import exports, query_log
from app.services.bi.authorization import BiAuthorization, authorize_query
from app.services.bi.errors import BiQueryError
from app.services.bi.exports import policy, runner
from app.services.bi.versions import BUILDER_VERSION
from app.storage.client import ObjectMetadata, StorageClient, StorageLocation

#: The job type. A literal here and in ``job_queue.JOB_TYPES``; ``job_queue``
#: validates the two agree at enqueue time.
JOB_TYPE = "bi_export"

#: ``jobs.entity_type`` — the institution, so the operator board can find a
#: tenant's exports the way it finds their mart builds.
ENTITY_TYPE = "bank"

#: The tier the spec names. ``temp`` deletes physically (``storage.client``:
#: every other tier keeps a delete marker), which is the correct lifecycle for
#: a file the bank already holds a copy of.
STORAGE_TIER = "temp"

#: Who the object store records as the writer.
WRITTEN_BY = "bi-export"

#: How long a minted download link lives. Short, because the link is a bearer
#: credential: long enough to click, not long enough to forward and forget.
DOWNLOAD_EXPIRY_SECONDS = 900

#: ``jobs.progress["status"]`` values this handler writes.
STATUS_SUCCEEDED = "succeeded"
STATUS_DENIED = "denied"


class BiExportJobError(Exception):
    """A ``bi_export`` job that cannot run: a malformed payload, a missing bank.

    Structural — a retry does not help — but the queue's bounded retry is the
    only failure channel a handler has, so it is raised and the row lands
    ``failed`` after its attempts (the ``bi_common.BiJobError`` idiom).
    """


def payload_for(  # noqa: PLR0913 - the job contract, spelled out
    *,
    organization_id: str,
    bank_id: str,
    principal_user_id: UUID,
    query: BiQuery,
    fmt: exports.ExportFormat,
    export_class: policy.ExportClass,
    reason: str,
) -> dict[str, Any]:
    """The queue payload. Everything the handler needs and nothing it does not."""

    return {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "builder_version": BUILDER_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "principal_user_id": str(principal_user_id),
        "format": fmt,
        "export_class": export_class,
        "query": query.model_dump(mode="json"),
        "reason": reason,
    }


def enqueue_export(  # noqa: PLR0913 - the job contract, spelled out
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    principal_user_id: UUID,
    query: BiQuery,
    fmt: exports.ExportFormat,
    export_class: policy.ExportClass,
    reason: str,
) -> Job:
    """Queue one export. No coalesce key: two exports are two files.

    Flushes but never commits — the route's transaction owns the commit, so the
    job lands with the ``bi_query_log`` row that recorded the request.
    """

    return job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        bank_id=bank_id,
        payload=payload_for(
            organization_id=organization_id,
            bank_id=bank_id,
            principal_user_id=principal_user_id,
            query=query,
            fmt=fmt,
            export_class=export_class,
            reason=reason,
        ),
        entity_type=ENTITY_TYPE,
        entity_id=bank_id,
    )


# --- reading the payload back ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportJobRequest:
    """One queue row, validated against the session it will run in."""

    bank: Bank
    user: User
    query: BiQuery
    fmt: exports.ExportFormat
    export_class: policy.ExportClass


def _require(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise BiExportJobError(f"Job payload is missing {key}.")
    return value


def read_request(session: Session, job: Job) -> ExportJobRequest:
    """The payload as typed values, proven to belong to the job's own tenant.

    ``worker.run_once`` already bound the session to ``job.organization_id``, so
    every lookup here is tenant-scoped. What is checked is that the PAYLOAD
    agrees with the row: a hand-edited or replayed payload must not point a
    tenant-bound session at a sibling bank, a sibling tenant or another user.
    """

    payload = job.payload or {}
    payload_org = payload.get("organization_id")
    if payload_org is not None and str(payload_org) != job.organization_id:
        raise BiExportJobError(
            f"Job payload organization_id {payload_org!r} does not match the job's "
            f"organization {job.organization_id!r}."
        )
    bank_id = job.bank_id or payload.get("bank_id")
    if not bank_id:
        raise BiExportJobError("Job has no bank_id.")
    payload_bank = payload.get("bank_id")
    if payload_bank is not None and str(payload_bank) != str(bank_id):
        raise BiExportJobError(
            f"Job payload bank_id {payload_bank!r} does not match the job's bank {bank_id!r}."
        )
    bank = session.scalar(
        select(Bank).where(Bank.id == str(bank_id), Bank.organization_id == job.organization_id)
    )
    if bank is None:
        raise BiExportJobError(f"Bank {bank_id} not found for organization.")

    try:
        principal_id = UUID(str(_require(payload, "principal_user_id")))
    except ValueError as exc:
        raise BiExportJobError("Job payload principal_user_id is not a UUID.") from exc
    user = session.scalar(
        select(User).where(User.id == principal_id, User.organization_id == job.organization_id)
    )
    if user is None:
        raise BiExportJobError("Job payload names a user this organization does not have.")

    fmt = str(_require(payload, "format"))
    if fmt not in exports.EXPORT_FORMATS:
        raise BiExportJobError(f"Job payload format {fmt!r} is not an export format.")
    export_class = str(_require(payload, "export_class"))
    if export_class not in (policy.SUMMARY, policy.RECORD_LEVEL):
        raise BiExportJobError(f"Job payload export_class {export_class!r} is not a class.")
    try:
        query = BiQuery.model_validate(_require(payload, "query"))
    except ValueError as exc:
        raise BiExportJobError("Job payload query is not a valid BiQuery.") from exc
    return ExportJobRequest(
        bank=bank,
        user=user,
        query=query,
        fmt=cast("exports.ExportFormat", fmt),
        export_class=cast("policy.ExportClass", export_class),
    )


# --- running it -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportJobResult:
    """What the handler records: the artifact, or why there is not one."""

    status: str
    bank_id: str
    principal_user_id: UUID
    export_class: policy.ExportClass
    fmt: str
    object_path: str | None = None
    storage_tier: str = STORAGE_TIER
    filename: str | None = None
    media_type: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    row_count: int | None = None
    truncated: bool = False
    reason: str | None = None
    denied_members: tuple[str, ...] = ()

    def progress(self) -> dict[str, Any]:
        """``jobs.progress`` for this outcome. Never a URL: see the module docstring."""

        record: dict[str, Any] = {
            "status": self.status,
            "format": self.fmt,
            "export_class": self.export_class,
        }
        if self.status == STATUS_SUCCEEDED:
            record.update(
                {
                    "object_path": self.object_path,
                    "storage_tier": self.storage_tier,
                    "filename": self.filename,
                    "media_type": self.media_type,
                    "checksum_sha256": self.checksum_sha256,
                    "size_bytes": self.size_bytes,
                    "row_count": self.row_count,
                    "truncated": self.truncated,
                }
            )
        else:
            record["reason"] = self.reason
            if self.denied_members:
                record["denied_members"] = list(self.denied_members)
        return record


def _context_for(job: Job, request: ExportJobRequest) -> TenantContext:
    """The requester as the evaluator sees them, at their CURRENT authority."""

    return TenantContext(
        organization_id=job.organization_id,
        actor_user_id=request.user.id,
        authorization_version=request.user.authorization_version,
    )


def _decide(
    session: Session, ctx: TenantContext, request: ExportJobRequest
) -> BiAuthorization | None:
    """Re-run the export's whole authorization sentence; ``None`` means denied."""

    cat = catalogue()
    allowed: BiAuthorization | None = None
    for permission in policy.permissions_for(request.export_class):
        decision = authorize_query(
            session,
            ctx,
            request.bank,
            cat,
            request.query,
            permission=permission,
            surface=exports.QUERY_LOG_SURFACE,
        )
        if not decision.allowed or not decision.data_scope.whole_institution:
            return None
        allowed = decision
    return allowed


def _store(  # noqa: PLR0913 - one storage write is its named parts
    storage: StorageClient,
    *,
    bank: Bank,
    job: Job,
    filename: str,
    payload: bytes,
    media_type: str,
) -> str:
    """Write the artifact to the temp tier and return its object path."""

    slug = bank.storage_slug
    if not slug:
        raise BiExportJobError(
            f"Bank {bank.id} has no storage slug, so no institution bucket exists. "
            "A slug is assigned by the Data Engine on the institution's first ingestion."
        )
    object_path = f"bi_exports/{bank.id}/{job.id}/{filename}"
    checksum = hashlib.sha256(payload).hexdigest()
    storage.ensure_institution(slug)
    storage.write(
        StorageLocation(institution_slug=slug, tier=STORAGE_TIER, object_path=object_path),
        io.BytesIO(payload),
        ObjectMetadata(
            institution_slug=slug,
            tier=STORAGE_TIER,
            checksum_sha256=checksum,
            written_at=datetime.now(UTC),
            written_by=WRITTEN_BY,
            source_reference=str(job.id),
        ),
        content_type=media_type,
    )
    return object_path


def download_url(storage: StorageClient, *, bank: Bank, object_path: str) -> str:
    """A short-lived presigned GET for a finished export."""

    slug = bank.storage_slug
    if not slug:
        raise BiExportJobError(f"Bank {bank.id} has no storage slug.")
    return storage.presigned_url(
        StorageLocation(institution_slug=slug, tier=STORAGE_TIER, object_path=object_path),
        "read",
        expires_in_seconds=DOWNLOAD_EXPIRY_SECONDS,
    )


def render_job(session: Session, job: Job, *, storage: StorageClient) -> ExportJobResult:
    """Re-authorize, run, render and store one queued export.

    Writes exactly one ``bi_query_log`` row with ``surface="export"`` — the
    render is its own read, distinct from the request that queued it — and no
    other table. The caller sets ``jobs.progress`` and records the audit event.
    """

    settings = get_settings().bi
    request = read_request(session, job)
    ctx = _context_for(job, request)
    base = ExportJobResult(
        status=STATUS_DENIED,
        bank_id=request.bank.id,
        principal_user_id=request.user.id,
        export_class=request.export_class,
        fmt=request.fmt,
    )
    attempt = query_log.QueryRecord(
        organization_id=job.organization_id,
        bank_id=request.bank.id,
        principal_user_id=request.user.id,
        surface=exports.QUERY_LOG_SURFACE,
        query_hash=query_log.query_digest(request.query),
        decision=query_log.DECISION_DENIED,
        catalogue_version=CATALOGUE_VERSION,
    )

    if not request.user.is_active:
        query_log.record(session, attempt)
        return replace(base, reason="principal_inactive")

    try:
        decision = _decide(session, ctx, request)
    except BiQueryError as exc:
        query_log.record(session, attempt)
        return replace(base, reason=exc.code)
    if decision is None:
        query_log.record(session, attempt)
        return replace(base, reason="authorization_revoked")

    cat = catalogue()
    try:
        run = runner.run_query(
            session,
            cat=cat,
            query=request.query,
            organization_id=job.organization_id,
            bank_id=request.bank.id,
            injected_filters=(),
            row_cap=settings.export_row_cap,
            timeout_ms=settings.export_timeout_ms,
        )
    except BiQueryError as exc:
        query_log.record(session, replace(attempt, member_ids=decision.member_ids))
        return replace(base, reason=exc.code)

    # Fail closed on the COMPILED member set: compilation may add a member the
    # decision did not cover (an injected data-scope filter), and a widened set
    # must not be served under the authority that covered the narrower one.
    compiled_class = policy.classify_ids(cat, run.compiled.member_ids)
    if compiled_class != request.export_class:
        query_log.record(session, replace(attempt, member_ids=decision.member_ids))
        return replace(base, reason="disclosure_class_changed")

    ctx_block = runner.build_context(
        session,
        bank=request.bank,
        query=request.query,
        cat=cat,
        data_scope=decision.data_scope,
        export_class=request.export_class,
        user_label=runner.principal_label(session, job.organization_id, request.user.id),
    )
    table = runner.to_table(run, ctx_block)
    payload = exports.render(request.fmt, table, ctx_block)
    filename = exports.filename_for(
        request.fmt, bank_id=request.bank.id, as_of_label=ctx_block.as_of_label
    )
    media_type = exports.MEDIA_TYPES[request.fmt]
    object_path = _store(
        storage,
        bank=request.bank,
        job=job,
        filename=filename,
        payload=payload,
        media_type=media_type,
    )
    query_log.record(
        session,
        replace(
            attempt,
            decision=query_log.DECISION_ALLOWED,
            member_ids=run.compiled.member_ids,
            row_count=run.row_count,
            duration_ms=run.result.elapsed_ms,
            build_fingerprint=ctx_block.build_fingerprint,
        ),
    )
    return ExportJobResult(
        status=STATUS_SUCCEEDED,
        bank_id=request.bank.id,
        principal_user_id=request.user.id,
        export_class=request.export_class,
        fmt=request.fmt,
        object_path=object_path,
        filename=filename,
        media_type=media_type,
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        row_count=run.row_count,
        truncated=run.truncated,
    )


__all__ = [
    "DOWNLOAD_EXPIRY_SECONDS",
    "ENTITY_TYPE",
    "JOB_TYPE",
    "STATUS_DENIED",
    "STATUS_SUCCEEDED",
    "STORAGE_TIER",
    "WRITTEN_BY",
    "BiExportJobError",
    "ExportJobRequest",
    "ExportJobResult",
    "download_url",
    "enqueue_export",
    "payload_for",
    "read_request",
    "render_job",
]
