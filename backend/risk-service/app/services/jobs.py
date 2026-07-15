from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job


def get_job_or_404(db: Session, organization_id: UUID, job_id: UUID) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.organization_id == organization_id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job
