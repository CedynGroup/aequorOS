"""Examiner mode endpoints (Phase 2 item 7) — read-only by construction."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.schemas.examiner import (
    ExaminerDocumentationRead,
    ExaminerRunsRead,
    RunReproductionRead,
)
from app.services import examiner_mode

router = APIRouter(tags=["examiner"])


@router.get(
    "/banks/{bank_id}/examiner/runs",
    response_model=ExaminerRunsRead,
    operation_id="listExaminerRuns",
)
def list_examiner_runs(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
    module: Annotated[str | None, Query()] = None,
) -> ExaminerRunsRead:
    return examiner_mode.list_runs(db, ctx, bank_id, reporting_period_id, module)


@router.get(
    "/banks/{bank_id}/examiner/runs/{run_id}/reproduction",
    response_model=RunReproductionRead,
    operation_id="reproduceExaminerRun",
)
def reproduce_run(
    bank_id: str, run_id: UUID, db: DbSession, ctx: Tenant
) -> RunReproductionRead:
    return examiner_mode.reproduce_run(db, ctx, bank_id, run_id)


@router.get(
    "/banks/{bank_id}/examiner/documentation-package",
    response_model=ExaminerDocumentationRead,
    operation_id="getExaminerDocumentationPackage",
)
def documentation_package(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
) -> ExaminerDocumentationRead:
    return examiner_mode.documentation_package(db, ctx, bank_id, reporting_period_id)
