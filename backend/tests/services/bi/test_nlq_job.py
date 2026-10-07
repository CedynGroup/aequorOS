"""The ``bi_nlq_translate`` lifecycle: what is queued, and what the handler writes.

The handler is the only process in the platform that holds a model credential, and
the only thing it is allowed to produce is a PROPOSAL. So the tests here are about
the four ways that could go wrong:

* **it runs after permission was withdrawn.** The AI gates are evaluated again at
  ``phase="run"``, and the BI plane's own two switches are re-read, because the gates
  cannot see them. Each produces a ``cancelled`` record and no model call;
* **it sends something other than what was frozen.** The payload digest is re-checked;
* **a reclaimed job spends a second model call.** A terminal record returns at once;
* **it widens.** A model naming a member the request did not offer produces a refusal,
  and the offending id is recorded on the row for a reviewer and returned to nobody.

Plus the property the whole phase is about: the quota source counts this surface's
requests against the tenant's ONE daily AI budget, so a question and an ICAAP draft
spend from the same number.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.jobs import bi_nlq
from app.models import Bank, Job, Organization, User
from app.models.ai import AiCommentarySettings
from app.services.ai import client as ai_client
from app.services.ai import features, gates, quota
from app.services.bi import nlq
from app.services.bi.nlq import candidates
from app.services.bi.nlq.schema import NlqDraft, NlqQueryDraft, NlqTimeDraft
from tests.support.helpers import ORG_1

BANK_ID = "BK-NLQJOB01"
AS_OF = dt.date(2026, 8, 31)
MEASURE = "loans.balance_rc"
READER = uuid4()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is None:
        existing = Bank(
            id=BANK_ID,
            organization_id=ORG_1,
            name="Nlq Job Bank",
            short_name="NlqJob",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type="universal_bank",
        )
        db_session.add(existing)
        db_session.commit()
    return existing


@pytest.fixture
def switches_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_NLQ_ENABLED", "1")
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def question_surface_consentable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A consent text that DESCRIBES the question surface — the product's future state.

    Since audit A360-5 M2 ``gates.evaluate`` refuses any feature the shipped consent text
    does not describe, at both phases, however the tenant row was written. The text was
    amended on 2026-09-29 so ``bi_nlq`` IS covered, making this fixture a no-op in the
    common case; it stays so the handler tests do not depend on that list.
    ``test_the_handler_cancels_a_question_the_consent_text_does_not_cover`` constructs the
    uncovered state synthetically instead.
    """

    # Idempotent: consent text v2 (2026-09-29) covers questions, so this fixture is
    # usually a no-op now. It stays because the tests that use it are about the
    # HANDLER, not about which surfaces the shipped text happens to describe — they
    # must keep working whichever way that list moves.
    monkeypatch.setattr(
        features,
        "CONSENT_COVERED_FEATURES",
        tuple(dict.fromkeys((*features.CONSENT_COVERED_FEATURES, bi_nlq.FEATURE))),
    )


def _write_consent_row(db: Session) -> AiCommentarySettings:
    existing = gates.tenant_row(db, ORG_1)
    if existing is not None:
        existing.enabled = True
        existing.enabled_features = [bi_nlq.FEATURE]
        existing.consent_version = get_settings().ai.consent_version
        db.commit()
        return existing
    row = AiCommentarySettings(
        organization_id=ORG_1,
        enabled=True,
        enabled_features=[bi_nlq.FEATURE],
        descriptor_only=True,
        consent_version=get_settings().ai.consent_version,
        consented_by=READER,
        consented_at=utc_now(),
        updated_by=READER,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def consented(db_session: Session, question_surface_consentable: None) -> AiCommentarySettings:
    _ = question_surface_consentable
    return _write_consent_row(db_session)


def _built(question: str = "gross loans by branch") -> Any:
    cat = catalogue()
    chosen = candidates.select(
        cat,
        list(cat.members()),
        question=question,
        institution_class="bank",
        capital_regime="crd",
    )
    return nlq.build_payload(chosen, question=question)


def _job(db: Session, bank: Bank, built: Any, **overrides: Any) -> Job:
    payload: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "principal_user_id": str(READER),
        "as_of": AS_OF.isoformat(),
        "catalogue_version": CATALOGUE_VERSION,
        "prompt_version": nlq.PROMPT_VERSION,
        "payload": built.payload,
        "payload_sha256": built.sha256,
        "offered_member_ids": list(built.offered_member_ids),
        **overrides,
    }
    job = Job(
        organization_id=ORG_1,
        job_type=bi_nlq.JOB_TYPE,
        status="running",
        bank_id=bank.id,
        payload=payload,
        attempts=1,
        max_attempts=1,
    )
    db.add(job)
    db.flush()
    return job


def _result(draft: NlqDraft | None, **kwargs: Any) -> ai_client.ModelResult[NlqDraft]:
    return ai_client.ModelResult(
        outcome=kwargs.pop("outcome", "ok"),
        model_requested=kwargs.pop("model_requested", get_settings().ai.model),
        model_served=kwargs.pop("model_served", get_settings().ai.model),
        parsed=draft,
        usage=ai_client.UsageRecord(input_tokens=900, output_tokens=120),
        **kwargs,
    )


def _run(db: Session, job: Job, result: ai_client.ModelResult[NlqDraft]) -> Job:
    model = ai_client.RecordedModel([result])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db, job)
    return job


def _ok_draft(measure: str = MEASURE) -> NlqDraft:
    return NlqDraft(
        answerable=True,
        query=NlqQueryDraft(measures=[measure], time=NlqTimeDraft()),
    )


# --- the happy path -----------------------------------------------------------------------


@pytest.mark.usefixtures("switches_on", "consented")
def test_the_handler_writes_a_proposal_and_runs_nothing(db_session: Session, bank: Bank) -> None:
    built = _built()
    job = _run(db_session, _job(db_session, bank, built), _result(_ok_draft()))
    progress = job.progress or {}

    assert progress["status"] == bi_nlq.STATUS_PROPOSED
    assert progress["query"]["measures"] == [MEASURE]
    assert progress["query"]["time"]["as_of"] == AS_OF.isoformat()
    # A proposal is not a result: nothing here is a row, a column or a figure.
    assert "rows" not in progress
    assert "columns" not in progress


@pytest.mark.usefixtures("switches_on", "consented")
def test_the_prompt_carries_the_frozen_payload_and_the_pinned_version(
    db_session: Session, bank: Bank
) -> None:
    built = _built()
    model = ai_client.RecordedModel([_result(_ok_draft())])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, _job(db_session, bank, built))

    assert len(model.requests) == 1
    request = model.requests[0]
    assert request.feature == bi_nlq.FEATURE
    assert request.prompt_version == nlq.PROMPT_VERSION
    assert request.output_type is NlqDraft
    assert built.payload["question"] in request.user_content
    assert all(block.cache for block in request.system)


# --- permission withdrawn between the request and the call --------------------------------


@pytest.mark.usefixtures("consented")
@pytest.mark.parametrize(
    ("env", "reason"),
    [
        pytest.param({"BI_ENABLED": "0"}, bi_nlq.REASON_BI_DISABLED, id="bi_off"),
        pytest.param({"BI_NLQ_ENABLED": "0"}, bi_nlq.REASON_NLQ_DISABLED, id="nlq_off"),
        pytest.param({"AI_COMMENTARY_ENABLED": "0"}, "deployment_disabled", id="ai_kill_switch"),
    ],
)
def test_a_switch_pulled_after_the_request_cancels_instead_of_sending(
    db_session: Session,
    bank: Bank,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    reason: str,
) -> None:
    for key, value in {
        "BI_ENABLED": "1",
        "BI_NLQ_ENABLED": "1",
        "AI_COMMENTARY_ENABLED": "1",
        "APP_ENV": "test",
        **env,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    job = _job(db_session, bank, _built())
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)
    get_settings.cache_clear()

    assert (job.progress or {})["status"] == bi_nlq.STATUS_CANCELLED
    assert (job.progress or {})["reason"] == reason
    assert (job.progress or {})["message"]
    assert model.requests == [], "a cancelled request must make no model call"


@pytest.mark.usefixtures("switches_on", "question_surface_consentable")
def test_a_tenant_that_has_not_switched_the_feature_on_is_cancelled(
    db_session: Session, bank: Bank
) -> None:
    """No consent row at all, which is the state every tenant is in today."""

    db_session.query(AiCommentarySettings).filter(
        AiCommentarySettings.organization_id == ORG_1
    ).delete()
    db_session.commit()
    job = _job(db_session, bank, _built())
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)

    assert (job.progress or {})["status"] == bi_nlq.STATUS_CANCELLED
    assert (job.progress or {})["reason"] == "tenant_disabled"
    assert model.requests == []


@pytest.mark.usefixtures("switches_on")
def test_the_handler_cancels_a_question_the_consent_text_does_not_cover(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A360-5 M2, at the process that holds the key.

    A tenant row naming ``bi_nlq`` — written directly, as ``psql`` or a migration
    would — used to pass ``gates.evaluate`` on ``feature in enabled_features`` alone,
    and the handler then sent the reader's words to a vendor under a consent text that
    does not describe the surface. Under the REAL constants the gate refuses with its
    own code at BOTH phases, the handler records a withdrawal (not a fault), and no
    model call is made.
    """

    # SYNTHETIC: v2 of the consent text covers questions, so the uncovered state is
    # constructed rather than waited for. What is being proven is not a fact about
    # `bi_nlq` — it is that the HANDLER refuses a surface the shipped consent text
    # does not describe, records it as a withdrawal rather than a fault, and makes
    # no model call. That rule must hold for whatever surface is added next.
    monkeypatch.setattr(
        features,
        "CONSENT_COVERED_FEATURES",
        tuple(f for f in features.CONSENT_COVERED_FEATURES if f != bi_nlq.FEATURE),
    )
    assert bi_nlq.FEATURE not in features.CONSENT_COVERED_FEATURES, (
        "the synthetic withholding did not take effect"
    )
    _write_consent_row(db_session)
    job = _job(db_session, bank, _built())
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)

    assert (job.progress or {})["status"] == bi_nlq.STATUS_CANCELLED
    assert (job.progress or {})["reason"] == "consent_not_covered"
    assert (job.progress or {})["message"] == gates.GATE_MESSAGES["consent_not_covered"]
    assert model.requests == [], "a row cannot out-rank the consent document"

    # And at ENQUEUE, through the site the route calls: nothing is queued either.
    from app.api.deps import TenantContext  # noqa: PLC0415

    outcome = bi_nlq.request_translation(
        db_session,
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=READER),
        bank=bank,
        requested_by=READER,
        as_of=AS_OF,
        built=_built(),
        catalogue_version=CATALOGUE_VERSION,
    )
    assert outcome.job is None
    assert outcome.reason == "consent_not_covered"


# --- a proposal is confirmed once, and not forever -----------------------------------------


@pytest.mark.usefixtures("switches_on", "consented")
def test_a_proposal_s_confirmation_window_is_anchored_on_when_it_was_proposed(
    db_session: Session, bank: Bank
) -> None:
    """Audit A360-5 L4. The handler stamps ``proposed_at`` beside the query; the
    window is the queue's own expiry measured from THAT, not from the question; a
    record predating the stamp falls back to the queue's own timestamps, which can only
    shorten the window; and spending the confirmation is visible on the row and never
    revisited by a reclaimed handler."""

    job = _run(db_session, _job(db_session, bank, _built()), _result(_ok_draft()))
    progress = job.progress or {}
    proposed_at = dt.datetime.fromisoformat(progress[bi_nlq.PROGRESS_PROPOSED_AT])
    assert proposed_at.tzinfo is not None
    window = dt.timedelta(seconds=get_settings().ai.queue_expiry_seconds)

    assert bi_nlq.proposal_expired(job, now=proposed_at + window - dt.timedelta(seconds=1)) is False
    assert bi_nlq.proposal_expired(job, now=proposed_at + window + dt.timedelta(seconds=1)) is True
    # The question may have waited most of the window in the queue: the proposal
    # still gets a whole window of its own from when it was written.
    job.queued_at = proposed_at - window + dt.timedelta(seconds=30)
    db_session.flush()
    assert bi_nlq.proposal_expired(job, now=proposed_at + dt.timedelta(minutes=5)) is False

    # A record from before the stamp existed: the queue's own clock, never later.
    legacy = _job(db_session, bank, _built())
    legacy.progress = {"status": bi_nlq.STATUS_PROPOSED, "query": progress["query"]}
    legacy.queued_at = utc_now() - window - dt.timedelta(minutes=1)
    legacy.completed_at = None
    db_session.flush()
    assert bi_nlq.proposal_expired(legacy) is True
    legacy.completed_at = utc_now()
    db_session.flush()
    assert bi_nlq.proposal_expired(legacy) is False, "completed_at is the worker's stamp"

    # Spending it.
    assert bi_nlq.is_confirmed(job) is False
    bi_nlq.mark_confirmed(job)
    assert bi_nlq.is_confirmed(job) is True
    assert (job.progress or {})["status"] == bi_nlq.STATUS_PROPOSED, "the record is kept"
    spent = dict(job.progress or {})
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)
    assert job.progress == spent, "a reclaimed handler leaves a spent proposal alone"
    assert model.requests == []


@pytest.mark.usefixtures("switches_on", "consented")
def test_a_question_that_waited_out_its_queue_expiry_cancels(
    db_session: Session, bank: Bank
) -> None:
    job = _job(db_session, bank, _built())
    job.queued_at = utc_now() - dt.timedelta(seconds=get_settings().ai.queue_expiry_seconds + 60)
    db_session.flush()
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)

    assert (job.progress or {})["status"] == bi_nlq.STATUS_CANCELLED
    assert (job.progress or {})["reason"] == "queue_expired"
    assert model.requests == []
    assert bi_nlq.is_expired(job) is True


# --- integrity and idempotence ------------------------------------------------------------


@pytest.mark.usefixtures("switches_on", "consented")
def test_a_payload_that_no_longer_matches_its_digest_is_not_sent(
    db_session: Session, bank: Bank
) -> None:
    """A hand-edited or replayed row must not reach a vendor."""

    built = _built()
    job = _job(db_session, bank, built)
    tampered = dict(job.payload)
    tampered["payload"] = {**built.payload, "question": "something else entirely"}
    job.payload = tampered
    db_session.flush()
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)

    assert (job.progress or {})["status"] == bi_nlq.STATUS_FAILED
    assert (job.progress or {})["reason"] == bi_nlq.REASON_INTEGRITY
    assert model.requests == []


@pytest.mark.usefixtures("switches_on", "consented")
def test_a_reclaimed_job_does_not_spend_a_second_model_call(
    db_session: Session, bank: Bank
) -> None:
    built = _built()
    job = _run(db_session, _job(db_session, bank, built), _result(_ok_draft()))
    first = dict(job.progress or {})
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        bi_nlq.run_bi_nlq_translate(db_session, job)

    assert job.progress == first
    assert model.requests == []


# --- the model is wrong -------------------------------------------------------------------


@pytest.mark.usefixtures("switches_on", "consented")
def test_a_model_naming_a_member_the_request_did_not_offer_is_refused(
    db_session: Session, bank: Bank
) -> None:
    """The widening test. The offered set is frozen in the payload, so the handler
    needs no authority of its own to refuse — and it must not repair the query."""

    built = _built()
    # A real catalogue measure this question was NOT shown. Forty of 1,456 measures are
    # offered, so there is always one — and picking it at run time keeps the test from
    # depending on which id the retrieval happened to rank.
    hidden = next(
        member.id for member in catalogue().measures() if member.id not in built.offered_member_ids
    )
    job = _run(db_session, _job(db_session, bank, built), _result(_ok_draft(hidden)))
    progress = job.progress or {}

    assert progress["status"] == bi_nlq.STATUS_REFUSED
    assert progress["reason"] == "unrecognised_member"
    assert "query" not in progress
    # Recorded for a reviewer, and returned to nobody: the route never reads this.
    assert progress["unoffered_members"] == [hidden]
    assert hidden not in progress["message"]


@pytest.mark.usefixtures("switches_on", "consented")
def test_an_unanswerable_answer_is_a_refusal_with_the_platform_s_own_words(
    db_session: Session, bank: Bank
) -> None:
    draft = NlqDraft(answerable=False, unanswerable_reason="ambiguous")
    job = _run(db_session, _job(db_session, bank, _built()), _result(draft))
    progress = job.progress or {}

    assert progress["status"] == bi_nlq.STATUS_REFUSED
    assert progress["reason"] == "unanswerable"
    assert progress["message"] == nlq.UNANSWERABLE_MESSAGES["ambiguous"]


@pytest.mark.usefixtures("switches_on", "consented")
@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        pytest.param("refused", bi_nlq.STATUS_FAILED, id="vendor_refused"),
        pytest.param("schema_invalid", bi_nlq.STATUS_FAILED, id="unparseable"),
        pytest.param("truncated", bi_nlq.STATUS_FAILED, id="truncated"),
        pytest.param("rate_limited", bi_nlq.STATUS_RATE_LIMITED, id="rate_limited"),
        pytest.param("failed", bi_nlq.STATUS_FAILED, id="unreachable"),
    ],
)
def test_every_non_ok_outcome_becomes_a_terminal_record_with_readable_copy(
    db_session: Session, bank: Bank, outcome: str, status: str
) -> None:
    job = _run(
        db_session,
        _job(db_session, bank, _built()),
        _result(None, outcome=outcome, failure_code=outcome),
    )
    progress = job.progress or {}

    assert progress["status"] == status
    assert progress["message"] == bi_nlq.MODEL_FAILURE_MESSAGES[outcome]
    # No vendor, no model id and no key in anything a reader sees.
    assert "api" not in progress["message"].lower()
    assert "query" not in progress


# --- the quota is the tenant's ONE budget -------------------------------------------------


@pytest.mark.usefixtures("switches_on", "consented")
def test_the_quota_source_counts_questions_and_excludes_cancelled_ones(
    db_session: Session, bank: Bank
) -> None:
    source = next(s for s in quota.USAGE_SOURCES if isinstance(s, bi_nlq._NlqUsage))
    since = utc_now() - dt.timedelta(hours=1)
    baseline = source.requests_since(db_session, ORG_1, since, user_id=None)

    spent = _run(db_session, _job(db_session, bank, _built()), _result(_ok_draft()))
    cancelled = _job(db_session, bank, _built())
    cancelled.progress = {"status": bi_nlq.STATUS_CANCELLED, "reason": "tenant_disabled"}
    other_reader = _job(db_session, bank, _built(), principal_user_id=str(uuid4()))
    other_reader.progress = {"status": bi_nlq.STATUS_PROPOSED}
    db_session.flush()

    assert source.requests_since(db_session, ORG_1, since, user_id=None) == baseline + 2
    assert source.requests_since(db_session, ORG_1, since, user_id=READER) == baseline + 1
    assert source.output_tokens_since(db_session, ORG_1, since) == int(
        (spent.progress or {})["usage"]["output_tokens"]
    )


def test_the_job_type_is_in_the_exclusive_ai_lane() -> None:
    """The process holding the model key runs nothing else, so this type may not be
    claimable by the core worker or by the API's in-process thread."""

    from app.services import job_queue  # noqa: PLC0415 - registry read, not a dependency
    from app.worker import resolve_job_types  # noqa: PLC0415

    assert job_queue.lane_of(bi_nlq.JOB_TYPE) == "ai"
    assert bi_nlq.JOB_TYPE not in resolve_job_types(None)
    assert bi_nlq.JOB_TYPE in resolve_job_types("lane:ai")
    with pytest.raises(Exception, match="exclusive"):
        resolve_job_types("lane:ai,lane:core")


def test_an_organization_is_not_needed_for_the_usage_source_to_be_registered() -> None:
    """The source is registered at import time, so a tenant's budget is one number
    across every AI surface rather than one per surface."""

    assert any(isinstance(source, bi_nlq._NlqUsage) for source in quota.USAGE_SOURCES)


@pytest.mark.usefixtures("switches_on", "consented")
def test_the_enqueue_site_is_what_a_route_calls(db_session: Session, bank: Bank) -> None:
    """The other half of the reachability guard: a job type with an enqueue site
    nothing calls is inert, so this drives the site the route drives."""

    from app.api.deps import TenantContext  # noqa: PLC0415

    organization = db_session.get(Organization, ORG_1)
    assert organization is not None
    # ``audit_events.actor_user_id`` is a real foreign key, so the requester must exist.
    db_session.add(
        User(id=READER, organization_id=ORG_1, email=f"{READER}@example.test", display_name="R")
    )
    db_session.flush()
    built = _built()
    outcome = bi_nlq.request_translation(
        db_session,
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=READER),
        bank=bank,
        requested_by=READER,
        as_of=AS_OF,
        built=built,
        catalogue_version=CATALOGUE_VERSION,
    )
    assert outcome.job is not None
    assert outcome.job.job_type == bi_nlq.JOB_TYPE
    assert outcome.job.payload["payload_sha256"] == built.sha256
    assert outcome.job.payload["offered_member_ids"] == list(built.offered_member_ids)
