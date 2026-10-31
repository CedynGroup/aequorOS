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
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.capital.domain.crm_haircuts import DEFAULT_CRM_HAIRCUTS
from app.core.key_management.presentation import (
    LOCKED_TEXT,
    SESSION_STATE,
    SESSION_SUPPORT,
    PresentationState,
)
from app.credit.schemas.credit_params import (
    CrmHaircutRead,
    CrmHaircutRegisterRead,
    CrmHaircutUpdate,
    EclAssumptionRead,
    EclAssumptionRegisterRead,
    EclAssumptionUpdate,
)
from app.credit.schemas.regulatory_credit import (
    ConcentrationLimitRead,
    ConcentrationLimitRegisterRead,
    ConcentrationLimitUpdate,
    CreditThresholdRead,
    CreditThresholdRegisterRead,
    CreditThresholdUpdate,
)
from app.db.base import utc_now
from app.domain.capital.ecl import ALL_SEGMENTS, normalize_segment
from app.identity.public import Bank
from app.live.domain.positions.families import LOAN_EXPOSURE_CATEGORIES
from app.live.public import live_refresh_triggers
from app.policy.public import (
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    get_active_params,
)
from app.services.audit import record_event

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


#: The segments an ECL assumption may name: the ``ALL`` fallback or a loan
#: exposure category, in the register's stored spelling.
_ECL_SEGMENTS = frozenset(
    {ALL_SEGMENTS} | {normalize_segment(category) for category in LOAN_EXPOSURE_CATEGORIES}
)


def _require_ecl_segment(raw: str) -> str:
    """The stored spelling of an ECL segment, refusing one no loan can land in.

    IFRS 9 ¶B5.5.5 groups exposures by shared credit risk characteristics; the
    group a Board assumption names must be the grouping the exposures carry, or
    the assumption prices nothing and the run silently uses the fallback.
    """
    segment = normalize_segment(raw)
    if segment not in _ECL_SEGMENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"ECL segment '{raw.strip()}' is not a loan exposure category. "
                f"Use one of: {', '.join(sorted(_ECL_SEGMENTS))}."
            ),
        )
    return segment


def update_ecl_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: EclAssumptionUpdate
) -> EclAssumptionRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = bank.jurisdiction_code
    seen: set[tuple[str, int]] = set()
    previous_assumptions: list[dict[str, JsonValue]] = []
    written: list[ParamEclAssumption] = []
    now = utc_now()
    for entry in payload.assumptions:
        segment = _require_ecl_segment(entry.segment)
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
            previous_assumptions.append(
                {
                    "segment": row.segment,
                    "stage": row.stage,
                    "pd_pct": str(row.pd_pct),
                    "lgd_pct": str(row.lgd_pct),
                }
            )
            row.effective_to = payload.effective_from
        created = ParamEclAssumption(
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
        db.add(created)
        written.append(created)
    db.flush()
    for created in written:
        db.refresh(created)
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
            "before": {
                "assumptions": sorted(
                    previous_assumptions,
                    key=lambda item: (str(item["segment"]), str(item["stage"])),
                )
            },
            "after": {
                "assumptions": [
                    {
                        "segment": entry.segment,
                        "stage": entry.stage,
                        "pd_pct": str(entry.pd_pct),
                        "lgd_pct": str(entry.lgd_pct),
                    }
                    for entry in sorted(written, key=lambda item: (item.segment, item.stage))
                ]
            },
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
    previous_haircuts: dict[str, JsonValue] = {}
    written: list[ParamCrmHaircut] = []
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
            previous_haircuts[row.collateral_class] = str(row.haircut_pct)
            row.effective_to = payload.effective_from
        created = ParamCrmHaircut(
            organization_id=ctx.organization_id,
            jurisdiction_code=jurisdiction,
            collateral_class=normalized,
            haircut_pct=pct,
            effective_from=payload.effective_from,
            approved_by=payload.approved_by,
            approval_timestamp=now,
            notes=payload.notes,
        )
        db.add(created)
        written.append(created)
    db.flush()
    for created in written:
        db.refresh(created)
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
            "before": {"haircuts": previous_haircuts},
            "after": {
                "haircuts": {entry.collateral_class: str(entry.haircut_pct) for entry in written}
            },
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
    presentation = db.info.get(SESSION_STATE)
    support = bool(db.info.get(SESSION_SUPPORT)) or (
        isinstance(presentation, PresentationState) and presentation.support
    )
    limits: list[ConcentrationLimitRead] = []
    for row in rows:
        key = row.bucket_key
        if (
            support
            and row.dimension == "single_name"
            and key is not None
            and not key.startswith("pos:")
        ):
            key = LOCKED_TEXT
            if isinstance(presentation, PresentationState):
                presentation.record(bank.id, "support")
        limits.append(
            ConcentrationLimitRead(
                dimension=row.dimension,
                limit_kind=row.limit_kind,
                bucket_key=key,
                value=row.value,
                effective_from=row.effective_from.isoformat(),
                approved_by=row.approved_by,
            )
        )
    return ConcentrationLimitRegisterRead(
        as_of=as_of.isoformat(),
        limits=limits,
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
    written: list[ParamConcentrationLimit] = []
    now = utc_now()
    open_rows = db.scalars(
        select(ParamConcentrationLimit).where(
            ParamConcentrationLimit.organization_id == ctx.organization_id,
            ParamConcentrationLimit.jurisdiction_code == jurisdiction,
            ParamConcentrationLimit.effective_to.is_(None),
        )
    ).all()
    previous_limits: list[dict[str, JsonValue]] = []
    for row in open_rows:
        if row.effective_from >= payload.effective_from:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"An open generation effective {row.effective_from} already starts "
                    "on or after the requested effective date."
                ),
            )
        previous_limits.append(
            {
                "dimension": row.dimension,
                "limit_kind": row.limit_kind,
                "bucket_key": row.bucket_key,
                "value": str(row.value),
            }
        )
        row.effective_to = payload.effective_from
    for entry in payload.limits:
        created = ParamConcentrationLimit(
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
        db.add(created)
        written.append(created)
    db.flush()
    for created in written:
        db.refresh(created)
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
            "before": {
                "limits": sorted(
                    previous_limits,
                    key=lambda item: (
                        str(item["dimension"]),
                        str(item["limit_kind"]),
                        item["bucket_key"] is not None,
                        str(item["bucket_key"] or ""),
                    ),
                )
            },
            "after": {
                "limits": [
                    {
                        "dimension": entry.dimension,
                        "limit_kind": entry.limit_kind,
                        "bucket_key": entry.bucket_key,
                        "value": str(entry.value),
                    }
                    for entry in sorted(
                        written,
                        key=lambda item: (
                            item.dimension,
                            item.limit_kind,
                            item.bucket_key is not None,
                            item.bucket_key or "",
                        ),
                    )
                ]
            },
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
    previous_thresholds: dict[str, JsonValue] = {}
    written: list[ParamCreditThreshold] = []
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
            previous_thresholds[row.threshold_code] = str(row.value_pct)
            row.effective_to = payload.effective_from
        created = ParamCreditThreshold(
            organization_id=ctx.organization_id,
            jurisdiction_code=jurisdiction,
            threshold_code=code,
            value_pct=pct,
            effective_from=payload.effective_from,
            approved_by=payload.approved_by,
            approval_timestamp=now,
        )
        db.add(created)
        written.append(created)
    db.flush()
    for created in written:
        db.refresh(created)
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
            "before": {"thresholds": previous_thresholds},
            "after": {
                "thresholds": {entry.threshold_code: str(entry.value_pct) for entry in written}
            },
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
