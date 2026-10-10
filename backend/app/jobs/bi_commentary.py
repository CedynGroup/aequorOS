"""The ``bi_commentary`` row lifecycle: the request, and the AI-lane handler.

This module owns every WRITE the feature makes, and that is a boundary rather
than a preference. ``ai_commentary_drafts`` is an AI-plane table (the egress
record, beside the consent row that gates it), and
``tests/architecture/test_bi_plane_boundary.py`` allows nothing under
``app/services/bi`` to write a table outside the ``bi_*`` marts. So the decisions
live in ``app/services/bi/commentary`` (a pure minimiser, a prompt, a validator, a
deterministic fallback, a renderer) and the writes live here — the same split
``app/jobs/bi_export.py`` makes for its ``audit_events`` write.

Two things shape the request path.

**Nobody is ever told "no commentary".** A gate refusal, an exhausted daily cap
and a sheet with nothing quotable all return the DETERMINISTIC commentary with no
row and no model call. It is composed from the platform's own insight statements,
so it is real commentary rather than an apology, and the caller is handed the gate
or quota decision beside it so the surface can say whose words these are and why.

**What is sent is frozen at request time.** The payload is minimised, hashed and
stored by the process that had the reader's authority; the worker re-hashes it and
refuses on a mismatch, and re-reads no bank data at all. A queued request can
therefore never widen to a figure the reader could not have queried, whatever
changes between the request and the call.

And two that shape the run path, both copied from ``ai_jobs.run_icaap_ai_draft``
because they were paid for once already:

* **the gates run AGAIN**, at ``phase="run"`` and with ``requested_at``, so a
  request that was legal when it was made and is not legal now is ``cancelled``
  rather than sent — and an entry that waited out ``AI_QUEUE_EXPIRY_SECONDS``
  cancels instead of calling the model;
* **a terminal row returns immediately**, which is what makes the handler
  idempotent under worker reclaim: the one crash re-run ``AI_JOB_MAX_ATTEMPTS``
  allows must not spend a second request.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.domain.ai.grounding import GroundingError, GroundingResult
from app.identity.public import Bank
from app.models import Job
from app.models.bi_commentary import (
    AI_COMMENTARY_DRAFT_TERMINAL_STATUSES,
    AiCommentaryDraft,
)
from app.services import audit, job_queue
from app.services.ai import client as ai_client
from app.services.ai import gates, observability, quota
from app.services.ai.features import AiFeature
from app.services.attestation.digests import canonical_json, sha256_hex

if TYPE_CHECKING:  # Annotations only. ``from __future__ import annotations`` keeps
    # them strings at run time, so this block never executes on a worker.
    from app.services.bi.insights.assemble import AssembledInsights

#: WHY THE BI SERVICE IS REACHED THROUGH ``importlib`` AND NEVER IMPORTED.
#:
#: A BI job handler is REGISTERED a release before the flag that enqueues it is
#: switched on (D-008), so every module under ``app/jobs`` must import cleanly on
#: a worker where the BI feature is absent. An import statement — even inside a
#: function — would name the dependency in a way ``sys.modules`` cannot stand in
#: for, so ``tests/services/test_bi_jobs.py`` refuses one anywhere in an
#: ``app/jobs/bi_*.py`` and ``bi_common.load_builder`` exists as the sanctioned
#: form. ``bi_export.py`` is written exactly this way. The accessors below are
#: that rule, and they are what a test binds a stub to.
_COMMENTARY_MODULE = "app.services.bi.commentary"
_PAYLOAD_MODULE = "app.services.bi.commentary.payload"
_PROMPT_MODULE = "app.services.bi.commentary.prompt"
_INSIGHTS_MODULE = "app.services.bi.insights"


def load_commentary() -> ModuleType:
    """The commentary service, imported at call time."""
    return importlib.import_module(_COMMENTARY_MODULE)


def _payload_module() -> ModuleType:
    return importlib.import_module(_PAYLOAD_MODULE)


def _prompt_version() -> str:
    return str(importlib.import_module(_PROMPT_MODULE).PROMPT_VERSION)


def _fact_sheet_hash(sheet: object) -> str:
    return str(importlib.import_module(_INSIGHTS_MODULE).fact_sheet_hash(sheet))


__all__ = [
    "ENTITY_TYPE",
    "EVENT_REQUESTED",
    "FAILURE_BI_DISABLED",
    "FEATURE",
    "JOB_TYPE",
    "CommentaryRequest",
    "current_fact_sheet_hash",
    "fallback_of",
    "is_expired",
    "latest_draft",
    "request_commentary",
    "run_bi_commentary",
]

JOB_TYPE = "bi_commentary"
FEATURE: AiFeature = "bi_commentary"

#: ``audit_events`` for a request that actually queued a model call. A refused or
#: capped request writes nothing, because nothing left the platform.
EVENT_REQUESTED = "bi.commentary.requested"
ENTITY_TYPE = "bi_commentary_draft"

_ACTIVE_STATUSES = ("queued", "running")

#: ``ModelResult.outcome`` -> the row status it becomes. One-to-one and total, so
#: no result can leave a row unfinished.
_OUTCOME_STATUS: dict[str, str] = {
    "ok": "validated",
    "refused": "refused",
    "truncated": "failed",
    "schema_invalid": "failed",
    "rate_limited": "rate_limited",
    "failed": "failed",
}

#: Codes that mean permission was WITHDRAWN rather than something broke. A
#: cancelled request is a governance outcome and reads as one; a failure is an
#: incident, and conflating them hides a pulled kill-switch inside an error rate.
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
        "bi_disabled",
    }
)

#: ``failure_code`` when ``BI_ENABLED`` went off after the request. The AI gates
#: cannot see it: it is the BI plane's own switch, and a deployment that has
#: switched BI off must not still be writing about a bank's figures.
FAILURE_BI_DISABLED = "bi_disabled"


# ---------------------------------------------------------------------------
# quota: one AI budget per tenant, across every surface
# ---------------------------------------------------------------------------
class _CommentaryUsage:
    """BI commentary's contribution to the tenant's ONE daily AI budget."""

    def requests_since(
        self, db: Session, organization_id: str, since: datetime, *, user_id: UUID | None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(AiCommentaryDraft)
            .where(
                AiCommentaryDraft.organization_id == organization_id,
                AiCommentaryDraft.created_at >= since,
                # A request the platform refused to send is not spend.
                AiCommentaryDraft.status != "cancelled",
            )
        )
        if user_id is not None:
            statement = statement.where(AiCommentaryDraft.requested_by == user_id)
        return int(db.scalar(statement) or 0)

    def output_tokens_since(self, db: Session, organization_id: str, since: datetime) -> int:
        total = 0
        for usage in db.scalars(
            select(AiCommentaryDraft.usage).where(
                AiCommentaryDraft.organization_id == organization_id,
                AiCommentaryDraft.created_at >= since,
            )
        ):
            if isinstance(usage, dict):
                total += int(usage.get("output_tokens") or 0)
        return total


quota.register_source(_CommentaryUsage())


# ---------------------------------------------------------------------------
# the request
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CommentaryRequest:
    """The outcome of asking for commentary, in one object the route can read.

    ``draft`` is ``None`` whenever nothing was queued — and that is a normal,
    serveable state, not an error: ``fallback_paragraphs`` is the platform's own
    commentary and ``gate``/``quota``/``reason`` say why it is what is on offer.
    """

    draft: AiCommentaryDraft | None
    created: bool
    gate: gates.GateDecision
    quota: quota.QuotaDecision
    fallback_paragraphs: tuple[str, ...]
    #: ``no_commentable_facts`` when the sheet held nothing quotable; otherwise
    #: the gate or quota code that stopped the request, or ``None``.
    reason: str | None = None

    @property
    def queued(self) -> bool:
        return self.draft is not None


def request_commentary(
    db: Session,
    *,
    ctx: TenantContext,
    bank: Bank,
    assembled: AssembledInsights,
    requested_by: UUID,
) -> CommentaryRequest:
    """Freeze one commentary payload and queue the model call, or explain why not.

    ``assembled`` must be the insights the BI query path built for THIS reader:
    its facts exist only for members ``authorize_query`` allowed, which is how
    commentary inherits the read's authority instead of inventing its own.
    """
    settings = get_settings()
    sheet = assembled.sheet
    insight_set = assembled.insight_set
    fallback = load_commentary().deterministic_commentary(
        sheet=sheet,
        insight_set=insight_set,
        institution_name=bank.name,
        compare_to=assembled.compare_to,
    )

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
        return CommentaryRequest(
            draft=None,
            created=False,
            gate=decision,
            quota=allowance,
            fallback_paragraphs=fallback,
            reason=decision.code,
        )
    if not allowance.allowed:
        return CommentaryRequest(
            draft=None,
            created=False,
            gate=decision,
            quota=allowance,
            fallback_paragraphs=fallback,
            reason=allowance.code,
        )

    existing = _in_flight(db, ctx.organization_id, bank.id, sheet.as_of, requested_by)
    if existing is not None:
        return CommentaryRequest(
            draft=existing,
            created=False,
            gate=decision,
            quota=allowance,
            fallback_paragraphs=tuple(str(text) for text in (existing.fallback_paragraphs or [])),
        )

    try:
        build = load_commentary().build_payload(
            db,
            bank=bank,
            sheet=sheet,
            insight_set=insight_set,
            descriptor_only=gates.descriptor_only(db, ctx.organization_id),
        )
    except load_commentary().NoCommentableFactsError:
        return CommentaryRequest(
            draft=None,
            created=False,
            gate=decision,
            quota=allowance,
            fallback_paragraphs=fallback,
            reason="no_commentable_facts",
        )

    metadata = load_commentary().model_metadata()
    draft = AiCommentaryDraft(
        id=new_uuid7(),
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        as_of=sheet.as_of,
        compare_to=assembled.compare_to,
        catalogue_version=sheet.catalogue_version,
        status="queued",
        requested_by=requested_by,
        payload_mode=build.mode,
        payload=build.payload,
        payload_sha256=build.sha256,
        fact_sheet_hash=insight_set.fact_sheet_hash,
        fact_bindings=_payload_module().bindings_as_dict(build.bindings),
        entity_keys=list(build.entity_map.keys),
        fallback_paragraphs=list(fallback),
        fact_count=build.fact_count,
        prompt_version=_prompt_version(),
        prompt_sha256=load_commentary().prompt_digest(build.mode),
        model_requested=str(metadata["model_requested"]),
        effort=str(metadata["effort"]),
        max_output_tokens=int(metadata["max_output_tokens"]),  # type: ignore[arg-type]
        fallbacks_mode=str(metadata["fallbacks_mode"]),
        consent_version=settings.ai.consent_version,
        deployment_approval_ref=settings.ai.production_approval_ref,
    )
    db.add(draft)
    try:
        db.flush()
    except IntegrityError:
        # The partial unique index is the debounce's race guard: a second
        # concurrent request for the same reader, institution and date loses and
        # reads the first one's commentary.
        db.rollback()
        concurrent = _in_flight(db, ctx.organization_id, bank.id, sheet.as_of, requested_by)
        return CommentaryRequest(
            draft=concurrent,
            created=False,
            gate=decision,
            quota=allowance,
            fallback_paragraphs=fallback,
            reason=None if concurrent is not None else "commentary_already_running",
        )

    job = job_queue.enqueue(
        db,
        ctx.organization_id,
        JOB_TYPE,
        bank_id=bank.id,
        payload={"draft_id": str(draft.id)},
        entity_type=ENTITY_TYPE,
        entity_id=draft.id,
        max_attempts=settings.ai.job_max_attempts,
    )
    draft.job_id = job.id
    audit.record_event(
        db,
        ctx,
        event_type=EVENT_REQUESTED,
        entity_type=ENTITY_TYPE,
        entity_id=draft.id,
        details={
            "bank_id": bank.id,
            "as_of": sheet.as_of.isoformat(),
            "compare_to": assembled.compare_to.isoformat(),
            "payload_mode": build.mode,
            "payload_sha256": build.sha256,
            "fact_sheet_hash": insight_set.fact_sheet_hash,
            "fact_count": build.fact_count,
            "scoped_facts_withheld": build.scoped_facts_withheld,
            "prompt_version": _prompt_version(),
            "model_requested": str(metadata["model_requested"]),
        },
    )
    observability.log_ai_event(
        observability.EVENT_ENQUEUED,
        feature=FEATURE,
        suggestion_id=str(draft.id),
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        fact_sheet_sha256=build.sha256,
        fact_sheet_mode=build.mode,
        fact_count=build.fact_count,
        prompt_version=_prompt_version(),
        model_requested=str(metadata["model_requested"]),
    )
    db.commit()
    db.refresh(draft)
    return CommentaryRequest(
        draft=draft,
        created=True,
        gate=decision,
        quota=allowance,
        fallback_paragraphs=fallback,
    )


def latest_draft(
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    as_of: Any,
    requested_by: UUID,
) -> AiCommentaryDraft | None:
    """This reader's most recent request for this institution and date.

    Scoped to the requester as well as the institution: a commentary request is
    one reader's, built from the members THAT reader could query, so serving it to
    a colleague with narrower grants would disclose exactly what their grants
    withhold.
    """
    return db.scalar(
        select(AiCommentaryDraft)
        .where(
            AiCommentaryDraft.organization_id == organization_id,
            AiCommentaryDraft.bank_id == bank_id,
            AiCommentaryDraft.as_of == as_of,
            AiCommentaryDraft.requested_by == requested_by,
        )
        .order_by(AiCommentaryDraft.created_at.desc())
        .limit(1)
    )


def _in_flight(
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: Any,
    requested_by: UUID,
) -> AiCommentaryDraft | None:
    return db.scalar(
        select(AiCommentaryDraft)
        .where(
            AiCommentaryDraft.organization_id == organization_id,
            AiCommentaryDraft.bank_id == bank_id,
            AiCommentaryDraft.as_of == as_of,
            AiCommentaryDraft.requested_by == requested_by,
            AiCommentaryDraft.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(AiCommentaryDraft.created_at.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------
def run_bi_commentary(session: Session, job: Job) -> None:
    """Write one commentary draft. Idempotent under reclaim."""
    draft_id = (job.payload or {}).get("draft_id")
    if draft_id is None:  # pragma: no cover - the request always sets it
        return
    row = session.scalar(
        select(AiCommentaryDraft).where(
            AiCommentaryDraft.id == UUID(str(draft_id)),
            AiCommentaryDraft.organization_id == job.organization_id,
        )
    )
    # Missing or already terminal: a reclaimed job must not rewrite a finished
    # row and must not send a second request.
    if row is None or row.status in AI_COMMENTARY_DRAFT_TERMINAL_STATUSES:
        return

    settings = get_settings()
    if not settings.bi.enabled:
        _finalize(session, row, status="cancelled", failure_code=FAILURE_BI_DISABLED)
        _log_gated(row, FAILURE_BI_DISABLED)
        return

    gate = gates.evaluate(
        session,
        row.organization_id,
        FEATURE,
        phase="run",
        prompt_version=row.prompt_version,
        requested_at=row.created_at,
        settings=settings,
    )
    if not gate.allowed:
        _finalize(
            session,
            row,
            status="cancelled" if gate.code in _WITHDRAWALS else "failed",
            failure_code=gate.code,
        )
        _log_gated(row, gate.code)
        return

    row.status = "running"
    session.commit()

    # The payload is the request's whole factual content; a mismatch means the row
    # no longer describes what would be sent, so nothing is sent.
    if load_commentary().payload_digest(row.payload) != row.payload_sha256:
        _finalize(session, row, status="failed", failure_code="integrity")
        return

    result = ai_client.complete_structured(
        load_commentary().build_request(row.payload, row.payload_mode), settings
    )
    _record(session, row, result)


def _log_gated(row: AiCommentaryDraft, code: str) -> None:
    observability.log_ai_event(
        observability.EVENT_GATED,
        feature=FEATURE,
        suggestion_id=str(row.id),
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        gate_code=code,
    )


def _record(session: Session, row: AiCommentaryDraft, result: ai_client.ModelResult[Any]) -> None:
    """Turn one model result into one terminal row, and log what happened."""
    usage = result.call_record()
    status = _OUTCOME_STATUS.get(result.outcome, "failed")
    output: dict[str, Any] | None = None
    validation_errors: list[dict[str, Any]] = []

    if result.outcome == "ok" and result.parsed is not None:
        parsed: dict[str, Any] = result.parsed.model_dump()
        output = parsed
        verdict = _validate(session, row, parsed)
        if not verdict.ok:
            # The reader is not told a draft failed and left to wonder: the read
            # path serves the deterministic commentary, and these codes are the
            # evidence for the eval that will improve the prompt.
            status = "rejected_validation"
            validation_errors = load_commentary().error_entries(verdict)

    _finalize(
        session,
        row,
        status=status,
        failure_code=result.failure_code,
        model_requested=result.model_requested,
        model_served=result.model_served,
        fallback_used=result.fallback_used,
        request_id=result.request_id,
        stop_reason=result.stop_reason,
        refusal_category=result.refusal_category,
        latency_ms=result.latency_ms,
        output=output,
        validation_errors=validation_errors,
        usage=usage,
    )
    observability.log_ai_event(
        observability.EVENT_COMPLETED,
        feature=FEATURE,
        suggestion_id=str(row.id),
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        status=status,
        outcome=result.outcome,
        failure_code=result.failure_code,
        refusal_category=result.refusal_category,
        stop_reason=result.stop_reason,
        validation_error_codes=sorted({entry["code"] for entry in validation_errors}),
        model_requested=result.model_requested,
        model_served=result.model_served,
        fallback_used=result.fallback_used,
        request_id=result.request_id,
        latency_ms=result.latency_ms,
        vendor=result.vendor,
        model_source=result.model_source,
        tier_position=result.tier_position,
        degraded_capabilities=sorted(result.degraded),
        tier_attempts=list(result.tier_attempts),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_input_tokens=usage.get("cache_read_input_tokens"),
        inference_geo=usage.get("inference_geo"),
    )
    if result.usage is not None and not result.degraded:
        # Only the vendor whose prompt cache the platform actually instructs can
        # report a cold prefix; on another tier a zero read is the recorded
        # degradation rather than a silent invalidator.
        observability.log_cache_miss_if_cold(
            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
            feature=FEATURE,
            suggestion_id=str(row.id),
            prompt_version=row.prompt_version,
            prompt_sha256=row.prompt_sha256,
        )


def _validate(session: Session, row: AiCommentaryDraft, output: dict[str, Any]) -> GroundingResult:
    bank = session.get(Bank, row.bank_id)
    paragraphs = [
        str(entry.get("text", ""))
        for entry in output.get("paragraphs", [])
        if isinstance(entry, dict)
    ]
    questions = [str(value) for value in output.get("open_questions", [])]
    if bank is None:  # pragma: no cover - the composite FK guarantees the bank
        return GroundingResult(
            ok=False, errors=(GroundingError(code="unknown_fact", where="draft"),)
        )
    return load_commentary().validate_draft(
        session,
        organization_id=row.organization_id,
        bank=bank,
        availability=_availability(row),
        entity_keys=[str(key) for key in (row.entity_keys or [])],
        paragraphs=paragraphs,
        open_questions=questions,
    )


def _availability(row: AiCommentaryDraft) -> dict[str, bool]:
    """Which ``{{F:...}}`` ids the platform holds a figure for.

    Read from the BINDINGS, not from the payload: the bindings are what a
    placeholder will actually resolve against at read time, so validating against
    anything else could admit a citation that cannot be rendered.
    """
    return {str(fid): True for fid in (row.fact_bindings or {})}


def _finalize(  # noqa: PLR0913 - one terminal write carries every result column
    session: Session,
    row: AiCommentaryDraft,
    *,
    status: str,
    failure_code: str | None = None,
    model_requested: str | None = None,
    model_served: str | None = None,
    fallback_used: bool = False,
    request_id: str | None = None,
    stop_reason: str | None = None,
    refusal_category: str | None = None,
    latency_ms: int | None = None,
    output: dict[str, Any] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    """ONE update to the terminal status. After this the row is finished."""
    row.status = status
    row.failure_code = failure_code
    # The model the VENDOR THAT ANSWERED was asked for, never the request-time
    # default: on a failover the row would otherwise describe a call that never
    # happened. ``None`` leaves the request-time value alone, which is right for a
    # row that never reached a vendor.
    if model_requested is not None:
        row.model_requested = model_requested
    row.model_served = model_served
    row.fallback_used = fallback_used
    row.request_id = request_id
    row.stop_reason = stop_reason
    row.refusal_category = refusal_category
    row.latency_ms = latency_ms
    row.output = output
    row.output_sha256 = sha256_hex(canonical_json(output)) if output is not None else None
    row.validation_errors = validation_errors or []
    row.usage = usage
    row.completed_at = utc_now()
    session.commit()


def is_expired(row: AiCommentaryDraft, *, now: datetime | None = None) -> bool:
    """Would the run gate cancel this queued request for age?

    Exposed so a surface can stop polling a request the worker will cancel,
    rather than spinning until it does.
    """
    moment = now or datetime.now(UTC)
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return moment - created > timedelta(seconds=get_settings().ai.queue_expiry_seconds)


def current_fact_sheet_hash(assembled: AssembledInsights) -> str:
    """The digest a freshly assembled sheet would carry, for the staleness check."""
    return _fact_sheet_hash(assembled.sheet)


def fallback_of(row: AiCommentaryDraft) -> Sequence[str]:
    """The deterministic commentary stored on a row, ready to serve."""
    return [str(text) for text in (row.fallback_paragraphs or [])]
