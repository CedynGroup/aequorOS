"""Board threshold register for the BoG liquidity monitoring tools.

LMTD 2026 ¶11(b)–(e): the Board sets internal thresholds for the six
monitoring tools at least annually. This service owns the register's
lifecycle — resolving the thresholds active on a date (Board rows over the
directive's published minimums) and recording new Board-approved generations
with full audit evidence. The LMT return generator reads the same resolution
seam, so the ratios a bank files are judged against exactly the floors its
Board adopted.

``BANK_MINIMUM_PCT`` is the single source of truth for the Table 1 ratio
codes and the directive's published bank minimums (the regulatory floor when
no Board row exists); ``le_generation`` imports it rather than duplicating.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.authority.outcomes import OutcomeState, outcome
from app.models import Bank, ParamLiquidityHaircut, ParamLiquidityThreshold
from app.schemas.liquidity_thresholds import (
    InstitutionClass,
    LiquidityHaircutRead,
    LiquidityHaircutScheduleRead,
    LiquidityHaircutUpdate,
    LiquidityThresholdHistoryRead,
    LiquidityThresholdRead,
    LiquidityThresholdRegisterRead,
    LiquidityThresholdUpdate,
)
from app.services import jurisdictions, regulatory_parameters
from app.services.audit import record_event
from app.services.institution_types import institution_class as _resolve_institution_class
from app.services.params import get_active_params

# LMTD Table 1 published minimums for BANKS.
#
# The LMTD is an EXPOSURE DRAFT — posted 19 February 2026, effective 1 January
# 2027 — so nothing here is an in-force regulatory floor today. ¶9 would make the
# SDI set binding compliance ratios ON COMMENCEMENT; until then these are the
# directive's published figures, used as the default when a Board has adopted no
# threshold of its own. ``app/domain/authority/registry.py`` carries the same
# status as data (``instrument_in_force=False``); this comment must not drift
# from it. SDI calibrations join when an SDI tenant exists.
BANK_MINIMUM_PCT: dict[str, Decimal] = {
    "narrow_to_volatile": Decimal("80"),
    "broad_to_volatile": Decimal("100"),
    "narrow_to_short_term": Decimal("50"),
    "broad_to_short_term": Decimal("70"),
    "narrow_to_total_deposits": Decimal("60"),
    "broad_to_total_deposits": Decimal("80"),
    "narrow_to_total_assets": Decimal("30"),
    "broad_to_total_assets": Decimal("50"),
}

# Limit-shaped codes the Board may adopt beyond the Table 1 ratio floors.
# No regulatory default exists for these: absent a Board row, no check runs
# (LMTD para 11(c)-(e) are Board obligations, not published calibrations).
EXTRA_THRESHOLD_CODES = ("currency_mismatch_limit_pct",)

_INSTITUTION_CLASSES = ("bank", "sdi")


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _jurisdiction(bank: Bank) -> str:
    # Fail-closed (enterprise audit 2026-08-20 §6): ``or "GH"`` here would file a
    # Nigerian tenant's board register under Ghana's jurisdiction key.
    return jurisdictions.jurisdiction_code(bank)


def _sdi_floor_unseeded(bank: Bank, jurisdiction: str, code: str) -> HTTPException:
    """The SDI Table-1 floor is not configured — refuse, never substitute.

    Carries WS-A's ``POLICY_UNRESOLVED`` outcome detail so the reason has the
    same shape as every other fail-closed state, and 409 (this codebase's
    configured-state conflict code) so the operator is told what to seed.
    """
    detail = outcome(
        OutcomeState.POLICY_UNRESOLVED,
        metric_id=code,
        reason=(
            f"The liquidity floor '{code}' is not configured for this institution's "
            "licence class. These ratios are binding compliance measures for a "
            "specialised deposit-taking institution and its floors differ from a "
            "bank's, so a bank floor is never shown in their place. Seed the class "
            "floors in the regulatory-parameter control plane."
        ),
        items=(f"param:{code}",),
        context={
            "bank_id": bank.id,
            "organization_id": bank.organization_id,
            "institution_class": "sdi",
            "jurisdiction_code": jurisdiction,
        },
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail.message)


def get_register(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> LiquidityThresholdRegisterRead:
    """The resolved register on ``as_of`` plus the full generation history."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = _jurisdiction(bank)

    # Resolve the tenant's institution class (docs/sdi.md §4.1): the Monitoring
    # register binds an SDI against the SDI Table-1 floors, a bank against the
    # bank floors — never the bank floors for both.
    klass = cast(InstitutionClass, _resolve_institution_class(db, bank))
    active_rows = {
        (row.institution_class, row.threshold_code): row
        for row in get_active_params(
            db, ctx.organization_id, jurisdiction, ParamLiquidityThreshold, as_of
        )
    }
    thresholds: list[LiquidityThresholdRead] = []
    for code, minimum in BANK_MINIMUM_PCT.items():
        board = active_rows.get((klass, code))
        if board is not None:
            # Tighten-only guard (QA audit 2026-08-20 P1-5): a board LMTD floor may
            # only be at least as strict as the control-plane regulatory floor. A
            # weaker board value is clamped up to it — the register cannot weaken the
            # regulatory minimum. A stricter board value (higher, for a floor) stands.
            board_value = Decimal(str(board.threshold_pct))
            param = regulatory_parameters.try_resolve(db, bank, code, as_of=as_of)
            control_floor = param.normalized_value if param is not None else None
            effective = (
                regulatory_parameters.tighten(code, board_value, control_floor)
                if control_floor is not None
                else board_value
            )
            thresholds.append(
                LiquidityThresholdRead(
                    threshold_code=code,
                    institution_class=klass,
                    threshold_pct=effective,
                    source="board_register",
                    effective_from=board.effective_from,
                    effective_to=board.effective_to,
                    approved_by=board.approved_by,
                    approval_timestamp=board.approval_timestamp,
                    notes=board.notes,
                )
            )
        else:
            # Class default from the control plane (normalised so 80.000000 shows
            # as 80 — audit M1).
            param = regulatory_parameters.try_resolve(db, bank, code, as_of=as_of)
            normalized = param.normalized_value if param is not None else None
            if normalized is not None:
                floor = normalized
            elif klass == "bank":
                # The directive's published BANK minimum, for a BANK — the
                # defensive fallback, byte-identical when the control plane is
                # unseeded.
                floor = minimum
            else:
                # An SDI must never inherit the bank floor. Table 1 BINDS for an
                # SDI (LMTD 2026 para 9) at different values, so returning the
                # bank number under an ``institution_class='sdi'`` label was the
                # wrong floor wearing the right label — 80% shown where the SDI
                # floor is 90% (forensic audit 2026-08-21). Same fail-closed rule
                # the LMT return generator already applies
                # (le_generation._table1_thresholds).
                raise _sdi_floor_unseeded(bank, jurisdiction, code)
            thresholds.append(
                LiquidityThresholdRead(
                    threshold_code=code,
                    institution_class=klass,
                    threshold_pct=floor,
                    source="regulatory_default",
                )
            )

    for (row_klass, code), row in sorted(active_rows.items()):
        if row_klass == klass and code in EXTRA_THRESHOLD_CODES:
            thresholds.append(
                LiquidityThresholdRead(
                    threshold_code=code,
                    institution_class=klass,
                    threshold_pct=Decimal(str(row.threshold_pct)),
                    source="board_register",
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                    approved_by=row.approved_by,
                    approval_timestamp=row.approval_timestamp,
                    notes=row.notes,
                )
            )

    history_rows = db.scalars(
        select(ParamLiquidityThreshold)
        .where(
            ParamLiquidityThreshold.organization_id == ctx.organization_id,
            ParamLiquidityThreshold.jurisdiction_code == jurisdiction,
        )
        .order_by(
            ParamLiquidityThreshold.threshold_code,
            ParamLiquidityThreshold.effective_from.desc(),
        )
    ).all()
    history = [
        LiquidityThresholdHistoryRead(
            threshold_code=row.threshold_code,
            institution_class=cast(InstitutionClass, row.institution_class),
            threshold_pct=Decimal(str(row.threshold_pct)),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_by=row.approved_by,
            approval_timestamp=row.approval_timestamp,
            notes=row.notes,
        )
        for row in history_rows
    ]
    return LiquidityThresholdRegisterRead(
        bank_id=bank.id,
        jurisdiction_code=jurisdiction,
        as_of_date=as_of,
        thresholds=thresholds,
        history=history,
    )


def update_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: LiquidityThresholdUpdate
) -> LiquidityThresholdRegisterRead:
    """Record a Board-approved threshold generation.

    Effective-dated, never destructive: open generations for the updated
    codes are closed at the new ``effective_from`` and new rows carry the
    Board evidence (``approved_by`` names the approving authority — a Board
    minute reference or officer — and ``approval_timestamp`` is stamped
    server-side). The write is audited; history keeps every generation.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = _jurisdiction(bank)
    if payload.institution_class not in _INSTITUTION_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"institution_class must be one of {_INSTITUTION_CLASSES}.",
        )
    # The register is read back for the TENANT's own licence class, so a
    # generation recorded under the other class is dead data that silently does
    # nothing. The payload's field defaults to "bank", which meant a specialised
    # deposit-taking institution's Board could adopt floors that were then never
    # applied to it (forensic audit 2026-08-21, parameter isolation). Refuse the
    # mismatch instead of writing a row that will not be read.
    klass = _resolve_institution_class(db, bank)
    if payload.institution_class != klass:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"This institution is supervised as '{klass}'. A threshold generation "
                f"recorded as '{payload.institution_class}' would never be applied to "
                "it. Record the generation for this institution's own class."
            ),
        )
    unknown = sorted(
        set(payload.thresholds) - set(BANK_MINIMUM_PCT) - set(EXTRA_THRESHOLD_CODES)
    )
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown threshold codes: {', '.join(unknown)}.",
        )
    non_positive = sorted(code for code, pct in payload.thresholds.items() if pct <= 0)
    if non_positive:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Threshold percentages must be positive: {', '.join(non_positive)}.",
        )

    now = utc_now()
    for code, pct in payload.thresholds.items():
        open_rows = db.scalars(
            select(ParamLiquidityThreshold).where(
                ParamLiquidityThreshold.organization_id == ctx.organization_id,
                ParamLiquidityThreshold.jurisdiction_code == jurisdiction,
                ParamLiquidityThreshold.institution_class == payload.institution_class,
                ParamLiquidityThreshold.threshold_code == code,
                ParamLiquidityThreshold.effective_to.is_(None),
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
            ParamLiquidityThreshold(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                institution_class=payload.institution_class,
                threshold_code=code,
                threshold_pct=pct,
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
        event_type="liquidity_thresholds.updated",
        entity_type="param_liquidity_threshold",
        entity_id=bank.id,
        details={
            "institution_class": payload.institution_class,
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "codes": sorted(payload.thresholds),
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_register(db, ctx, bank_id, payload.effective_from)


def get_haircut_schedule(
    db: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> LiquidityHaircutScheduleRead:
    """The active liquidity-value schedule (LRMD ¶60–63) plus history."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = _jurisdiction(bank)
    active = [
        LiquidityHaircutRead(
            asset_class=row.asset_class,
            haircut_pct=Decimal(str(row.haircut_pct)),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_by=row.approved_by,
            approval_timestamp=row.approval_timestamp,
            notes=row.notes,
        )
        for row in get_active_params(
            db, ctx.organization_id, jurisdiction, ParamLiquidityHaircut, as_of
        )
    ]
    history_rows = db.scalars(
        select(ParamLiquidityHaircut)
        .where(
            ParamLiquidityHaircut.organization_id == ctx.organization_id,
            ParamLiquidityHaircut.jurisdiction_code == jurisdiction,
        )
        .order_by(ParamLiquidityHaircut.asset_class, ParamLiquidityHaircut.effective_from.desc())
    ).all()
    history = [
        LiquidityHaircutRead(
            asset_class=row.asset_class,
            haircut_pct=Decimal(str(row.haircut_pct)),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_by=row.approved_by,
            approval_timestamp=row.approval_timestamp,
            notes=row.notes,
        )
        for row in history_rows
    ]
    return LiquidityHaircutScheduleRead(
        bank_id=bank.id,
        jurisdiction_code=jurisdiction,
        as_of_date=as_of,
        haircuts=sorted(active, key=lambda item: item.asset_class),
        history=history,
    )


def update_haircut_schedule(
    db: Session, ctx: TenantContext, bank_id: str, payload: LiquidityHaircutUpdate
) -> LiquidityHaircutScheduleRead:
    """Record a reviewed liquidity-value generation (LRMD ¶62(b) evidence)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    jurisdiction = _jurisdiction(bank)
    out_of_range = sorted(
        asset_class
        for asset_class, pct in payload.haircuts.items()
        if pct < 0 or pct > Decimal("100")
    )
    if out_of_range:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Haircut percentages must be within [0, 100]: {', '.join(out_of_range)}.",
        )

    now = utc_now()
    for asset_class, pct in payload.haircuts.items():
        normalized = asset_class.strip().upper()
        open_rows = db.scalars(
            select(ParamLiquidityHaircut).where(
                ParamLiquidityHaircut.organization_id == ctx.organization_id,
                ParamLiquidityHaircut.jurisdiction_code == jurisdiction,
                ParamLiquidityHaircut.asset_class == normalized,
                ParamLiquidityHaircut.effective_to.is_(None),
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
            ParamLiquidityHaircut(
                organization_id=ctx.organization_id,
                jurisdiction_code=jurisdiction,
                asset_class=normalized,
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
        event_type="liquidity_haircuts.updated",
        entity_type="param_liquidity_haircut",
        entity_id=bank.id,
        details={
            "effective_from": payload.effective_from.isoformat(),
            "approved_by": payload.approved_by,
            "asset_classes": sorted(cls.strip().upper() for cls in payload.haircuts),
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_haircut_schedule(db, ctx, bank_id, payload.effective_from)
