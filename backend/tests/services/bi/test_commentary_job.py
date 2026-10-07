"""The whole commentary path with a canned model. No network, ever.

The cases that matter most are the ones that produce no model prose, because each
of them has to produce COMMENTARY anyway:

* the tenant has not switched the feature on — no row, no call, platform prose;
* the daily cap is spent — no row, no call, platform prose;
* the kill-switch was pulled after the request — the row is ``cancelled`` and the
  reader still gets platform prose;
* the queue entry waited out its expiry — ``cancelled``, and nothing was sent;
* the payload was tampered with — ``failed``, and nothing was sent;
* the model refused, or wrote prose that failed grounding — the row records the
  codes and the reader gets platform prose.

And the one that produces something: a grounded draft renders with the PLATFORM's
own figures and names, resolved server-side, never the model's.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.jobs import bi_commentary
from app.models import Bank, Job
from app.models.ai import AiCommentarySettings
from app.models.bi_commentary import AiCommentaryDraft
from app.services import job_queue
from app.services.ai import client as ai_client
from app.services.bi import commentary
from app.services.bi.commentary import CommentaryDraft, CommentaryParagraph, build_view
from app.services.bi.insights import facts, rules
from app.services.bi.insights.assemble import AssembledInsights
from tests.support.helpers import ORG_1, USER_1

AS_OF = date(2026, 6, 30)
PRIOR = date(2026, 5, 31)
BANK_ID = "BK-COMMJOB1"
BANK_NAME = "Commentary Job Bank"

RATIO = "engine.car_pct.crd.official"


@pytest.fixture(autouse=True)
def surfaces_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Both deployment switches on, in an undeployed environment."""
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    row = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name=BANK_NAME,
        short_name="CommJob",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def consented(db_session: Session) -> AiCommentarySettings:
    """A tenant that has switched BI commentary on, and opted OUT of values."""
    row = AiCommentarySettings(
        organization_id=ORG_1,
        enabled=True,
        enabled_features=["bi_commentary"],
        descriptor_only=True,
        consent_version=get_settings().ai.consent_version,
        consented_by=USER_1,
        consented_at=utc_now(),
        updated_by=USER_1,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _ctx() -> TenantContext:
    return TenantContext(
        organization_id=ORG_1, actor_user_id=USER_1, roles=("viewer",), authorization_version=1
    )


def _movement(
    prior: str | None = "12.10",
    current: str | None = "13.40",
    *,
    missing_reason: facts.MissingReason | None = None,
) -> facts.MovementFact:
    from app.domain.bi.catalogue import catalogue  # noqa: PLC0415 - fixture-local

    return facts.movement_fact(
        catalogue().measure(RATIO),
        as_of=AS_OF,
        prior_as_of=PRIOR,
        provenance=facts.FactProvenance(
            fact_id=uuid4(), derived_at=datetime.now(tz=UTC), build_id=uuid4()
        ),
        prior=None if prior is None else Decimal(prior),
        current=None if current is None else Decimal(current),
        missing_reason=missing_reason,
    )


def _assembled(*items: facts.Fact) -> AssembledInsights:
    from app.domain.bi.catalogue import catalogue  # noqa: PLC0415 - fixture-local

    sheet = facts.fact_sheet(
        institution_id=BANK_ID,
        as_of=AS_OF,
        catalogue_version=catalogue().version,
        facts=items or (_movement(),),
        generated_at=datetime.now(tz=UTC),
    )
    return AssembledInsights(
        insight_set=rules.derive_insights(sheet),
        sheet=sheet,
        as_of=AS_OF,
        compare_to=PRIOR,
        member_ids=(RATIO,),
        denied_members=(),
        measures_read=1,
        measures_withheld=0,
        compiled_reads=1,
        build_fingerprint="f" * 64,
    )


def _request(db: Session, bank_row: Bank, *items: facts.Fact) -> bi_commentary.CommentaryRequest:
    return bi_commentary.request_commentary(
        db, ctx=_ctx(), bank=bank_row, assembled=_assembled(*items), requested_by=USER_1
    )


def _result(draft: CommentaryDraft | None, **kwargs: Any) -> ai_client.ModelResult[CommentaryDraft]:
    return ai_client.ModelResult(
        outcome=kwargs.pop("outcome", "ok"),
        model_requested=kwargs.pop("model_requested", get_settings().ai.model),
        model_served=kwargs.pop("model_served", get_settings().ai.model),
        parsed=draft,
        usage=ai_client.UsageRecord(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=900
        ),
        **kwargs,
    )


def _grounded(fid: str) -> CommentaryDraft:
    return CommentaryDraft(
        paragraphs=[
            CommentaryParagraph(
                text=(
                    "{{E:bank}} reported a capital adequacy ratio of "
                    f"{{{{F:{fid}}}}} at {{{{E:as_of}}}}, higher than the period "
                    "compared against."
                )
            )
        ],
        open_questions=["Please confirm the driver of the movement with the finance team."],
    )


def _run(db: Session, draft_id: Any) -> AiCommentaryDraft:
    job = Job(
        organization_id=ORG_1,
        job_type=bi_commentary.JOB_TYPE,
        status="running",
        payload={"draft_id": str(draft_id)},
        attempts=1,
        max_attempts=1,
    )
    db.add(job)
    db.flush()
    bi_commentary.run_bi_commentary(db, job)
    row = db.get(AiCommentaryDraft, draft_id)
    assert row is not None
    db.refresh(row)
    return row


def _current_fid(row: AiCommentaryDraft) -> str:
    """The placeholder id for the figure at the reporting date."""
    fid = next(
        (key for key, value in (row.fact_bindings or {}).items() if value["role"] == "current"),
        None,
    )
    assert fid is not None
    return fid


# --- the request -------------------------------------------------------------


def test_a_request_freezes_the_payload_and_queues_an_ai_lane_job(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)

    assert outcome.created and outcome.draft is not None
    draft = outcome.draft
    assert draft.status == "queued"
    assert draft.payload_mode == "descriptor_only"
    assert draft.payload_sha256 == commentary.payload_digest(draft.payload)
    assert draft.fallback_paragraphs, "the deterministic commentary is written up front"
    assert draft.consent_version == get_settings().ai.consent_version
    job = db_session.get(Job, draft.job_id)
    assert job is not None
    assert job.job_type == bi_commentary.JOB_TYPE
    assert job_queue.lane_of(job.job_type) == "ai"
    assert job.max_attempts == get_settings().ai.job_max_attempts


def test_a_tenant_that_has_not_consented_gets_the_platforms_commentary(
    db_session: Session, bank: Bank
) -> None:
    """No consent row at all: nothing is sent, nothing is recorded, and the reader
    still gets commentary — the platform's own."""
    outcome = _request(db_session, bank)

    assert outcome.draft is None
    assert outcome.reason == "tenant_disabled"
    assert outcome.fallback_paragraphs
    assert BANK_NAME in outcome.fallback_paragraphs[0]
    assert db_session.query(AiCommentaryDraft).count() == 0


def test_a_feature_the_tenant_did_not_name_is_refused(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """Consent is to a NAMED use: ICAAP drafting is not BI commentary."""
    consented.enabled_features = ["icaap_drafting"]
    db_session.commit()
    outcome = _request(db_session, bank)

    assert outcome.draft is None
    assert outcome.reason == "feature_disabled"
    assert outcome.fallback_paragraphs


def test_an_exhausted_daily_cap_still_answers(
    db_session: Session,
    bank: Bank,
    consented: AiCommentarySettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = consented
    monkeypatch.setenv("AI_DAILY_REQUESTS_PER_ORG", "0")
    get_settings.cache_clear()
    outcome = _request(db_session, bank)

    assert outcome.draft is None
    assert outcome.reason == "org_requests"
    assert outcome.quota.retry_after_seconds > 0
    assert outcome.fallback_paragraphs
    assert db_session.query(AiCommentaryDraft).count() == 0


def test_a_sheet_with_nothing_quotable_answers_without_sending_anything(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank, _movement(None, None, missing_reason="not_supplied"))

    assert outcome.draft is None
    assert outcome.reason == "no_commentable_facts"
    assert outcome.fallback_paragraphs
    assert db_session.query(AiCommentaryDraft).count() == 0


def test_a_second_request_joins_the_one_in_flight(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    first = _request(db_session, bank)
    second = _request(db_session, bank)

    assert first.draft is not None and second.draft is not None
    assert second.created is False
    assert second.draft.id == first.draft.id
    assert db_session.query(AiCommentaryDraft).count() == 1


def test_the_request_counts_against_the_tenants_one_ai_budget(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """One budget across every AI surface, so a commentary request is spend."""
    from app.services.ai import quota  # noqa: PLC0415 - the registry under test

    _ = consented
    _request(db_session, bank)
    source = next(item for item in quota.USAGE_SOURCES if type(item).__name__ == "_CommentaryUsage")
    since = datetime.now(UTC) - timedelta(days=1)
    assert source.requests_since(db_session, ORG_1, since, user_id=None) == 1
    assert source.requests_since(db_session, ORG_1, since, user_id=USER_1) == 1


# --- the run -----------------------------------------------------------------


def test_the_happy_path_validates_and_renders_the_platforms_own_figures(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    fid = _current_fid(outcome.draft)
    model = ai_client.RecordedModel([_result(_grounded(fid))])
    with ai_client.use_model(model):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "validated"
    assert row.validation_errors == []
    assert row.completed_at is not None
    # What was SENT carried no figure at all.
    sent = model.requests[0].user_content
    assert "13.4" not in sent
    assert BANK_NAME not in sent

    view = build_view(db_session, bank=bank, draft=row)
    assert view.source == "model"
    text = view.paragraphs[0].text
    assert BANK_NAME in text, "the platform resolves the name, from its own registers"
    assert "13.4 %" in text, "the platform resolves the figure, from its own binding"
    assert "{{" not in text
    assert view.open_questions


def test_a_refusal_shows_no_prose_and_falls_back(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    refusal = _result(None, outcome="refused", refusal_category="other")
    with ai_client.use_model(ai_client.RecordedModel([refusal])):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "refused"
    assert row.output is None
    view = build_view(db_session, bank=bank, draft=row)
    assert view.source == "platform"
    assert view.paragraphs
    assert BANK_NAME in view.paragraphs[0].text


def test_ungrounded_prose_is_stored_as_codes_and_never_served(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """A model that writes a number instead of a placeholder is refused — and the
    reader gets the deterministic commentary rather than the model's number."""
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    ungrounded = CommentaryDraft(
        paragraphs=[
            CommentaryParagraph(
                text="The capital adequacy ratio rose to 13.4 % at the reporting date."
            )
        ],
        open_questions=[],
    )
    with ai_client.use_model(ai_client.RecordedModel([_result(ungrounded)])):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "rejected_validation"
    codes = {entry["code"] for entry in row.validation_errors}
    assert "digit" in codes
    assert "percent" in codes
    # Stored for the eval, never shown. The served prose is the platform's — which
    # does state the figure, because the platform's figure is not in question; what
    # must not appear anywhere is the MODEL's sentence.
    assert row.output is not None
    view = build_view(db_session, bank=bank, draft=row)
    assert view.source == "platform"
    served = " ".join(paragraph.text for paragraph in view.paragraphs)
    assert ungrounded.paragraphs[0].text not in served
    assert "The capital adequacy ratio rose to" not in served
    assert view.validation_error_codes == tuple(sorted(codes))


def test_a_draft_that_names_the_bank_itself_is_refused(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    named = CommentaryDraft(
        paragraphs=[CommentaryParagraph(text=f"{BANK_NAME} improved its capital position.")],
        open_questions=[],
    )
    with ai_client.use_model(ai_client.RecordedModel([_result(named)])):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "rejected_validation"
    assert "tenant_name" in {entry["code"] for entry in row.validation_errors}


def test_a_kill_switch_pulled_after_the_request_cancels_it(
    db_session: Session,
    bank: Bank,
    consented: AiCommentarySettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    model = ai_client.RecordedModel([_result(_grounded(_current_fid(outcome.draft)))])
    with ai_client.use_model(model):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "cancelled"
    assert row.failure_code == "deployment_disabled"
    assert model.requests == [], "a cancelled request is never sent"


def test_bi_switched_off_after_the_request_cancels_it(
    db_session: Session,
    bank: Bank,
    consented: AiCommentarySettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BI plane's own switch. The AI gates cannot see it, so the handler does."""
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    monkeypatch.setenv("BI_ENABLED", "0")
    get_settings.cache_clear()
    model = ai_client.RecordedModel([_result(_grounded(_current_fid(outcome.draft)))])
    with ai_client.use_model(model):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "cancelled"
    assert row.failure_code == bi_commentary.FAILURE_BI_DISABLED
    assert model.requests == []


def test_a_request_that_waited_too_long_cancels_instead_of_calling(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    expiry = get_settings().ai.queue_expiry_seconds
    outcome.draft.created_at = datetime.now(UTC) - timedelta(seconds=expiry * 2)
    db_session.commit()
    assert bi_commentary.is_expired(outcome.draft)

    model = ai_client.RecordedModel([_result(_grounded(_current_fid(outcome.draft)))])
    with ai_client.use_model(model):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "cancelled"
    assert row.failure_code == "queue_expired"
    assert model.requests == []


def test_a_tampered_payload_is_never_sent(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    tampered = dict(outcome.draft.payload)
    tampered["mode"] = "standard"
    outcome.draft.payload = tampered
    db_session.commit()

    model = ai_client.RecordedModel([_result(_grounded(_current_fid(outcome.draft)))])
    with ai_client.use_model(model):
        row = _run(db_session, outcome.draft.id)

    assert row.status == "failed"
    assert row.failure_code == "integrity"
    assert model.requests == []


def test_a_reclaimed_job_never_sends_a_second_request(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """The one crash re-run ``AI_JOB_MAX_ATTEMPTS`` allows must not spend again."""
    _ = consented
    outcome = _request(db_session, bank)
    assert outcome.draft is not None
    fid = _current_fid(outcome.draft)
    with ai_client.use_model(ai_client.RecordedModel([_result(_grounded(fid))])):
        first = _run(db_session, outcome.draft.id)
    assert first.status == "validated"

    second_model = ai_client.RecordedModel([_result(_grounded(fid))])
    with ai_client.use_model(second_model):
        again = _run(db_session, outcome.draft.id)

    assert second_model.requests == []
    assert again.status == "validated"
    assert again.completed_at == first.completed_at


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("truncated", "failed"),
        ("schema_invalid", "failed"),
        ("rate_limited", "rate_limited"),
        ("failed", "failed"),
    ],
)
def test_every_api_outcome_reaches_a_terminal_status_without_prose(
    db_session: Session,
    bank: Bank,
    consented: AiCommentarySettings,
    outcome: str,
    expected: str,
) -> None:
    _ = consented
    request = _request(db_session, bank)
    assert request.draft is not None
    result = _result(None, outcome=outcome, failure_code="upstream_error")
    with ai_client.use_model(ai_client.RecordedModel([result])):
        row = _run(db_session, request.draft.id)

    assert row.status == expected
    assert row.output is None
    assert row.completed_at is not None
    assert build_view(db_session, bank=bank, draft=row).source == "platform"


def test_the_sealed_row_records_which_vendor_answered(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """Since D-053 identical inputs can produce different prose depending on who
    was up, so the row has to say who was."""
    _ = consented
    request = _request(db_session, bank)
    assert request.draft is not None
    fid = _current_fid(request.draft)
    failed_over = dataclasses.replace(
        _result(_grounded(fid)),
        vendor="openai",
        tier_position=2,
        model_requested="gpt-5.1",
        model_served="gpt-5.1",
        degraded=("prompt_cache",),
    )
    with ai_client.use_model(ai_client.RecordedModel([failed_over])):
        row = _run(db_session, request.draft.id)

    assert row.status == "validated"
    assert row.model_requested == "gpt-5.1"
    assert row.model_served == "gpt-5.1"
    assert row.usage is not None
    assert row.usage["vendor"] == "openai"
    assert row.usage["tier_position"] == 2
    assert row.usage["degraded_capabilities"] == ["prompt_cache"]


# --- the read ----------------------------------------------------------------


def test_a_queued_request_reads_as_pending_over_the_platforms_commentary(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    _ = consented
    request = _request(db_session, bank)
    assert request.draft is not None
    view = build_view(db_session, bank=bank, draft=request.draft)

    assert view.pending
    assert view.source == "platform"
    assert view.paragraphs
    assert view.poll_after_seconds == get_settings().ai.client_poll_seconds


def test_moved_figures_make_a_draft_stale(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """The check is value-based: re-derivation over unchanged figures is not stale."""
    _ = consented
    request = _request(db_session, bank)
    assert request.draft is not None
    unchanged = bi_commentary.current_fact_sheet_hash(_assembled())
    moved = bi_commentary.current_fact_sheet_hash(_assembled(_movement("12.10", "15.90")))

    fresh = build_view(
        db_session, bank=bank, draft=request.draft, current_fact_sheet_hash=unchanged
    )
    stale = build_view(db_session, bank=bank, draft=request.draft, current_fact_sheet_hash=moved)
    unknown = build_view(db_session, bank=bank, draft=request.draft)

    assert fresh.stale is False
    assert stale.stale is True
    assert unknown.stale is None


def test_a_placeholder_with_no_binding_un_serves_the_whole_draft(
    db_session: Session, bank: Bank, consented: AiCommentarySettings
) -> None:
    """Half a commentary is worse than the deterministic one."""
    _ = consented
    request = _request(db_session, bank)
    assert request.draft is not None
    fid = _current_fid(request.draft)
    with ai_client.use_model(ai_client.RecordedModel([_result(_grounded(fid))])):
        row = _run(db_session, request.draft.id)
    assert row.status == "validated"

    row.fact_bindings = {}
    db_session.commit()
    view = build_view(db_session, bank=bank, draft=row)

    assert view.source == "platform"
    assert view.paragraphs
