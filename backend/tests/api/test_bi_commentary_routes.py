"""The AI commentary surface: what a reader can ask for, and what they get back.

The feature behind these two routes was complete and inert — a job type, an
exclusive worker lane, a handler, a row, a payload minimiser, a prompt, a
grounding validator, a deterministic fallback and passing tests, and no caller
anywhere. So the first thing this file is for is the property that made it
inert: a request from a real reader over HTTP must reach
``request_commentary``, queue a job in the ``ai`` lane, and write the egress
audit event. ``tests/architecture/test_job_enqueue_reachability.py`` proves the
path EXISTS statically; these prove it executes.

The rest are the ways a commentary panel lies, one test each:

* **a refusal renders as an empty panel.** A shut gate, a spent daily cap and a
  sheet with nothing quotable each produce commentary — the platform's own — and
  each says so. None of them is an HTTP error, and none of them serves nothing.
* **model prose and platform prose read the same.** ``author`` is on every
  answer, and the model's figures and names arrive as PLATFORM segments resolved
  server-side, never as text the model wrote.
* **one reader's draft is served to another.** A colleague polling the same
  institution and date gets their own request or none, whatever their grants.
* **a draft written against an older book is presented as current.** Staleness is
  reported.
* **a client polls forever.** An expired queued request stops being pollable, and
  ``poll_after_seconds`` is the contract that says so.
* **the surface invents its own authority.** A reader whose access covers none of
  the headline figures is refused, and a sibling institution their binding does
  not name is refused too.

The deployment flag's 404, the cross-tenant 404, the impersonated operator and
the machine principal are covered for BOTH routes by the shared sweeps in
``tests/api/test_bi_routes.py`` (``ROUTES``), which is where every BI route
answers them together; the sibling-institution refusal is not in those sweeps and
is proven here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.features import manage_bi_commentary as surface
from app.jobs import bi_commentary
from app.models import AuditEvent, AuthorizationBinding, Bank, Job, User
from app.models.ai import AiCommentarySettings
from app.models.bi import BiAggPositionDaily, BiFactEngineMetric, BiFactPositionDaily, BiMartBuild
from app.models.bi_commentary import AiCommentaryDraft
from app.services import authorization
from app.services.ai import client as ai_client
from app.services.bi import commentary
from app.services.bi.commentary import CommentaryDraft, CommentaryParagraph
from app.services.bi.insights import default_compare_to
from tests.api.test_bi_routes import AS_OF, BANK_ID, BUILT_AT, FINGERPRINT, seed_bi_mart
from tests.support.helpers import ORG_1, headers

PRIOR = default_compare_to(AS_OF)
BASE = f"/api/v1/banks/{BANK_ID}/bi"
#: A SECOND institution of the same tenant. In scope for the tenancy check, out of
#: scope for an institution-scoped binding that names only the first.
SAME_TENANT_BANK_ID = "BK-BIROUTE2"

READER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
COLLEAGUE = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def surfaces_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Both deployment switches on, in an undeployed environment."""

    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _clone_day(db: Session, bank: Bank, *, target: dt.date, factor: Decimal) -> None:
    """Copy the fixture's one day onto ``target``, scaled, so a movement is real.

    Every ``Decimal`` column is scaled by the same factor, so the ratios move in a
    way the bridge can attribute and the totals stay internally consistent.
    """

    for model in (BiFactPositionDaily, BiAggPositionDaily, BiFactEngineMetric):
        for row in db.scalars(
            select(model).where(model.bank_id == bank.id, model.as_of_date == AS_OF)
        ).all():
            values: dict[str, Any] = {
                column.name: getattr(row, column.name) for column in model.__table__.columns
            }
            values["as_of_date"] = target
            if "id" in values:
                values["id"] = uuid4()
            for key, value in list(values.items()):
                if isinstance(value, Decimal):
                    values[key] = value * factor
            db.add(model(**values))
    db.add(
        BiMartBuild(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            as_of_date=target,
            scope="engine",
            fingerprint=FINGERPRINT,
            status="succeeded",
            builder_version=1,
            started_at=BUILT_AT,
            finished_at=BUILT_AT,
            row_counts={},
        )
    )
    db.commit()


def _user(db: Session, user_id: UUID) -> User:
    existing = db.get(User, user_id)
    if existing is not None:
        return existing
    row = User(
        id=user_id,
        organization_id=ORG_1,
        email=f"{user_id}@example.test",
        display_name=f"Person {str(user_id)[:4]}",
    )
    db.add(row)
    db.flush()
    return row


def _grant(
    db: Session,
    user_id: UUID,
    *,
    module: ModuleScope = ModuleScope.ALL,
    sensitivity: SensitivityScope = SensitivityScope.ALL,
    institution: InstitutionScope = InstitutionScope.INSTITUTION,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            institution,
            BANK_ID if institution is InstitutionScope.INSTITUTION else None,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the BI commentary surface.",
        commit=False,
    )
    db.flush()


def _authv(db: Session, user_id: UUID) -> int:
    user = db.get(User, user_id)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


@pytest.fixture
def plane(db_session: Session) -> Bank:
    """The mart at two dates, and exactly two readers with stated grants.

    ``READER`` reads every headline figure of this institution; ``COLLEAGUE``
    reads credit aggregates only. The hermetic fixture's organization-wide
    all/all sentences are removed first, because every test here is about what a
    NAMED reader may see.
    """

    bank = seed_bi_mart(db_session)
    _clone_day(db_session, bank, target=PRIOR, factor=Decimal("0.8"))
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    _user(db_session, READER)
    _user(db_session, COLLEAGUE)
    _grant(db_session, READER)
    _grant(
        db_session,
        COLLEAGUE,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
    )
    db_session.commit()
    return bank


@pytest.fixture
def consented(db_session: Session) -> AiCommentarySettings:
    """A tenant that has switched BI commentary on and opted OUT of sending values."""

    row = AiCommentarySettings(
        organization_id=ORG_1,
        enabled=True,
        enabled_features=["bi_commentary"],
        descriptor_only=True,
        consent_version=get_settings().ai.consent_version,
        consented_by=READER,
        consented_at=utc_now(),
        updated_by=READER,
    )
    db_session.add(row)
    db_session.commit()
    return row


# --- helpers -----------------------------------------------------------------------------


def _as(db: Session, user_id: UUID) -> dict[str, str]:
    return headers(user_id=user_id, authorization_version=_authv(db, user_id))


def _post(
    client: TestClient,
    db: Session,
    *,
    user_id: UUID = READER,
    base: str = BASE,
    body: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        f"{base}/commentary", json=body or {"as_of": AS_OF.isoformat()}, headers=_as(db, user_id)
    )


def _get(
    client: TestClient, db: Session, *, user_id: UUID = READER, base: str = BASE, query: str = ""
) -> Any:
    suffix = query or f"as_of={AS_OF.isoformat()}"
    return client.get(f"{base}/commentary?{suffix}", headers=_as(db, user_id))


def _drafts(db: Session) -> list[AiCommentaryDraft]:
    return list(db.scalars(select(AiCommentaryDraft).order_by(AiCommentaryDraft.created_at)).all())


def _requested_events(db: Session) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == bi_commentary.EVENT_REQUESTED)
        ).all()
    )


def _jobs(db: Session) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.job_type == bi_commentary.JOB_TYPE)).all())


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


def _current_fid(row: AiCommentaryDraft) -> str:
    """The placeholder id for a figure at the reporting date."""

    fid = next(
        (key for key, value in (row.fact_bindings or {}).items() if value["role"] == "current"),
        None,
    )
    assert fid is not None, row.fact_bindings
    return fid


def _grounded(fid: str) -> CommentaryDraft:
    return CommentaryDraft(
        paragraphs=[
            CommentaryParagraph(
                text=(
                    f"{{{{E:bank}}}} reported {{{{F:{fid}}}}} at {{{{E:as_of}}}}, "
                    "which is higher than the period compared against."
                )
            )
        ],
        open_questions=["Confirm the driver of the movement with the finance team."],
    )


def _run(
    db: Session, draft_id: Any, result: ai_client.ModelResult[CommentaryDraft]
) -> AiCommentaryDraft:
    """Drive the AI-lane handler once, with a canned model reply."""

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
    with ai_client.use_model(ai_client.RecordedModel([result])):
        bi_commentary.run_bi_commentary(db, job)
    row = db.get(AiCommentaryDraft, draft_id)
    assert row is not None
    db.refresh(row)
    return row


# --- the defect: a request reaches the queue ---------------------------------------------


def test_both_routes_are_mounted_on_the_tenant_api() -> None:
    """The half of reachability an AST guard cannot see.

    ``tests/architecture/test_job_enqueue_reachability.py`` walks from every
    ``@router.get``/``@router.post`` decorator, so it reports a handler as
    reachable whether or not ``app/api/router.py`` ever includes the router it
    hangs on. That is the same shape as the defect it was written for — a
    complete call site nothing calls — one level up, so it is asserted here
    against the app the platform actually serves.
    """

    from fastapi.routing import APIRoute  # noqa: PLC0415 - registry inspection only

    from app.main import create_app  # noqa: PLC0415 - one app, built for this check

    mounted = {
        (method, route.path)
        for route in create_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    path = "/api/v1/banks/{bank_id}/bi/commentary"
    missing = sorted(pair for pair in (("GET", path), ("POST", path)) if pair not in mounted)
    assert not missing, (
        "the commentary router is not included in app/api/router.py, so the feature is "
        "unreachable however complete it is; it needs the import and the "
        f"include_router call with BANK_ROUTE_DEPENDENCIES + Depends(require_bi_enabled): {missing}"
    )


def test_a_request_queues_a_model_call_and_records_the_egress(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The property whose absence made this feature inert.

    One POST from a real reader: a draft row, a job in the ``ai`` lane and the
    egress audit event — all three, in the request that asked.
    """

    _ = plane, surfaces_on, consented
    response = _post(db_client, db_session)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["state"] == "pending"
    assert payload["author"] == "platform", "the platform's prose is shown while the model writes"
    assert payload["paragraphs"], "a pending panel is never empty"
    assert payload["poll_after_seconds"] and payload["poll_after_seconds"] > 0
    assert payload["requested"] is True
    assert payload["draft_id"]

    drafts = _drafts(db_session)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.status == "queued"
    assert str(draft.id) == payload["draft_id"]
    assert draft.requested_by == READER
    assert draft.payload_sha256 == commentary.payload_digest(draft.payload)

    jobs = _jobs(db_session)
    assert len(jobs) == 1
    assert jobs[0].id == draft.job_id
    from app.services import job_queue  # noqa: PLC0415 - the lane registry

    assert job_queue.lane_of(jobs[0].job_type) == "ai"

    events = _requested_events(db_session)
    assert len(events) == 1
    assert events[0].entity_id == str(draft.id)
    assert events[0].details["fact_sheet_hash"] == draft.fact_sheet_hash


def test_a_second_request_joins_the_one_in_flight(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """Debounced, and the status code says so: 202 queued something, 200 did not."""

    _ = plane, surfaces_on, consented
    first = _post(db_client, db_session)
    second = _post(db_client, db_session)

    assert first.status_code == 202, first.text
    assert second.status_code == 200, second.text
    assert second.json()["draft_id"] == first.json()["draft_id"]
    assert second.json()["state"] == "pending"
    assert len(_drafts(db_session)) == 1
    assert len(_jobs(db_session)) == 1
    # One model call was authorized, so ONE egress event exists.
    assert len(_requested_events(db_session)) == 1


# --- a refusal is an answer --------------------------------------------------------------


def test_a_tenant_that_has_not_consented_is_served_the_platforms_commentary(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None
) -> None:
    """No consent row: nothing is sent, nothing is recorded, and the reader still
    gets commentary — with a sentence saying whose it is and what to do."""

    _ = plane, surfaces_on
    response = _post(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["author"] == "platform"
    assert payload["paragraphs"], "a refusal is never an empty panel"
    assert payload["reason"] == "tenant_disabled"
    assert payload["can_request_draft"] is False
    assert payload["poll_after_seconds"] is None, "there is nothing to poll for"
    # Production copy, and no gate code in it.
    assert "tenant_disabled" not in payload["notice"]
    assert "Organisation Owner" in payload["notice"]

    assert _drafts(db_session) == []
    assert _requested_events(db_session) == [], "nothing left the platform, so nothing is recorded"
    assert _jobs(db_session) == []


def test_an_exhausted_daily_cap_is_served_the_platforms_commentary(  # noqa: PLR0913 - one fixture per switch in the chain
    db_client: TestClient,
    db_session: Session,
    plane: Bank,
    surfaces_on: None,
    consented: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = plane, surfaces_on, consented
    monkeypatch.setenv("AI_DAILY_REQUESTS_PER_ORG", "0")
    get_settings.cache_clear()

    response = _post(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["author"] == "platform"
    assert payload["reason"] == "org_requests"
    assert payload["paragraphs"]
    assert payload["can_request_draft"] is False
    assert "daily limit" in payload["notice"]
    assert "org_requests" not in payload["notice"]
    assert _drafts(db_session) == []
    assert _requested_events(db_session) == []


def test_a_date_with_nothing_quotable_is_served_the_platforms_commentary(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """A sheet whose figures cannot support a grounded paragraph sends nothing.

    Asked about a date the mart holds no rows for. Every headline measure is
    READ — the reader's access covers all of them, so this is not the
    authorization refusal — and every one comes back without a value, so the
    minimiser has nothing a grounded sentence could rest on. That is the
    fixture's own state, not a stub, and the reader still gets commentary: the
    platform's own, which says nothing has been computed for the date.
    """

    _ = plane, surfaces_on, consented
    empty_date = dt.date(2026, 11, 30)
    response = _post(db_client, db_session, body={"as_of": empty_date.isoformat()})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reason"] == "no_commentable_facts"
    assert payload["author"] == "platform"
    assert payload["state"] == "ready"
    assert payload["paragraphs"], "a refusal is never an empty panel"
    assert "not enough computed detail" in payload["notice"]
    # And the platform's own commentary names the gap rather than reading it as a
    # figure of zero, which is the whole reason it can be served here.
    served = " ".join(p["text"] for p in payload["paragraphs"])
    assert "It is not zero, and it has not stayed flat." in served
    assert _drafts(db_session) == []
    assert _requested_events(db_session) == []


def test_a_draft_the_model_refused_is_answered_with_the_platforms_commentary(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """Every terminal outcome that is not usable prose ends in the same answer, and
    the reader is told the draft could not be used rather than shown nothing."""

    _ = plane, surfaces_on, consented
    queued = _post(db_client, db_session)
    assert queued.status_code == 202, queued.text
    row = _run(
        db_session,
        _drafts(db_session)[0].id,
        _result(None, outcome="refused", refusal_category="other"),
    )
    assert row.status == "refused"

    response = _get(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["author"] == "platform"
    assert payload["reason"] == "refused"
    assert payload["paragraphs"]
    assert payload["open_questions"] == []
    assert payload["poll_after_seconds"] is None
    assert "could not be used" in payload["notice"]
    assert "refused" not in payload["notice"]


# --- model prose is marked as the model's ------------------------------------------------


def test_a_grounded_draft_is_served_as_the_models_words_with_the_platforms_figures(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The positive control, and the disclosure rule in one test.

    ``author`` says the model wrote it; every figure and name arrives as its own
    segment, resolved by the platform from the frozen binding and the current
    registers, and no placeholder reaches the reader.
    """

    _ = plane, surfaces_on, consented
    assert _post(db_client, db_session).status_code == 202
    draft = _drafts(db_session)[0]
    fid = _current_fid(draft)
    row = _run(db_session, draft.id, _result(_grounded(fid)))
    assert row.status == "validated", row.validation_errors

    response = _get(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["author"] == "model"
    assert payload["reason"] is None
    assert payload["open_questions"]
    assert payload["poll_after_seconds"] is None
    assert "assistant" in payload["notice"]

    paragraph = payload["paragraphs"][0]
    kinds = {segment["kind"] for segment in paragraph["segments"]}
    assert "figure" in kinds, "the figure is the platform's own segment, not the model's text"
    assert "name" in kinds
    assert "{{" not in paragraph["text"]
    figure = next(s for s in paragraph["segments"] if s["kind"] == "figure")
    assert figure["measure_id"], "a figure segment links back to the chart it came from"
    assert figure["text"] == (draft.fact_bindings or {})[fid]["display"]
    assert paragraph["measure_ids"] == [figure["measure_id"]]


def test_the_author_vocabulary_matches_the_commentary_package() -> None:
    """The wire words and the service's own constants cannot drift apart."""

    assert surface.AUTHOR_MODEL == commentary.MODEL_SOURCE
    assert surface.AUTHOR_PLATFORM == commentary.FALLBACK_SOURCE


def test_the_read_says_whether_a_draft_could_be_asked_for(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None
) -> None:
    """The control the panel shows is honest before anything is asked.

    Both directions, on the same tenant: with no consent row a draft cannot be
    asked for, and the moment the tenant switches the feature on it can. The field
    is advisory — the request route decides again — so this proves only that a
    reader is not offered a button that would always refuse.
    """

    _ = plane, surfaces_on
    before = _get(db_client, db_session)
    assert before.status_code == 200, before.text
    assert before.json()["can_request_draft"] is False
    assert before.json()["requested"] is False

    db_session.add(
        AiCommentarySettings(
            organization_id=ORG_1,
            enabled=True,
            enabled_features=["bi_commentary"],
            descriptor_only=True,
            consent_version=get_settings().ai.consent_version,
            consented_by=READER,
            consented_at=utc_now(),
            updated_by=READER,
        )
    )
    db_session.commit()

    after = _get(db_client, db_session)
    assert after.status_code == 200, after.text
    assert after.json()["can_request_draft"] is True


# --- a draft is one reader's -------------------------------------------------------------


def test_a_colleague_never_reads_another_readers_draft(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The disclosure this scoping exists to prevent.

    ``COLLEAGUE`` holds credit aggregates only, so the wider reader's draft was
    built from figures their grants withhold. They get their own commentary (they
    have none) and never the other reader's — not its prose, not its id.
    """

    _ = plane, surfaces_on, consented
    assert _post(db_client, db_session, user_id=READER).status_code == 202
    draft = _drafts(db_session)[0]
    row = _run(db_session, draft.id, _result(_grounded(_current_fid(draft))))
    assert row.status == "validated"
    mine = _get(db_client, db_session, user_id=READER)
    assert mine.json()["author"] == "model"

    theirs = _get(db_client, db_session, user_id=COLLEAGUE)
    assert theirs.status_code == 200, theirs.text
    payload = theirs.json()
    assert payload["requested"] is False
    assert payload["draft_id"] is None
    assert payload["author"] == "platform"
    assert payload["reason"] is None
    assert "Ask for a written draft" in payload["notice"]
    # And none of the wider reader's prose reached them.
    served = " ".join(p["text"] for p in payload["paragraphs"])
    for paragraph in mine.json()["paragraphs"]:
        assert paragraph["text"] not in served
    assert str(draft.id) not in theirs.text


def test_a_colleagues_own_request_is_their_own_row(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The positive control for the scoping: two readers, two drafts, no sharing."""

    _ = plane, surfaces_on, consented
    assert _post(db_client, db_session, user_id=READER).status_code == 202
    colleague = _post(db_client, db_session, user_id=COLLEAGUE)
    assert colleague.status_code == 202, colleague.text
    ids = {str(draft.id): draft.requested_by for draft in _drafts(db_session)}
    assert len(ids) == 2
    assert ids[colleague.json()["draft_id"]] == COLLEAGUE
    assert set(ids.values()) == {READER, COLLEAGUE}


# --- staleness and expiry ----------------------------------------------------------------


@pytest.mark.usefixtures("plane", "surfaces_on", "consented")
@pytest.mark.parametrize("metric_date", [AS_OF, PRIOR])
@pytest.mark.parametrize(("metric_tier", "expected_stale"), [("live", False), ("official", True)])
def test_a_draft_written_against_an_older_book_is_reported_as_stale(
    db_client: TestClient,
    db_session: Session,
    metric_date: dt.date,
    metric_tier: str,
    expected_stale: bool,
) -> None:
    """Only official figures at either date make a finished draft stale.

    The prose is still served, and the reader is told it describes different
    figures when the official book moves; the live tier is not in its sheet.
    """

    assert _post(db_client, db_session).status_code == 202
    draft = _drafts(db_session)[0]
    row = _run(db_session, draft.id, _result(_grounded(_current_fid(draft))))
    assert row.status == "validated"
    fresh = _get(db_client, db_session).json()
    assert fresh["stale"] is False, "nothing moved yet; this test would be vacuous"

    # Select the exact figure: an unordered first() could change the live tier
    # even though commentary reads only official figures. The hash is value-based.
    metric = db_session.scalars(
        select(BiFactEngineMetric).where(
            BiFactEngineMetric.organization_id == ORG_1,
            BiFactEngineMetric.bank_id == BANK_ID,
            BiFactEngineMetric.as_of_date == metric_date,
            BiFactEngineMetric.module == "capital",
            BiFactEngineMetric.metric_id == "car_pct",
            BiFactEngineMetric.regime == "crd",
            BiFactEngineMetric.tier == metric_tier,
        )
    ).one()
    metric.value = (metric.value or Decimal("0")) + Decimal("1.75")
    db_session.commit()

    response = _get(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["stale"] is expected_stale
    assert payload["author"] == "model", "a stale draft is reported, not withheld"
    assert (payload["fact_sheet_hash"] != row.fact_sheet_hash) is expected_stale
    assert ("have changed since this draft was prepared" in payload["notice"]) is expected_stale


def test_an_expired_queued_request_stops_being_polled(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The run gate will cancel this for age, so the surface says so now.

    A client that kept polling would spin until a worker got round to the row.
    ``poll_after_seconds`` is ``None``, which is the contract's word for stop.
    """

    _ = plane, surfaces_on, consented
    assert _post(db_client, db_session).status_code == 202
    draft = _drafts(db_session)[0]
    assert draft.status == "queued"
    expiry = get_settings().ai.queue_expiry_seconds
    draft.created_at = utc_now() - dt.timedelta(seconds=expiry * 2)
    db_session.commit()
    assert bi_commentary.is_expired(draft)

    response = _get(db_client, db_session)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["poll_after_seconds"] is None
    assert payload["reason"] == "queue_expired"
    assert payload["author"] == "platform"
    assert payload["paragraphs"]
    assert "waited too long" in payload["notice"]
    assert "queue_expired" not in payload["notice"]


# --- the surface never widens the read ---------------------------------------------------


def test_an_institution_the_binding_does_not_name_is_refused(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """Two institutions of one tenant share an RLS tenant, so the binding decides.

    ``READER``'s sentence names the first institution only. On the second, every
    headline figure is withheld — so nothing is written and nothing is queued,
    because a model call over a sheet this reader could not read is exactly the
    widening this surface must not do.
    """

    _ = plane, surfaces_on, consented
    other = f"/api/v1/banks/{SAME_TENANT_BANK_ID}/bi"
    for response in (
        _post(db_client, db_session, base=other),
        _get(db_client, db_session, base=other),
    ):
        assert response.status_code == 403, response.text
        details = response.json()["error"]["details"]
        assert details["error_code"] == "bi_commentary_authorization_denied"
        assert "denied_members" not in details
        assert "paragraphs" not in response.text
    assert _drafts(db_session) == []
    assert _requested_events(db_session) == []


def test_a_narrow_reader_gets_commentary_only_about_what_they_may_see(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """Commentary inherits the read: the payload holds no withheld measure.

    ``COLLEAGUE`` reads credit aggregates only, so a capital or liquidity measure
    may not appear in the bindings the model is offered — which is what makes the
    egress safe without a second authorization decision.
    """

    _ = plane, surfaces_on, consented
    wide = _post(db_client, db_session, user_id=READER)
    narrow = _post(db_client, db_session, user_id=COLLEAGUE)
    assert wide.status_code == 202 and narrow.status_code == 202, narrow.text
    by_user = {draft.requested_by: draft for draft in _drafts(db_session)}
    wide_measures = {b["measure_id"] for b in (by_user[READER].fact_bindings or {}).values()}
    narrow_measures = {b["measure_id"] for b in (by_user[COLLEAGUE].fact_bindings or {}).values()}

    assert narrow_measures < wide_measures, (narrow_measures, wide_measures)
    assert not any(measure.startswith("engine.") for measure in narrow_measures), narrow_measures


def test_a_comparison_that_is_not_earlier_is_refused_on_both_routes(
    db_client: TestClient, db_session: Session, plane: Bank, surfaces_on: None, consented: Any
) -> None:
    """The row's own CHECK says ``compare_to < as_of``; the routes say it first."""

    _ = plane, surfaces_on, consented
    body = {"as_of": AS_OF.isoformat(), "compare_to": AS_OF.isoformat()}
    posted = _post(db_client, db_session, body=body)
    fetched = _get(
        db_client,
        db_session,
        query=f"as_of={AS_OF.isoformat()}&compare_to={AS_OF.isoformat()}",
    )
    for response in (posted, fetched):
        assert response.status_code == 422, response.text
        assert (
            response.json()["error"]["details"]["error_code"]
            == "bi_commentary_comparison_not_earlier"
        )
    assert _drafts(db_session) == []
