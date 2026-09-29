"""Ask the platform a question in words, see what it understood, then run it.

``docs/bi.md`` §Phase 5: "the model emits a ``BiQuery`` restricted to the members the
user can see, never SQL. It's shown to the user for confirmation and logged." Three
routes, and each clause of that sentence is a property of one of them.

**`POST /banks/{bank_id}/bi/ask` — restricted to the members the user can see.**
The catalogue handed to the model is the CALLER'S authorized catalogue: the same
``visible_pairs`` pair probe ``GET /bi/catalogue`` serves them, narrowed to the
figures this question retrieved. Nothing here widens it, there is no privileged
session, and the id set that was offered is frozen into the queue row so the worker
that reads the model's answer cannot widen it either. A reader whose grants cover no
figure at all is refused before any model call, because there is no question they
could ask.

**`GET /banks/{bank_id}/bi/ask/{job_id}` — shown for confirmation.** It returns the
proposal and the READING of it, and it runs nothing. 404 for anybody but the
principal who asked: a question is translated over the members THAT reader may see,
so serving it to a colleague with narrower grants would disclose exactly what their
grants withhold (the ``bi_export`` rule, for the same reason).

**`POST /banks/{bank_id}/bi/ask/{job_id}/run` — the model's output is a request, not
an authority.** The caller echoes the query back and the server refuses unless its
digest equals the proposal's, which is what makes confirmation a server-side property
rather than a client's promise. Then the query goes through ``read_bi.authorize`` and
``read_bi.run_query`` — the same pipeline, including ``authorize_query``, that a
hand-built ``POST /bi/query`` takes. There is no path here where "the model chose it"
substitutes for a decision.

**Logged.** ``POST /bi/ask`` writes exactly ONE ``bi_query_log`` row per question,
before any of the AI machinery is consulted: ``allowed`` with the candidate ids the
model was given, or ``denied`` when the reader's authority admits nothing.
``row_count`` is NULL because no row was served, which is what that column already
means. Confirming adds exactly one more row — the READ's own row, written by the
shared pipeline, indistinguishable from a hand-built query's because it is one.

**Why the log row says ``catalogue``.** ``bi_query_log.surface`` admits ten values
and ``nlq`` is not one of them (``app/models/bi.py::QUERY_LOG_SURFACES``, a CHECK in
the database, and a migration is not this track's to write). The read these routes
perform IS a catalogue read — the identical pair probe over the identical members —
so it is recorded as one rather than as nothing, and the read budget counts it like
any other. ``query_hash`` is what distinguishes it: :func:`_digest` hashes the ACTION
beside the question's own digest, so asking a question and fetching the catalogue do
not hash alike, and the column still holds nothing that can be read back. This is the
decision ``app/features/manage_bi_commentary.py`` already records for its ``insights``
rows.

**A refusal never names a member the caller was not entitled to know exists.** Three
different internal facts — an id the catalogue does not hold, an id this reader's
grants hide, and an id this reader could see but which this question was not offered —
produce one refusal with one message and no member list. The ids are on the queue row
for a reviewer and never in a response.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import get_settings
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.features import read_bi
from app.features.read_bi import BiRead, BiReadAccess
from app.jobs import bi_nlq
from app.models import Job
from app.schemas.bi import BiQuery, BiQueryResult
from app.schemas.bi_nlq import (
    BiAskClauseRead,
    BiAskRead,
    BiAskReadingRead,
    BiAskRequest,
    BiAskRunRequest,
    BiAskState,
    BiAskSuggestionRead,
)
from app.schemas.common import ErrorResponse
from app.services import audit, institution_types
from app.services.bi import nlq, query_log

router = APIRouter(tags=["bi"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}

_PATH = "/banks/{bank_id}/bi/ask"
_ONE = "/banks/{bank_id}/bi/ask/{job_id}"

#: The surface the question's log row is written under, and the ACTION that
#: distinguishes it inside the digest. See the module docstring.
#:
#: It was ``catalogue`` while the log's own vocabulary had no honest value for a
#: question — defensible, because the read this performs genuinely IS a catalogue
#: read. Migration ``202609280077`` added ``nlq``, so it is now its own value: the
#: first question an auditor asks about a model-assisted surface is which reads
#: came from a model proposing a query rather than a person composing one, and
#: under ``catalogue`` that question had no answer.
_SURFACE = "nlq"
_ACTION_ASK = "ask.question"

_DIGEST_SEPARATOR = "\x1f"

#: What the reader is told in each state. Production copy, never a code.
_STATE_MESSAGES: dict[BiAskState, str] = {
    "translating": "Working out what you asked for. This usually takes a few seconds.",
    "proposed": "Check this is the question you meant, then run it.",
    "refused": "",
    "stopped": "",
}

_EXPIRED_MESSAGE = (
    "That question waited too long and was cancelled. Nothing was sent and nothing "
    "was read. Ask it again."
)

#: Every reason the platform itself declined to send a question, as ONE refusal the
#: reader can act on. A 409 rather than a 403: nothing about the reader's authority is
#: wrong — the platform is not in a state where it can ask.
_NOT_AVAILABLE_CODE = "bi_ask_unavailable"

#: The reader holds no figure at all, so there is no question to ask. The same 403
#: shape a denied query takes, without naming a member.
_NO_FIGURES_CODE = "bi_ask_no_figures_available"
_NO_FIGURES_MESSAGE = (
    "Your access does not yet cover any figure this can answer questions about. An Org "
    "Owner can grant the modules you need."
)


def _digest(*parts: object) -> str:
    """A one-way digest of the ACTION and the question. Holds no readable value."""

    material = _DIGEST_SEPARATOR.join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record(
    db: Session,
    access: BiReadAccess,
    *,
    question_digest: str,
    decision: str,
    member_ids: tuple[str, ...],
) -> None:
    """The ONE log row this question writes. Served nothing, so ``row_count`` is NULL."""

    read_bi.append_query_log(
        db,
        query_log.QueryRecord(
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            principal_user_id=access.principal_user_id,
            surface=_SURFACE,
            query_hash=question_digest,
            decision=decision,
            catalogue_version=CATALOGUE_VERSION,
            member_ids=member_ids,
        ),
    )


def _unavailable(message: str, *, reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": _NOT_AVAILABLE_CODE, "message": message, "reason": reason},
    )


def _suggestions(member_ids: tuple[str, ...]) -> list[BiAskSuggestionRead]:
    """Suggested members, with the PLATFORM's label for each id the model named."""

    return [
        BiAskSuggestionRead(member_id=member_id, label=label)
        for member_id, label in nlq.measure_labels(catalogue(), member_ids)
    ]


def _reading(query: BiQuery) -> BiAskReadingRead:
    built = nlq.build_reading(catalogue(), query)
    return BiAskReadingRead(
        sentence=built.sentence,
        clauses=[BiAskClauseRead(kind=clause.kind, text=clause.text) for clause in built.clauses],
        as_of=built.as_of,
        range_start=built.range_start,
        range_end=built.range_end,
        compare_to=built.compare_to,
        member_ids=list(built.member_ids),
    )


def _state(job: Job) -> BiAskState:
    """Where the question has got to, read off the queue row and its progress."""

    reported = str((job.progress or {}).get("status") or "")
    if reported == bi_nlq.STATUS_PROPOSED:
        return "proposed"
    if reported == bi_nlq.STATUS_REFUSED:
        return "refused"
    if reported in bi_nlq.TERMINAL_STATUSES:
        return "stopped"
    if job.status in {"succeeded", "failed"}:
        # Finished with nothing recorded: a handler that never ran (a worker that does
        # not claim the ``ai`` lane) or one that exhausted its attempts. Say stopped
        # rather than leave the reader polling a row that has finished.
        return "stopped"
    if bi_nlq.is_expired(job):
        return "stopped"
    return "translating"


def _proposed_query(job: Job) -> BiQuery | None:
    """The proposal as a ``BiQuery``, or ``None`` if this row carries no proposal.

    Re-validated on the way out rather than trusted: the stored form is JSON in a
    queue row, and a query the platform is about to SHOW as its own understanding has
    to be one it would accept.
    """

    raw = (job.progress or {}).get("query")
    if not isinstance(raw, dict):
        return None
    try:
        return BiQuery.model_validate(raw)
    except ValueError:  # pragma: no cover - the handler writes a validated query
        return None


def _read(job: Job, *, figures_offered: int) -> BiAskRead:
    """One question as the reader sees it. Runs nothing."""

    payload = job.payload or {}
    progress = job.progress or {}
    state = _state(job)
    query = _proposed_query(job) if state == "proposed" else None
    if state == "proposed" and query is None:  # pragma: no cover - defensive
        state = "stopped"
    message = str(progress.get("message") or "") or _STATE_MESSAGES[state]
    if state == "translating" and bi_nlq.is_expired(job):  # pragma: no cover - time-dependent
        message = _EXPIRED_MESSAGE
    if state == "stopped" and not message:
        message = _EXPIRED_MESSAGE
    question = str((payload.get("payload") or {}).get("question") or "")
    return BiAskRead(
        question_id=job.id,
        state=state,
        message=message,
        question=question,
        as_of=payload["as_of"],
        catalogue_version=str(payload.get("catalogue_version") or CATALOGUE_VERSION),
        figures_offered=figures_offered,
        query=query,
        reading=None if query is None else _reading(query),
        suggestions=_suggestions(
            tuple(str(value) for value in (progress.get("suggested_members") or []))
        ),
    )


def _offered_count(job: Job) -> int:
    return len((job.payload or {}).get("offered_member_ids") or [])


# --- routes -----------------------------------------------------------------------------


@router.post(
    _PATH,
    response_model=BiAskRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="askBiQuestion",
    responses=_ERRORS,
)
def ask_bi_question(
    bank_id: str,
    request: BiAskRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
) -> BiAskRead:
    """Ask one question in words. Proposes a query; executes nothing."""

    _ = bank_id  # resolved by the router's dependency
    settings = get_settings()
    if not settings.bi.nlq_enabled:
        # A deployment with BI on but this surface off. Not a 404: every BI path is
        # 404 when BI itself is off, and answering 404 here too would make a
        # configured surface indistinguishable from an absent one for the ONE flag a
        # tenant's Org Owner may be waiting on.
        raise _unavailable(
            "Asking questions in words is not switched on for this platform.",
            reason="nlq_disabled",
        )
    cat = catalogue()
    read_bi.require_budget(db, access)

    # The caller's OWN catalogue, through the query path's own decision function.
    visibility = read_bi.visible_pairs(db, access, cat, surface=_SURFACE)
    visible = [member for member in cat.members() if read_bi.visible_member(member, visibility)]
    institution_type = institution_types.get_type(db, access.bank)
    candidates = nlq.select_candidates(
        cat,
        visible,
        question=request.question,
        institution_class=institution_type.institution_class,
        capital_regime=institution_type.capital_regime,
    )
    question_digest = _digest(_ACTION_ASK, _digest(request.question), request.as_of)
    if candidates.empty:
        _record(
            db,
            access,
            question_digest=question_digest,
            decision=query_log.DECISION_DENIED,
            member_ids=(),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": _NO_FIGURES_CODE, "message": _NO_FIGURES_MESSAGE},
        )
    # The one row for this question, written BEFORE anything leaves the platform and
    # naming exactly what the model may name.
    _record(
        db,
        access,
        question_digest=question_digest,
        decision=query_log.DECISION_ALLOWED,
        member_ids=candidates.member_ids,
    )

    try:
        question = nlq.screen_question(
            db,
            organization_id=access.ctx.organization_id,
            bank=access.bank,
            question=request.question,
        )
    except nlq.QuestionWithheld as withheld:
        raise _unavailable(withheld.message, reason=withheld.code) from withheld

    built = nlq.build_payload(candidates, question=question)
    outcome = bi_nlq.request_translation(
        db,
        ctx=access.ctx,
        bank=access.bank,
        requested_by=access.principal_user_id,
        as_of=request.as_of,
        built=built,
        catalogue_version=CATALOGUE_VERSION,
    )
    if outcome.job is None:
        raise _unavailable(outcome.message, reason=outcome.reason or "unavailable")
    response.headers["Cache-Control"] = "no-store"
    return _read(outcome.job, figures_offered=len(built.offered_member_ids))


@router.get(
    _ONE,
    response_model=BiAskRead,
    operation_id="getBiQuestion",
    responses=_ERRORS,
)
def get_bi_question(
    bank_id: str,
    job_id: UUID,
    db: DbSession,
    access: BiRead,
    response: Response,
) -> BiAskRead:
    """One of your OWN questions, and whatever the platform proposes for it."""

    _ = bank_id
    job = bi_nlq.find_question(
        db,
        job_id=job_id,
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    response.headers["Cache-Control"] = "no-store"
    return _read(job, figures_offered=_offered_count(job))


@router.post(
    f"{_ONE}/run",
    response_model=BiQueryResult,
    operation_id="runBiQuestion",
    responses=_ERRORS,
)
def run_bi_question(  # noqa: PLR0913 - FastAPI injects db/access/response
    bank_id: str,
    job_id: UUID,
    request: BiAskRunRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
) -> BiQueryResult:
    """Run a proposal the reader has confirmed, under the ordinary read authority."""

    _ = bank_id
    job = bi_nlq.find_question(
        db,
        job_id=job_id,
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    proposed = _proposed_query(job)
    if proposed is None:
        raise _unavailable(
            "There is no question to run here yet. Ask again, or wait for this one to finish.",
            reason="not_proposed",
        )
    if str((job.payload or {}).get("catalogue_version") or "") != CATALOGUE_VERSION:
        # The figures were redefined between the proposal and the confirmation, so the
        # sentence the reader confirmed may no longer mean what it said.
        raise _unavailable(
            "The figures available changed since that question was worked out. Ask it again.",
            reason="catalogue_changed",
        )
    if query_log.query_digest(request.query) != query_log.query_digest(proposed):
        # THE confirmation. The reader may only run the query they were shown; a
        # different one is a hand-built query and belongs on ``POST /bi/query``, where
        # it is authorized and logged in its own right.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "bi_ask_not_the_proposed_query",
                "message": (
                    "That is not the question the platform proposed, so it was not run. "
                    "Ask again and confirm what you are shown."
                ),
            },
        )

    settings = get_settings().bi
    # From here it is an ordinary BI read: the same budget, the same
    # ``authorize_query``, the same log row, the same caps. Nothing about the query
    # having been written by a model changes what it is allowed to return.
    authorized = read_bi.authorize(
        db, access, proposed, surface=read_bi.SURFACE_QUERY, if_none_match=None
    )
    _, result = read_bi.run_query(
        db,
        access,
        proposed,
        authorized,
        row_cap=settings.ui_row_cap,
        timeout_ms=settings.interactive_timeout_ms,
    )
    audit.record_event(
        db,
        access.ctx,
        event_type=bi_nlq.EVENT_CONFIRMED,
        entity_type=bi_nlq.ENTITY_TYPE,
        entity_id=job.id,
        details={
            "bank_id": access.bank.id,
            "as_of": str((job.payload or {}).get("as_of") or ""),
            "catalogue_version": CATALOGUE_VERSION,
            "query_hash": authorized.record.query_hash,
            "members": list(authorized.decision.member_ids),
            "row_count": len(result.rows),
        },
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return BiQueryResult(
        columns=read_bi.result_columns(result.columns),
        rows=[list(row) for row in result.rows],
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
        used_aggregate=result.used_aggregate,
        trust=read_bi.trust_badge(
            db, access.ctx.organization_id, access.bank.id, authorized.window
        ),
        catalogue_version=CATALOGUE_VERSION,
        build_fingerprint=authorized.build_fingerprint,
        data_scope=read_bi.scope_read(authorized.scope),
    )


__all__ = ["ask_bi_question", "get_bi_question", "router", "run_bi_question"]
