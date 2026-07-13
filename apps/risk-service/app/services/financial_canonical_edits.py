from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    FinancialAccount,
    FinancialBalance,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialReportingPeriod,
)
from app.schemas.financial_workspace import (
    FinancialAccountCreate,
    FinancialAccountMutationResponse,
    FinancialAccountUpdate,
    FinancialBalanceCreate,
    FinancialBalanceMutationResponse,
    FinancialBalanceUpdate,
    FinancialInstitutionCreate,
    FinancialInstitutionMutationResponse,
    FinancialInstitutionUpdate,
    FinancialObligationCreate,
    FinancialObligationMutationResponse,
    FinancialObligationUpdate,
    FinancialReportingPeriodCreate,
    FinancialReportingPeriodMutationResponse,
    FinancialReportingPeriodUpdate,
)
from app.services.audit import record_event
from app.services.cases import get_case_or_404
from app.services.financial_mapping.normalization import normalize_currency, normalize_text
from app.services.financial_mapping.upserts import (
    canonical_account_dedupe_key,
    canonical_dedupe_key,
)
from app.services.financial_validation import validate_financial_data


def create_institution(
    db: Session, ctx: TenantContext, case_id: UUID, payload: FinancialInstitutionCreate
) -> FinancialInstitutionMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    require_value(values["name"], "Institution name is required.")
    values["name"] = values["name"].strip()
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = institution_dedupe(values)
    return cast(
        FinancialInstitutionMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialInstitution,
            "financial_institutions",
            values,
            reason,
            FinancialInstitutionMutationResponse,
        ),
    )


def update_institution(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    record_id: UUID,
    payload: FinancialInstitutionUpdate,
) -> FinancialInstitutionMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    if "name" in updates:
        require_value(updates["name"], "Institution name is required.")
        updates["name"] = updates["name"].strip()
    return cast(
        FinancialInstitutionMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            record_id,
            FinancialInstitution,
            "financial_institutions",
            updates,
            reason,
            institution_dedupe,
            FinancialInstitutionMutationResponse,
        ),
    )


def create_account(
    db: Session, ctx: TenantContext, case_id: UUID, payload: FinancialAccountCreate
) -> FinancialAccountMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    validate_link(
        db, ctx, case_id, FinancialInstitution, values.get("institution_id"), "Institution"
    )
    require_value(values["account_name"], "Account name is required.")
    values["account_name"] = values["account_name"].strip()
    if "currency" in values:
        values["currency"] = validated_currency(values["currency"])
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = account_dedupe(values)
    return cast(
        FinancialAccountMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialAccount,
            "financial_accounts",
            values,
            reason,
            FinancialAccountMutationResponse,
        ),
    )


def update_account(
    db: Session, ctx: TenantContext, case_id: UUID, record_id: UUID, payload: FinancialAccountUpdate
) -> FinancialAccountMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    if "institution_id" in updates:
        validate_link(
            db, ctx, case_id, FinancialInstitution, updates["institution_id"], "Institution"
        )
    if "account_name" in updates:
        require_value(updates["account_name"], "Account name is required.")
        updates["account_name"] = updates["account_name"].strip()
    if "currency" in updates:
        updates["currency"] = validated_currency(updates["currency"])
    return cast(
        FinancialAccountMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            record_id,
            FinancialAccount,
            "financial_accounts",
            updates,
            reason,
            account_dedupe,
            FinancialAccountMutationResponse,
        ),
    )


def create_reporting_period(
    db: Session, ctx: TenantContext, case_id: UUID, payload: FinancialReportingPeriodCreate
) -> FinancialReportingPeriodMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = period_dedupe(values)
    return cast(
        FinancialReportingPeriodMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialReportingPeriod,
            "financial_reporting_periods",
            values,
            reason,
            FinancialReportingPeriodMutationResponse,
        ),
    )


def update_reporting_period(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    record_id: UUID,
    payload: FinancialReportingPeriodUpdate,
) -> FinancialReportingPeriodMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    if "period_type" in updates and updates["period_type"] is None:
        bad_request("Reporting period type is required.")
    return cast(
        FinancialReportingPeriodMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            record_id,
            FinancialReportingPeriod,
            "financial_reporting_periods",
            updates,
            reason,
            period_dedupe,
            FinancialReportingPeriodMutationResponse,
        ),
    )


def create_balance(
    db: Session, ctx: TenantContext, case_id: UUID, payload: FinancialBalanceCreate
) -> FinancialBalanceMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    validate_link(db, ctx, case_id, FinancialAccount, values.get("account_id"), "Account")
    validate_link(
        db,
        ctx,
        case_id,
        FinancialReportingPeriod,
        values.get("reporting_period_id"),
        "Reporting period",
    )
    require_value(values["balance_type"], "Balance type is required.")
    if "currency" in values:
        values["currency"] = validated_currency(values["currency"])
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = balance_dedupe(values)
    return cast(
        FinancialBalanceMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialBalance,
            "financial_balances",
            values,
            reason,
            FinancialBalanceMutationResponse,
        ),
    )


def update_balance(
    db: Session, ctx: TenantContext, case_id: UUID, record_id: UUID, payload: FinancialBalanceUpdate
) -> FinancialBalanceMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    if "account_id" in updates:
        validate_link(db, ctx, case_id, FinancialAccount, updates["account_id"], "Account")
    if "reporting_period_id" in updates:
        validate_link(
            db,
            ctx,
            case_id,
            FinancialReportingPeriod,
            updates["reporting_period_id"],
            "Reporting period",
        )
    if "currency" in updates:
        updates["currency"] = validated_currency(updates["currency"])
    if "balance_type" in updates:
        require_value(updates["balance_type"], "Balance type is required.")
    if "amount" in updates and updates["amount"] is None:
        bad_request("Balance amount is required.")
    return cast(
        FinancialBalanceMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            record_id,
            FinancialBalance,
            "financial_balances",
            updates,
            reason,
            balance_dedupe,
            FinancialBalanceMutationResponse,
        ),
    )


def create_obligation(
    db: Session, ctx: TenantContext, case_id: UUID, payload: FinancialObligationCreate
) -> FinancialObligationMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    validate_obligation_links(db, ctx, case_id, values)
    require_value(values["obligation_type"], "Obligation type is required.")
    if "currency" in values:
        values["currency"] = validated_currency(values["currency"])
    values["dedupe_key"] = obligation_dedupe(values)
    values["details"] = manual_metadata(values.get("details", {}), "manual")
    return cast(
        FinancialObligationMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialObligation,
            "financial_obligations",
            values,
            reason,
            FinancialObligationMutationResponse,
        ),
    )


def update_obligation(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    record_id: UUID,
    payload: FinancialObligationUpdate,
) -> FinancialObligationMutationResponse:
    updates, reason = payload_values(payload, exclude_unset=True)
    validate_obligation_links(db, ctx, case_id, updates)
    if "currency" in updates:
        updates["currency"] = validated_currency(updates["currency"])
    if "obligation_type" in updates:
        require_value(updates["obligation_type"], "Obligation type is required.")
    return cast(
        FinancialObligationMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            record_id,
            FinancialObligation,
            "financial_obligations",
            updates,
            reason,
            obligation_dedupe,
            FinancialObligationMutationResponse,
        ),
    )


def create_record(  # noqa: PLR0913, UP047
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    model: Any,
    table: str,
    values: dict[str, Any],
    reason: str,
    response_type: type[BaseModel],
) -> BaseModel:
    require_actor(ctx)
    get_case_or_404(db, ctx.organization_id, case_id)
    record = model(organization_id=ctx.organization_id, case_id=case_id, **values)
    db.add(record)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manual entry conflicts with an existing canonical record.",
        ) from exc
    for field_name, new_value in auditable_values(values).items():
        add_history(
            db, ctx, case_id, table, cast(Any, record).id, field_name, None, new_value, reason
        )
    record_event(
        db,
        ctx,
        event_type="financial_record.created",
        entity_type=table,
        entity_id=cast(Any, record).id,
        details={"reason": reason},
    )
    validation = validate_and_commit(
        db,
        ctx,
        case_id,
        "Manual entry conflicts with an existing canonical record.",
    )
    db.refresh(record)
    return response_type(record=record, validation=validation)


def update_record(  # noqa: PLR0913, UP047
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    record_id: UUID,
    model: Any,
    table: str,
    updates: dict[str, Any],
    reason: str,
    dedupe: Callable[[dict[str, Any]], str],
    response_type: type[BaseModel],
) -> BaseModel:
    require_actor(ctx)
    get_case_or_404(db, ctx.organization_id, case_id)
    record = get_record_or_404(db, ctx, case_id, record_id, model, table)
    changed: dict[str, tuple[Any, Any]] = {}
    for field_name, new_value in updates.items():
        attr = "metadata_" if field_name == "metadata" else field_name
        previous = getattr(record, attr)
        normalized = (
            {} if field_name in {"metadata", "details"} and new_value is None else new_value
        )
        if previous != normalized:
            setattr(record, attr, normalized)
            changed[field_name] = (previous, normalized)
    provenance_attr = "details" if table == "financial_obligations" else "metadata_"
    provenance = manual_metadata(getattr(record, provenance_attr), "corrected")
    if provenance != getattr(record, provenance_attr):
        previous = getattr(record, provenance_attr)
        setattr(record, provenance_attr, provenance)
        provenance_field = "details" if provenance_attr == "details" else "metadata"
        original = changed.get(provenance_field, (previous, provenance))[0]
        changed[provenance_field] = (original, provenance)
    record.dedupe_key = dedupe(model_values(record))
    for field_name, (previous, new_value) in changed.items():
        add_history(db, ctx, case_id, table, record_id, field_name, previous, new_value, reason)
    record_event(
        db,
        ctx,
        event_type="financial_record.corrected",
        entity_type=table,
        entity_id=record_id,
        details={"reason": reason, "fields": sorted(changed)},
    )
    validation = validate_and_commit(
        db,
        ctx,
        case_id,
        "Correction conflicts with an existing canonical record.",
    )
    db.refresh(record)
    return response_type(record=record, validation=validation)


def get_record_or_404(  # noqa: PLR0913, UP047
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    record_id: UUID,
    model: Any,
    table: str,
) -> Any:
    record = db.scalar(
        select(model).where(
            model.id == record_id,
            model.organization_id == ctx.organization_id,
            model.case_id == case_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{table.removeprefix('financial_').replace('_', ' ').title()} not found.",
        )
    return record


def payload_values(
    payload: BaseModel, *, exclude_unset: bool = False
) -> tuple[dict[str, Any], str]:
    values = payload.model_dump(exclude_unset=exclude_unset)
    return values, values.pop("reason")


def auditable_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        ("metadata" if key == "metadata_" else key): value
        for key, value in values.items()
        if key != "dedupe_key"
    }


def add_history(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    table: str,
    record_id: UUID,
    field: str,
    previous: Any,
    new: Any,
    reason: str,
) -> None:
    db.add(
        FinancialManualEditHistory(
            organization_id=ctx.organization_id,
            case_id=case_id,
            record_table=table,
            record_id=record_id,
            field_name=field,
            previous_value=jsonable_encoder(previous),
            new_value=jsonable_encoder(new),
            edited_by=ctx.actor_user_id,
            reason=reason,
        )
    )


def manual_metadata(value: dict[str, Any], provenance: str) -> dict[str, Any]:
    return {**value, "provenance": provenance}


def require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required for canonical mutations.",
        )


def validate_link(  # noqa: PLR0913
    db: Session, ctx: TenantContext, case_id: UUID, model: type, record_id: UUID | None, label: str
) -> None:
    if record_id is None:
        return
    exists = db.scalar(
        select(model.id).where(
            model.id == record_id,
            model.organization_id == ctx.organization_id,
            model.case_id == case_id,
        )
    )
    if exists is None:
        bad_request(f"{label} link is not valid for this case.")


def validate_obligation_links(
    db: Session, ctx: TenantContext, case_id: UUID, values: dict[str, Any]
) -> None:
    for field, model, label in (
        ("institution_id", FinancialInstitution, "Institution"),
        ("account_id", FinancialAccount, "Account"),
        ("reporting_period_id", FinancialReportingPeriod, "Reporting period"),
    ):
        if field in values:
            validate_link(db, ctx, case_id, model, values[field], label)


def validated_currency(value: Any) -> str | None:
    if value is None:
        return None
    currency = normalize_currency(value)
    if currency is None:
        bad_request("Currency must be a 3-letter code.")
    return currency


def require_value(value: Any, message: str) -> None:
    if value is None or not str(value).strip():
        bad_request(message)


def bad_request(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def commit_or_conflict(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc


def validate_and_commit(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    conflict_message: str,
) -> Any:
    try:
        validation = validate_financial_data(db, ctx, case_id, commit=False)
        commit_or_conflict(db, conflict_message)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_message,
        ) from exc
    except Exception:
        db.rollback()
        raise
    return validation


def model_values(record: Any) -> dict[str, Any]:
    return {column.name: getattr(record, column.key) for column in record.__table__.columns}


def institution_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key("institution", [normalize_text(values["name"])])


def account_dedupe(values: dict[str, Any]) -> str:
    return canonical_account_dedupe_key(
        values.get("institution_id"),
        values.get("account_number"),
        values["account_name"],
        values.get("currency"),
    )


def period_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key(
        "reporting_period",
        [
            values.get("period_type"),
            values.get("start_date"),
            values.get("end_date"),
            values.get("as_of_date"),
            normalize_text(values.get("label")),
        ],
    )


def balance_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key(
        "balance",
        [
            values.get("account_id"),
            values.get("reporting_period_id"),
            normalize_text(values.get("balance_type")),
            cast(Decimal | None, values.get("amount")),
            values.get("currency"),
            values.get("as_of_date"),
        ],
    )


def obligation_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key(
        "obligation",
        [
            values.get("institution_id"),
            values.get("account_id"),
            values.get("reporting_period_id"),
            normalize_text(values.get("obligation_type")),
            normalize_text(values.get("facility_type")),
            values.get("principal_amount"),
            values.get("outstanding_amount"),
            values.get("currency"),
        ],
    )
