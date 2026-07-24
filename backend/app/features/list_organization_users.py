"""Organization user directory (read-only).

Resolves actor UUIDs to display names/roles across governance surfaces —
approval trails, submission history, notification recipients. Read access
only; user administration lives in the auth/SSO admin surfaces.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import DbSession, Tenant
from app.models import User

router = APIRouter(tags=["organization"])


class OrganizationUserRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    job_title: str | None
    role: str
    is_active: bool


class OrganizationUserListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[OrganizationUserRead]


@router.get(
    "/organization/users",
    response_model=OrganizationUserListRead,
    operation_id="listOrganizationUsers",
)
def list_organization_users(db: DbSession, ctx: Tenant) -> OrganizationUserListRead:
    rows = db.scalars(
        select(User)
        .where(User.organization_id == ctx.organization_id)
        .order_by(User.display_name, User.email)
    )
    return OrganizationUserListRead(
        users=[OrganizationUserRead.model_validate(row) for row in rows]
    )
