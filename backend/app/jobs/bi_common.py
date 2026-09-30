"""Shared plumbing for the three ``bi`` lane handlers (``app/jobs/bi_*.py``).

The handlers are deliberately thin: every mart write and every fingerprint
lives in ``app.services.bi.mart_builder`` (the BUILDER), and a
handler's whole job is to (1) honour the run-time kill-switch, (2) apply the
version rule, (3) resolve the tenant and bank the queue row names, (4) call one
builder entrypoint, and (5) classify what came back for the queue.

Two things here are structural rather than convenience:

* **The builder is imported lazily** through :func:`load_builder`. The queue
  registration (``JOB_TYPES``/``HANDLERS``/``JOB_LANES``) ships one release
  BEFORE the builder is enabled — the deploy order D-008 requires — so the
  worker must import cleanly without it, and tests replace this one seam with a
  stub. Nothing else in ``app/jobs`` may import ``app.services.bi`` at module
  level.
* **The version rule (P1-B5).** Every BI payload carries ``builder_version``.
  A handler whose ``BUILDER_VERSION`` is OLDER than the payload's marks the job
  ``succeeded`` with ``progress={"status": "skipped", "reason": "version"}``
  and does nothing, because a stale worker building a mart the newer enqueuer
  described would write the wrong shape under the newer fingerprint. A newer
  handler runs an older payload: its output supersedes by construction.

Tenant binding is the worker's, not the handler's: ``worker.run_once`` opens
the handler session with ``session.info["organization_id"]`` set to the job's
organization, so the RLS GUC is already in place (the ``reporting_deadline_scan``
idiom). The helpers here only CHECK that the payload agrees with that binding —
a payload must never point a tenant-bound session at another tenant.
"""

from __future__ import annotations

import importlib
from datetime import date
from types import ModuleType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bank, Job

#: The module the handlers dispatch into. A dotted string, not an import, so
#: the worker boots without it (see the module docstring).
BUILDER_MODULE = "app.services.bi.mart_builder"

#: ``job.progress["reason"]`` values a BI handler writes when it does no work.
#: A skipped job is ``succeeded`` — "skipped" is a progress fact, never a queue
#: status (the ``pipeline.run_refresh`` idiom; no ``skipped`` status exists).
SKIP_REASON_VERSION = "version"
SKIP_REASON_DISABLED = "bi_mart_enqueue_disabled"
SKIP_REASON_SCHEDULER_DISABLED = "bi_scheduler_disabled"


class BiJobError(Exception):
    """A BI job could not run: a malformed payload or a bank the tenant lacks.

    Structural, in the ``pipeline.PipelineError`` sense — retrying does not
    help, but the queue's bounded retry is the only failure channel a handler
    has, so it is raised and the job lands ``failed`` after its attempts.
    """


class TransientBiBuildError(BiJobError):
    """The builder raised: the worker's bounded backoff should retry this hop.

    The BI analogue of ``pipeline.TransientLiveRefreshError``: it classifies a
    builder failure for the queue history and keeps the structural errors above
    distinguishable from a database hiccup. It carries no live-plane retry state
    because the builder owns ``bi_mart_builds``, which is where a failed build
    is recorded.
    """

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        detail = str(cause) or type(cause).__name__
        super().__init__(f"Transient BI build failure in {stage}: {detail}")


def load_builder() -> ModuleType:
    """Import the mart builder at call time. The ONE seam tests stub."""
    return importlib.import_module(BUILDER_MODULE)


#: The BI service modules a handler may reach, named so a handler cannot import
#: an arbitrary one by string. `app/jobs` must import cleanly on a worker with no
#: BI feature enabled, because a handler is registered a release before the flag
#: that enqueues it (D-008), and a guard refuses an `app.services.bi` import
#: statement anywhere in `app/jobs/bi_*.py` — including inside a function, since
#: `sys.modules` cannot stand in for a name bound by an import statement.
LOADABLE_MODULES: frozenset[str] = frozenset(
    {
        BUILDER_MODULE,
        "app.services.bi.alerts",
        "app.services.bi.subscriptions",
    }
)


def load_module(dotted: str) -> ModuleType:
    """Import one NAMED BI service module at call time.

    Refuses a name that is not in :data:`LOADABLE_MODULES`, so this cannot become
    a general back door into the BI plane from the job layer.
    """
    if dotted not in LOADABLE_MODULES:
        raise BiJobError(f"{dotted} is not a loadable BI service module.")
    return importlib.import_module(dotted)


def payload_builder_version(job: Job) -> int:
    """The ``builder_version`` the enqueuer stamped on ``job``. Required."""
    raw = (job.payload or {}).get("builder_version")
    if raw is None:
        raise BiJobError("Job payload is missing builder_version.")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BiJobError(f"Job payload builder_version {raw!r} is not an integer.") from exc


def skip_if_payload_is_newer(job: Job, handler_version: int) -> bool:
    """Apply the version rule; ``True`` means the caller must return at once.

    Sets the skip progress on the job so ``job_queue.complete`` records it. The
    payload version is read BEFORE the comparison so a malformed payload fails
    as a payload error, not as a version mismatch.
    """
    payload_version = payload_builder_version(job)
    if payload_version <= handler_version:
        return False
    job.progress = {
        "status": "skipped",
        "reason": SKIP_REASON_VERSION,
        "payload_version": payload_version,
        "handler_version": handler_version,
    }
    return True


def mark_skipped(job: Job, reason: str, **detail: Any) -> None:
    """Record a run-gate skip (kill-switch honoured at run time)."""
    job.progress = {"status": "skipped", "reason": reason, **detail}


def resolve_bank(session: Session, job: Job) -> Bank:
    """The bank this job names, proven to belong to the job's organization.

    ``job.bank_id`` is the queue column every enqueue site sets; the payload's
    ``bank_id`` and ``organization_id`` are the contract's copies and must agree
    with the row, so a hand-edited or replayed payload cannot steer a
    tenant-bound session at a sibling bank or tenant.
    """
    payload = job.payload or {}
    payload_org = payload.get("organization_id")
    if payload_org is not None and str(payload_org) != job.organization_id:
        raise BiJobError(
            f"Job payload organization_id {payload_org!r} does not match the job's "
            f"organization {job.organization_id!r}."
        )
    bank_id = job.bank_id or payload.get("bank_id")
    if bank_id is None:
        raise BiJobError("Job has no bank_id.")
    payload_bank = payload.get("bank_id")
    if payload_bank is not None and str(payload_bank) != str(bank_id):
        raise BiJobError(
            f"Job payload bank_id {payload_bank!r} does not match the job's bank {bank_id!r}."
        )
    bank = session.scalar(
        select(Bank).where(Bank.id == str(bank_id), Bank.organization_id == job.organization_id)
    )
    if bank is None:
        raise BiJobError(f"Bank {bank_id} not found for organization.")
    return bank


def payload_date(job: Job, key: str) -> date:
    """An ISO date the payload must carry under ``key``."""
    raw = (job.payload or {}).get(key)
    if not raw:
        raise BiJobError(f"Job payload is missing {key}.")
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise BiJobError(f"Job payload {key} {raw!r} is not an ISO date.") from exc


__all__ = [
    "BUILDER_MODULE",
    "SKIP_REASON_DISABLED",
    "SKIP_REASON_SCHEDULER_DISABLED",
    "SKIP_REASON_VERSION",
    "BiJobError",
    "TransientBiBuildError",
    "load_builder",
    "mark_skipped",
    "payload_builder_version",
    "payload_date",
    "resolve_bank",
    "skip_if_payload_is_newer",
]
