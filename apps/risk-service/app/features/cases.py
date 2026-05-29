from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Tenant
from app.features import cases_service
from app.models import RiskCase

router = APIRouter(prefix="/cases", tags=["cases"])

CaseStatus = Literal["draft", "active", "in_review", "completed", "archived"]


class CaseCreate(BaseModel):
    title: str
    case_type: str
    subject_type: str | None = None
    subject_name: str | None = None
    description: str | None = None
    status: CaseStatus = "draft"
    metadata: dict[str, object] = Field(default_factory=dict)


class CaseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    subject_type: str | None = None
    subject_name: str | None = None
    status: CaseStatus | None = None


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    description: str | None
    status: str
    metadata: dict[str, object] = Field(alias="metadata_")
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@router.post("", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.create_case(db, ctx, payload)


@router.get("", response_model=list[CaseRead])
def list_cases(db: DbSession, ctx: Tenant, include_archived: bool = False) -> list[RiskCase]:
    return cases_service.list_cases(db, ctx, include_archived=include_archived)


@router.get("/{case_id}", response_model=CaseRead)
def get_case(case_id: UUID, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.get_case_or_404(db, ctx.organization_id, case_id)


@router.patch("/{case_id}", response_model=CaseRead)
def update_case(case_id: UUID, payload: CaseUpdate, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.update_case(db, ctx, case_id, payload)


@router.post("/{case_id}/archive", response_model=CaseRead)
def archive_case(case_id: UUID, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.archive_case(db, ctx, case_id)
