"""AI commentary: ask the platform to say in words what the figures show.

Two routes under ``/banks/{bank_id}/bi/commentary`` — a POST that asks for one
draft and a GET that reads back whatever is on offer — and both exist to make one
feature reachable that was, until this module, complete and inert: the job type,
the exclusive ``ai`` lane, the handler, the row, the payload minimiser, the
prompt, the grounding validator and the deterministic fallback were all built and
tested, and nothing in ``app/`` called
:func:`app.jobs.bi_commentary.request_commentary`. That is the defect
``tests/architecture/test_job_enqueue_reachability.py`` names, and a surface is
the only honest way to clear it.

Four properties decide the shape of this module, and each of them is a rule
somewhere else that this module must not restate differently.

**Commentary inherits the read's authority; it never invents its own.** Both
routes assemble the reader's insights through the SAME call the ``/bi/insights``
route makes (``app.services.bi.insights.assemble``), which authorizes every
candidate measure through ``authorize_query`` and drops the ones this reader may
not see. The sheet handed to ``request_commentary`` therefore holds only facts
this reader could have queried interactively, which is what makes it safe to send
a minimised form of it to a model. Nothing here widens that: there is no second
assembly, no privileged session and no "read it as the institution" path. And a
reader whose access covers none of the headline figures is refused exactly as the
strip refuses them, because the platform's own commentary is still a set of
statements about the bank's figures.

**A draft is one reader's.** :func:`~app.jobs.bi_commentary.latest_draft` is
scoped to the requester, and this module never widens that lookup: a colleague
polling the same institution and date sees their OWN request or none at all.
Serving one reader's draft to another would disclose precisely what the second
reader's grants withheld.

**A refusal is an answer.** A shut gate, a spent daily cap, a sheet with nothing
quotable and a draft that failed grounding all produce COMMENTARY — the
platform's own, composed from the same insight statements the strip shows — and
``author`` says whose words the reader is looking at. So none of those paths is
an HTTP error here: an error would render as an empty panel, and an empty panel
where a refusal belongs is the outcome the whole fallback exists to prevent. The
refusals this module DOES raise are about the read, not the commentary: the read
budget (429), a comparison date that is not earlier (422), and a reader whose
access covers nothing (403).

**The route agrees with the handler rather than deciding again.** ``BI_ENABLED``
is the router's dependency (404, like every BI route) and is re-read by the
handler, which cancels a queued draft with ``bi_disabled`` if the switch moved;
the AI gates and the daily cap are evaluated inside ``request_commentary`` and
this module only renders the decision it returns. The one thing evaluated here
that is not acted on here is ``can_request_draft``, which previews the same gate
and quota functions so a surface can present an honest control — the request
route re-decides, and it is the only thing that may.

**Two more, smaller, easy to lose.** Staleness is reported and never silently
corrected: a model paragraph describes the book it was given, so when the current
sheet's digest differs the reader is told. And an expired queued request stops
being polled — ``is_expired`` answers the question the run gate will answer later,
so the surface says "stopped" now instead of spinning until the worker says it.

**Why the log row says ``insights``.** ``bi_query_log.surface`` admits ten values
and ``commentary`` is not one of them (``app/models/bi.py::QUERY_LOG_SURFACES``,
a CHECK in the database). The read these routes perform IS an insights read — the
same assembly, the same members, the same compiled statements — so it is recorded
as one rather than as nothing, and the read budget counts it like any other.
``query_hash`` is what distinguishes it: :func:`_digest` hashes the ACTION beside
the two dates, so a commentary read and a strip read of the same date do not hash
alike, and the column still holds no value that can be read back.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import get_settings
from app.domain.bi.catalogue import CATALOGUE_VERSION, catalogue
from app.features import read_bi
from app.features.read_bi import BiRead, BiReadAccess
from app.jobs import bi_commentary
from app.models.bi_commentary import AiCommentaryDraft
from app.schemas.bi import BiTime, BiTrustBadge
from app.schemas.bi_commentary import (
    BiCommentaryAuthor,
    BiCommentaryParagraphRead,
    BiCommentaryRead,
    BiCommentaryRequest,
    BiCommentarySegmentRead,
    BiCommentaryState,
)
from app.schemas.common import ErrorResponse
from app.services.ai import gates, quota
from app.services.bi import commentary, query_log
from app.services.bi.commentary import MODEL_SOURCE
from app.services.bi.errors import BiQueryError
from app.services.bi.insights import AssembledInsights, assemble, default_compare_to

router = APIRouter(tags=["bi"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}

_PATH = "/banks/{bank_id}/bi/commentary"

#: What the ``query_hash`` distinguishes. The log row's ``surface`` has to be
#: ``insights`` (the table's CHECK admits no ``commentary`` value), so the action
#: rides in the digest instead of being lost.
_ACTION_REQUEST = "commentary.request"
_ACTION_READ = "commentary.read"

_DIGEST_SEPARATOR = "\x1f"

#: The two authors, as the wire spells them. ``MODEL_SOURCE`` and
#: ``FALLBACK_SOURCE`` are the commentary package's own constants; they are
#: re-stated as typed literals here so the response model cannot drift from them,
#: and :func:`test_the_author_vocabulary_matches_the_commentary_package` pins it.
AUTHOR_MODEL: BiCommentaryAuthor = "model"
AUTHOR_PLATFORM: BiCommentaryAuthor = "platform"

STATE_PENDING: BiCommentaryState = "pending"
STATE_READY: BiCommentaryState = "ready"

#: ``reason`` when a ``validated`` draft's prose cited a figure the frozen
#: bindings cannot resolve. ``build_view`` refuses the whole draft in that case
#: (half a commentary is worse than the platform's), and the reader is owed a
#: sentence saying so rather than silence.
REASON_UNRESOLVED = "unresolved_citation"

# --- production copy ---------------------------------------------------------------------
#
# Jurisdiction-neutral by construction: no currency, no regulator, no country.
# Nothing here shows a gate code, a status or a wire key to a reader; the codes
# travel in ``reason``, which the contract marks as telemetry.

_PLATFORM_TAIL = "The platform's own commentary is shown instead."

_NOT_REQUESTED = (
    "This is the platform's own commentary on the figures for this reporting date. "
    "Ask for a written draft to have the assistant summarise them."
)
_MODEL = (
    "The assistant wrote this commentary from the platform's own figures. Every figure and "
    "name in it is the platform's own, resolved when you opened it, and marked where it appears."
)
_PENDING = (
    "A written draft is being prepared. The platform's own commentary is shown until it arrives."
)
_UNUSABLE = f"The written draft could not be used, so none of it is shown. {_PLATFORM_TAIL}"
_STALE_TAIL = (
    " The figures for this reporting date have changed since this draft was prepared, "
    "so read it against the current ones."
)

#: Copy for the outcomes that are not a gate refusal. A gate code resolves through
#: ``gates.GATE_MESSAGES``, which is already production copy and already says what
#: an Organisation Owner can do about it.
_REASON_NOTICES: dict[str, str] = {
    "org_requests": (
        "Your organisation has reached its daily limit for written drafts, which resets at "
        f"midnight UTC. {_PLATFORM_TAIL}"
    ),
    "org_tokens": (
        "Your organisation has reached its daily limit for written drafts, which resets at "
        f"midnight UTC. {_PLATFORM_TAIL}"
    ),
    "user_requests": (
        "You have reached your own daily limit for written drafts, which resets at midnight "
        f"UTC. {_PLATFORM_TAIL}"
    ),
    "no_commentable_facts": (
        "There is not enough computed detail at this reporting date to draft commentary from. "
        f"{_PLATFORM_TAIL}"
    ),
    "commentary_already_running": (
        f"A draft of this reporting date is already being prepared. {_PLATFORM_TAIL}"
    ),
    REASON_UNRESOLVED: _UNUSABLE,
}


# --- refusals ----------------------------------------------------------------------------


def _budget(db: Session, access: BiReadAccess) -> None:
    """Meter this principal against the window every BI read is metered by.

    A polling surface is exactly the one that must be metered, and it is metered
    over ``bi_query_log`` rather than in-process because four uvicorn workers do
    not share a bucket.
    """

    budget = query_log.budget_for(
        db,
        organization_id=access.ctx.organization_id,
        principal_user_id=access.principal_user_id,
    )
    if budget.exceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": "bi_rate_limited",
                "message": (
                    "Too many analytics requests in the last minute. Wait a moment and try again."
                ),
            },
            headers={"Retry-After": str(budget.retry_after_seconds)},
        )


def _comparison_not_earlier() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error_code": "bi_commentary_comparison_not_earlier",
            "message": "The period compared against has to be earlier than the reporting date.",
        },
    )


def _authorization_denied() -> HTTPException:
    """403 naming NO measure, exactly as the insights strip refuses.

    The platform's own commentary is composed from the insight statements, so a
    reader shown none of the figures may not be handed a paragraph about them
    either — and the withheld member ids go to the append-only log, not to a
    reader who is not the operator writing the grant.
    """

    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "bi_commentary_authorization_denied",
            "message": (
                "Your access does not cover any of the figures this commentary would be "
                "built from, so none is written. An organization owner can grant them."
            ),
        },
    )


# --- the shared read ---------------------------------------------------------------------


def _digest(action: str, *parts: object) -> str:
    """A one-way digest of a read that is not a ``BiQuery``.

    Same material shape as the insights strip's, with the ACTION in front so a
    commentary read and a strip read of the same date do not hash alike. Nothing
    in it can be read back as a value.
    """

    material = _DIGEST_SEPARATOR.join(
        (action, *("" if part is None else str(part) for part in parts))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record(  # noqa: PLR0913 - one row, spelled out
    access: BiReadAccess,
    *,
    query_hash: str,
    decision: str,
    served: Sequence[str] = (),
    denied: Sequence[str] = (),
    row_count: int | None = None,
    duration_ms: int | None = None,
    build_fingerprint: str | None = None,
) -> query_log.QueryRecord:
    """This request's log row. ONE per request, whatever it took to answer."""

    return query_log.QueryRecord(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        principal_user_id=access.principal_user_id,
        surface=read_bi.SURFACE_INSIGHTS,
        query_hash=query_hash,
        decision=decision,
        catalogue_version=CATALOGUE_VERSION,
        member_ids=tuple(served),
        denied_members=tuple(denied),
        row_count=row_count,
        duration_ms=duration_ms,
        build_fingerprint=build_fingerprint,
    )


class _Read:
    """The reader's own insights for one date, plus the log row it produced.

    Not a dataclass: it carries the elapsed measurement and the log row alongside
    the assembly so neither route can answer without recording, and so both routes
    run the identical guarded steps in the identical order.
    """

    __slots__ = ("assembled", "duration_ms", "query_hash")

    def __init__(self, *, assembled: AssembledInsights, duration_ms: int, query_hash: str) -> None:
        self.assembled = assembled
        self.duration_ms = duration_ms
        self.query_hash = query_hash


def _read(
    db: Session, access: BiReadAccess, *, action: str, as_of: date, compare_to: date | None
) -> _Read:
    """Budget, assemble under this reader's authority, log, refuse. In that order.

    Every step is the insights route's own, reached through the public aliases or
    the assembler itself. The refusals below are about the READ; nothing here
    decides anything about commentary.
    """

    _budget(db, access)
    prior = compare_to or default_compare_to(as_of)
    query_hash = _digest(action, as_of.isoformat(), prior.isoformat())
    if prior >= as_of:
        read_bi.append_query_log(
            db, _record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED)
        )
        raise _comparison_not_earlier()

    settings = get_settings().bi
    started = perf_counter()
    try:
        assembled = assemble(
            db,
            ctx=access.ctx,
            bank=access.bank,
            cat=catalogue(),
            as_of=as_of,
            compare_to=prior,
            surface=read_bi.SURFACE_INSIGHTS,
            row_cap=settings.ui_row_cap,
            timeout_ms=settings.interactive_timeout_ms,
        )
    except BiQueryError as exc:
        read_bi.append_query_log(
            db, _record(access, query_hash=query_hash, decision=query_log.DECISION_DENIED)
        )
        raise read_bi.query_error(exc) from exc
    elapsed_ms = int((perf_counter() - started) * 1000)

    # Nothing read AND something withheld is a refused read. The row goes in
    # first: the read happened, the budget is counted over these rows, and a
    # probe loop must not be free.
    if assembled.measures_read == 0 and assembled.measures_withheld > 0:
        read_bi.append_query_log(
            db,
            _record(
                access,
                query_hash=query_hash,
                decision=query_log.DECISION_DENIED,
                denied=assembled.denied_members,
                duration_ms=elapsed_ms,
                build_fingerprint=assembled.build_fingerprint,
            ),
        )
        raise _authorization_denied()
    return _Read(assembled=assembled, duration_ms=elapsed_ms, query_hash=query_hash)


def _log_served(db: Session, access: BiReadAccess, read: _Read, *, paragraphs: int) -> None:
    """Record the served read. ``row_count`` is the paragraphs this answer carried."""

    read_bi.append_query_log(
        db,
        _record(
            access,
            query_hash=read.query_hash,
            decision=query_log.DECISION_ALLOWED,
            served=read.assembled.member_ids,
            denied=read.assembled.denied_members,
            row_count=paragraphs,
            duration_ms=read.duration_ms,
            build_fingerprint=read.assembled.build_fingerprint,
        ),
    )


# --- composing the answer ----------------------------------------------------------------


def _platform_paragraphs(
    access: BiReadAccess, assembled: AssembledInsights
) -> list[BiCommentaryParagraphRead]:
    """The platform's own commentary on the sheet that was JUST read.

    Deliberately recomposed rather than read off the draft row's stored copy. The
    stored copy is the worker's record of what was written at request time; a
    reader looking at the panel beside the insights strip must not be shown
    commentary about an older book than the strip is showing. Both come from the
    same fact sheet, so they cannot disagree.
    """

    return [
        BiCommentaryParagraphRead(
            segments=[BiCommentarySegmentRead(kind="text", text=text)], text=text
        )
        for text in commentary.deterministic_commentary(
            sheet=assembled.sheet,
            insight_set=assembled.insight_set,
            institution_name=access.bank.name,
            compare_to=assembled.compare_to,
        )
    ]


def _model_paragraphs(view: commentary.CommentaryView) -> list[BiCommentaryParagraphRead]:
    """The model's prose, with every figure and name resolved by the platform."""

    return [
        BiCommentaryParagraphRead(
            segments=[
                BiCommentarySegmentRead(
                    kind=segment.kind, text=segment.text, measure_id=segment.measure_id
                )
                for segment in paragraph.segments
            ],
            text=paragraph.text,
            measure_ids=list(paragraph.measure_ids),
        )
        for paragraph in view.paragraphs
    ]


def _notice(*, state: str, author: str, reason: str | None, requested: bool, stale: bool) -> str:
    """The sentence the reader is shown. Never a code, never an empty string."""

    if state == STATE_PENDING:
        text = _PENDING
    elif author == AUTHOR_MODEL:
        text = _MODEL
    elif reason is None:
        text = _UNUSABLE if requested else _NOT_REQUESTED
    elif reason in gates.GATE_MESSAGES:
        text = f"{gates.GATE_MESSAGES[reason]} {_PLATFORM_TAIL}"
    else:
        text = _REASON_NOTICES.get(reason, _UNUSABLE)
    return f"{text}{_STALE_TAIL}" if stale else text


def _terminal_reason(draft: AiCommentaryDraft, *, served_model_prose: bool) -> str | None:
    """Why this finished draft is not the reader's commentary — or ``None``.

    A ``validated`` row whose placeholders all resolved is the answer, so there is
    no reason. A ``validated`` row whose prose cited a figure the frozen bindings
    cannot resolve was refused WHOLE by ``build_view``, and that gets its own
    code: it is neither a vendor failure nor a governance withdrawal.
    """

    if served_model_prose:
        return None
    if draft.status == "validated":
        return REASON_UNRESOLVED
    return draft.failure_code or draft.status


def _answer(  # noqa: PLR0913 - one response, and every field is decided here
    db: Session,
    access: BiReadAccess,
    read: _Read,
    *,
    draft: AiCommentaryDraft | None,
    reason: str | None,
    can_request_draft: bool,
) -> BiCommentaryRead:
    """Turn one reader, one sheet and (maybe) one draft into the answer.

    The single place both routes compose from, so the POST and the GET cannot
    describe the same state two different ways.
    """

    assembled = read.assembled
    current_hash = bi_commentary.current_fact_sheet_hash(assembled)
    state: BiCommentaryState = STATE_READY
    author: BiCommentaryAuthor = AUTHOR_PLATFORM
    open_questions: list[str] = []
    paragraphs = _platform_paragraphs(access, assembled)
    stale = False
    poll_after: int | None = None

    if draft is not None:
        view = commentary.build_view(
            db, bank=access.bank, draft=draft, current_fact_sheet_hash=current_hash
        )
        stale = bool(view.stale)
        served_model_prose = view.source == MODEL_SOURCE
        if served_model_prose:
            author = AUTHOR_MODEL
            paragraphs = _model_paragraphs(view)
            open_questions = list(view.open_questions)
        if view.pending and bi_commentary.is_expired(draft):
            # The run gate will cancel this for age, so say so now rather than
            # letting a client poll until the worker gets to it.
            reason = "queue_expired"
        elif view.pending:
            state = STATE_PENDING
            poll_after = view.poll_after_seconds
            # Whatever the platform is showing meanwhile, the reader is waiting on
            # a model draft; the fallback is not the answer yet.
            reason = None
        else:
            reason = _terminal_reason(draft, served_model_prose=served_model_prose)

    _log_served(db, access, read, paragraphs=len(paragraphs))
    window = read_bi.data_window(BiTime(as_of=assembled.as_of, compare_to=assembled.compare_to))
    return BiCommentaryRead(
        as_of=assembled.as_of,
        compare_to=assembled.compare_to,
        state=state,
        author=author,
        notice=_notice(
            state=state, author=author, reason=reason, requested=draft is not None, stale=stale
        ),
        paragraphs=paragraphs,
        open_questions=open_questions,
        requested=draft is not None,
        draft_id=None if draft is None else draft.id,
        requested_at=None if draft is None else draft.created_at,
        completed_at=None if draft is None else draft.completed_at,
        stale=stale,
        fact_sheet_hash=current_hash,
        poll_after_seconds=poll_after,
        can_request_draft=can_request_draft,
        reason=reason,
        trust=_trust(db, access, window),
        catalogue_version=CATALOGUE_VERSION,
    )


def _trust(db: Session, access: BiReadAccess, window: tuple[date, date]) -> BiTrustBadge:
    return read_bi.trust_badge(db, access.ctx.organization_id, access.bank.id, window)


def _can_request(db: Session, access: BiReadAccess) -> bool:
    """Would the deployment, the tenant's AI settings and today's budget allow one?

    A PREVIEW of the same two functions ``request_commentary`` calls, so a surface
    can present an honest control. It is advisory and this module acts on it
    nowhere: the request route evaluates them again, inside the transaction that
    would queue the call, and that evaluation is the only one that decides.
    """

    settings = get_settings()
    gate = gates.evaluate(
        db,
        access.ctx.organization_id,
        bi_commentary.FEATURE,
        phase="enqueue",
        prompt_version=commentary.PROMPT_VERSION,
        settings=settings,
    )
    if not gate.allowed:
        return False
    allowance = quota.check(
        db, access.ctx.organization_id, access.principal_user_id, settings=settings
    )
    return allowance.allowed


# --- the routes --------------------------------------------------------------------------


@router.post(
    _PATH,
    response_model=BiCommentaryRead,
    operation_id="requestBiCommentary",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
)
def request_bi_commentary(  # noqa: PLR0913 - FastAPI injects db/access/response
    bank_id: str,
    payload: BiCommentaryRequest,
    db: DbSession,
    access: BiRead,
    response: Response,
) -> BiCommentaryRead:
    """Ask for commentary on one reporting date.

    Answers ``202`` when a model call was queued and ``200`` otherwise — because
    "otherwise" is a real answer: a request that coalesced onto the one already in
    flight, or one the gates, the daily cap or the sheet itself stopped, is served
    the platform's own commentary with ``author`` and ``notice`` saying so.
    """

    _ = bank_id  # the institution is the router's dependency
    read = _read(
        db, access, action=_ACTION_REQUEST, as_of=payload.as_of, compare_to=payload.compare_to
    )
    outcome = bi_commentary.request_commentary(
        db,
        ctx=access.ctx,
        bank=access.bank,
        assembled=read.assembled,
        requested_by=access.principal_user_id,
    )
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return _answer(
        db,
        access,
        read,
        draft=outcome.draft,
        reason=outcome.reason,
        can_request_draft=outcome.gate.allowed and outcome.quota.allowed,
    )


@router.get(
    _PATH,
    response_model=BiCommentaryRead,
    operation_id="getBiCommentary",
    responses=_ERRORS,
)
def get_bi_commentary(  # noqa: PLR0913 - FastAPI injects db/access
    bank_id: str,
    db: DbSession,
    access: BiRead,
    as_of: Annotated[date, Query(description="The reporting date to read commentary for.")],
    compare_to: Annotated[
        date | None,
        Query(description="The earlier period movements are measured against."),
    ] = None,
) -> BiCommentaryRead:
    """Read the commentary on offer for this reader and this reporting date.

    Always answers: this reader's own draft when there is one, and the platform's
    own commentary when there is not. The read is re-authorized on every call
    rather than trusted from the request that made the draft, because a reader's
    grants can narrow between the two and a draft holds figures from the wider
    set.
    """

    _ = bank_id
    read = _read(db, access, action=_ACTION_READ, as_of=as_of, compare_to=compare_to)
    draft = bi_commentary.latest_draft(
        db,
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        as_of=read.assembled.as_of,
        requested_by=access.principal_user_id,
    )
    return _answer(
        db,
        access,
        read,
        draft=draft,
        reason=None,
        can_request_draft=_can_request(db, access),
    )


__all__ = [
    "AUTHOR_MODEL",
    "AUTHOR_PLATFORM",
    "REASON_UNRESOLVED",
    "STATE_PENDING",
    "STATE_READY",
    "get_bi_commentary",
    "request_bi_commentary",
    "router",
]
