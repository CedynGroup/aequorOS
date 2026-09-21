"""The whole ICAAP drafting path, with a canned model. No network, ever.

The cases that matter most are the ones that produce NOTHING:

* a refusal shows a status and no draft;
* a validation failure shows a status and no draft;
* a kill-switch flipped after enqueue cancels the request at run.

And the one that produces something: accepting inserts ``factRef`` nodes bound
to the blocks the fact sheet was frozen from, through P1's ordinary save path,
carrying the AI marker that becomes the badge.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.ai import placeholders
from app.models import Job
from app.models.ai import AiCommentarySettings
from app.models.icaap import IcaapCycle
from app.models.icaap_ai import IcaapAiSuggestion
from app.schemas.icaap import IcaapCycleRead, IcaapDataBlockCreate
from app.schemas.icaap_ai import (
    DraftParagraph,
    IcaapAiDraftAccept,
    IcaapAiDraftReject,
    SectionDraft,
)
from app.services import job_queue
from app.services.ai import client as ai_client
from app.services.icaap import ai_drafting, ai_jobs, blocks, sections
from tests.api.helpers import ORG_1, USER_1

SECTION = "executive_summary"


@pytest.fixture(autouse=True)
def ai_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def tenant_consented(canonical_book: Session) -> AiCommentarySettings:
    row = AiCommentarySettings(
        organization_id=ORG_1,
        enabled=True,
        enabled_features=["icaap_drafting"],
        descriptor_only=False,
        consent_version=get_settings().ai.consent_version,
        consented_by=USER_1,
        consented_at=utc_now(),
        updated_by=USER_1,
    )
    canonical_book.add(row)
    canonical_book.commit()
    return row


@pytest.fixture
def capital_run(canonical_book: Session, access: IcaapAccess) -> None:
    """A sealed baseline capital run for the cycle's own year end."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import BankReportingPeriod  # noqa: PLC0415
    from app.schemas.regulatory_liquidity import RegulatoryRunCreate  # noqa: PLC0415
    from app.services import regulatory_capital  # noqa: PLC0415
    from tests.services.icaap.conftest import AS_OF  # noqa: PLC0415

    period = canonical_book.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None
    regulatory_capital.create_capital_run(
        canonical_book,
        access.ctx,
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    canonical_book.commit()


@pytest.fixture
def bound_cycle(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    capital_run: None,
) -> IcaapCycleRead:
    """A cycle with the capital block linked to a sealed run, so a draft has grounds."""
    _ = capital_run
    read = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="capital_position")
    )
    assert read.status == "fresh", "the fact sheet only draws on FRESH or PINNED blocks"
    return cycle


def _result(draft: SectionDraft | None, **kwargs: Any) -> ai_client.ModelResult[SectionDraft]:
    return ai_client.ModelResult(
        outcome=kwargs.pop("outcome", "ok"),
        model_requested=get_settings().ai.model,
        model_served=kwargs.pop("model_served", get_settings().ai.model),
        parsed=draft,
        usage=ai_client.UsageRecord(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=900
        ),
        **kwargs,
    )


def _run(session: Session, suggestion_id: Any) -> IcaapAiSuggestion:
    job = Job(
        organization_id=ORG_1,
        job_type="icaap_ai_draft",
        status="running",
        payload={"suggestion_id": str(suggestion_id)},
        attempts=1,
        max_attempts=1,
    )
    session.add(job)
    session.flush()
    ai_jobs.run_icaap_ai_draft(session, job)
    row = session.get(IcaapAiSuggestion, suggestion_id)
    assert row is not None
    session.refresh(row)
    return row


def _grounded_draft() -> SectionDraft:
    return SectionDraft(
        paragraphs=[
            DraftParagraph(
                text=(
                    "{{E:bank}} held a total capital ratio of "
                    "{{F:capital_position.car_pct}} at {{E:as_of}}."
                ),
                requirement_ids=[],
            )
        ],
        open_questions=["Please confirm the Board approval date for the capital plan."],
    )


# --- enqueue ----------------------------------------------------------------


def test_enqueue_freezes_a_sheet_and_queues_an_ai_lane_job(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    read, created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    assert created
    assert read.status == "queued"
    assert read.draft is None

    row = canonical_book.get(IcaapAiSuggestion, read.id)
    assert row is not None
    assert row.fact_sheet["schema"] == "icaap-ai-fact-sheet-v1"
    assert row.consent_version == get_settings().ai.consent_version
    assert row.fact_bindings

    job = canonical_book.get(Job, row.job_id)
    assert job is not None
    assert job_queue.lane_of(job.job_type) == "ai"


def test_the_frozen_sheet_carries_no_tenant_identity(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The identifiability rule, asserted against the tenant's OWN registers."""
    import json  # noqa: PLC0415

    from app.services.ai import pseudonymise  # noqa: PLC0415

    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    row = canonical_book.get(IcaapAiSuggestion, read.id)
    assert row is not None
    serialised = json.dumps(row.fact_sheet).casefold()

    for term in pseudonymise.tenant_deny_terms(canonical_book, ORG_1, access.bank):
        assert term.casefold() not in serialised, f"the sheet leaks {term!r}"
    for term in pseudonymise.jurisdiction_deny_terms(canonical_book):
        assert term.casefold() not in serialised, f"the sheet leaks {term!r}"
    # No identifiers either: block ids stay in fact_bindings, which is not sent.
    assert str(bound_cycle.id) not in serialised
    assert access.bank.id.casefold() not in serialised


def test_amounts_and_dates_are_withheld_even_in_standard_mode(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    row = canonical_book.get(IcaapAiSuggestion, read.id)
    assert row is not None
    assert row.fact_sheet["mode"] == "standard"
    for fact in row.fact_sheet["facts"]:
        if fact["kind"] in {"amount", "date"} and fact["available"]:
            assert fact.get("value") is None
            assert fact["value_withheld"] is True


def test_descriptor_only_mode_strips_every_value(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    tenant_consented.descriptor_only = True
    canonical_book.commit()
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    row = canonical_book.get(IcaapAiSuggestion, read.id)
    assert row is not None
    assert row.fact_sheet["mode"] == "descriptor_only"
    assert all("value" not in fact for fact in row.fact_sheet["facts"])
    assert any(fact["descriptors"] for fact in row.fact_sheet["facts"])


def test_a_section_with_no_linked_figures_is_refused(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """A grounded draft needs grounds."""
    _ = tenant_consented
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.enqueue(canonical_book, access, cycle.id, SECTION)
    assert excinfo.value.detail["error_code"] == "no_usable_facts"  # type: ignore[index]


def test_a_second_request_is_debounced_into_the_first(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    first, created_first = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    second, created_second = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    assert created_first and not created_second
    assert first.id == second.id


def test_enqueue_is_refused_when_the_tenant_has_not_switched_it_on(
    canonical_book: Session, access: IcaapAccess, bound_cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error_code"] == "tenant_disabled"  # type: ignore[index]


def test_enqueue_is_refused_when_the_kill_switch_is_off(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = tenant_consented
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    assert excinfo.value.detail["error_code"] == "deployment_disabled"  # type: ignore[index]


# --- run --------------------------------------------------------------------


def test_the_happy_path_validates_and_serves_a_preview(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    with ai_client.use_model(ai_client.RecordedModel([_result(_grounded_draft())])):
        row = _run(canonical_book, read.id)
    assert row.status == "validated"
    assert row.output is not None
    assert row.usage is not None
    assert row.usage["output_tokens"] == 200

    served = ai_drafting.get_draft(canonical_book, access, bound_cycle.id, SECTION, read.id)
    assert served.draft is not None
    segments = served.draft.paragraphs[0].segments
    assert any(segment.kind == "fact" and segment.display for segment in segments)
    assert served.draft.open_questions


def test_a_refusal_shows_a_status_and_no_draft(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The binding constraint: never fall back to showing ungrounded text."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    refusal = _result(
        None, outcome="refused", stop_reason="refusal", refusal_category="cyber", model_served=None
    )
    with ai_client.use_model(ai_client.RecordedModel([refusal])):
        row = _run(canonical_book, read.id)
    assert row.status == "refused"
    assert row.output is None
    assert row.refusal_category == "cyber"

    served = ai_drafting.get_draft(canonical_book, access, bound_cycle.id, SECTION, read.id)
    assert served.draft is None
    assert served.status == "refused"


def test_a_validation_failure_stores_codes_and_serves_nothing(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """An invented number is kept for audit and never shown to a person."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    ungrounded = SectionDraft(
        paragraphs=[DraftParagraph(text="The ratio was 15.4% at year end.", requirement_ids=[])],
        open_questions=[],
    )
    with ai_client.use_model(ai_client.RecordedModel([_result(ungrounded)])):
        row = _run(canonical_book, read.id)

    assert row.status == "rejected_validation"
    codes = {entry["code"] for entry in row.validation_errors}
    assert {"digit", "percent"} <= codes
    # Kept for audit and the eval harness...
    assert row.output is not None
    # ...and never served.
    served = ai_drafting.get_draft(canonical_book, access, bound_cycle.id, SECTION, read.id)
    assert served.draft is None
    assert set(served.validation_error_codes) == codes


def test_a_kill_switch_flipped_after_enqueue_cancels_the_queued_request(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The queued request must not be the one call that still goes out."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()

    model = ai_client.RecordedModel([_result(_grounded_draft())])
    with ai_client.use_model(model):
        row = _run(canonical_book, read.id)
    assert row.status == "cancelled"
    assert row.failure_code == "deployment_disabled"
    assert model.requests == [], "the model must not have been called"


def test_a_tenant_toggle_flipped_after_enqueue_cancels_the_queued_request(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    tenant_consented.enabled = False
    canonical_book.commit()

    model = ai_client.RecordedModel([_result(_grounded_draft())])
    with ai_client.use_model(model):
        row = _run(canonical_book, read.id)
    assert row.status == "cancelled"
    assert row.failure_code == "tenant_disabled"
    assert model.requests == []


def test_a_tampered_fact_sheet_is_never_sent(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The row must describe exactly what was sent, or nothing is sent."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    row = canonical_book.get(IcaapAiSuggestion, read.id)
    assert row is not None
    sheet = dict(row.fact_sheet)
    sheet["facts"] = []
    row.fact_sheet = sheet
    canonical_book.commit()

    model = ai_client.RecordedModel([_result(_grounded_draft())])
    with ai_client.use_model(model):
        finished = _run(canonical_book, read.id)
    assert finished.status == "failed"
    assert finished.failure_code == "integrity"
    assert model.requests == []


@pytest.mark.parametrize(
    ("outcome", "expected_status", "failure_code"),
    [
        ("rate_limited", "rate_limited", "rate_limited"),
        ("failed", "failed", "timeout"),
        ("truncated", "failed", "max_tokens"),
        ("schema_invalid", "failed", "parse_failed"),
    ],
)
def test_every_api_outcome_reaches_a_terminal_status_without_output(  # noqa: PLR0913 - the parametrised triple plus four fixtures
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
    outcome: str,
    expected_status: str,
    failure_code: str,
) -> None:
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    result = _result(None, outcome=outcome, failure_code=failure_code, model_served=None)
    with ai_client.use_model(ai_client.RecordedModel([result])):
        row = _run(canonical_book, read.id)
    assert row.status == expected_status
    assert row.output is None
    assert row.completed_at is not None


def test_a_reclaimed_job_never_sends_a_second_request(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """Idempotent under reclaim: a terminal row is left alone."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    with ai_client.use_model(ai_client.RecordedModel([_result(_grounded_draft())])):
        _run(canonical_book, read.id)

    second = ai_client.RecordedModel([_result(_grounded_draft())])
    with ai_client.use_model(second):
        row = _run(canonical_book, read.id)
    assert second.requests == []
    assert row.status == "validated"


def test_the_prompt_sends_the_fact_sheet_last_and_the_static_block_first(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    model = ai_client.RecordedModel([_result(_grounded_draft())])
    with ai_client.use_model(model):
        _run(canonical_book, read.id)

    (request,) = model.requests
    assert len(request.system) == 2
    assert request.system[0].text.startswith("You write a first draft")
    assert all(block.cache for block in request.system)
    assert "icaap-ai-fact-sheet-v1" in request.user_content
    # The section addendum carries framework text only — no tenant data.
    assert access.bank.name not in request.system[1].text


# --- accept / reject --------------------------------------------------------


def _validated(session: Session, access: IcaapAccess, cycle: IcaapCycleRead) -> IcaapAiSuggestion:
    read, _created = ai_drafting.enqueue(session, access, cycle.id, SECTION)
    with ai_client.use_model(ai_client.RecordedModel([_result(_grounded_draft())])):
        return _run(session, read.id)


def test_accepting_inserts_bound_fact_references_and_the_ai_marker(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    before = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)

    saved = ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=before.working_rev, paragraph_indexes=[0]),
    )
    assert saved.working_rev == before.working_rev + 1

    paragraph = saved.working_doc["content"][-1]
    assert paragraph["attrs"]["aiSuggestionId"] == str(row.id)
    refs = [child for child in paragraph["content"] if child["type"] == "factRef"]
    assert refs, "the figure must be a bound reference, not text"
    assert refs[0]["attrs"]["suggestionId"] == str(row.id)
    assert refs[0]["attrs"]["factKey"] == "car_pct"
    # The entity placeholder was resolved server-side into prose.
    text = "".join(child.get("text", "") for child in paragraph["content"])
    assert access.bank.name in text
    assert "{{" not in text


def test_accepting_records_an_unalterable_decision(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    before = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=before.working_rev, paragraph_indexes=[0]),
    )
    served = ai_drafting.get_draft(canonical_book, access, bound_cycle.id, SECTION, row.id)
    assert served.decision is not None
    assert served.decision.decision == "accepted"
    assert served.decision.paragraph_indexes == [0]


def test_a_draft_can_only_be_decided_once(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
    )
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=current.working_rev + 1, paragraph_indexes=[0]),
        )
    assert excinfo.value.detail["error_code"] == "ai_draft_already_decided"  # type: ignore[index]


def test_a_non_validated_draft_can_never_be_inserted(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """There is no path from a refused draft into a document."""
    _ = tenant_consented
    read, _created = ai_drafting.enqueue(canonical_book, access, bound_cycle.id, SECTION)
    refusal = _result(None, outcome="refused", stop_reason="refusal", model_served=None)
    with ai_client.use_model(ai_client.RecordedModel([refusal])):
        row = _run(canonical_book, read.id)
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=0, paragraph_indexes=[0]),
        )
    assert excinfo.value.detail["error_code"] == "ai_draft_not_ready"  # type: ignore[index]


def test_an_out_of_range_paragraph_selection_is_refused(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[7]),
        )
    assert excinfo.value.detail["error_code"] == "invalid_paragraph_selection"  # type: ignore[index]


def test_a_stale_draft_needs_an_explicit_acknowledgement(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """Figures moved after the draft was written; the user must say so."""
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    # Rebind the block the way a refresh does: a NEW binding with the next seq.
    # The draft still points at the old one, which is exactly the situation the
    # reviewer has to be told about.
    from app.models.icaap import IcaapBlockBinding  # noqa: PLC0415

    recorded = next(iter(row.fact_bindings.values()))
    cycle_row = canonical_book.get(IcaapCycle, bound_cycle.id)
    assert cycle_row is not None
    current = next(
        binding
        for binding in blocks.current_bindings(canonical_book, access, cycle_row).values()
        if str(binding.block_id) == recorded["block_id"]
    )
    canonical_book.add(
        IcaapBlockBinding(
            organization_id=current.organization_id,
            bank_id=current.bank_id,
            cycle_id=current.cycle_id,
            block_id=current.block_id,
            seq=current.seq + 1,
            resolver=current.resolver,
            resolver_version=current.resolver_version,
            source_kind=current.source_kind,
            source_ref=dict(current.source_ref),
            source_key=current.source_key,
            source_as_of=current.source_as_of,
            source_run_ids=list(current.source_run_ids or []),
            payload=dict(current.payload),
            facts=dict(current.facts),
            payload_sha256=current.payload_sha256,
            bound_by=current.bound_by,
            reason="A later capital run superseded the figures.",
        )
    )
    canonical_book.commit()

    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
        )
    assert excinfo.value.detail["error_code"] == "ai_draft_stale"  # type: ignore[index]

    saved = ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(
            base_rev=current.working_rev, paragraph_indexes=[0], acknowledge_stale=True
        ),
    )
    assert saved.working_rev == current.working_rev + 1
    served = ai_drafting.get_draft(canonical_book, access, bound_cycle.id, SECTION, row.id)
    assert served.decision is not None
    assert served.decision.acknowledged_stale is True


def test_a_concurrent_edit_still_wins_its_revision_conflict(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """AI text goes through P1's ordinary save path, not a privileged one."""
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=999, paragraph_indexes=[0]),
        )
    assert excinfo.value.detail["error_code"] == "section_rev_conflict"  # type: ignore[index]


def test_rejecting_records_a_decision_and_inserts_nothing(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    before = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    served = ai_drafting.reject(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftReject(reason="Too generic for this section."),
    )
    assert served.decision is not None
    assert served.decision.decision == "rejected"
    after = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    assert after.working_rev == before.working_rev


def test_accepting_is_refused_once_the_tenant_switches_the_feature_off(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The text exists, but consent to the process that produced it is withdrawn."""
    row = _validated(canonical_book, access, bound_cycle)
    tenant_consented.enabled = False
    canonical_book.commit()
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    with pytest.raises(HTTPException) as excinfo:
        ai_drafting.accept(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            row.id,
            IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
        )
    assert excinfo.value.status_code == 403


def test_open_questions_are_shown_but_never_inserted(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    saved = ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
    )
    from app.domain.icaap.prosemirror import plain_text  # noqa: PLC0415

    assert "Board approval date" not in plain_text(saved.working_doc)


def test_the_grammar_round_trips_through_the_accepted_document(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """No placeholder survives into the document in either form."""
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    saved = ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
    )
    import json  # noqa: PLC0415

    serialised = json.dumps(saved.working_doc)
    assert not placeholders.extract(serialised)
    assert "{{" not in serialised


# --- provenance and the badge -----------------------------------------------


def test_committing_records_which_paragraphs_were_ai_assisted(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The filed document states the count, so the Board signs a text that says so."""
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    saved = ai_drafting.accept(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        row.id,
        IcaapAiDraftAccept(base_rev=current.working_rev, paragraph_indexes=[0]),
    )
    from app.schemas.icaap import IcaapSectionCommit  # noqa: PLC0415

    version = sections.commit_version(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        IcaapSectionCommit(base_rev=saved.working_rev, note="First pass."),
    )
    assert version.ai_provenance is not None
    assert version.ai_provenance["schema"] == "icaap-ai-provenance-v1"
    assert version.ai_provenance["ai_paragraph_count"] == 1
    assert version.ai_provenance["suggestion_ids"] == [str(row.id)]
    assert version.ai_provenance["prompt_versions"] == ["icaap-draft-v1"]


def test_a_wholly_human_section_records_no_provenance(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """Nothing rather than a zero: the absence is the honest record."""
    _ = tenant_consented
    from app.schemas.icaap import IcaapSectionCommit, IcaapSectionWorkingSave  # noqa: PLC0415

    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    saved = sections.save_working(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        IcaapSectionWorkingSave(
            base_rev=current.working_rev,
            doc={
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "My own words."}]}
                ],
            },
        ),
    )
    version = sections.commit_version(
        canonical_book,
        access,
        bound_cycle.id,
        SECTION,
        IcaapSectionCommit(base_rev=saved.working_rev, note="Mine."),
    )
    assert version.ai_provenance is None


def test_a_forged_ai_marker_is_refused_at_save(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """The badge must name a draft somebody accepted, or it is not a badge."""
    _ = tenant_consented
    from app.schemas.icaap import IcaapSectionWorkingSave  # noqa: PLC0415

    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    with pytest.raises(HTTPException) as excinfo:
        sections.save_working(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            IcaapSectionWorkingSave(
                base_rev=current.working_rev,
                doc={
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "attrs": {"aiSuggestionId": "0199a3c0-0000-7000-8000-00000000dead"},
                            "content": [{"type": "text", "text": "Not actually AI-drafted."}],
                        }
                    ],
                },
            ),
        )
    assert excinfo.value.detail["error_code"] == "unknown_ai_suggestion"  # type: ignore[index]


def test_an_undecided_suggestion_cannot_be_claimed_as_a_badge(
    canonical_book: Session,
    access: IcaapAccess,
    bound_cycle: IcaapCycleRead,
    tenant_consented: AiCommentarySettings,
) -> None:
    """A real suggestion nobody accepted is as forged as an invented id."""
    _ = tenant_consented
    row = _validated(canonical_book, access, bound_cycle)
    from app.schemas.icaap import IcaapSectionWorkingSave  # noqa: PLC0415

    current = sections.get_section(canonical_book, access, bound_cycle.id, SECTION)
    with pytest.raises(HTTPException) as excinfo:
        sections.save_working(
            canonical_book,
            access,
            bound_cycle.id,
            SECTION,
            IcaapSectionWorkingSave(
                base_rev=current.working_rev,
                doc={
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "attrs": {"aiSuggestionId": str(row.id)},
                            "content": [{"type": "text", "text": "Claimed without accepting."}],
                        }
                    ],
                },
            ),
        )
    assert excinfo.value.detail["error_code"] == "unknown_ai_suggestion"  # type: ignore[index]
