"""Institution API ``/v1``: returns, obligations, submissions and queries."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, JsonValue
from sqlalchemy import select

from app.auth.institution import InstitutionPrincipal, require_scope
from app.core.clock import iso, iso_or_none
from app.core.context import AppContext, get_ctx
from app.core.errors import ApiError, Issue, Problem
from app.core.jsonvalue import NotJsonError, loads_object
from app.db.models import Institution, Submission
from app.filing.obligations import ObligationOut, compute_obligations
from app.filing.service import (
    FilerContext,
    FilingRequest,
    ReceiptEnvelope,
    SubmissionState,
    receipt_envelope,
    state_of,
    submit,
)
from app.filing.validation import validate_submission
from app.queries.service import (
    InstitutionQuery,
    QueryMessageOut,
    ResponseIn,
    acknowledge,
    list_for_institution,
    message_out,
    respond,
)
from app.returns.rules import Rule
from app.returns.service import (
    ReturnDetail,
    ReturnSummary,
    SchemaAndRules,
    all_definitions,
    definition,
    detail,
    rules_of,
    summary,
    versions_of,
)

router = APIRouter(prefix="/v1", tags=["Institution API v1"])

AUTH_ERRORS: dict[int | str, dict[str, object]] = {
    401: {
        "model": Problem,
        "description": "certificate_required, certificate_mismatch or unauthenticated",
    },
    403: {"model": Problem, "description": "insufficient_scope or forbidden"},
    429: {"model": Problem, "description": "rate_limited"},
}


class ReturnList(BaseModel):
    items: list[ReturnSummary]


class ObligationList(BaseModel):
    items: list[ObligationOut]


class ValidationSummary(BaseModel):
    rule_set: str
    passed: int
    failed: int
    warnings: int
    issues: list[Issue]


class SubmissionStatus(BaseModel):
    receipt_id: str
    return_code: str
    return_version: str
    reporting_date: str
    basis: Literal["solo", "consolidated"]
    version: int
    status: SubmissionState
    received_at: str
    validation: ValidationSummary | None
    decided_at: str | None
    superseded_by: str | None


class SubmissionList(BaseModel):
    items: list[SubmissionStatus]
    next_cursor: str | None


class QueryList(BaseModel):
    items: list[InstitutionQuery]
    next_cursor: str | None


# --- returns --------------------------------------------------------------------------


@router.get(
    "/returns",
    response_model=ReturnList,
    responses=AUTH_ERRORS,
    summary="Published return definitions",
)
def list_returns(
    request: Request, _p: InstitutionPrincipal = Depends(require_scope("returns:read"))
) -> ReturnList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        items = [
            s for s in (summary(session, d) for d in all_definitions(session)) if s is not None
        ]
    return ReturnList(items=items)


@router.get(
    "/returns/{code}",
    response_model=ReturnDetail,
    responses={**AUTH_ERRORS, 404: {"model": Problem}},
)
def get_return(
    request: Request, code: str, _p: InstitutionPrincipal = Depends(require_scope("returns:read"))
) -> ReturnDetail:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        row = definition(session, code)
        result = detail(session, row) if row is not None else None
    if result is None:
        raise ApiError("not_found", f"No published return {code}.")
    return result


@router.get(
    "/returns/{code}/versions/{version}/schema",
    response_model=SchemaAndRules,
    response_model_by_alias=True,
    responses={**AUTH_ERRORS, 404: {"model": Problem}},
    summary="The version's JSON Schema (2020-12) and validation rules",
)
def get_schema(
    request: Request,
    code: str,
    version: str,
    _p: InstitutionPrincipal = Depends(require_scope("returns:read")),
) -> JSONResponse:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        row = definition(session, code)
        match = None
        if row is not None:
            match = next(
                (
                    v
                    for v in versions_of(session, row.id)
                    if v.version == version and v.status in ("published", "retired")
                ),
                None,
            )
        if match is None:
            raise ApiError("not_found", f"No published version {version} of {code}.")
        rules: list[Rule] = rules_of(match)
        body: dict[str, JsonValue] = {
            "schema": match.schema,
            "rules": [r.model_dump(mode="json") for r in rules],  # pyright: ignore[reportAny]
        }
    return JSONResponse(body)


# --- obligations ------------------------------------------------------------------------


@router.get("/obligations", response_model=ObligationList, responses=AUTH_ERRORS)
def list_obligations(
    request: Request,
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
    principal: InstitutionPrincipal = Depends(require_scope("obligations:read")),
) -> ObligationList:
    """Obligations of the calling institution with a reporting date in ``[from, to]``."""
    if to < from_ or to - from_ > timedelta(days=400):
        raise ApiError(
            "invalid_request", "from must not be after to, and the range is at most 400 days."
        )
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        institution = session.get(Institution, principal.institution_id)
        assert institution is not None
        rows = compute_obligations(session, ctx.settings, [institution], from_, to, ctx.clock.now())
    rows.sort(key=lambda r: (r.due_at, r.definition.position))
    return ObligationList(items=[r.out() for r in rows])


# --- submissions ------------------------------------------------------------------------


def _filer(principal: InstitutionPrincipal, institution: Institution) -> FilerContext:
    return FilerContext(
        institution=institution,
        actor=principal.actor,
        channel="api",
        environment=principal.environment,
        client_ref=principal.client_id,
        client_id=principal.client_id,
        certificate_thumbprint=principal.thumbprint,
        certificate_not_after=principal.certificate_not_after,
    )


async def _file(
    request: Request,
    background: BackgroundTasks,
    principal: InstitutionPrincipal,
    idempotency_key: str | None,
    amendment_of: str | None,
) -> JSONResponse:
    started = time.monotonic()
    raw_body = await request.body()
    try:
        raw = loads_object(raw_body)
    except NotJsonError as exc:
        raise ApiError("invalid_request", "The body must be a JSON object.") from exc
    ctx = get_ctx(request)
    path = request.url.path

    def work() -> tuple[int, dict[str, JsonValue], str | None]:
        with ctx.db.session() as session:
            institution = session.get(Institution, principal.institution_id)
            assert institution is not None
            outcome = submit(
                session,
                ctx,
                _filer(principal, institution),
                raw,
                path=path,
                idempotency_key=idempotency_key,
                amendment_of=amendment_of,
                started=started,
            )
        return outcome.status_code, outcome.body, outcome.submission_id

    status_code, body, submission_id = await run_in_threadpool(work)
    if submission_id is not None:
        background.add_task(validate_submission, ctx, submission_id)
    return JSONResponse(body, status_code=status_code)


FILING_RESPONSES: dict[int | str, dict[str, object]] = {
    202: {"model": ReceiptEnvelope, "description": "Receipted; validation follows"},
    400: {"model": Problem, "description": "invalid_request or idempotency_key_required"},
    **AUTH_ERRORS,
    409: {
        "model": Problem,
        "description": "idempotency_conflict, duplicate_submission or supersedes_not_current",
    },
    422: {
        "model": Problem,
        "description": "signature, digest, return, version or schema refusals (no receipt)",
    },
}


@router.post(
    "/submissions",
    status_code=202,
    response_model=ReceiptEnvelope,
    responses=FILING_RESPONSES,
    summary="File a first version of a return",
)
async def file_submission(
    request: Request,
    background: BackgroundTasks,
    body: FilingRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    principal: InstitutionPrincipal = Depends(require_scope("submissions:write")),
) -> JSONResponse:
    del body  # validated for the contract; the raw JSON is what the digests cover
    return await _file(request, background, principal, idempotency_key, None)


@router.post(
    "/submissions/{receipt_id}/amendments",
    status_code=202,
    response_model=ReceiptEnvelope,
    responses={**FILING_RESPONSES, 404: {"model": Problem}},
    summary="File an amendment (a new version)",
)
async def file_amendment(
    request: Request,
    background: BackgroundTasks,
    receipt_id: str,
    body: FilingRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    principal: InstitutionPrincipal = Depends(require_scope("submissions:write")),
) -> JSONResponse:
    del body
    return await _file(request, background, principal, idempotency_key, receipt_id)


def _validation_summary(sub: Submission) -> ValidationSummary | None:
    validation = sub.validation
    if validation is None:
        return None
    return ValidationSummary.model_validate(
        {k: validation.get(k) for k in ("rule_set", "passed", "failed", "warnings", "issues")}
    )


def submission_status(superseded_by: dict[str, str], sub: Submission) -> SubmissionStatus:
    return SubmissionStatus(
        receipt_id=sub.receipt_id,
        return_code=sub.return_code,
        return_version=sub.return_version,
        reporting_date=sub.reporting_date.isoformat(),
        basis="consolidated" if sub.basis == "consolidated" else "solo",
        version=sub.version,
        status=state_of(sub.status),
        received_at=iso(sub.received_at),
        validation=_validation_summary(sub),
        decided_at=iso_or_none(sub.decided_at),
        superseded_by=superseded_by.get(sub.id),
    )


def _superseders(ctx: AppContext, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    with ctx.db.session() as session:
        rows = session.execute(
            select(Submission.supersedes_id, Submission.receipt_id).where(
                Submission.supersedes_id.in_(ids)
            )
        ).all()
    return {str(a): str(b) for a, b in rows if a is not None}


@router.get("/submissions", response_model=SubmissionList, responses=AUTH_ERRORS)
def list_submissions(
    request: Request,
    reporting_date: date | None = None,
    return_code: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    principal: InstitutionPrincipal = Depends(require_scope("submissions:read")),
) -> SubmissionList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        statement = select(Submission).where(
            Submission.institution_id == principal.institution_id,
            Submission.environment == principal.environment,
        )
        if reporting_date is not None:
            statement = statement.where(Submission.reporting_date == reporting_date)
        if return_code:
            statement = statement.where(Submission.return_code == return_code)
        if cursor:
            statement = statement.where(Submission.receipt_id < cursor)
        rows = list(
            session.scalars(statement.order_by(Submission.receipt_id.desc()).limit(limit + 1))
        )
    page = rows[:limit]
    superseders = _superseders(ctx, [r.id for r in page])
    return SubmissionList(
        items=[submission_status(superseders, r) for r in page],
        next_cursor=page[-1].receipt_id if len(rows) > limit and page else None,
    )


def _own(ctx: AppContext, principal: InstitutionPrincipal, receipt_id: str) -> Submission:
    with ctx.db.session() as session:
        sub = session.scalars(select(Submission).where(Submission.receipt_id == receipt_id)).first()
    if sub is None or sub.institution_id != principal.institution_id:
        raise ApiError("not_found", f"No filing {receipt_id}.")
    return sub


@router.get(
    "/submissions/{receipt_id}",
    response_model=SubmissionStatus,
    responses={**AUTH_ERRORS, 404: {"model": Problem}},
)
def get_submission(
    request: Request,
    receipt_id: str,
    principal: InstitutionPrincipal = Depends(require_scope("submissions:read")),
) -> SubmissionStatus:
    ctx = get_ctx(request)
    sub = _own(ctx, principal, receipt_id)
    return submission_status(_superseders(ctx, [sub.id]), sub)


@router.get(
    "/submissions/{receipt_id}/receipt",
    response_model=ReceiptEnvelope,
    responses={**AUTH_ERRORS, 404: {"model": Problem}},
)
def get_receipt(
    request: Request,
    receipt_id: str,
    principal: InstitutionPrincipal = Depends(require_scope("submissions:read")),
) -> ReceiptEnvelope:
    ctx = get_ctx(request)
    sub = _own(ctx, principal, receipt_id)
    with ctx.db.session() as session:
        return receipt_envelope(session, sub)


# --- queries ------------------------------------------------------------------------------


@router.get("/queries", response_model=QueryList, responses=AUTH_ERRORS)
def list_queries(
    request: Request,
    status: Literal["open", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    principal: InstitutionPrincipal = Depends(require_scope("queries:read")),
) -> QueryList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        items, next_cursor = list_for_institution(
            session,
            ctx,
            principal.institution_id,
            principal.institution_code,
            principal.actor,
            include_closed=status == "all",
            limit=limit,
            cursor=cursor,
        )
    return QueryList(items=items, next_cursor=next_cursor)


@router.post(
    "/queries/{number}/acknowledgements",
    status_code=204,
    responses={**AUTH_ERRORS, 404: {"model": Problem}},
)
def acknowledge_query(
    request: Request,
    number: str,
    principal: InstitutionPrincipal = Depends(require_scope("queries:write")),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        acknowledge(
            session,
            ctx,
            principal.institution_id,
            principal.institution_code,
            principal.actor,
            number,
        )
    return Response(status_code=204)


@router.post(
    "/queries/{number}/responses",
    status_code=201,
    response_model=QueryMessageOut,
    responses={**AUTH_ERRORS, 404: {"model": Problem}, 409: {"model": Problem}},
)
def respond_to_query(
    request: Request,
    number: str,
    body: ResponseIn,
    principal: InstitutionPrincipal = Depends(require_scope("queries:write")),
) -> QueryMessageOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        institution = session.get(Institution, principal.institution_id)
        assert institution is not None
        message = respond(session, ctx, institution, principal.actor, number, body)
        return message_out(message)
