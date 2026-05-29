from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, Tenant
from app.features import jobs_service
from app.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    job_type: str
    status: str
    entity_type: str | None
    entity_id: UUID | None
    attempts: int
    max_attempts: int
    progress: dict[str, object]
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, db: DbSession, ctx: Tenant) -> Job:
    return jobs_service.get_job_or_404(db, ctx.organization_id, job_id)
