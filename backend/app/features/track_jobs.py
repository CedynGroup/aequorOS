from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, JobVisibility, Tenant
from app.models import Job
from app.schemas.jobs import JobListRead, JobRead
from app.services import jobs as jobs_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListRead, operation_id="listJobs")
def list_jobs(
    db: DbSession,
    ctx: Tenant,
    visibility: JobVisibility,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListRead:
    """The visible slice of the queue, paged (#186).

    The page and its total ride the same ``tenant_visibility_condition`` as
    GET detail — complete locator, tenant-principal provenance, one current
    view grant — so legacy and worker-only rows shift neither count nor page
    and their existence stays opaque.
    """
    rows, total = jobs_service.list_jobs(
        db, ctx.organization_id, visibility, limit=limit, offset=offset
    )
    return JobListRead(
        jobs=[JobRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, db: DbSession, ctx: Tenant, visibility: JobVisibility) -> Job:
    """One queue row, visible only through its persisted locator authority (#186).

    A row without a complete locator or tenant-principal provenance, or
    outside the caller's current view authority, answers the same 404 an
    unknown id does. Claim and execution are untouched, so the worker keeps
    processing rows no tenant can poll.
    """
    return jobs_service.get_job_or_404(db, ctx.organization_id, job_id, visibility)
