from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.schemas.financial_workspace_mapping import (
    FinancialWorkspaceMapRequest,
    FinancialWorkspaceMapResponse,
    FinancialWorkspaceMapRowSummary,
    FinancialWorkspaceMapSummary,
)
from app.services.cases import get_case_or_404
from app.services.financial_mapping.extraction import parse_extracted_rows, resolve_extraction
from app.services.financial_mapping.row_mapper import RowMappingContext, map_source_row
from app.services.financial_mapping.types import count, empty_counts
from app.services.financial_mapping.upserts import get_or_create_source_row


def map_financial_workspace(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    payload: FinancialWorkspaceMapRequest,
) -> FinancialWorkspaceMapResponse:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    resolved = resolve_extraction(db, ctx, case.id, payload)
    rows = parse_extracted_rows(resolved.extraction.extracted_json)

    created_counts = empty_counts()
    reused_counts = empty_counts()
    unmapped_rows: list[FinancialWorkspaceMapRowSummary] = []
    mapped_row_count = 0

    for extracted_row in rows:
        source_row, source_row_created = get_or_create_source_row(
            db,
            ctx,
            case.id,
            document_id=resolved.document.id,
            document_extraction_id=resolved.extraction.id,
            row=extracted_row,
        )
        count(created_counts if source_row_created else reused_counts, "source_rows")

        mapped_record_count = map_source_row(
            RowMappingContext(
                db=db,
                tenant=ctx,
                case_id=case.id,
                source_row=source_row,
                row=extracted_row,
                document_extraction_id=resolved.extraction.id,
                created_counts=created_counts,
                reused_counts=reused_counts,
            )
        )
        if mapped_record_count == 0:
            unmapped_rows.append(
                FinancialWorkspaceMapRowSummary(
                    row_index=extracted_row.index,
                    source_row_id=source_row.id,
                    reason="No supported financial fields were found.",
                    locator=extracted_row.locator,
                )
            )
        else:
            mapped_row_count += 1

    db.commit()

    return FinancialWorkspaceMapResponse(
        case_id=case.id,
        organization_id=case.organization_id,
        document_id=resolved.document.id,
        document_extraction_id=resolved.extraction.id,
        summary=FinancialWorkspaceMapSummary(
            source_row_count=len(rows),
            mapped_source_row_count=mapped_row_count,
            unmapped_source_row_count=len(unmapped_rows),
        ),
        created=created_counts,
        reused=reused_counts,
        unmapped_rows=unmapped_rows,
    )
