"""Examiner mode contracts (Phase 2 item 7)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExaminerRunRead(ClosedModel):
    run_id: UUID
    module: str
    scenario_code: str
    status: str
    engine_version: str
    input_schema_version: str
    input_hash: str
    created_at: datetime


class ExaminerRunsRead(ClosedModel):
    bank_id: str
    reporting_period_id: UUID
    runs: list[ExaminerRunRead]


class RunReproductionRead(ClosedModel):
    run_id: UUID
    module: str
    scenario_code: str
    engine_version: str
    input_schema_version: str
    stored_input_hash: str
    recomputed_input_hash: str
    reproducible: bool
    fact_count: int | None = None
    created_at: datetime


class ExaminerPackageRead(ClosedModel):
    package_id: UUID
    return_code: str
    version: int
    status: str
    basis: str
    content_digest: str | None = None


class ExaminerDocumentationRead(ClosedModel):
    bank_id: str
    bank_name: str
    jurisdiction_code: str
    reporting_period_id: UUID
    period_label: str
    as_of_date: date
    latest_runs: list[ExaminerRunRead]
    packages: list[ExaminerPackageRead]
    cfp_approved_version: int | None = None
    cfp_active: bool = False
    audit_event_count: int
    register_endpoints: list[str]
