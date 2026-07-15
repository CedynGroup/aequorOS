from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import FinancialCovenant, FinancialObligation, FinancialReportingPeriod
from app.schemas.financial_workspace import (
    FinancialCovenantComplianceStatus,
    FinancialCovenantCreate,
    FinancialCovenantMutationResponse,
    FinancialCovenantOperator,
    FinancialCovenantUpdate,
)
from app.services.financial_canonical_edits import (
    bad_request,
    create_record,
    manual_metadata,
    payload_values,
    update_record,
    validate_link,
)
from app.services.financial_mapping.normalization import normalize_text
from app.services.financial_mapping.upserts import canonical_dedupe_key

COVENANT_TABLE = "financial_covenants"


def create_covenant(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    payload: FinancialCovenantCreate,
) -> FinancialCovenantMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    validate_links(db, ctx, case_id, values)
    if not values["name"].strip():
        bad_request("Covenant name is required.")
    if not values["metric"].strip():
        bad_request("Covenant metric is required.")
    values["name"] = values["name"].strip()
    values["metric"] = normalize_text(values["metric"])
    if values.get("compliance_status") is None:
        values["compliance_status"] = compliance_status(
            values["operator"], values["threshold"], values.get("actual_value")
        )
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = covenant_dedupe(values)
    return cast(
        FinancialCovenantMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialCovenant,
            COVENANT_TABLE,
            values,
            reason,
            FinancialCovenantMutationResponse,
        ),
    )


def update_covenant(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    covenant_id: UUID,
    payload: FinancialCovenantUpdate,
) -> FinancialCovenantMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    validate_links(db, ctx, case_id, updates)
    if "name" in updates and updates["name"] is not None:
        if not updates["name"].strip():
            bad_request("Covenant name is required.")
        updates["name"] = updates["name"].strip()
    elif "name" in updates:
        bad_request("Covenant name is required.")
    if "metric" in updates and updates["metric"] is not None:
        if not updates["metric"].strip():
            bad_request("Covenant metric is required.")
        updates["metric"] = normalize_text(updates["metric"])
    elif "metric" in updates:
        bad_request("Covenant metric is required.")
    if "operator" in updates and updates["operator"] is None:
        bad_request("Covenant operator is required.")
    if "threshold" in updates and updates["threshold"] is None:
        bad_request("Covenant threshold is required.")
    if "compliance_status" not in updates and {
        "operator",
        "threshold",
        "actual_value",
    }.intersection(updates):
        current = db.scalar(
            select(FinancialCovenant).where(
                FinancialCovenant.id == covenant_id,
                FinancialCovenant.organization_id == ctx.organization_id,
                FinancialCovenant.case_id == case_id,
            )
        )
        if current is not None:
            updates["compliance_status"] = compliance_status(
                updates.get("operator", current.operator),
                updates.get("threshold", current.threshold),
                updates.get("actual_value", current.actual_value),
            )
    return cast(
        FinancialCovenantMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            covenant_id,
            FinancialCovenant,
            COVENANT_TABLE,
            updates,
            reason,
            covenant_dedupe,
            FinancialCovenantMutationResponse,
        ),
    )


def compliance_status(
    operator: FinancialCovenantOperator,
    threshold: Decimal,
    actual_value: Decimal | None,
) -> FinancialCovenantComplianceStatus:
    if actual_value is None:
        return "unknown"
    comparisons = {
        "lt": actual_value < threshold,
        "lte": actual_value <= threshold,
        "eq": actual_value == threshold,
        "gte": actual_value >= threshold,
        "gt": actual_value > threshold,
    }
    return "compliant" if comparisons[operator] else "non_compliant"


def covenant_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key(
        "covenant",
        [
            values.get("obligation_id"),
            values.get("reporting_period_id"),
            normalize_text(values.get("name")),
            normalize_text(values.get("metric")),
            values.get("operator"),
            values.get("threshold"),
        ],
    )


def validate_links(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    values: dict[str, Any],
) -> None:
    if "obligation_id" in values:
        validate_link(
            db,
            ctx,
            case_id,
            FinancialObligation,
            values["obligation_id"],
            "Obligation/facility",
        )
    if "reporting_period_id" in values:
        validate_link(
            db,
            ctx,
            case_id,
            FinancialReportingPeriod,
            values["reporting_period_id"],
            "Reporting period",
        )
