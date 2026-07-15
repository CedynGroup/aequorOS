from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.models import Job
from app.schemas.jobs import JobRead
from app.services import jobs as jobs_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, db: DbSession, ctx: Tenant) -> Job:
    return jobs_service.get_job_or_404(db, ctx.organization_id, job_id)
