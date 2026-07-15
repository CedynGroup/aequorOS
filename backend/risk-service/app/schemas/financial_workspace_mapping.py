from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, model_validator

from app.schemas.common import JsonObject


class FinancialWorkspaceMapRequest(BaseModel):
    document_id: UUID | None = None
    document_extraction_id: UUID | None = None

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> FinancialWorkspaceMapRequest:
        if (self.document_id is None) == (self.document_extraction_id is None):
            raise ValueError("Exactly one of document_id or document_extraction_id is required.")
        return self


class FinancialWorkspaceMapRowSummary(BaseModel):
    row_index: int
    source_row_id: UUID
    reason: str
    locator: JsonObject


class FinancialWorkspaceMapSummary(BaseModel):
    source_row_count: int
    mapped_source_row_count: int
    unmapped_source_row_count: int


class FinancialWorkspaceMapResponse(BaseModel):
    case_id: UUID
    organization_id: UUID
    document_id: UUID
    document_extraction_id: UUID
    summary: FinancialWorkspaceMapSummary
    created: dict[str, int]
    reused: dict[str, int]
    unmapped_rows: list[FinancialWorkspaceMapRowSummary]
