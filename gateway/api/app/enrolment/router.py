"""Operator API (``/ops/api``, SPEC Part 5). Operator machine clients only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.access.service import AccessRequestIn, NumberOut, request_access
from app.auth.operator import OperatorPrincipal, require_operator
from app.core.context import get_ctx
from app.core.errors import Problem
from app.enrolment.service import (
    EnrolInstitutionRequest,
    EnrolledInstitution,
    EnrolledInstitutionList,
    IssueClientRequest,
    IssuedClient,
    RegisterKeyRequest,
    RevokeRequest,
    StatusRequest,
    enrol_institution,
    get_enrolled,
    issue_client,
    list_enrolled,
    register_signing_key,
    retire_signing_key,
    revoke_client,
    set_status,
)

router = APIRouter(prefix="/ops/api", tags=["Operator"])
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": Problem},
    403: {"model": Problem},
    404: {"model": Problem},
    409: {"model": Problem},
}


@router.post(
    "/institutions",
    response_model=EnrolledInstitution,
    status_code=201,
    summary="Enrol an institution (idempotent on code)",
    responses={200: {"model": EnrolledInstitution}, **ERRORS},
)
def enrol(
    request: Request,
    body: EnrolInstitutionRequest,
    operator: OperatorPrincipal = Depends(require_operator),
) -> JSONResponse:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        created, view = enrol_institution(session, ctx, operator.actor, body)
    return JSONResponse(view.model_dump(), status_code=201 if created else 200)


@router.get(
    "/institutions",
    response_model=EnrolledInstitutionList,
    summary="Every institution on the channel, by code",
    responses=ERRORS,
)
def list_institutions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=8),
    operator: OperatorPrincipal = Depends(require_operator),
) -> EnrolledInstitutionList:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return list_enrolled(session, ctx, operator.actor, limit=limit, cursor=cursor)


@router.get("/institutions/{code}", response_model=EnrolledInstitution, responses=ERRORS)
def read_institution(
    request: Request, code: str, operator: OperatorPrincipal = Depends(require_operator)
) -> EnrolledInstitution:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return get_enrolled(session, ctx, operator.actor, code)


@router.post(
    "/institutions/{code}/clients",
    response_model=IssuedClient,
    status_code=201,
    summary="Issue an API client certificate from a CSR",
    responses={200: {"model": IssuedClient}, **ERRORS},
)
def create_client(
    request: Request,
    code: str,
    body: IssueClientRequest,
    operator: OperatorPrincipal = Depends(require_operator),
) -> JSONResponse:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        created, issued = issue_client(session, ctx, operator.actor, code, body)
    return JSONResponse(issued.model_dump(), status_code=201 if created else 200)


@router.post("/clients/{client_id}/revoke", status_code=204, responses=ERRORS)
def revoke(
    request: Request,
    client_id: str,
    body: RevokeRequest,
    operator: OperatorPrincipal = Depends(require_operator),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        revoke_client(session, ctx, operator.actor, client_id, body.reason)
    return Response(status_code=204)


@router.post(
    "/institutions/{code}/signing-keys",
    status_code=201,
    summary="Register a signing key (idempotent on kid and key)",
    responses={200: {"description": "Already registered"}, **ERRORS},
)
def register_key(
    request: Request,
    code: str,
    body: RegisterKeyRequest,
    operator: OperatorPrincipal = Depends(require_operator),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        created = register_signing_key(session, ctx, operator.actor, code, body)
    return Response(status_code=201 if created else 200)


@router.post("/institutions/{code}/signing-keys/{kid}/retire", status_code=204, responses=ERRORS)
def retire_key(
    request: Request, code: str, kid: str, operator: OperatorPrincipal = Depends(require_operator)
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        retire_signing_key(session, ctx, operator.actor, code, kid)
    return Response(status_code=204)


@router.post("/institutions/{code}/status", status_code=204, responses=ERRORS)
def change_status(
    request: Request,
    code: str,
    body: StatusRequest,
    operator: OperatorPrincipal = Depends(require_operator),
) -> Response:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        set_status(session, ctx, operator.actor, code, body)
    return Response(status_code=204)


@router.post("/access-requests", response_model=NumberOut, status_code=201, responses=ERRORS)
def create_access_request(
    request: Request, body: AccessRequestIn, operator: OperatorPrincipal = Depends(require_operator)
) -> NumberOut:
    ctx = get_ctx(request)
    with ctx.db.session() as session:
        return NumberOut(number=request_access(session, ctx, operator.actor, body))
