"""The natural-language surface over HTTP: what runs, what refuses, and what is logged.

``docs/bi.md`` §Phase 5 is one sentence and every clause of it is a property here.

* **restricted to the members the user can see** — the ids handed to the model are the
  caller's own, and a reader whose grants cover no figure is refused before anything
  leaves the platform;
* **never SQL** — proved structurally in ``tests/services/bi/test_nlq_validate.py``;
  what is proved here is that the surface never runs anything the model wrote WITHOUT
  the reader confirming it;
* **shown to the user for confirmation** — the proposal route executes nothing, a
  query that is not the proposed one is refused, and the refusal is a 409 rather than a
  quietly substituted query;
* **and logged** — exactly ONE ``bi_query_log`` row per question, allowed and denied,
  plus exactly one more for the read when the reader confirms. The counts are asserted,
  because a log this feature silently stopped writing would look identical from the UI.
  And the row tells the truth about egress (audit A360-5 M1): a question the platform
  never sent — withheld, gated or capped — is logged with NO member ids, so it can
  never read like one a model was shown; the confirmed read is logged under ``nlq``, so
  "which reads came from a model" is answerable from the one table.

Two adversarial cases carry the most weight and both construct a bad state on purpose:

* a PROPOSAL that names a member the reader may not see is still refused at run time,
  because the model's output is a request and ``authorize_query`` is the authority;
* the refusals for "that figure does not exist" and "your grants hide that figure" are
  compared byte for byte.

**The consent text is modelled explicitly.** Since audit A360-5 M2 the egress gate
refuses any feature the shipped consent text does not describe, at both phases, however
the tenant row was written. The text was amended on 2026-09-29
(``ai-consent-2026-09-v2``) so ``bi_nlq`` IS covered now — which is why the test for the
refusal withholds a covered feature synthetically rather than relying on one happening to
be uncovered. ``consented`` therefore means what it says: a tenant that consented under a
text describing the surface.

The deployment flag's 404, the cross-tenant 404, the impersonated operator and the
zero-binding human are covered for all three routes by the shared sweeps in
``tests/api/test_bi_routes.py`` (``ROUTES``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
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
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.jobs import bi_nlq
from app.models import AuditEvent, AuthorizationBinding, Bank, Job, User
from app.models.ai import AiCommentarySettings
from app.models.bi import BiQueryLog
from app.services import authorization
from app.services.ai import client as ai_client
from app.services.ai import features, gates
from app.services.bi import nlq
from app.services.bi.nlq.schema import NlqDraft, NlqQueryDraft, NlqTimeDraft
from tests.api.helpers import ORG_1, headers
from tests.api.test_bi_routes import AS_OF, BANK_ID, seed_bi_mart

BASE = f"/api/v1/banks/{BANK_ID}/bi"
READER = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
COLLEAGUE = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
QUESTION = "gross loans by branch"
MEASURE = "loans.balance_rc"
DIMENSION = "branch.code"


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def surfaces_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_NLQ_ENABLED", "1")
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, BANK_ID, module, sensitivity
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the natural-language surface.",
        commit=False,
    )
    db.flush()


@pytest.fixture
def plane(db_session: Session) -> Bank:
    """The mart, and two readers: one who sees everything, one who sees nothing."""

    bank = seed_bi_mart(db_session)
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    _user(db_session, READER)
    _user(db_session, COLLEAGUE)
    _grant(db_session, READER)
    db_session.commit()
    return bank


@pytest.fixture
def question_surface_consentable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A consent text that DESCRIBES the question surface.

    Idempotent, and usually a no-op since 2026-09-29: ``ai-consent-2026-09-v2`` describes
    the question surface and ``bi_nlq`` is in ``features.CONSENT_COVERED_FEATURES``. It
    stays because these tests are about the ROUTES, not about which surfaces the shipped
    text happens to describe — they must keep working whichever way that list moves.
    The happy-path tests model the amended text; the smuggled-row test below does not.
    """

    monkeypatch.setattr(
        features,
        "CONSENT_COVERED_FEATURES",
        (*features.CONSENT_COVERED_FEATURES, bi_nlq.FEATURE),
    )


def _write_consent_row(db: Session) -> AiCommentarySettings:
    """A tenant row naming the question surface, written directly — no schema in the way."""

    row = gates.tenant_row(db, ORG_1)
    if row is None:
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
    else:
        row.enabled = True
        row.enabled_features = [bi_nlq.FEATURE]
        row.consent_version = get_settings().ai.consent_version
    db.commit()
    return row


@pytest.fixture
def consented(db_session: Session, question_surface_consentable: None) -> AiCommentarySettings:
    """A tenant that switched questions on under a consent text that covers them."""

    _ = question_surface_consentable
    return _write_consent_row(db_session)


# --- helpers -----------------------------------------------------------------------------


def _authv(db: Session, user_id: UUID) -> int:
    user = db.get(User, user_id)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def _as(db: Session, user_id: UUID) -> dict[str, str]:
    return headers(user_id=user_id, authorization_version=_authv(db, user_id))


def _ask(
    client: TestClient,
    db: Session,
    *,
    user_id: UUID = READER,
    question: str = QUESTION,
    as_of: dt.date = AS_OF,
) -> Any:
    return client.post(
        f"{BASE}/ask",
        json={"question": question, "as_of": as_of.isoformat()},
        headers=_as(db, user_id),
    )


def _get(client: TestClient, db: Session, job_id: str, *, user_id: UUID = READER) -> Any:
    return client.get(f"{BASE}/ask/{job_id}", headers=_as(db, user_id))


def _run(
    client: TestClient, db: Session, job_id: str, query: dict[str, Any], *, user_id: UUID = READER
) -> Any:
    return client.post(f"{BASE}/ask/{job_id}/run", json={"query": query}, headers=_as(db, user_id))


def _log_rows(db: Session) -> list[BiQueryLog]:
    return list(db.scalars(select(BiQueryLog).order_by(BiQueryLog.queried_at)).all())


def _jobs(db: Session) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.job_type == bi_nlq.JOB_TYPE)).all())


def _events(db: Session, event_type: str) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type)).all())


def _draft(measures: list[str], dimensions: list[str] | None = None) -> NlqDraft:
    return NlqDraft(
        answerable=True,
        query=NlqQueryDraft(measures=measures, dimensions=dimensions or [], time=NlqTimeDraft()),
    )


def _result(draft: NlqDraft | None, **kwargs: Any) -> ai_client.ModelResult[NlqDraft]:
    return ai_client.ModelResult(
        outcome=kwargs.pop("outcome", "ok"),
        model_requested=kwargs.pop("model_requested", get_settings().ai.model),
        model_served=kwargs.pop("model_served", get_settings().ai.model),
        parsed=draft,
        usage=ai_client.UsageRecord(input_tokens=800, output_tokens=90),
        **kwargs,
    )


def _translate(db: Session, job_id: str, draft: NlqDraft | None, **kwargs: Any) -> Job:
    """Drive the AI-lane handler once, with a canned model reply."""

    job = db.get(Job, UUID(job_id))
    assert job is not None
    with ai_client.use_model(ai_client.RecordedModel([_result(draft, **kwargs)])):
        bi_nlq.run_bi_nlq_translate(db, job)
    db.flush()
    return job


def _proposed(client: TestClient, db: Session, draft: NlqDraft | None = None) -> tuple[str, Any]:
    """Ask, translate, and read back — the whole path up to the confirmation."""

    asked = _ask(client, db)
    assert asked.status_code == 202, asked.text
    job_id = asked.json()["question_id"]
    _translate(db, job_id, draft or _draft([MEASURE], [DIMENSION]))
    read = _get(client, db, job_id)
    assert read.status_code == 200, read.text
    return job_id, read


# --- the surface is off by default -------------------------------------------------------


@pytest.mark.usefixtures("plane")
def test_bi_on_but_the_question_surface_off_refuses_in_words(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a 404: every BI path is 404 when BI itself is off, and answering 404 for
    this one flag would make a configured surface indistinguishable from an absent one
    for exactly the switch an Org Owner may be waiting on."""

    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    response = _ask(db_client, db_session)
    get_settings.cache_clear()

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_ask_unavailable"
    assert detail["reason"] == "nlq_disabled"
    assert detail["message"]


# --- asking: what is offered, and what is logged ------------------------------------------


@pytest.mark.usefixtures("surfaces_on", "consented")
def test_asking_queues_one_translation_and_executes_nothing(
    db_client: TestClient, db_session: Session, plane: Bank
) -> None:
    before = len(_log_rows(db_session))
    response = _ask(db_client, db_session)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "translating"
    assert body["query"] is None
    assert body["reading"] is None
    assert body["figures_offered"] > 0
    assert body["catalogue_version"] == CATALOGUE_VERSION
    assert body["question"] == QUESTION

    jobs = _jobs(db_session)
    assert len(jobs) == 1
    assert jobs[0].job_type == bi_nlq.JOB_TYPE
    assert jobs[0].status == "queued"

    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1, "exactly one log row per question"
    assert rows[0].decision == "allowed"
    assert rows[0].row_count is None, "a question serves no rows"
    assert rows[0].member_ids, "the row must name what the model was allowed to name"
    assert len(rows[0].member_ids) == body["figures_offered"]


@pytest.mark.usefixtures("surfaces_on", "consented")
def test_the_ids_offered_to_the_model_are_the_caller_s_own_catalogue(
    db_client: TestClient, db_session: Session, plane: Bank
) -> None:
    """The members handed to the model must be a subset of what ``/bi/catalogue``
    serves this same caller — the whole "restricted to the members the user can see"
    clause, asserted against the other surface rather than against a restatement."""

    catalogue_response = db_client.get(f"{BASE}/catalogue", headers=_as(db_session, READER))
    assert catalogue_response.status_code == 200, catalogue_response.text
    served = {m["id"] for m in catalogue_response.json()["measures"]} | {
        d["id"] for d in catalogue_response.json()["dimensions"]
    }
    asked = _ask(db_client, db_session)
    assert asked.status_code == 202, asked.text
    job = _jobs(db_session)[0]
    offered = set(job.payload["offered_member_ids"])

    assert offered <= served
    assert offered


@pytest.mark.usefixtures("surfaces_on", "consented")
def test_a_reader_whose_grants_cover_no_figure_is_refused_and_logged_as_denied(
    db_client: TestClient, db_session: Session, plane: Bank
) -> None:
    before = len(_log_rows(db_session))
    response = _ask(db_client, db_session, user_id=COLLEAGUE)

    assert response.status_code == 403, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_ask_no_figures_available"
    assert "Org Owner" in detail["message"]
    assert _jobs(db_session) == [], "nothing may be sent for a reader with no figures"

    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1, "exactly one log row per question, denied as well as allowed"
    assert rows[0].decision == "denied"
    assert rows[0].member_ids == []


@pytest.mark.usefixtures("surfaces_on", "consented")
def test_a_question_naming_the_institution_is_never_sent(
    db_client: TestClient, db_session: Session, plane: Bank
) -> None:
    response = _ask(db_client, db_session, question=f"gross loans for {plane.name}")

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["reason"] == "question_withheld"
    assert "web address" in detail["message"]
    assert _jobs(db_session) == []


@pytest.mark.usefixtures("surfaces_on", "plane", "question_surface_consentable")
def test_a_tenant_that_has_not_consented_gets_an_understandable_refusal(
    db_client: TestClient, db_session: Session
) -> None:
    """The gate, not an error: a reader must be able to tell why and who can fix it."""

    db_session.execute(delete(AiCommentarySettings))
    db_session.commit()
    before = len(_log_rows(db_session))
    response = _ask(db_client, db_session)

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_ask_unavailable"
    assert detail["reason"] == "tenant_disabled"
    assert "Organisation Owner" in detail["message"]
    assert _jobs(db_session) == []
    # The READ still happened, so it is still one row — the decision column is the
    # authorization decision, never a description of the HTTP status — and it names
    # no member, because the model was given none.
    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1
    assert rows[0].decision == "allowed"
    assert rows[0].member_ids == []


@pytest.mark.usefixtures("surfaces_on", "plane")
def test_a_tenant_row_that_smuggles_the_question_surface_is_refused_at_the_gate(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A360-5 M2. The consent-coverage rule lived ONLY in the PUT schema.

    Any other writer of ``ai_commentary_settings`` — ``psql``, a data migration, a
    future staff fix endpoint — could produce a row naming ``bi_nlq``, and the egress
    gate admitted it on ``feature in enabled_features`` alone: readers' questions then
    went to a vendor under a consent text promising no customer name or amount ever
    leaves. Under the REAL constants (no consent-text patch here) the gate itself must
    refuse, at the enqueue phase, with its own code — and the log must show the model
    was given nothing.
    """

    # SYNTHETIC: the consent text was amended to v2 on 2026-09-29 and now DOES
    # describe questions, so `bi_nlq` is covered. The rule this test protects is
    # not about `bi_nlq` in particular — it is that a tenant row naming a surface
    # the shipped consent text does not describe is refused at the ROUTE, however
    # the row was written. Hold the feature out of the covered set and require it.
    monkeypatch.setattr(
        features,
        "CONSENT_COVERED_FEATURES",
        tuple(f for f in features.CONSENT_COVERED_FEATURES if f != bi_nlq.FEATURE),
    )
    assert bi_nlq.FEATURE not in features.CONSENT_COVERED_FEATURES, (
        "retire it deliberately rather than let it pass vacuously"
    )
    _write_consent_row(db_session)
    before = len(_log_rows(db_session))
    response = _ask(db_client, db_session)

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_ask_unavailable"
    assert detail["reason"] == "consent_not_covered"
    assert detail["message"] == gates.GATE_MESSAGES["consent_not_covered"]
    assert _jobs(db_session) == [], "a row cannot out-rank the consent document"
    assert _events(db_session, bi_nlq.EVENT_REQUESTED) == []
    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1
    assert rows[0].decision == "allowed"
    assert rows[0].member_ids == []


@pytest.mark.usefixtures("surfaces_on")
@pytest.mark.parametrize("path", ["withheld", "no_consent_row", "allowance_spent"])
def test_a_question_that_never_left_the_platform_names_no_member_in_the_log(  # noqa: PLR0913 - one fixture per lever
    db_client: TestClient,
    db_session: Session,
    plane: Bank,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    path: str,
) -> None:
    """Audit A360-5 M1. The row used to be written BEFORE the screen, the gate and the
    quota, ``allowed`` over the whole candidate list — so a question that was withheld
    left a row claiming a model had been shown 109 figures. ``surface='nlq'`` exists so
    "which reads came from a model" is answerable from this table; that needs the row
    to name what the model was GIVEN, which for these three paths is nothing. The row
    itself must still exist: losing the refusal would be worse."""

    question = QUESTION
    if path == "withheld":
        request.getfixturevalue("consented")
        question = f"gross loans for {plane.name}"
        expected = "question_withheld"
    elif path == "no_consent_row":
        request.getfixturevalue("question_surface_consentable")
        db_session.execute(delete(AiCommentarySettings))
        db_session.commit()
        expected = "tenant_disabled"
    else:
        request.getfixturevalue("consented")
        monkeypatch.setenv("AI_DAILY_REQUESTS_PER_ORG", "0")
        get_settings.cache_clear()
        expected = "org_requests"
    before = len(_log_rows(db_session))
    response = _ask(db_client, db_session, question=question)
    get_settings.cache_clear()

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == expected
    assert _jobs(db_session) == []
    assert _events(db_session, bi_nlq.EVENT_REQUESTED) == []
    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1, "a refused question is still logged"
    assert rows[0].surface == "nlq"
    # The AUTHORIZATION decision: the reader's grants admitted the question.
    assert rows[0].decision == "allowed"
    # What the model was given: nothing. This is the whole fix.
    assert rows[0].member_ids == []
    assert rows[0].row_count is None


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_spent_daily_allowance_is_an_understandable_refusal(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_DAILY_REQUESTS_PER_ORG", "0")
    get_settings.cache_clear()
    response = _ask(db_client, db_session)
    get_settings.cache_clear()

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["reason"] == "org_requests"
    assert "allowance" in detail["message"]
    assert _jobs(db_session) == []


# --- reading it back: shown for confirmation ----------------------------------------------


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_proposal_is_shown_as_a_sentence_and_still_runs_nothing(
    db_client: TestClient, db_session: Session
) -> None:
    before = len(_log_rows(db_session))
    _job_id, read = _proposed(db_client, db_session)
    body = read.json()

    assert body["state"] == "proposed"
    assert body["query"]["measures"] == [MEASURE]
    assert body["query"]["time"]["as_of"] == AS_OF.isoformat()
    reading = body["reading"]
    # The sentence is the platform's own labels, not the model's words and not ids.
    assert catalogue().member(MEASURE).label in reading["sentence"]
    assert catalogue().member(DIMENSION).label in reading["sentence"]
    assert MEASURE not in reading["sentence"]
    assert AS_OF.isoformat() in reading["sentence"]
    kinds = [clause["kind"] for clause in reading["clauses"]]
    assert kinds[:3] == ["figure", "grouping", "period"]
    assert "Check this is the question you meant" in body["message"]
    # Reading a proposal is not a read of the mart: still one row, the question's.
    assert len(_log_rows(db_session)[before:]) == 1


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_colleague_cannot_read_another_reader_s_question(
    db_client: TestClient, db_session: Session
) -> None:
    job_id, _read = _proposed(db_client, db_session)
    _grant(db_session, COLLEAGUE)
    db_session.commit()
    response = _get(db_client, db_session, job_id, user_id=COLLEAGUE)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Question not found."


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_the_refusals_for_an_absent_and_a_hidden_figure_are_byte_identical(
    db_client: TestClient, db_session: Session
) -> None:
    """The disclosure test at the surface. A reader must not be able to use this
    feature to discover that a figure they may not see exists."""

    hidden = next(
        member.id
        for member in catalogue().measures()
        if member.module == "cap" and member.sensitivity == "aggregated"
    )
    first_id, _ = _proposed(db_client, db_session, draft=_draft([hidden]))
    second_id, _ = _proposed(db_client, db_session, draft=_draft(["no.such.figure.at.all"]))

    first = _get(db_client, db_session, first_id).json()
    second = _get(db_client, db_session, second_id).json()
    assert first["state"] == second["state"] == "refused"
    assert first["message"] == second["message"]
    assert first["suggestions"] == second["suggestions"] == []
    for body in (first, second):
        assert hidden not in str(body)
        assert "no.such.figure.at.all" not in str(body)


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_model_that_could_not_be_reached_stops_rather_than_spinning(
    db_client: TestClient, db_session: Session
) -> None:
    asked = _ask(db_client, db_session)
    job_id = asked.json()["question_id"]
    _translate(db_session, job_id, None, outcome="failed", failure_code="timeout")
    body = _get(db_client, db_session, job_id).json()

    assert body["state"] == "stopped"
    assert body["message"] == bi_nlq.MODEL_FAILURE_MESSAGES["failed"]
    assert body["query"] is None


# --- confirmation is enforced by the server ------------------------------------------------


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_confirmed_proposal_runs_and_writes_exactly_one_more_log_row(
    db_client: TestClient, db_session: Session
) -> None:
    job_id, read = _proposed(db_client, db_session)
    before = len(_log_rows(db_session))
    response = _run(db_client, db_session, job_id, read.json()["query"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["catalogue_version"] == CATALOGUE_VERSION
    assert body["columns"], "a confirmed question returns the read it described"

    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1, "the read's own row, and only that"
    # Audit A360-5 L7: the read a model proposed says so. ``202609280077`` added the
    # value to answer "which reads came from a model proposing a query", and under
    # ``query`` this row was indistinguishable from a hand-built one.
    assert rows[0].surface == "nlq"
    assert rows[0].decision == "allowed"
    assert rows[0].row_count is not None
    assert MEASURE in rows[0].member_ids
    assert len(_events(db_session, bi_nlq.EVENT_CONFIRMED)) == 1

    # The SAME query built by hand is logged as ``query``: the surface alone tells
    # the two apart, with no join to ``audit_events``.
    by_hand = db_client.post(
        f"{BASE}/query", json=read.json()["query"], headers=_as(db_session, READER)
    )
    assert by_hand.status_code == 200, by_hand.text
    assert _log_rows(db_session)[-1].surface == "query"
    assert _log_rows(db_session)[-1].query_hash == rows[0].query_hash, (
        "one question, two surfaces: the hash says same query, the surface says who wrote it"
    )


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_an_unconfirmed_query_does_not_execute(db_client: TestClient, db_session: Session) -> None:
    """The whole point of the confirmation step: a query the reader did not agree to
    is refused, and refusing it reads nothing."""

    job_id, read = _proposed(db_client, db_session)
    edited = {**read.json()["query"], "dimensions": []}
    before = len(_log_rows(db_session))
    response = _run(db_client, db_session, job_id, edited)

    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_ask_not_the_proposed_query"
    assert _log_rows(db_session)[before:] == [], "a refused confirmation reads nothing"


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_question_with_no_proposal_yet_cannot_be_run(
    db_client: TestClient, db_session: Session
) -> None:
    asked = _ask(db_client, db_session)
    job_id = asked.json()["question_id"]
    before = len(_log_rows(db_session))
    response = _run(
        db_client,
        db_session,
        job_id,
        {"measures": [MEASURE], "time": {"as_of": AS_OF.isoformat()}},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "not_proposed"

    assert _log_rows(db_session)[before:] == []


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_proposal_worked_out_under_an_older_catalogue_cannot_be_run(
    db_client: TestClient, db_session: Session
) -> None:
    """The figures were redefined between the proposal and the confirmation, so the
    sentence the reader confirmed may no longer mean what it said."""

    job_id, read = _proposed(db_client, db_session)
    job = db_session.get(Job, UUID(job_id))
    assert job is not None
    job.payload = {**job.payload, "catalogue_version": "0.0.1"}
    db_session.flush()
    response = _run(db_client, db_session, job_id, read.json()["query"])

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "catalogue_changed"


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_proposal_naming_a_member_the_reader_may_not_see_is_refused_at_run_time(
    db_client: TestClient, db_session: Session
) -> None:
    """THE property: the model's output is a request, not an authority.

    The proposal is written straight onto the queue row, bypassing the offered-set
    check entirely, so this is the second line of defence on its own: the confirmed
    query goes through ``authorize_query`` like any hand-built one, and a reader whose
    grants do not cover it is refused with no row served.
    """

    asked = _ask(db_client, db_session)
    job_id = asked.json()["question_id"]
    # A restricted GROUPING, which is how a question reaches an obligor by name. The
    # reader below holds ALL/AGGREGATED, and sensitivity is exact rather than a ladder.
    restricted = next(
        member.id
        for member in catalogue().dimensions()
        if member.sensitivity == "restricted"
        and member.id in catalogue().measure(MEASURE).allowed_dimensions
    )
    job = db_session.get(Job, UUID(job_id))
    assert job is not None
    smuggled = {
        "measures": [MEASURE],
        "dimensions": [restricted],
        "time": {"as_of": AS_OF.isoformat()},
    }
    job.progress = {"status": bi_nlq.STATUS_PROPOSED, "query": smuggled}
    db_session.flush()
    # The reader holds VIEWER over all/all, which does not carry a confidential
    # sentence — the drill surface is where a record-level read belongs.
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    _grant(db_session, READER, module=ModuleScope.ALL, sensitivity=SensitivityScope.AGGREGATED)
    db_session.commit()
    before = len(_log_rows(db_session))
    response = _run(db_client, db_session, job_id, smuggled)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_authorization_denied"
    rows = _log_rows(db_session)[before:]
    assert len(rows) == 1
    assert rows[0].decision == "denied"
    assert rows[0].row_count is None


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_the_question_switch_pulled_after_a_proposal_stops_reading_and_running_it(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A360-5 L3. ``BI_NLQ_ENABLED=0`` stopped new questions and nothing else:
    an existing proposal could still be read back and CONFIRMED, writing
    ``bi.ask.confirmed`` events during the very vendor incident the flag was pulled
    for. A kill switch stops the feature, all three routes of it."""

    job_id, read = _proposed(db_client, db_session)
    before = len(_log_rows(db_session))
    monkeypatch.setenv("BI_NLQ_ENABLED", "0")
    get_settings.cache_clear()
    shown = _get(db_client, db_session, job_id)
    ran = _run(db_client, db_session, job_id, read.json()["query"])
    get_settings.cache_clear()

    for response in (shown, ran):
        assert response.status_code == 409, response.text
        detail = response.json()["error"]["details"]
        assert detail["error_code"] == "bi_ask_unavailable"
        assert detail["reason"] == "nlq_disabled"
    assert MEASURE not in shown.text, "a switched-off surface shows no proposal either"
    assert _log_rows(db_session)[before:] == []
    assert _events(db_session, bi_nlq.EVENT_CONFIRMED) == []


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_confirmation_is_spent_by_running_it_once(
    db_client: TestClient, db_session: Session
) -> None:
    """Audit A360-5 L4. Three identical runs were 3×200 and three ``bi.ask.confirmed``
    events for a question confirmed once, and the id was a permanent re-runnable
    handle. Each run re-authorizes anyway, so this is about the audit trail meaning
    what it says — and about a replayed request reading nothing."""

    job_id, read = _proposed(db_client, db_session)
    query = read.json()["query"]
    first = _run(db_client, db_session, job_id, query)
    assert first.status_code == 200, first.text
    before = len(_log_rows(db_session))

    for _attempt in range(2):
        again = _run(db_client, db_session, job_id, query)
        assert again.status_code == 409, again.text
        detail = again.json()["error"]["details"]
        assert detail["error_code"] == "bi_ask_unavailable"
        assert detail["reason"] == "already_run"
        assert "already been run" in detail["message"]
    assert _log_rows(db_session)[before:] == [], "a refused replay reads nothing"
    assert len(_events(db_session, bi_nlq.EVENT_CONFIRMED)) == 1

    shown = _get(db_client, db_session, job_id).json()
    assert shown["state"] == "stopped", "nothing left to confirm"
    assert shown["query"] is None
    assert "already been run" in shown["message"]


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_proposal_nobody_ran_in_time_can_no_longer_be_confirmed(
    db_client: TestClient, db_session: Session
) -> None:
    """Audit A360-5 L4, the other half. ``is_expired`` was consulted only for a row
    still translating, so a ``proposed`` row 400 days old still read ``proposed`` and
    still ran. A proposal gets the hour the queue gives the request, from when it was
    written — and the read route and the run route agree it is over."""

    job_id, read = _proposed(db_client, db_session)
    job = db_session.get(Job, UUID(job_id))
    assert job is not None
    long_ago = utc_now() - dt.timedelta(days=400)
    job.progress = {**job.progress, bi_nlq.PROGRESS_PROPOSED_AT: long_ago.isoformat()}
    job.queued_at = long_ago
    db_session.flush()

    shown = _get(db_client, db_session, job_id).json()
    assert shown["state"] == "stopped"
    assert shown["query"] is None
    assert "waited too long" in shown["message"]

    before = len(_log_rows(db_session))
    ran = _run(db_client, db_session, job_id, read.json()["query"])
    assert ran.status_code == 409, ran.text
    assert ran.json()["error"]["details"]["reason"] == "proposal_expired"
    assert _log_rows(db_session)[before:] == []
    assert _events(db_session, bi_nlq.EVENT_CONFIRMED) == []


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
@pytest.mark.parametrize(
    "reported",
    [
        pytest.param(bi_nlq.STATUS_REFUSED, id="refused"),
        pytest.param(bi_nlq.STATUS_CANCELLED, id="cancelled"),
        pytest.param(bi_nlq.STATUS_FAILED, id="failed"),
        pytest.param(bi_nlq.STATUS_RATE_LIMITED, id="rate_limited"),
        pytest.param(None, id="translating"),
    ],
)
def test_a_record_that_is_not_a_proposal_does_not_run_whatever_it_carries(
    db_client: TestClient, db_session: Session, reported: str | None
) -> None:
    """Audit A360-5 L5. The run route read ``progress["query"]`` unconditionally while
    the read route surfaced it only for ``status == "proposed"``, so a record shown as
    refused on GET RAN on POST. Reachable only by a direct write today; the point is
    that two routes may not disagree about what a proposal is."""

    asked = _ask(db_client, db_session)
    job_id = asked.json()["question_id"]
    job = db_session.get(Job, UUID(job_id))
    assert job is not None
    carried = {
        "measures": [MEASURE],
        "dimensions": [DIMENSION],
        "time": {"as_of": AS_OF.isoformat()},
    }
    progress: dict[str, Any] = {"message": "Nothing to see here.", "query": carried}
    if reported is not None:
        progress["status"] = reported
    job.progress = progress
    db_session.flush()

    shown = _get(db_client, db_session, job_id).json()
    assert shown["state"] != "proposed"
    assert shown["query"] is None
    before = len(_log_rows(db_session))
    ran = _run(db_client, db_session, job_id, carried)
    assert ran.status_code == 409, ran.text
    assert ran.json()["error"]["details"]["reason"] == "not_proposed"
    assert _log_rows(db_session)[before:] == []
    assert _events(db_session, bi_nlq.EVENT_CONFIRMED) == []


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_watching_your_own_question_is_not_metered_but_running_it_is(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A360-5 L6, the decision written down. ``GET /ask/{id}`` is an own-row
    lookup with no egress and no mart read, and the dashboard polls it every two
    seconds while a question is worked out — metering it would let a reader exhaust
    their own budget by waiting. So it answers under an exhausted budget and writes no
    row; the RUN is a real read and is refused like any other. A pin, not a fix: the
    code already did this and the module docstring did not say so."""

    job_id, read = _proposed(db_client, db_session)  # the question's own row: one read used
    before = len(_log_rows(db_session))
    monkeypatch.setenv("BI_RATE_LIMIT_MAX_QUERIES", "1")
    get_settings.cache_clear()
    shown = _get(db_client, db_session, job_id)
    ran = _run(db_client, db_session, job_id, read.json()["query"])
    get_settings.cache_clear()

    assert shown.status_code == 200, shown.text
    assert shown.json()["state"] == "proposed"
    assert ran.status_code == 429, ran.text
    assert ran.json()["error"]["details"]["error_code"] == "bi_rate_limited"
    assert _log_rows(db_session)[before:] == [], (
        "neither wrote a row: GET by design, the run because the budget refuses first"
    )


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_a_question_id_that_is_not_a_question_is_404(
    db_client: TestClient, db_session: Session
) -> None:
    unknown = str(uuid4())
    assert _get(db_client, db_session, unknown).status_code == 404
    assert (
        _run(
            db_client,
            db_session,
            unknown,
            {"measures": [MEASURE], "time": {"as_of": AS_OF.isoformat()}},
        ).status_code
        == 404
    )


@pytest.mark.usefixtures("surfaces_on", "consented", "plane")
def test_the_question_surface_writes_no_prose_the_model_produced(
    db_client: TestClient, db_session: Session
) -> None:
    """Belt and braces on the injection boundary: whatever the model returns, every
    reader-facing string is one of the platform's own."""

    job_id, read = _proposed(
        db_client,
        db_session,
        draft=NlqDraft(
            answerable=False,
            unanswerable_reason="ambiguous",
            suggested_members=[MEASURE],
        ),
    )
    body = read.json()
    assert body["state"] == "refused"
    assert body["message"] == nlq.UNANSWERABLE_MESSAGES["ambiguous"]
    assert body["suggestions"] == [
        {"member_id": MEASURE, "label": catalogue().member(MEASURE).label}
    ]
    assert job_id
