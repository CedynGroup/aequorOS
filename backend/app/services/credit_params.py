"""ECL assumption + CRM haircut registers (product.md §Phase 2 items 8/9).

Both follow the effective-dated generation discipline of the liquidity
threshold register: PUT closes the open generation and records a new one
with approval evidence, audited. The CRM GET merges the Basel II ¶151 code
defaults (``regulatory_capital.DEFAULT_CRM_HAIRCUTS``) under the bank's own
rows so the caller always sees the schedule the engine will actually apply.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
)
from app.schemas.credit_params import (
    CrmHaircutRead,
    CrmHaircutRegisterRead,
    CrmHaircutUpdate,
    EclAssumptionRead,
    EclAssumptionRegisterRead,
    EclAssumptionUpdate,
)
from app.schemas.regulatory_credit import (
    ConcentrationLimitRead,
    ConcentrationLimitRegisterRead,
    ConcentrationLimitUpdate,
    CreditThresholdRead,
    CreditThresholdRegisterRead,
    CreditThresholdUpdate,
)
from app.services import live_refresh_triggers
from app.services.audit import record_event
from app.services.params import get_active_params
from app.services.regulatory_capital import DEFAULT_CRM_HAIRCUTS

_HUNDRED = Decimal("100")


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _ecl_read(row: ParamEclAssumption) -> EclAssumptionRead:
    return EclAssumptionRead(
        segment=row.segment,
        stage=row.stage,
        pd_pct=Decimal(str(row.pd_pct)),
        lgd_pct=Decimal(str(row.lgd_pct)),
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        approved_by=row.approved_by,
        approval_timestamp=row.approval_timestamp,
        notes=row.notes,
    )


def get_ecl_register(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> EclAssumptionRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    active = [
        _ecl_read(row)
        for row in get_active_params(
            db, ctx.organization_id, jurisdiction, ParamEclAssumption, as_of
        )
    ]
    history = [
        _ecl_read(row)
        for row in db.scalars(
            select(ParamEclAssumption)
            .where(
                ParamEclAssumption.organization_id == ctx.organization_id,
                ParamEclAssumption.jurisdiction_code == jurisdiction,
            )
            .order_by(
                ParamEclAssumption.segment,
                ParamEclAssumption.stage,
                ParamEclAssumption.effective_from.desc(),
            )
        ).all()
    ]
    return EclAssumptionRegisterRead(
        bank_id=bank.id,
        jurisdiction_code=jurisdiction,
        as_of_date=as_of,
        assumptions=sorted(active, key=lambda item: (item.segment, item.stage)),
        history=history,
    )


def update_ecl_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: EclAssumptionUpdate
) -> EclAssumptionRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    seen: set[tuple[str, int]] = set()
    now = utc_now()
    for entry in payload.assumptions:
        segment = entry.segment.strip().upper()
        key = (segment, entry.stage)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Duplicate assumption for segment '{segment}' stage {entry.stage}.",
            )
        seen.add(key)
        open_rows = db.scalars(
            select(ParamEclAssumption).where(
                ParamEclAssumption.organization_id == ctx.organization_id,
                ParamEclAssumption.jurisdiction_code == jurisdiction,
                ParamEclAssumption.segment == segment,
                ParamEclAssumption.stage == entry.stage,
                ParamEclAssumption.effective_to.is_(None),
            )
        ).all()
        for row in open_rows:
            if row.effective_from >= payload.effective_from:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{segment} stage {entry.stage}: an open generation effective "
                        f"{row.effective_from} already starts on or after the requested date."
                    ),
                )
            row.effective_to = payload.effective_from
        db.add(
            ParamEclAssumption(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                segment=segment,
                stage=entry.stage,
                pd_pct=entry.pd_pct,
                lgd_pct=entry.lgd_pct,
                effective_from=payload.effective_from,
                approved_by=payload.approved_by,
                approval_timestamp=now,
                notes=payload.notes,
            )
        )
    db.flush()
    record_event(
        db,
        ctx,
        event_type="ecl_assumptions.updated",
        entity_type="param_ecl_assumption",
        entity_id=bank.id,
        details={
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "entries": sorted(f"{segment}:stage{stage}" for segment, stage in seen),
            "reason": payload.reason,
        },
    )
    live_refresh_triggers.enqueue_organization_change(
        db,
        organization_id=ctx.organization_id,
        jurisdiction_code=jurisdiction,
        reason="ECL assumption register updated",
    )
    db.commit()
    return get_ecl_register(db, ctx, bank_id, payload.effective_from)


def get_crm_register(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> CrmHaircutRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    rows = get_active_params(db, ctx.organization_id, jurisdiction, ParamCrmHaircut, as_of)
    merged: dict[str, CrmHaircutRead] = {
        collateral_class: CrmHaircutRead(
            collateral_class=collateral_class, haircut_pct=haircut, is_default=True
        )
        for collateral_class, haircut in DEFAULT_CRM_HAIRCUTS.items()
    }
    for row in rows:
        merged[row.collateral_class] = CrmHaircutRead(
            collateral_class=row.collateral_class,
            haircut_pct=Decimal(str(row.haircut_pct)),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_by=row.approved_by,
            approval_timestamp=row.approval_timestamp,
            notes=row.notes,
            is_default=False,
        )
    history = [
        CrmHaircutRead(
            collateral_class=row.collateral_class,
            haircut_pct=Decimal(str(row.haircut_pct)),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_by=row.approved_by,
            approval_timestamp=row.approval_timestamp,
            notes=row.notes,
        )
        for row in db.scalars(
            select(ParamCrmHaircut)
            .where(
                ParamCrmHaircut.organization_id == ctx.organization_id,
                ParamCrmHaircut.jurisdiction_code == jurisdiction,
            )
            .order_by(ParamCrmHaircut.collateral_class, ParamCrmHaircut.effective_from.desc())
        ).all()
    ]
    return CrmHaircutRegisterRead(
        bank_id=bank.id,
        jurisdiction_code=jurisdiction,
        as_of_date=as_of,
        haircuts=sorted(merged.values(), key=lambda item: item.collateral_class),
        history=history,
    )


def update_crm_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: CrmHaircutUpdate
) -> CrmHaircutRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    out_of_range = sorted(
        collateral_class
        for collateral_class, pct in payload.haircuts.items()
        if pct < 0 or pct > _HUNDRED
    )
    if out_of_range:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Haircut percentages must be within [0, 100]: {', '.join(out_of_range)}.",
        )
    now = utc_now()
    for collateral_class, pct in payload.haircuts.items():
        normalized = collateral_class.strip().upper()
        open_rows = db.scalars(
            select(ParamCrmHaircut).where(
                ParamCrmHaircut.organization_id == ctx.organization_id,
                ParamCrmHaircut.jurisdiction_code == jurisdiction,
                ParamCrmHaircut.collateral_class == normalized,
                ParamCrmHaircut.effective_to.is_(None),
            )
        ).all()
        for row in open_rows:
            if row.effective_from >= payload.effective_from:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{normalized}: an open generation effective {row.effective_from} "
                        "already starts on or after the requested effective date."
                    ),
                )
            row.effective_to = payload.effective_from
        db.add(
            ParamCrmHaircut(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                collateral_class=normalized,
                haircut_pct=pct,
                effective_from=payload.effective_from,
                approved_by=payload.approved_by,
                approval_timestamp=now,
                notes=payload.notes,
            )
        )
    db.flush()
    record_event(
        db,
        ctx,
        event_type="crm_haircuts.updated",
        entity_type="param_crm_haircut",
        entity_id=bank.id,
        details={
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "collateral_classes": sorted(cls.strip().upper() for cls in payload.haircuts),
            "reason": payload.reason,
        },
    )
    live_refresh_triggers.enqueue_organization_change(
        db,
        organization_id=ctx.organization_id,
        jurisdiction_code=jurisdiction,
        reason="CRM haircut register updated",
    )
    db.commit()
    return get_crm_register(db, ctx, bank_id, payload.effective_from)


# ---------------------------------------------------------------------------
# Board concentration-limit + credit-threshold registers (credit PR-3)
# ---------------------------------------------------------------------------

_CONCENTRATION_DIMENSIONS = (
    "single_name",
    "sector",
    "geography",
    "product",
    "collateral",
    "funding",
    "employer",
)
_CONCENTRATION_LIMIT_KINDS = ("share_of_book_pct", "share_of_capital_pct", "hhi")

#: The credit early-warning trigger codes the Board may set. A closed list so a
#: typo cannot mint an indicator nothing evaluates.
CREDIT_THRESHOLD_CODES = (
    "npl_board_trigger_pct",
    "provision_coverage_floor_pct",
    "employer_par30_ewi_pct",
    "restructured_ratio_watch_pct",
)


def get_concentration_limit_register(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> ConcentrationLimitRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamConcentrationLimit, as_of
    )
    return ConcentrationLimitRegisterRead(
        as_of=as_of.isoformat(),
        limits=[
            ConcentrationLimitRead(
                dimension=row.dimension,
                limit_kind=row.limit_kind,
                bucket_key=row.bucket_key,
                value=row.value,
                effective_from=row.effective_from.isoformat(),
                approved_by=row.approved_by,
            )
            for row in rows
        ],
    )


def update_concentration_limit_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: ConcentrationLimitUpdate
) -> ConcentrationLimitRegisterRead:
    """Replace the open generation of the Board concentration limits.

    The register starts EMPTY by design — the Guidelines prescribe the limit
    STRUCTURE, not values — so every row here is a Board decision with approver
    evidence, exactly like the CRM/ECL registers.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    problems = sorted(
        f"{entry.dimension}/{entry.limit_kind}"
        for entry in payload.limits
        if entry.dimension not in _CONCENTRATION_DIMENSIONS
        or entry.limit_kind not in _CONCENTRATION_LIMIT_KINDS
        or entry.value < 0
    )
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown dimension/kind or negative value: {', '.join(problems)}.",
        )
    seen: set[tuple[str, str, str | None]] = set()
    for entry in payload.limits:
        key = (entry.dimension, entry.limit_kind, entry.bucket_key)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Duplicate limit row: {entry.dimension}/{entry.limit_kind}.",
            )
        seen.add(key)
    now = utc_now()
    open_rows = db.scalars(
        select(ParamConcentrationLimit).where(
            ParamConcentrationLimit.organization_id == ctx.organization_id,
            ParamConcentrationLimit.jurisdiction_code == jurisdiction,
            ParamConcentrationLimit.effective_to.is_(None),
        )
    ).all()
    for row in open_rows:
        if row.effective_from >= payload.effective_from:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"An open generation effective {row.effective_from} already starts "
                    "on or after the requested effective date."
                ),
            )
        row.effective_to = payload.effective_from
    for entry in payload.limits:
        db.add(
            ParamConcentrationLimit(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                dimension=entry.dimension,
                limit_kind=entry.limit_kind,
                bucket_key=entry.bucket_key,
                value=entry.value,
                effective_from=payload.effective_from,
                approved_by=payload.approved_by,
                approval_timestamp=now,
            )
        )
    db.flush()
    record_event(
        db,
        ctx,
        event_type="concentration_limits.updated",
        entity_type="param_concentration_limit",
        entity_id=bank.id,
        details={
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "rows": len(payload.limits),
            "reason": payload.reason,
        },
    )
    live_refresh_triggers.enqueue_organization_change(
        db,
        organization_id=ctx.organization_id,
        jurisdiction_code=jurisdiction,
        reason="Concentration limit register updated",
    )
    db.commit()
    return get_concentration_limit_register(db, ctx, bank_id, payload.effective_from)


def get_credit_threshold_register(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> CreditThresholdRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCreditThreshold, as_of
    )
    return CreditThresholdRegisterRead(
        as_of=as_of.isoformat(),
        thresholds=[
            CreditThresholdRead(
                threshold_code=row.threshold_code,
                value_pct=row.value_pct,
                effective_from=row.effective_from.isoformat(),
                approved_by=row.approved_by,
            )
            for row in rows
        ],
    )


def update_credit_threshold_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: CreditThresholdUpdate
) -> CreditThresholdRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    unknown = sorted(code for code in payload.thresholds if code not in CREDIT_THRESHOLD_CODES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown credit threshold code(s): {', '.join(unknown)}.",
        )
    negative = sorted(code for code, pct in payload.thresholds.items() if pct < 0)
    if negative:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Threshold values must be non-negative: {', '.join(negative)}.",
        )
    now = utc_now()
    for code, pct in payload.thresholds.items():
        open_rows = db.scalars(
            select(ParamCreditThreshold).where(
                ParamCreditThreshold.organization_id == ctx.organization_id,
                ParamCreditThreshold.jurisdiction_code == jurisdiction,
                ParamCreditThreshold.threshold_code == code,
                ParamCreditThreshold.effective_to.is_(None),
            )
        ).all()
        for row in open_rows:
            if row.effective_from >= payload.effective_from:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{code}: an open generation effective {row.effective_from} "
                        "already starts on or after the requested effective date."
                    ),
                )
            row.effective_to = payload.effective_from
        db.add(
            ParamCreditThreshold(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                threshold_code=code,
                value_pct=pct,
                effective_from=payload.effective_from,
                approved_by=payload.approved_by,
                approval_timestamp=now,
            )
        )
    db.flush()
    record_event(
        db,
        ctx,
        event_type="credit_thresholds.updated",
        entity_type="param_credit_threshold",
        entity_id=bank.id,
        details={
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "codes": sorted(payload.thresholds),
            "reason": payload.reason,
        },
    )
    live_refresh_triggers.enqueue_organization_change(
        db,
        organization_id=ctx.organization_id,
        jurisdiction_code=jurisdiction,
        reason="Credit threshold register updated",
    )
    db.commit()
    return get_credit_threshold_register(db, ctx, bank_id, payload.effective_from)
