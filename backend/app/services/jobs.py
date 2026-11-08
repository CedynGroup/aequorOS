from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.authorization import BindingGrant, Permission
from app.core.locator_visibility import locator_visibility
from app.models import Job


def tenant_visibility_condition(grants: Sequence[BindingGrant]) -> ColumnElement[bool]:
    """When a tenant principal may see one queue row (#186).

    A job is visible only when it carries a complete locator, its enqueue
    provenance names a tenant principal — the ``human:``/``machine:`` forms,
    never the ``worker``/``system`` markers or the staff-only ``operator:``
    form (mission D9) — and one current view grant matches the locator.
    Locator-less rows (legacy rows and defined worker/system enqueues) match
    no clause and are hidden. Claim, retry and execution ignore every one of
    these columns, so the worker keeps processing what tenants cannot see.
    """
    return and_(
        Job.institution_scope.is_not(None),
        Job.module.is_not(None),
        Job.sensitivity.is_not(None),
        or_(
            Job.enqueued_by_principal.like("human:%"),
            Job.enqueued_by_principal.like("machine:%"),
        ),
        locator_visibility(
            grants,
            Permission.VIEW,
            institution_scope=Job.institution_scope,
            bank_id=Job.bank_id,
            module=Job.module,
            sensitivity=Job.sensitivity,
        ),
    )


def get_job_or_404(
    db: Session,
    organization_id: str,
    job_id: UUID,
    visibility: ColumnElement[bool] | None = None,
) -> Job:
    """The one queue row for a tenant, applying any read-visibility filter.

    A hidden row answers exactly what an unknown id answers, so a denied
    caller cannot distinguish a foreign job from a nonexistent one.
    """
    conditions = [Job.id == job_id, Job.organization_id == organization_id]
    if visibility is not None:
        conditions.append(visibility)
    job = db.scalar(select(Job).where(*conditions))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


def list_jobs(
    db: Session,
    organization_id: str,
    visibility: ColumnElement[bool],
    *,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    """The visible page of the queue and its exact visible total (#186).

    Page and count share the caller's ``tenant_visibility_condition``, so a
    hidden row shifts neither ``total`` nor any page boundary: its existence
    is as opaque to the list as the 404 is to the detail read. Ordering is
    deterministic (newest first, id as the tie-break).
    """
    conditions = [Job.organization_id == organization_id, visibility]
    rows = list(
        db.scalars(
            select(Job)
            .where(*conditions)
            .order_by(Job.queued_at.desc(), Job.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    return rows, total
