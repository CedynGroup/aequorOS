"""The ``icaap_ai_draft`` handler: the one place a model is actually called.

It runs on the AI worker only (``job_queue.JOB_LANES``), and it is written so
that every path except one ends in a terminal status on the row:

* **The gates run AGAIN here**, at ``phase="run"``. A request that was legal at
  enqueue and is not legal now is ``cancelled`` — not sent, not retried, and
  visibly different from a failure.
* **The fact sheet is re-hashed before it is sent.** What the audit row says was
  sent is what was sent; a mismatch is ``failed``/``integrity`` and no call.
* **A refusal is checked first and its content is never read.** It becomes a
  status with a category, and the user sees "the model declined", not prose.
* **A validation failure stores its codes, not its text.** The output is kept
  for audit and evals but never served.

The only path that raises is an infrastructure failure (the database), which the
worker retries. An API outcome is never retried here: a retry would double-spend
and, on a sealed row, could not be recorded anyway.

**At-least-once, bounded deliberately.** ``AI_JOB_MAX_ATTEMPTS`` is 1, and
``claim_next`` does not consume an attempt, so a worker that DIES mid-handler
gets exactly one re-run — which is the point: a crash before the model call must
recover. The residual exposure is a crash in the narrow window between the model
answering and ``_finalize`` committing, which would send a second request. Two
things keep that window small and rare: the finalising write is a SINGLE update,
and the AI reclaim window (1920 s at the defaults) is longer than the worst-case
call (1800 s), so a live job is never reclaimed under it. A second crash in the
same job exhausts the attempt and the row is left for an operator rather than
retried again.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.ai import grounding as grounding_domain
from app.domain.icaap.frameworks import registry
from app.models import Bank, Job
from app.models.icaap import IcaapCycle
from app.models.icaap_ai import ICAAP_AI_TERMINAL_STATUSES, IcaapAiSuggestion
from app.services.ai import client as ai_client
from app.services.ai import gates, observability
from app.services.ai import grounding as grounding_service
from app.services.ai.client import ModelResult
from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.icaap import ai_drafting, ai_prompt

#: ``ModelResult.outcome`` -> the row status it becomes. One-to-one and total:
#: every outcome the client can produce has a status, so no result can leave a
#: row unfinished.
_OUTCOME_STATUS: dict[str, str] = {
    "ok": "validated",
    "refused": "refused",
    "truncated": "failed",
    "schema_invalid": "failed",
    "rate_limited": "rate_limited",
    "failed": "failed",
}


def run_icaap_ai_draft(session: Session, job: Job) -> None:
    """Draft one ICAAP section. Idempotent under reclaim."""
    suggestion_id = (job.payload or {}).get("suggestion_id")
    if suggestion_id is None:  # pragma: no cover - enqueue always sets it
        return
    row = session.scalar(
        select(IcaapAiSuggestion).where(
            IcaapAiSuggestion.id == UUID(str(suggestion_id)),
            IcaapAiSuggestion.organization_id == job.organization_id,
        )
    )
    # Missing or already terminal: a reclaimed job must not rewrite a sealed row
    # (the database would refuse) and must not send a second request.
    if row is None or row.status in ICAAP_AI_TERMINAL_STATUSES:
        return

    settings = get_settings()
    gate = gates.evaluate(
        session,
        row.organization_id,
        ai_drafting.FEATURE,
        phase="run",
        prompt_version=row.prompt_version,
        requested_at=row.created_at,
        settings=settings,
    )
    if not gate.allowed:
        _finalize(
            session,
            row,
            status="cancelled" if _is_withdrawal(gate.code) else "failed",
            failure_code=gate.code,
        )
        observability.log_ai_event(
            observability.EVENT_GATED,
            feature=ai_drafting.FEATURE,
            suggestion_id=str(row.id),
            organization_id=row.organization_id,
            bank_id=row.bank_id,
            section_key=row.section_key,
            gate_code=gate.code,
        )
        return

    row.status = "running"
    session.commit()

    # The sheet is the request's whole factual content; a mismatch means the row
    # no longer describes what would be sent, so nothing is sent.
    if sha256_hex(canonical_json(row.fact_sheet)) != row.fact_sheet_sha256:
        _finalize(session, row, status="failed", failure_code="integrity")
        return

    try:
        request = _build_request(session, row)
    except LookupError:
        _finalize(session, row, status="failed", failure_code="framework_unavailable")
        return

    result: ModelResult[Any] = ai_client.get_model(settings).generate(request)
    _record(session, row, result)


def _is_withdrawal(code: str) -> bool:
    """Did the platform or the tenant withdraw permission, or did it break?

    A cancelled request is a governance outcome and reads as one to the user;
    a failure is an incident. Conflating them would hide a switched-off
    kill-switch inside an error rate.
    """
    return code in {
        "deployment_disabled",
        "deployment_not_approved",
        "configuration_not_approved",
        "tenant_disabled",
        "feature_disabled",
        "consent_outdated",
        "queue_expired",
        "not_configured",
    }


def _build_request(session: Session, row: IcaapAiSuggestion):
    framework = registry.get(row.framework_code, row.framework_version)
    if not framework.has_section(row.section_key):
        raise LookupError(row.section_key)
    return ai_prompt.build_request(framework, framework.section(row.section_key), row.fact_sheet)


def _record(session: Session, row: IcaapAiSuggestion, result: ModelResult[Any]) -> None:
    usage = result.usage.as_dict() if result.usage is not None else None
    status = _OUTCOME_STATUS.get(result.outcome, "failed")
    output: dict[str, Any] | None = None
    validation_errors: list[dict[str, Any]] = []

    if result.outcome == "ok" and result.parsed is not None:
        parsed_output: dict[str, Any] = result.parsed.model_dump()
        output = parsed_output
        verdict = _validate(session, row, parsed_output)
        if not verdict.ok:
            status = "rejected_validation"
            validation_errors = [
                {
                    "code": error.code,
                    "where": error.where,
                    "span": list(error.span) if error.span else None,
                }
                for error in verdict.errors
            ]

    _finalize(
        session,
        row,
        status=status,
        failure_code=result.failure_code,
        model_served=result.model_served,
        fallback_used=result.fallback_used,
        request_id=result.request_id,
        stop_reason=result.stop_reason,
        refusal_category=result.refusal_category,
        latency_ms=result.latency_ms,
        # Stored for validated AND rejected_validation (audit and evals); the
        # read path serves it only when validated.
        output=output,
        validation_errors=validation_errors,
        usage=usage,
    )
    observability.log_ai_event(
        observability.EVENT_COMPLETED,
        feature=ai_drafting.FEATURE,
        suggestion_id=str(row.id),
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        section_key=row.section_key,
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
        **_usage_fields(usage),
    )
    if usage is not None:
        observability.log_cache_miss_if_cold(
            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
            feature=ai_drafting.FEATURE,
            suggestion_id=str(row.id),
            prompt_version=row.prompt_version,
            prompt_sha256=row.prompt_sha256,
        )


def _usage_fields(usage: dict[str, Any] | None) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "inference_geo": usage.get("inference_geo"),
    }


def _validate(
    session: Session, row: IcaapAiSuggestion, output: dict[str, Any]
) -> grounding_domain.GroundingResult:
    bank = session.get(Bank, row.bank_id)
    cycle = session.get(IcaapCycle, row.cycle_id)
    if bank is None or cycle is None:  # pragma: no cover - FKs guarantee both
        return grounding_domain.GroundingResult(
            ok=False,
            errors=(grounding_domain.GroundingError(code="unknown_fact", where="draft"),),
        )
    framework = registry.get(row.framework_code, row.framework_version)
    section = framework.section(row.section_key)
    availability = {
        str(fact["id"]): bool(fact.get("available"))
        for fact in (row.fact_sheet or {}).get("facts", [])
        if isinstance(fact, dict) and fact.get("id")
    }
    ctx = grounding_service.build_context(
        session,
        organization_id=row.organization_id,
        bank=bank,
        facts=availability,
        entity_keys=[str(key) for key in (row.entity_keys or [])],
        requirement_ids=[item.id for item in section.requirements],
    )
    paragraphs = [
        (
            str(paragraph.get("text", "")),
            [str(value) for value in (paragraph.get("requirement_ids") or [])],
        )
        for paragraph in output.get("paragraphs", [])
        if isinstance(paragraph, dict)
    ]
    questions = [str(value) for value in output.get("open_questions", [])]
    return grounding_domain.validate(
        paragraphs, questions, ctx, grounding_service.limits_from_settings()
    )


def _finalize(  # noqa: PLR0913 - one terminal write carries every result column
    session: Session,
    row: IcaapAiSuggestion,
    *,
    status: str,
    failure_code: str | None = None,
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
    """ONE update to the terminal status. After this the row is sealed."""
    row.status = status
    row.failure_code = failure_code
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


__all__ = ["run_icaap_ai_draft"]
