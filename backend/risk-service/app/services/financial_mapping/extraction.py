from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Document, DocumentExtraction
from app.schemas.common import JsonObject, JsonValue
from app.schemas.financial_workspace_mapping import FinancialWorkspaceMapRequest
from app.services.financial_mapping.types import ExtractedRow, ResolvedExtraction


def resolve_extraction(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    payload: FinancialWorkspaceMapRequest,
) -> ResolvedExtraction:
    if payload.document_extraction_id is not None:
        result = db.execute(
            select(DocumentExtraction, Document)
            .join(Document, Document.id == DocumentExtraction.document_id)
            .where(
                DocumentExtraction.id == payload.document_extraction_id,
                DocumentExtraction.organization_id == ctx.organization_id,
                Document.organization_id == ctx.organization_id,
                Document.case_id == case_id,
            )
        ).one_or_none()
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document extraction not found.",
            )
        extraction, document = result
        if extraction.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document extraction is not completed.",
            )
        return ResolvedExtraction(document=document, extraction=extraction)

    if payload.document_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly one of document_id or document_extraction_id is required.",
        )

    document = db.scalar(
        select(Document).where(
            Document.id == payload.document_id,
            Document.organization_id == ctx.organization_id,
            Document.case_id == case_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    extraction = db.scalar(
        select(DocumentExtraction)
        .where(
            DocumentExtraction.organization_id == ctx.organization_id,
            DocumentExtraction.document_id == document.id,
            DocumentExtraction.status == "completed",
        )
        .order_by(DocumentExtraction.created_at.desc(), DocumentExtraction.id.desc())
        .limit(1)
    )
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Completed document extraction not found.",
        )
    return ResolvedExtraction(document=document, extraction=extraction)


def parse_extracted_rows(extracted_json: object) -> list[ExtractedRow]:
    if not isinstance(extracted_json, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document extraction payload must be an object.",
        )

    parsed_rows: list[ExtractedRow] = []
    if "rows" in extracted_json:
        rows = extracted_json["rows"]
        if not isinstance(rows, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document extraction rows must be a list.",
            )
        for row_index, row in enumerate(rows):
            parsed_rows.append(
                ExtractedRow(
                    index=len(parsed_rows),
                    payload=row_payload(row),
                    locator={"shape": "rows", "row_index": row_index},
                )
            )
        return parsed_rows

    tables = extracted_json.get("tables")
    if isinstance(tables, list):
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            table_rows = table.get("rows")
            if not isinstance(table_rows, list):
                continue
            for row_index, row in enumerate(table_rows):
                locator: JsonObject = {
                    "shape": "tables",
                    "table_index": table_index,
                    "row_index": row_index,
                }
                for key in ("name", "page", "page_start", "page_end"):
                    if key in table:
                        locator[key] = cast(JsonValue, table[key])
                parsed_rows.append(
                    ExtractedRow(
                        index=len(parsed_rows),
                        payload=row_payload(row),
                        locator=locator,
                    )
                )
        return parsed_rows

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Document extraction payload must contain rows or tables.",
    )


def row_payload(row: object) -> JsonObject:
    if isinstance(row, dict):
        return {str(key): cast(JsonValue, value) for key, value in row.items()}
    return {"value": cast(JsonValue, row)}
