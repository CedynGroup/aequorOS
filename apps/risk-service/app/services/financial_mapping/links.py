from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialObligation,
    FinancialRecordSourceLink,
)
from app.schemas.common import JsonObject, JsonValue
from app.services.financial_mapping.normalization import normalize_key
from app.services.financial_mapping.types import (
    MAPPER_VERSION,
    SUPPORTED_FIELD_NAMES,
    ExtractedRow,
    MapperCounts,
    RecordTable,
    count,
)


def linked_record(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    source_row_id: UUID,
    record_table: Literal[
        "financial_balances",
        "financial_cash_flows",
        "financial_obligations",
        "financial_covenants",
    ],
) -> FinancialBalance | FinancialCashFlow | FinancialObligation | FinancialCovenant | None:
    link = db.scalar(
        select(FinancialRecordSourceLink).where(
            FinancialRecordSourceLink.organization_id == ctx.organization_id,
            FinancialRecordSourceLink.case_id == case_id,
            FinancialRecordSourceLink.source_row_id == source_row_id,
            FinancialRecordSourceLink.record_table == record_table,
        )
    )
    if link is None:
        return None
    if record_table == "financial_balances":
        return db.scalar(
            select(FinancialBalance).where(
                FinancialBalance.id == link.record_id,
                FinancialBalance.organization_id == ctx.organization_id,
                FinancialBalance.case_id == case_id,
            )
        )
    if record_table == "financial_cash_flows":
        return db.scalar(
            select(FinancialCashFlow).where(
                FinancialCashFlow.id == link.record_id,
                FinancialCashFlow.organization_id == ctx.organization_id,
                FinancialCashFlow.case_id == case_id,
            )
        )
    if record_table == "financial_obligations":
        return db.scalar(
            select(FinancialObligation).where(
                FinancialObligation.id == link.record_id,
                FinancialObligation.organization_id == ctx.organization_id,
                FinancialObligation.case_id == case_id,
            )
        )
    if record_table == "financial_covenants":
        return db.scalar(
            select(FinancialCovenant).where(
                FinancialCovenant.id == link.record_id,
                FinancialCovenant.organization_id == ctx.organization_id,
                FinancialCovenant.case_id == case_id,
            )
        )
    return None


def link_field(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    record_table: RecordTable,
    record_id: UUID,
    source_row_id: UUID,
    field_name: str,
    source_field: str,
    metadata: JsonObject,
    created_counts: MapperCounts,
    reused_counts: MapperCounts,
) -> None:
    existing = existing_source_link(
        db,
        ctx,
        case_id,
        record_table=record_table,
        record_id=record_id,
        source_row_id=source_row_id,
        field_name=field_name,
        source_field=source_field,
    )
    if existing is not None:
        count(reused_counts, "record_source_links")
        return

    source_link = FinancialRecordSourceLink(
        organization_id=ctx.organization_id,
        case_id=case_id,
        record_table=record_table,
        record_id=record_id,
        source_row_id=source_row_id,
        field_name=field_name,
        source_field=source_field,
        confidence=Decimal("1.0000"),
        metadata_=metadata,
    )
    try:
        with db.begin_nested():
            db.add(source_link)
            db.flush()
    except IntegrityError:
        with db.no_autoflush:
            existing = existing_source_link(
                db,
                ctx,
                case_id,
                record_table=record_table,
                record_id=record_id,
                source_row_id=source_row_id,
                field_name=field_name,
                source_field=source_field,
            )
        if existing is None:
            raise
        count(reused_counts, "record_source_links")
        return

    count(created_counts, "record_source_links")


def existing_source_link(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    record_table: RecordTable,
    record_id: UUID,
    source_row_id: UUID,
    field_name: str,
    source_field: str,
) -> FinancialRecordSourceLink | None:
    return db.scalar(
        select(FinancialRecordSourceLink).where(
            FinancialRecordSourceLink.organization_id == ctx.organization_id,
            FinancialRecordSourceLink.case_id == case_id,
            FinancialRecordSourceLink.record_table == record_table,
            FinancialRecordSourceLink.record_id == record_id,
            FinancialRecordSourceLink.source_row_id == source_row_id,
            FinancialRecordSourceLink.field_name == field_name,
            FinancialRecordSourceLink.source_field == source_field,
        )
    )


def mapper_metadata(
    row: ExtractedRow,
    document_extraction_id: UUID,
) -> JsonObject:
    normalized_fields = {normalize_key(key) for key in row.payload}
    unknown_source_fields: list[JsonValue] = [
        field for field in sorted(normalized_fields - SUPPORTED_FIELD_NAMES)
    ]
    return {
        "mapper_version": MAPPER_VERSION,
        "document_extraction_id": str(document_extraction_id),
        "locator": row.locator,
        "unknown_source_fields": unknown_source_fields,
    }
