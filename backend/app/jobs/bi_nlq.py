"""The ``bi_nlq_translate`` row lifecycle: the request, and the AI-lane handler.

Everything this feature WRITES is here, and that is a boundary rather than a
preference: ``app/services/bi/nlq`` decides and writes nothing (the BI plane's
rule (c) allows it to write ``bi_*`` tables only), so the queue row and the audit
event live in this module — the same split ``bi_commentary``/``services/bi/commentary``
and ``bi_export``/``services/bi/exports`` already make.

**Why there is a job at all.** ``backend/docker-compose.ai.prod.yml`` exists so that
the vendor credentials live in exactly one container: "The main app's .env never
contains any of the keys." The API process therefore cannot call a model, and a
synchronous natural-language route would be a feature that passes every test and
503s in production. So the API enqueues, the ``ai``-lane worker calls the model, and
the reader polls — the ``bi_commentary`` shape, for the identical reason.

**Why the proposal lives in ``job.progress``.** ``bi_export`` set the precedent: a
short-lived, one-reader outcome keyed by its queue row, read back by a status route
that 404s for anybody else. A proposal is exactly that, so it needs no table and
therefore no migration.

**What is frozen at request time.** The payload — the screened question and the
catalogue metadata for the members THIS reader may see — is hashed by the process
holding the reader's authority. The worker re-hashes it and refuses on a mismatch,
and reads no bank data at all. A queued question can therefore never widen to a
figure the reader could not have asked for.

**What runs again at run time**, both copied from ``bi_commentary`` because they were
paid for once already: the AI gates are re-evaluated at ``phase="run"`` with
``requested_at``, so a request that was legal when it was made and is not legal now is
cancelled rather than sent; and a terminal row returns immediately, which is what
makes the handler idempotent under worker reclaim.

**What this handler never does** is execute anything. It writes a PROPOSAL. Running it
requires the reader to confirm it on ``POST /banks/{bank_id}/bi/ask/{job_id}/run``,
which re-authorizes it through ``read_bi._authorize`` like any hand-built query.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import Bank, Job
from app.services import audit, job_queue
from app.services.ai import client as ai_client
from app.services.ai import gates, observability, quota
from app.services.ai.features import AiFeature

JOB_TYPE = "bi_nlq_translate"
FEATURE: AiFeature = "bi_nlq"

#: ``audit_events`` for a question that actually queued a model call. A gated or
#: capped question writes nothing, because nothing left the platform.
EVENT_REQUESTED = "bi.ask.requested"
#: ``audit_events`` for a proposal the reader confirmed and ran. Written by the
#: feature route, which is where the confirmation happens.
EVENT_CONFIRMED = "bi.ask.confirmed"
ENTITY_TYPE = "bi_ask_question"

#: ``job.progress["status"]``. ``proposed`` is the only one that carries a query.
STATUS_PROPOSED = "proposed"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_RATE_LIMITED = "rate_limited"
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_PROPOSED, STATUS_REFUSED, STATUS_FAILED, STATUS_CANCELLED, STATUS_RATE_LIMITED}
)

#: ``progress["reason"]`` when ``BI_ENABLED`` or ``BI_NLQ_ENABLED`` went off after
#: the request. The AI gates cannot see either: they are the BI plane's own
#: switches, and a deployment that switched the surface off must not still be
#: sending its readers' questions to a vendor.
REASON_BI_DISABLED = "bi_disabled"
REASON_NLQ_DISABLED = "bi_nlq_disabled"
REASON_INTEGRITY = "integrity"

#: What a reader is told when the MODEL, rather than the platform, is the reason
#: there is no proposal. None of these names a vendor, a model or a key.
MODEL_FAILURE_MESSAGES: dict[str, str] = {
    "refused": (
        "The assistant would not answer that question. Ask for a figure, a grouping and a period."
    ),
    "truncated": ("The answer to that question did not fit. Ask for fewer figures at a time."),
    "schema_invalid": (
        "The platform could not turn that question into a question it can answer. "
        "Try rephrasing it."
    ),
    "rate_limited": "The assistant is busy. Wait a moment and ask again.",
    "failed": "The assistant could not be reached. Wait a moment and ask again.",
}

#: Gate codes that mean permission was WITHDRAWN rather than something broke.
_WITHDRAWALS: frozenset[str] = frozenset(
    {
        "deployment_disabled",
        "deployment_not_approved",
        "configuration_not_approved",
        "tenant_disabled",
        "feature_disabled",
        "consent_outdated",
        "queue_expired",
        "not_configured",
        REASON_BI_DISABLED,
        REASON_NLQ_DISABLED,
    }
)

#: WHY THE BI SERVICE IS REACHED THROUGH ``importlib`` AND NEVER IMPORTED: a BI job
#: handler is REGISTERED a release before the surface that enqueues it is switched on
#: (D-008), so every module under ``app/jobs`` must import cleanly on a worker where
#: the BI feature is absent. ``tests/services/test_bi_jobs.py`` refuses an
#: ``app.services.bi`` import statement anywhere in an ``app/jobs/bi_*.py``.
_NLQ_MODULE = "app.services.bi.nlq"
_CATALOGUE_MODULE = "app.domain.bi.catalogue"


def load_nlq() -> ModuleType:
    """The NLQ service, imported at call time. The ONE seam tests stub."""

    return importlib.import_module(_NLQ_MODULE)


def _catalogue_module() -> ModuleType:
    return importlib.import_module(_CATALOGUE_MODULE)


def _prompt_version() -> str:
    return str(load_nlq().PROMPT_VERSION)


# ---------------------------------------------------------------------------
# quota: one AI budget per tenant, across every surface
# ---------------------------------------------------------------------------
class _NlqUsage:
    """This surface's contribution to the tenant's ONE daily AI budget.

    Counted off the queue rows, because the queue row IS this feature's record —
    there is no table of its own. The per-user split and the cancelled exclusion
    are applied in Python rather than in SQL: the principal and the outcome live in
    two JSON columns, a JSON predicate is the one thing that does not render the
    same way on SQLite and Postgres, and the row count here is bounded by the daily
    cap itself (``AI_DAILY_REQUESTS_PER_ORG``, 200), so there is nothing to optimise.
    """

    def _rows(self, db: Session, organization_id: str, since: datetime) -> Sequence[Job]:
        return list(
            db.scalars(
                select(Job).where(
                    Job.job_type == JOB_TYPE,
                    Job.organization_id == organization_id,
                    Job.queued_at >= since,
                )
            )
        )

    def requests_since(
        self, db: Session, organization_id: str, since: datetime, *, user_id: UUID | None
    ) -> int:
        total = 0
        for row in self._rows(db, organization_id, since):
            # A request the platform refused to send is not spend.
            if (row.progress or {}).get("status") == STATUS_CANCELLED:
                continue
            if user_id is not None and str((row.payload or {}).get("principal_user_id")) != str(
                user_id
            ):
                continue
            total += 1
        return total

    def output_tokens_since(self, db: Session, organization_id: str, since: datetime) -> int:
        total = 0
        for row in self._rows(db, organization_id, since):
            usage = (row.progress or {}).get("usage")
            if isinstance(usage, dict):
                total += int(usage.get("output_tokens") or 0)
        return total


quota.register_source(_NlqUsage())


# ---------------------------------------------------------------------------
# the request
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AskRequest:
    """The outcome of asking one question, in one object the route can read.

    ``job`` is ``None`` whenever nothing was queued, and ``reason``/``message`` then
    say why in words a reader can act on.
    """

    job: Job | None
    gate: gates.GateDecision
    quota: quota.QuotaDecision
    reason: str | None = None
    message: str = ""

    @property
    def queued(self) -> bool:
        return self.job is not None


def request_translation(  # noqa: PLR0913 - the whole frozen request, spelled out
    db: Session,
    *,
    ctx: TenantContext,
    bank: Bank,
    requested_by: UUID,
    as_of: date,
    built: Any,
    catalogue_version: str,
) -> AskRequest:
    """Gate, meter and queue ONE translation, or explain why there is none.

    ``built`` is the frozen payload the API process produced from the members THIS
    reader may see (``services/bi/nlq/payload.build``). Nothing here re-derives it,
    and nothing here executes anything.
    """

    settings = get_settings()
    decision = gates.evaluate(
        db,
        ctx.organization_id,
        FEATURE,
        phase="enqueue",
        prompt_version=_prompt_version(),
        settings=settings,
    )
    allowance = quota.check(db, ctx.organization_id, requested_by, settings=settings)
    if not decision.allowed:
        observability.log_ai_event(
            observability.EVENT_GATED,
            feature=FEATURE,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            gate_code=decision.code,
        )
        return AskRequest(
            job=None,
            gate=decision,
            quota=allowance,
            reason=decision.code,
            message=decision.message,
        )
    if not allowance.allowed:
        return AskRequest(
            job=None,
            gate=decision,
            quota=allowance,
            reason=allowance.code,
            message=(
                "Your organisation has used its AI allowance for today. It resets at midnight UTC."
            ),
        )

    metadata = load_nlq().model_metadata()
    job = job_queue.enqueue(
        db,
        ctx.organization_id,
        JOB_TYPE,
        bank_id=bank.id,
        payload={
            "organization_id": ctx.organization_id,
            "bank_id": bank.id,
            "principal_user_id": str(requested_by),
            "as_of": as_of.isoformat(),
            "catalogue_version": catalogue_version,
            "prompt_version": _prompt_version(),
            "payload": built.payload,
            "payload_sha256": built.sha256,
            "offered_member_ids": list(built.offered_member_ids),
            "consent_version": settings.ai.consent_version,
            **{key: str(value) for key, value in metadata.items()},
        },
        entity_type=ENTITY_TYPE,
        max_attempts=settings.ai.job_max_attempts,
    )
    audit.record_event(
        db,
        ctx,
        event_type=EVENT_REQUESTED,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        details={
            "bank_id": bank.id,
            "as_of": as_of.isoformat(),
            "catalogue_version": catalogue_version,
            "payload_sha256": built.sha256,
            "offered_members": len(built.offered_member_ids),
            "prompt_version": _prompt_version(),
            "model_requested": str(metadata["model_requested"]),
        },
    )
    observability.log_ai_event(
        observability.EVENT_ENQUEUED,
        feature=FEATURE,
        suggestion_id=str(job.id),
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        fact_sheet_sha256=built.sha256,
        fact_count=len(built.offered_member_ids),
        prompt_version=_prompt_version(),
        model_requested=str(metadata["model_requested"]),
    )
    db.commit()
    db.refresh(job)
    return AskRequest(job=job, gate=decision, quota=allowance)


def find_question(
    db: Session,
    *,
    job_id: UUID,
    organization_id: str,
    bank_id: str,
    principal_user_id: UUID,
) -> Job | None:
    """One question of THIS reader, on THIS institution. ``None`` for anyone else.

    Scoped to the requester as well as the tenant and the institution: a question
    is one reader's, translated over the members THAT reader may see, so serving it
    to a colleague with narrower grants would disclose exactly what their grants
    withhold. The route turns ``None`` into 404 — the existence of another person's
    question is not this caller's business either (the ``bi_export`` rule).
    """

    job = db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.job_type == JOB_TYPE,
            Job.organization_id == organization_id,
            Job.bank_id == bank_id,
        )
    )
    if job is None:
        return None
    if str((job.payload or {}).get("principal_user_id")) != str(principal_user_id):
        return None
    return job


def is_expired(job: Job, *, now: datetime | None = None) -> bool:
    """Whether the run gate will cancel this queued question rather than send it."""

    settings = get_settings()
    queued_at = job.queued_at
    aware = queued_at if queued_at.tzinfo else queued_at.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    return moment - aware > timedelta(seconds=settings.ai.queue_expiry_seconds)


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------
def _finish(job: Job, progress: dict[str, Any]) -> None:
    job.progress = {**(job.progress or {}), **progress}


#: What a reader is told for each way the platform itself withdrew permission
#: between the question and the model call. The AI gates carry their own copy for
#: the codes they own; these two are the BI plane's switches, which the gates
#: cannot see.
_BI_SWITCH_MESSAGES: dict[str, str] = {
    REASON_BI_DISABLED: (
        "Business intelligence was switched off before that question was sent, so "
        "nothing was sent and nothing was read."
    ),
    REASON_NLQ_DISABLED: (
        "Asking questions in words was switched off before that question was sent, so "
        "nothing was sent and nothing was read."
    ),
}


def _cancel(job: Job, reason: str) -> None:
    """Record a withdrawal: permission changed, so the question was not sent."""

    message = _BI_SWITCH_MESSAGES.get(reason) or gates.GATE_MESSAGES.get(
        reason,  # type: ignore[arg-type]
        "That question was not sent, and nothing was read.",
    )
    _finish(job, {"status": STATUS_CANCELLED, "reason": reason, "message": message})
    observability.log_ai_event(
        observability.EVENT_GATED,
        feature=FEATURE,
        suggestion_id=str(job.id),
        organization_id=job.organization_id,
        bank_id=job.bank_id,
        gate_code=reason,
    )


def run_bi_nlq_translate(session: Session, job: Job) -> None:
    """Turn one question into a PROPOSED ``BiQuery``, or into a refusal.

    Idempotent under reclaim, and it executes nothing: the only write is this job's
    own progress record.
    """

    if (job.progress or {}).get("status") in TERMINAL_STATUSES:
        # A reclaimed job must not rewrite a finished proposal, and must not spend
        # a second model call.
        return

    settings = get_settings()
    if not settings.bi.enabled:
        _cancel(job, REASON_BI_DISABLED)
        return
    if not settings.bi.nlq_enabled:
        _cancel(job, REASON_NLQ_DISABLED)
        return

    payload = job.payload or {}
    gate = gates.evaluate(
        session,
        job.organization_id,
        FEATURE,
        phase="run",
        prompt_version=str(payload.get("prompt_version") or ""),
        requested_at=job.queued_at,
        settings=settings,
    )
    if not gate.allowed:
        if gate.code in _WITHDRAWALS:
            _cancel(job, gate.code)
        else:  # pragma: no cover - every current gate code is a withdrawal
            _finish(job, {"status": STATUS_FAILED, "reason": gate.code, "message": gate.message})
        return

    nlq = load_nlq()
    frozen = payload.get("payload")
    if not isinstance(frozen, dict) or nlq.payload_digest(frozen) != payload.get("payload_sha256"):
        # The payload is the request's whole content; a mismatch means the row no
        # longer describes what would be sent, so nothing is sent.
        _finish(
            job,
            {
                "status": STATUS_FAILED,
                "reason": REASON_INTEGRITY,
                "message": "That question could not be sent. Ask it again.",
            },
        )
        return

    result = ai_client.complete_structured(nlq.build_request(frozen), settings)
    _record(session, job, result)


def _record(session: Session, job: Job, result: ai_client.ModelResult[Any]) -> None:
    """One model result as one terminal progress record."""

    _ = session
    nlq = load_nlq()
    payload = job.payload or {}
    usage = result.call_record()
    base: dict[str, Any] = {
        "usage": usage,
        "model_served": result.model_served,
        "fallback_used": result.fallback_used,
        "outcome": result.outcome,
        "failure_code": result.failure_code,
    }
    if result.outcome != "ok" or result.parsed is None:
        status = STATUS_RATE_LIMITED if result.outcome == "rate_limited" else STATUS_FAILED
        _finish(
            job,
            {
                **base,
                "status": status,
                "reason": result.outcome,
                "message": MODEL_FAILURE_MESSAGES.get(
                    result.outcome, MODEL_FAILURE_MESSAGES["failed"]
                ),
            },
        )
        observability.log_ai_event(
            observability.EVENT_COMPLETED,
            feature=FEATURE,
            suggestion_id=str(job.id),
            organization_id=job.organization_id,
            bank_id=job.bank_id,
            outcome=result.outcome,
            failure_code=result.failure_code,
            model_served=result.model_served,
        )
        return

    translation = nlq.translate(
        _catalogue_module().catalogue(),
        result.parsed,
        offered_member_ids=frozenset(str(v) for v in (payload.get("offered_member_ids") or [])),
        as_of=date.fromisoformat(str(payload["as_of"])),
    )
    if translation.query is None:
        _finish(
            job,
            {
                **base,
                "status": STATUS_REFUSED,
                "reason": translation.refusal,
                "unanswerable_reason": translation.unanswerable_reason,
                "message": translation.message,
                "suggested_members": list(translation.suggested_members),
                # Recorded for review and NEVER returned to the caller: a model
                # naming a member this reader may not see is exactly what a reviewer
                # of this surface is looking for.
                "unoffered_members": list(translation.unoffered_members),
            },
        )
        return

    _finish(
        job,
        {
            **base,
            "status": STATUS_PROPOSED,
            "query": translation.query.model_dump(mode="json"),
            "suggested_members": list(translation.suggested_members),
        },
    )


__all__ = [
    "ENTITY_TYPE",
    "EVENT_CONFIRMED",
    "EVENT_REQUESTED",
    "FEATURE",
    "JOB_TYPE",
    "MODEL_FAILURE_MESSAGES",
    "REASON_BI_DISABLED",
    "REASON_INTEGRITY",
    "REASON_NLQ_DISABLED",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_PROPOSED",
    "STATUS_RATE_LIMITED",
    "STATUS_REFUSED",
    "TERMINAL_STATUSES",
    "AskRequest",
    "find_question",
    "is_expired",
    "load_nlq",
    "request_translation",
    "run_bi_nlq_translate",
]
