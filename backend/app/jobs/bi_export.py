"""The ``bi_export`` handler: render one queued governed export, in the ``bi`` lane.

Thin, like its three siblings (``app/jobs/bi_*.py``). Everything that decides
anything — re-authorizing the requester, compiling, running under the export
caps, rendering, storing and writing the one ``bi_query_log`` row — lives in
``app.services.bi.exports.jobs``. What this module owns is the four things a
handler owns:

1. **the run-time kill-switch.** ``BI_ENABLED`` is re-read here, not trusted from
   the enqueue: a job queued before the flag went off must not produce a file
   after it (the ``bi_mart_refresh`` idiom). A switched-off deployment drains
   its backlog as ``skipped``;
2. **the version rule (P1-B5).** A handler OLDER than the payload's
   ``builder_version`` skips: the mart shape it would read is not the one the
   enqueuer described;
3. **the lazy import.** ``app/jobs`` must import cleanly on a worker that has no
   BI service layer, because the queue registration ships one release BEFORE the
   feature is enabled (D-008). The service module is imported at call time, like
   ``bi_common.load_builder``;
4. **the audit event.** ``app/services/bi`` writes ``bi_*`` tables and nothing
   else (the plane guard), so ``audit_events`` — which ``docs/bi.md`` requires
   for every export — is written here, from the outcome the service returns.

A DENIED export is a ``succeeded`` job. The queue's retry cannot change an
authority decision, and three attempts would bury the reason; the outcome says
``denied`` and the owner's status route reads it back as production copy.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.jobs import bi_common
from app.models import Job
from app.services import audit
from app.storage.client import StorageClient

#: The service module this handler dispatches into. A dotted string, not an
#: import, for the reason ``bi_common.BUILDER_MODULE`` is one.
EXPORT_MODULE = "app.services.bi.exports.jobs"

#: ``audit_events.event_type`` for a rendered export, and for a refused one.
#: Both are recorded: a refusal at render time means authority changed between
#: the request and the file, which is exactly what a reviewer looks for.
EVENT_COMPLETED = "bi.export.completed"
EVENT_DENIED = "bi.export.denied"

#: ``audit_events.entity_type``: the queue row, because that is what the owner's
#: status route names and what the artifact is keyed by.
ENTITY_TYPE = "bi_export"

#: ``job.progress["reason"]`` when ``BI_ENABLED`` went off after the enqueue.
SKIP_REASON_DISABLED = "bi_disabled"


def load_exports() -> ModuleType:
    """Import the export service at call time. The ONE seam tests stub."""

    return importlib.import_module(EXPORT_MODULE)


def storage_client() -> StorageClient:
    """The object store, resolved at call time so a test can replace it."""

    from app.storage.factory import get_storage_client  # noqa: PLC0415 - lazy, like ingestion

    return get_storage_client()


def run_bi_export(session: Session, job: Job) -> None:
    """Worker handler: produce one governed export, or record why there is none."""

    settings = get_settings().bi
    if not settings.enabled:
        bi_common.mark_skipped(job, SKIP_REASON_DISABLED)
        return

    module = load_exports()
    if bi_common.skip_if_payload_is_newer(job, module.BUILDER_VERSION):
        return

    outcome = module.render_job(session, job, storage=storage_client())
    ctx = TenantContext(
        organization_id=job.organization_id,
        actor_user_id=outcome.principal_user_id,
    )
    succeeded = outcome.status == module.STATUS_SUCCEEDED
    audit.record_event(
        session,
        ctx,
        event_type=EVENT_COMPLETED if succeeded else EVENT_DENIED,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        details={
            "bank_id": outcome.bank_id,
            "format": outcome.fmt,
            "export_class": outcome.export_class,
            "delivery": "asynchronous",
            **(
                {
                    "object_path": outcome.object_path,
                    "storage_tier": outcome.storage_tier,
                    "checksum_sha256": outcome.checksum_sha256,
                    "size_bytes": outcome.size_bytes,
                    "row_count": outcome.row_count,
                    "truncated": outcome.truncated,
                }
                if succeeded
                else {"reason": outcome.reason}
            ),
        },
    )
    session.commit()
    job.progress = outcome.progress()


__all__ = [
    "ENTITY_TYPE",
    "EVENT_COMPLETED",
    "EVENT_DENIED",
    "EXPORT_MODULE",
    "SKIP_REASON_DISABLED",
    "load_exports",
    "run_bi_export",
    "storage_client",
]
