from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.integrations.storage.base import ObjectStorage
from app.integrations.storage.s3 import get_object_storage
from app.models import Organization, User


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_user_id: UUID | None = None


def get_tenant_context(
    x_org_id: Annotated[str, Header(alias="X-Org-Id")],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> TenantContext:
    if not x_org_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Org-Id header is required."
        )
    try:
        organization_id = UUID(x_org_id)
        actor_user_id = UUID(x_user_id) if x_user_id else None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant headers must be valid UUIDs.",
        ) from exc
    return TenantContext(organization_id=organization_id, actor_user_id=actor_user_id)


def get_mutation_tenant_context(
    x_org_id: Annotated[str, Header(alias="X-Org-Id")],
    x_user_id: Annotated[str, Header(alias="X-User-Id")],
) -> TenantContext:
    return get_tenant_context(x_org_id=x_org_id, x_user_id=x_user_id)


def get_tenant_db_session(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> Iterator[Session]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ctx.organization_id
    try:
        validate_tenant_context(session, ctx)
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def validate_tenant_context(session: Session, ctx: TenantContext) -> None:
    organization_id = session.scalar(
        select(Organization.id).where(Organization.id == ctx.organization_id)
    )
    if organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context is not valid.",
        )

    if ctx.actor_user_id is None:
        return

    actor_user_id = session.scalar(
        select(User.id).where(
            User.id == ctx.actor_user_id,
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    )
    if actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context is not valid.",
        )


DbSession = Annotated[Session, Depends(get_tenant_db_session)]
Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
MutationTenant = Annotated[TenantContext, Depends(get_mutation_tenant_context)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]
