"""Supervisor API (``/portal/api``, SPEC Part 3). BoG staff ID tokens only."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, select

from app.access.service import AccessView, ApproveIn, DenyIn, access_view, approve, deny
from app.audit.views import (
    AuditEntryOut,
    AuditList,
    VerifyOut,
    audit_entry,
    audit_list,
    export_bundle,
)
from app.auth.staff import Role, StaffPrincipal, authenticate_staff, require_role
from app.core.clock import iso, iso_or_none
from app.core.context import get_ctx
from app.core.errors import ApiError, Problem
from app.db.models import Institution, Submission
from app.db.models import Query as QueryRow
from app.jobs import latest_verification, verify_audit
from app.portal.calendar import CalendarOut, calendar_view
from app.portal.common import default_cycle_date, record_view
from app.portal.filed_package import (
    Download,
    document_download,
    package_download,
    receipt_download,
)
from app.portal.institutions import (
    InstitutionDetail,
    InstitutionList,
    Note,
    NoteIn,
    Onboarding,
    add_note,
    institution_detail,
    list_institutions,
    onboarding,
)
from app.portal.overview import Overview, overview
from app.portal.submissions import (
    AcceptIn,
    AssignIn,
    NumberOut,
    ResubmissionIn,
    SubmissionDetail,
    SubmissionList,
    accept,
    assign,
    list_submissions,
    request_resubmission,
    submission_detail,
)
from app.queries.service import (
    OpenQueryIn,
    QueryMessageOut,
    StaffMessageIn,
    close_query,
    load_query,
    message_out,
    messages_of,
    staff_message,
    staff_open_query,
)
from app.returns.service import (
    DraftIn,
    ReturnDetail,
    ReturnIn,
    ReturnSummary,
    all_definitions,
    approvals,
    create_draft,
    current_published,
    definition,
    detail,
    pending_draft,
    return_to_drafter,
    summary,
)
from app.returns.service import (
    approve as approve_version,
)

staff_bearer = HTTPBearer(
    auto_error=False, scheme_name="StaffIdToken", description="The BoG identity provider's ID token"
)


def staff(
    request: Request, _c: HTTPAuthorizationCredentials | None = Depends(staff_bearer)
) -> StaffPrincipal:
    return authenticate_staff(request)


router = APIRouter(prefix="/portal/api", tags=["Supervisor API"])
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": Problem},
    403: {"model": Problem},
    404: {"model": Problem},
    409: {"model": Problem},
}


class Me(BaseModel):
    subject: str
    name: str
    email: str
    roles: list[Role]


@router.get("/me", response_model=Me, responses=ERRORS)
def me(principal: StaffPrincipal = Depends(staff)) -> Me:
    return Me(
        subject=principal.subject,
        name=principal.name,
        email=principal.email,
        roles=sorted(principal.roles),
    )


# --- overview and submissions --------------------------------------------------------------


@router.get("/overview", response_model=Overview, responses=ERRORS)
def get_overview(
    request: Request, reporting_date: date | None = None, principal: StaffPrincipal = Depends(staff)
) -> Overview:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        result = overview(session, ctx, reporting_date or default_cycle_date(ctx, ctx.clock.now()))
        record_view(
            session,
            ctx,
            principal,
            action="Viewed overview",
            object_type="overview",
            object_ref=result.reporting_date,
        )
        return result


SubmissionFilter = Literal["needs_review", "validating", "rejected", "queried", "accepted"]


@router.get("/submissions", response_model=SubmissionList, responses=ERRORS)
def get_submissions(
    request: Request,
    status: SubmissionFilter | None = None,
    return_code: str | None = None,
    institution_code: str | None = None,
    reporting_date: date | None = None,
    channel: Literal["api", "portal_upload"] | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: StaffPrincipal = Depends(staff),
) -> SubmissionList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        result = list_submissions(
            session,
            status=status,
            return_code=return_code,
            institution_code=institution_code,
            reporting_date=reporting_date.isoformat() if reporting_date else None,
            channel=channel,
            q=q,
            cursor=cursor,
            limit=limit,
        )
        record_view(
            session,
            ctx,
            principal,
            action="Viewed submissions",
            object_type="submission_list",
            object_ref="inbox",
            detail={
                "status": status,
                "return_code": return_code,
                "institution_code": institution_code,
                "q": q,
            },
        )
        return result


@router.get("/submissions/{receipt_id}", response_model=SubmissionDetail, responses=ERRORS)
def get_submission(
    request: Request, receipt_id: str, principal: StaffPrincipal = Depends(staff)
) -> SubmissionDetail:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return submission_detail(session, ctx, principal, receipt_id)


@router.get(
    "/submissions/{receipt_id}/receipt",
    responses={200: {"content": {"application/json": {}}}, **ERRORS},
)
def download_receipt(
    request: Request, receipt_id: str, principal: StaffPrincipal = Depends(staff)
) -> Response:
    """The filing's signed receipt, exactly as the institution's own API returns it."""
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        download = receipt_download(session, ctx, principal, receipt_id)
    return _attachment(download)


@router.get(
    "/submissions/{receipt_id}/package",
    responses={200: {"content": {"application/zip": {}}}, **ERRORS},
)
def download_package(
    request: Request, receipt_id: str, principal: StaffPrincipal = Depends(staff)
) -> Response:
    """The filing as received: signed manifest, payload, declaration, receipt and keys."""
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        download = package_download(session, ctx, principal, receipt_id)
    return _attachment(download)


@router.get(
    "/submissions/{receipt_id}/documents/{name}",
    responses={200: {"content": {"application/octet-stream": {}}}, **ERRORS},
)
def download_document(
    request: Request, receipt_id: str, name: str, principal: StaffPrincipal = Depends(staff)
) -> Response:
    """One document filed with the return (Excel, CSV or PDF), byte for byte."""
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        download = document_download(session, ctx, principal, receipt_id, name)
    return _attachment(download)


def _attachment(download: Download) -> Response:
    return Response(
        content=download.body,
        media_type=download.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{download.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/submissions/{receipt_id}/accept", status_code=204, responses=ERRORS)
def accept_submission(
    request: Request,
    receipt_id: str,
    body: AcceptIn | None = None,
    principal: StaffPrincipal = Depends(staff),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        accept(session, ctx, principal, receipt_id, body.note if body else None)
    return Response(status_code=204)


@router.post(
    "/submissions/{receipt_id}/resubmission-request",
    status_code=201,
    response_model=NumberOut,
    responses=ERRORS,
)
def resubmission(
    request: Request,
    receipt_id: str,
    body: ResubmissionIn,
    principal: StaffPrincipal = Depends(staff),
) -> NumberOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return NumberOut(number=request_resubmission(session, ctx, principal, receipt_id, body))


@router.post("/submissions/{receipt_id}/assign", status_code=204, responses=ERRORS)
def assign_submission(
    request: Request, receipt_id: str, body: AssignIn, principal: StaffPrincipal = Depends(staff)
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        assign(session, ctx, principal, receipt_id, body.reviewer_subject)
    return Response(status_code=204)


# --- queries ------------------------------------------------------------------------------

PortalQueryStatus = Literal["awaiting_institution", "awaiting_supervisor", "overdue", "closed"]


class QueryRowOut(BaseModel):
    number: str
    institution_name: str
    subject: str
    status: PortalQueryStatus
    kind: Literal["query", "resubmission"]
    return_code: str
    due_at: str | None
    last_activity_at: str


class QueryListOut(BaseModel):
    items: list[QueryRowOut]
    counts: dict[str, int]
    next_cursor: str | None


class PortalMessage(QueryMessageOut):
    author_role: str | None


class QueryDetailOut(BaseModel):
    number: str
    subject: str
    kind: Literal["query", "resubmission"]
    status: PortalQueryStatus
    institution_code: str
    institution_name: str
    receipt_id: str
    return_code: str
    reporting_date: str
    opened_by: str
    opened_at: str
    due_at: str | None
    delivered_at: str | None
    acknowledged_at: str | None
    messages: list[PortalMessage]


def _portal_status(query: QueryRow, now_ts: float) -> PortalQueryStatus:
    if query.status == "closed":
        return "closed"
    if query.status == "awaiting_supervisor":
        return "awaiting_supervisor"
    if query.due_at is not None and query.due_at.timestamp() < now_ts:
        return "overdue"
    return "awaiting_institution"


def _kind(value: str) -> Literal["query", "resubmission"]:
    return "resubmission" if value == "resubmission" else "query"


@router.get("/queries", response_model=QueryListOut, responses=ERRORS)
def get_queries(
    request: Request,
    status: Literal["open", "awaiting_institution", "awaiting_supervisor", "closed"] = "open",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: StaffPrincipal = Depends(staff),
) -> QueryListOut:
    ctx = get_ctx(request)
    now_ts = ctx.clock.now().timestamp()
    with ctx.db.session() as session:
        counts = {
            "open": int(
                session.scalar(
                    select(func.count()).select_from(QueryRow).where(QueryRow.status != "closed")
                )
                or 0
            ),
            "awaiting_institution": int(
                session.scalar(
                    select(func.count())
                    .select_from(QueryRow)
                    .where(QueryRow.status == "awaiting_institution")
                )
                or 0
            ),
            "awaiting_supervisor": int(
                session.scalar(
                    select(func.count())
                    .select_from(QueryRow)
                    .where(QueryRow.status == "awaiting_supervisor")
                )
                or 0
            ),
            "closed": int(
                session.scalar(
                    select(func.count()).select_from(QueryRow).where(QueryRow.status == "closed")
                )
                or 0
            ),
        }
        statement = select(QueryRow)
        if status == "open":
            statement = statement.where(QueryRow.status != "closed")
        else:
            statement = statement.where(QueryRow.status == status)
        if cursor:
            statement = statement.where(QueryRow.number < cursor)
        page = list(
            session.scalars(
                statement.order_by(QueryRow.last_activity_at.desc(), QueryRow.number.desc()).limit(
                    limit + 1
                )
            )
        )
        items = page[:limit]
        institutions = {i.id: i for i in session.scalars(select(Institution))}
        subs = (
            {
                s.id: s
                for s in session.scalars(
                    select(Submission).where(Submission.id.in_([q.submission_id for q in items]))
                )
            }
            if items
            else {}
        )
        out = [
            QueryRowOut(
                number=q.number,
                institution_name=institutions[q.institution_id].name,
                subject=q.subject,
                status=_portal_status(q, now_ts),
                kind=_kind(q.kind),
                return_code=subs[q.submission_id].return_code if q.submission_id in subs else "",
                due_at=iso_or_none(q.due_at),
                last_activity_at=iso(q.last_activity_at),
            )
            for q in items
        ]
        record_view(
            session,
            ctx,
            principal,
            action="Viewed queries",
            object_type="query_list",
            object_ref=status,
        )
    return QueryListOut(
        items=out,
        counts=counts,
        next_cursor=items[-1].number if len(page) > limit and items else None,
    )


@router.get("/queries/{number}", response_model=QueryDetailOut, responses=ERRORS)
def get_query(
    request: Request, number: str, principal: StaffPrincipal = Depends(staff)
) -> QueryDetailOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        query, institution, submission = load_query(session, number)
        messages = [
            PortalMessage(
                id=m.id,
                author=message_out(m).author,
                author_label=m.author_label,
                body=m.body,
                created_at=iso(m.created_at),
                author_role=m.author_role,
            )
            for m in messages_of(session, query.id)
        ]
        record_view(
            session,
            ctx,
            principal,
            action="Opened query",
            object_type="query",
            object_ref=number,
            institution_code=institution.code,
        )
        return QueryDetailOut(
            number=query.number,
            subject=query.subject,
            kind=_kind(query.kind),
            status=_portal_status(query, ctx.clock.now().timestamp()),
            institution_code=institution.code,
            institution_name=institution.name,
            receipt_id=query.receipt_id,
            return_code=submission.return_code,
            reporting_date=submission.reporting_date.isoformat(),
            opened_by=query.opened_by_name,
            opened_at=iso(query.opened_at),
            due_at=iso_or_none(query.due_at),
            delivered_at=iso_or_none(query.delivered_at),
            acknowledged_at=iso_or_none(query.acknowledged_at),
            messages=messages,
        )


@router.post("/queries", status_code=201, response_model=NumberOut, responses=ERRORS)
def create_query(
    request: Request, body: OpenQueryIn, principal: StaffPrincipal = Depends(staff)
) -> NumberOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return NumberOut(number=staff_open_query(session, ctx, principal, body).number)


@router.post(
    "/queries/{number}/messages", status_code=201, response_model=QueryMessageOut, responses=ERRORS
)
def add_message(
    request: Request, number: str, body: StaffMessageIn, principal: StaffPrincipal = Depends(staff)
) -> QueryMessageOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return message_out(staff_message(session, ctx, principal, number, body))


@router.post("/queries/{number}/close", status_code=204, responses=ERRORS)
def close(request: Request, number: str, principal: StaffPrincipal = Depends(staff)) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        close_query(session, ctx, principal, number)
    return Response(status_code=204)


# --- institutions, onboarding (read-only) and calendar ---------------------------------------


@router.get("/institutions", response_model=InstitutionList, responses=ERRORS)
def get_institutions(
    request: Request, q: str | None = None, principal: StaffPrincipal = Depends(staff)
) -> InstitutionList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        result = list_institutions(session, ctx, q)
        record_view(
            session,
            ctx,
            principal,
            action="Viewed institutions",
            object_type="institution_list",
            object_ref=q or "all",
        )
        return result


@router.get("/institutions/{code}", response_model=InstitutionDetail, responses=ERRORS)
def get_institution(
    request: Request, code: str, principal: StaffPrincipal = Depends(staff)
) -> InstitutionDetail:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return institution_detail(session, ctx, principal, code)


@router.post("/institutions/{code}/notes", status_code=201, response_model=Note, responses=ERRORS)
def post_note(
    request: Request, code: str, body: NoteIn, principal: StaffPrincipal = Depends(staff)
) -> Note:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return add_note(session, ctx, principal, code, body.body)


@router.get(
    "/onboarding",
    response_model=Onboarding,
    responses=ERRORS,
    summary="The institutions on the channel (read-only; enrolment is the operator's)",
)
def get_onboarding(request: Request, principal: StaffPrincipal = Depends(staff)) -> Onboarding:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        result = onboarding(session, ctx)
        record_view(
            session,
            ctx,
            principal,
            action="Viewed institution register",
            object_type="institution_list",
            object_ref="register",
        )
        return result


@router.get("/calendar", response_model=CalendarOut, responses=ERRORS)
def get_calendar(
    request: Request,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    _principal: StaffPrincipal = Depends(staff),
) -> CalendarOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return calendar_view(session, ctx, month or ctx.clock.now().strftime("%Y-%m"))


# --- return definitions ---------------------------------------------------------------------


class PortalReturn(ReturnSummary):
    rule_count: int
    status: Literal["published", "draft_pending"]


class PortalReturnList(BaseModel):
    items: list[PortalReturn]


class ApprovalStep(BaseModel):
    step: Literal["drafted", "approved", "pending"]
    by: str | None
    role_label: str
    at: str | None


class DraftOut(BaseModel):
    version: str
    change_summary: str
    first_reporting_date: str
    notice_days: int
    breaking: bool
    approvals: list[ApprovalStep]


class PortalReturnDetail(ReturnDetail):
    draft: DraftOut | None


@router.get("/returns", response_model=PortalReturnList, responses=ERRORS)
def get_returns(request: Request, _principal: StaffPrincipal = Depends(staff)) -> PortalReturnList:
    ctx = get_ctx(request)
    items: list[PortalReturn] = []
    with ctx.db.session() as session:
        for row in all_definitions(session):
            base = summary(session, row)
            current = current_published(session, row.id)
            if base is None or current is None:
                continue
            items.append(
                PortalReturn(
                    code=base.code,
                    name=base.name,
                    cadence=base.cadence,
                    current_version=base.current_version,
                    applies_to=base.applies_to,
                    rule_count=current.rule_count,
                    status="draft_pending"
                    if pending_draft(session, row.id) is not None
                    else "published",
                )
            )
    return PortalReturnList(items=items)


@router.get("/returns/{code}", response_model=PortalReturnDetail, responses=ERRORS)
def get_return(
    request: Request, code: str, _principal: StaffPrincipal = Depends(staff)
) -> PortalReturnDetail:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        row = definition(session, code)
        base = detail(session, row) if row is not None else None
        if row is None or base is None:
            raise ApiError("not_found", f"No return {code}.")
        draft = pending_draft(session, row.id)
        draft_out = None
        if draft is not None:
            steps = approvals(session, draft.id)
            out = [
                ApprovalStep(
                    step="drafted" if a.step == "drafted" else "approved",
                    by=a.actor_name,
                    role_label=a.role_label,
                    at=iso(a.at),
                )
                for a in steps
            ]
            approved = [a for a in steps if a.step == "approved"]
            for index in range(len(approved), 2):
                labels = {a.role_label for a in approved}
                suggestion = "Supervision" if "Policy" in labels else "Policy"
                out.append(
                    ApprovalStep(
                        step="pending",
                        by=None,
                        role_label=f"{'Second' if index == 1 else 'First'} approver, {suggestion}",
                        at=None,
                    )
                )
            draft_out = DraftOut(
                version=draft.version,
                change_summary=draft.change_summary,
                first_reporting_date=draft.first_reporting_date.isoformat(),
                notice_days=draft.notice_days,
                breaking=draft.breaking,
                approvals=out,
            )
        return PortalReturnDetail(
            code=base.code,
            name=base.name,
            cadence=base.cadence,
            current_version=base.current_version,
            applies_to=base.applies_to,
            versions=base.versions,
            draft=draft_out,
        )


@router.post("/returns/{code}/drafts", status_code=201, responses=ERRORS)
def post_draft(
    request: Request, code: str, body: DraftIn, principal: StaffPrincipal = Depends(staff)
) -> JSONResponse:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        version = create_draft(session, ctx, principal, code, body)
        result = {
            "version": version.version,
            "schema_sha256": version.schema_sha256,
            "rule_count": version.rule_count,
        }
    return JSONResponse(result, status_code=201)


@router.post("/returns/{code}/versions/{version}/approve", status_code=204, responses=ERRORS)
def post_approve(
    request: Request, code: str, version: str, principal: StaffPrincipal = Depends(staff)
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        approve_version(session, ctx, principal, code, version)
    return Response(status_code=204)


@router.post("/returns/{code}/versions/{version}/return", status_code=204, responses=ERRORS)
def post_return(
    request: Request,
    code: str,
    version: str,
    body: ReturnIn,
    principal: StaffPrincipal = Depends(staff),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return_to_drafter(session, ctx, principal, code, version, body.reason)
    return Response(status_code=204)


# --- audit ------------------------------------------------------------------------------------


@router.get("/audit", response_model=AuditList, responses=ERRORS)
def get_audit(
    request: Request,
    actor_type: Literal["staff", "institution", "system", "operator"] | None = None,
    category: str | None = None,
    institution_code: str | None = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _principal: StaffPrincipal = Depends(staff),
) -> AuditList:
    ctx = get_ctx(request)
    verification = latest_verification(ctx)
    if verification is None:
        _, verification = verify_audit(ctx, trigger="scheduled")
    with ctx.db.session() as session:
        return audit_list(
            session,
            verification,
            actor_type=actor_type,
            category=category,
            institution_code=institution_code,
            from_=from_,
            to=to,
            cursor=cursor,
            limit=limit,
        )


@router.get("/audit/export", responses={200: {"content": {"application/zip": {}}}, **ERRORS})
def get_export(
    request: Request,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    principal: StaffPrincipal = Depends(staff),
) -> Response:
    require_role(principal, "auditor", "senior_supervisor")
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        data = export_bundle(session, ctx, from_, to)
        record_view(
            session,
            ctx,
            principal,
            action="Exported audit log",
            object_type="audit_export",
            object_ref=f"{from_ or 'start'}..{to or 'now'}",
        )
    name = f"srg-audit-{from_ or 'start'}-{to or 'now'}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/audit/{seq}", response_model=AuditEntryOut, responses=ERRORS)
def get_audit_entry(
    request: Request, seq: int, _principal: StaffPrincipal = Depends(staff)
) -> AuditEntryOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return audit_entry(session, ctx, seq)


@router.post("/audit/verify", response_model=VerifyOut, responses=ERRORS)
def post_verify(request: Request, principal: StaffPrincipal = Depends(staff)) -> VerifyOut:
    ctx = get_ctx(request)
    result, record = verify_audit(ctx, trigger="manual", actor_label=principal.short_name)
    return VerifyOut(
        intact=result.intact,
        entries=result.entries,
        verified_at=iso(record.verified_at),
        first_bad_seq=result.first_bad_seq,
    )


# --- access and approvals -----------------------------------------------------------------------


@router.get("/access", response_model=AccessView, responses=ERRORS)
def get_access(request: Request, _principal: StaffPrincipal = Depends(staff)) -> AccessView:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return access_view(session, ctx)


@router.post("/access/requests/{number}/approve", status_code=204, responses=ERRORS)
def post_access_approve(
    request: Request, number: str, body: ApproveIn, principal: StaffPrincipal = Depends(staff)
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        approve(session, ctx, principal, number, body.duration_minutes)
    return Response(status_code=204)


@router.post("/access/requests/{number}/deny", status_code=204, responses=ERRORS)
def post_access_deny(
    request: Request, number: str, body: DenyIn, principal: StaffPrincipal = Depends(staff)
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        deny(session, ctx, principal, number, body.reason)
    return Response(status_code=204)
