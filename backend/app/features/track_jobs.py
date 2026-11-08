from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, JobVisibility, Tenant
from app.models import Job
from app.schemas.jobs import JobRead
from app.services import jobs as jobs_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, db: DbSession, ctx: Tenant, visibility: JobVisibility) -> Job:
    """One queue row, visible only through its persisted locator authority (#186).

    A row without a complete locator or tenant-principal provenance, or
    outside the caller's current view authority, answers the same 404 an
    unknown id does. Claim and execution are untouched, so the worker keeps
    processing rows no tenant can poll.
    """
    return jobs_service.get_job_or_404(db, ctx.organization_id, job_id, visibility)
