"""The governed forecast assumption register: author, approve and version a bank's presets.

Every forecast, what-if and optimizer run resolves its base, adverse and severely
adverse assumptions from here, and only from an APPROVED version. The lifecycle:

* a maker with Forecasting ``edit`` drafts a complete set and submits it;
* a checker with Forecasting ``approve`` who neither drafted, revised nor submitted it
  approves or rejects it — maker ≠ checker is a runtime condition of the
  ``approve`` decision itself, not a convention of the screen;
* approved and rejected versions are final. A later change is a new version.

Only one draft or submitted version may be in flight per bank: the pending
proposal is the single decision everyone reviews before another is authored.

Effective dating is by BOOK date: a run on a book dated ``d`` resolves the
approved version with the latest ``effective_from <= d`` (a later approval on the
same date supersedes). Corrections may take effect before an already approved
version, including a future-dated one. New runs resolve the current approvals;
saved runs keep their snapshots, ``input_hash``, results and provenance.

Nothing here substitutes a value. A bank with no approved version effective on
the book date resolves no presets and its runs refuse with ``missing_parameter``;
the bank must author and approve its own set.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, ConditionKind, Module, Permission, Sensitivity
from app.db.base import utc_now
from app.forecasting.domain.assumptions import (
    InvalidAssumptionSet,
    PresetValues,
    validate_presets,
)
from app.forecasting.models import OPEN_STATUSES, ForecastAssumptionVersion
from app.forecasting.schemas import (
    ForecastAssumptionDecision,
    ForecastAssumptionProvenanceRead,
    ForecastAssumptionRegisterRead,
    ForecastAssumptionVersionCreate,
    ForecastAssumptionVersionRead,
    ForecastAssumptionVersionUpdate,
    ForecastPresetSetRead,
    ForecastPresetSetWrite,
)
from app.identity.public import User, require_resolved_bank_permission, resolve_bank
from app.live.public import Bank, BankReportingPeriod, enqueue_bank_change
from app.models.audit_event import AuditEvent
from app.services.audit import record_event

_ENTITY = "forecast_assumption_version"


@dataclass(frozen=True)
class EffectiveAssumptions:
    """What a run on one book date resolves: the approved presets and their provenance.

    ``presets`` is empty and ``provenance`` ``None`` when no approved version is
    effective, which every caller treats as the fail-closed ``missing_parameter``.
    """

    presets: PresetValues
    provenance: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Engine-facing resolution
# ---------------------------------------------------------------------------


def resolve_effective(
    db: Session, *, organization_id: str, bank_id: str, as_of: date
) -> EffectiveAssumptions:
    """The approved set governing a run on a book dated ``as_of``."""
    version = _effective_version(db, organization_id, bank_id, as_of)
    if version is None:
        return EffectiveAssumptions(presets={}, provenance=None)
    names = _names(db, organization_id, [version.reviewed_by])
    return EffectiveAssumptions(
        presets=_decimal_presets(version.presets),
        provenance=_provenance(version, names).model_dump(mode="json"),
    )


def provenance_read(payload: Any) -> ForecastAssumptionProvenanceRead | None:
    """A run's recorded provenance, or ``None`` for a run that resolved no version."""
    if not isinstance(payload, dict):
        return None
    return ForecastAssumptionProvenanceRead.model_validate(payload)


# ---------------------------------------------------------------------------
# Register reads
# ---------------------------------------------------------------------------


def get_register(db: Session, ctx: TenantContext, bank_id: str) -> ForecastAssumptionRegisterRead:
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    versions = list(
        db.scalars(
            select(ForecastAssumptionVersion)
            .where(
                ForecastAssumptionVersion.organization_id == ctx.organization_id,
                ForecastAssumptionVersion.bank_id == bank.id,
            )
            .order_by(ForecastAssumptionVersion.version_number.desc())
        )
    )
    as_of = latest_book_date(db, ctx.organization_id, bank.id)
    effective = (
        _effective_version(db, ctx.organization_id, bank.id, as_of) if as_of is not None else None
    )
    names = _names(db, ctx.organization_id, _actors(versions))
    return ForecastAssumptionRegisterRead(
        bank_id=bank.id,
        as_of=as_of,
        effective_version_id=effective.id if effective is not None else None,
        open_version_id=next(
            (version.id for version in versions if version.status in OPEN_STATUSES), None
        ),
        versions=[_read(version, names) for version in versions],
    )


def get_version(
    db: Session, ctx: TenantContext, bank_id: str, version_id: UUID
) -> ForecastAssumptionVersionRead:
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    return _read_one(db, _version_or_404(db, ctx, bank, version_id))


def latest_book_date(db: Session, organization_id: str, bank_id: str) -> date | None:
    """The bank's latest reporting-period end: the book date the register is read against."""
    return db.scalar(
        select(func.max(BankReportingPeriod.period_end)).where(
            BankReportingPeriod.organization_id == organization_id,
            BankReportingPeriod.bank_id == bank_id,
        )
    )


# ---------------------------------------------------------------------------
# Maker: draft, revise, submit
# ---------------------------------------------------------------------------


def create_version(
    db: Session, ctx: TenantContext, bank_id: str, payload: ForecastAssumptionVersionCreate
) -> ForecastAssumptionVersionRead:
    actor = _require_actor(ctx)
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    presets = _validated(payload.presets)
    next_number = (
        db.scalar(
            select(func.max(ForecastAssumptionVersion.version_number)).where(
                ForecastAssumptionVersion.organization_id == ctx.organization_id,
                ForecastAssumptionVersion.bank_id == bank.id,
            )
        )
        or 0
    ) + 1
    version = ForecastAssumptionVersion(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        version_number=next_number,
        status="draft",
        effective_from=payload.effective_from,
        presets=_stored(presets),
        change_note=payload.change_note,
        created_by=actor,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "assumption_version_open",
            "This bank already has a forecast assumption version in draft or awaiting "
            "approval. Revise or decide that one first.",
        ) from exc
    _audit(db, ctx, version, "forecast_assumptions.drafted")
    db.commit()
    return _read_one(db, version)


def update_version(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionVersionUpdate,
) -> ForecastAssumptionVersionRead:
    _require_actor(ctx)
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    version = _version_or_404(db, ctx, bank, version_id, for_update=True)
    _require_status(version, "draft", "Only a draft can be revised")
    if payload.presets is not None:
        version.presets = _stored(_validated(payload.presets))
    if payload.effective_from is not None:
        version.effective_from = payload.effective_from
    if payload.change_note is not None:
        version.change_note = payload.change_note
    _audit(db, ctx, version, "forecast_assumptions.revised")
    db.commit()
    return _read_one(db, version)


def submit_version(
    db: Session, ctx: TenantContext, bank_id: str, version_id: UUID
) -> ForecastAssumptionVersionRead:
    actor = _require_actor(ctx)
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    version = _version_or_404(db, ctx, bank, version_id, for_update=True)
    _require_status(version, "draft", "Only a draft can be submitted for approval")
    version.status = "submitted"
    version.submitted_by = actor
    version.submitted_at = utc_now()
    _audit(db, ctx, version, "forecast_assumptions.submitted")
    db.commit()
    return _read_one(db, version)


# ---------------------------------------------------------------------------
# Checker: approve or reject
# ---------------------------------------------------------------------------


def approve_version(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionDecision,
) -> ForecastAssumptionVersionRead:
    version, bank = _decision_target(db, ctx, bank_id, version_id)
    _decide(version, ctx, "approved", payload.note)
    _audit(db, ctx, version, "forecast_assumptions.approved")
    enqueue_bank_change(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reason=f"forecast assumptions approved:v{version.version_number}",
    )
    db.commit()
    return _read_one(db, version)


def reject_version(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionDecision,
) -> ForecastAssumptionVersionRead:
    if payload.note is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "rejection_reason_required",
                "message": "Say why the version is rejected so its author can revise it.",
            },
        )
    version, _ = _decision_target(db, ctx, bank_id, version_id)
    _decide(version, ctx, "rejected", payload.note)
    _audit(db, ctx, version, "forecast_assumptions.rejected")
    db.commit()
    return _read_one(db, version)


def _decision_target(
    db: Session, ctx: TenantContext, bank_id: str, version_id: UUID
) -> tuple[ForecastAssumptionVersion, Bank]:
    """The submitted version, once the caller is shown to be an independent checker.

    The lookup is bank-scoped and comes first, so a version the caller cannot
    address answers 404 exactly as an unknown id does. The ``approve`` decision
    then carries the maker-checker verdict for THIS version: whoever drafted,
    revised or submitted it is refused, whatever their bindings say.
    """
    actor = _require_actor(ctx)
    bank = resolve_bank(db, ctx, bank_id, module=Module.FORECASTING)
    version = _version_or_404(db, ctx, bank, version_id, for_update=True)
    maker_event = db.scalar(
        select(AuditEvent.id)
        .where(
            AuditEvent.organization_id == ctx.organization_id,
            AuditEvent.entity_type == _ENTITY,
            AuditEvent.entity_id == str(version.id),
            AuditEvent.actor_user_id == actor,
            AuditEvent.event_type.in_(
                (
                    "forecast_assumptions.drafted",
                    "forecast_assumptions.revised",
                    "forecast_assumptions.submitted",
                )
            ),
        )
        .limit(1)
    )
    independent = actor not in {version.created_by, version.submitted_by} and maker_event is None
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.APPROVE,
        module=Module.FORECASTING,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="forecast_assumption_decision",
        conditions=(
            ConditionCheck(
                kind=ConditionKind.MAKER_CHECKER,
                passed=independent,
                reason=(
                    "the checker neither drafted, revised nor submitted this assumption version"
                    if independent
                    else (
                        "whoever drafted, revised or submitted an assumption version "
                        "cannot decide it"
                    )
                ),
            ),
        ),
        denial_detail=(
            "Deciding a forecast assumption version requires Forecasting approval authority "
            "and a checker who neither drafted, revised nor submitted it."
        ),
    )
    _require_status(version, "submitted", "Only a submitted version can be decided")
    return version, bank


def _decide(
    version: ForecastAssumptionVersion, ctx: TenantContext, decision: str, note: str | None
) -> None:
    version.status = decision
    version.reviewed_by = ctx.actor_user_id
    version.reviewed_at = utc_now()
    version.review_note = note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _effective_version(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> ForecastAssumptionVersion | None:
    return db.scalar(
        select(ForecastAssumptionVersion)
        .where(
            ForecastAssumptionVersion.organization_id == organization_id,
            ForecastAssumptionVersion.bank_id == bank_id,
            ForecastAssumptionVersion.status == "approved",
            ForecastAssumptionVersion.effective_from <= as_of,
        )
        .order_by(
            ForecastAssumptionVersion.effective_from.desc(),
            ForecastAssumptionVersion.reviewed_at.desc(),
            ForecastAssumptionVersion.version_number.desc(),
        )
        .limit(1)
    )


def _version_or_404(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    version_id: UUID,
    *,
    for_update: bool = False,
) -> ForecastAssumptionVersion:
    statement = select(ForecastAssumptionVersion).where(
        ForecastAssumptionVersion.id == version_id,
        ForecastAssumptionVersion.organization_id == ctx.organization_id,
        ForecastAssumptionVersion.bank_id == bank.id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    version = db.scalar(statement)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Forecast assumption version not found.",
        )
    return version


def _require_actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
    return ctx.actor_user_id


def _require_status(version: ForecastAssumptionVersion, expected: str, action: str) -> None:
    if version.status != expected:
        raise _conflict(
            f"assumption_version_not_{expected}",
            f"{action}; version {version.version_number} is {version.status}.",
        )


def _validated(presets: ForecastPresetSetWrite) -> PresetValues:
    try:
        return validate_presets(presets.model_dump())
    except InvalidAssumptionSet as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "invalid_assumption_set",
                "message": str(exc),
                "problems": [
                    {
                        "scenario_code": problem.scenario_code,
                        "assumption_key": problem.assumption_key,
                        "message": problem.message,
                    }
                    for problem in exc.problems
                ],
            },
        ) from exc


def _conflict(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message},
    )


def _stored(presets: PresetValues) -> dict[str, dict[str, str]]:
    return {
        code: {key: _plain(value) for key, value in values.items()}
        for code, values in presets.items()
    }


def _plain(value: Decimal) -> str:
    """``Decimal`` as positional text, without an exponent or trailing zeros."""
    return format(value.normalize(), "f")


def _decimal_presets(stored: dict[str, Any]) -> PresetValues:
    return {
        code: {key: Decimal(str(value)) for key, value in values.items()}
        for code, values in stored.items()
    }


def _actors(versions: Iterable[ForecastAssumptionVersion]) -> list[UUID | None]:
    return [
        actor
        for version in versions
        for actor in (version.created_by, version.submitted_by, version.reviewed_by)
    ]


def _names(db: Session, organization_id: str, user_ids: Iterable[UUID | None]) -> dict[UUID, str]:
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    rows = db.execute(
        select(User.id, User.display_name, User.email).where(
            User.organization_id == organization_id, User.id.in_(ids)
        )
    )
    return {user_id: display_name or email for user_id, display_name, email in rows}


def _provenance(
    version: ForecastAssumptionVersion, names: dict[UUID, str]
) -> ForecastAssumptionProvenanceRead:
    assert version.reviewed_by is not None
    return ForecastAssumptionProvenanceRead(
        version_id=version.id,
        version_number=version.version_number,
        effective_from=version.effective_from,
        approved_by=version.reviewed_by,
        approved_by_name=names.get(version.reviewed_by),
        # ``ck_fav_reviewed_at``: an approved version always records when.
        approved_at=version.reviewed_at,  # type: ignore[arg-type]
    )


def _read_one(db: Session, version: ForecastAssumptionVersion) -> ForecastAssumptionVersionRead:
    return _read(version, _names(db, version.organization_id, _actors([version])))


def _read(
    version: ForecastAssumptionVersion, names: dict[UUID, str]
) -> ForecastAssumptionVersionRead:
    def name(user_id: UUID | None) -> str | None:
        return names.get(user_id) if user_id is not None else None

    return ForecastAssumptionVersionRead(
        id=version.id,
        bank_id=version.bank_id,
        version_number=version.version_number,
        status=version.status,  # type: ignore[arg-type]
        effective_from=version.effective_from,
        presets=ForecastPresetSetRead.model_validate(version.presets),
        change_note=version.change_note,
        created_by=version.created_by,
        created_by_name=name(version.created_by),
        created_at=version.created_at,
        submitted_by=version.submitted_by,
        submitted_by_name=name(version.submitted_by),
        submitted_at=version.submitted_at,
        reviewed_by=version.reviewed_by,
        reviewed_by_name=name(version.reviewed_by),
        reviewed_at=version.reviewed_at,
        review_note=version.review_note,
        updated_at=version.updated_at,
    )


def _audit(
    db: Session, ctx: TenantContext, version: ForecastAssumptionVersion, event_type: str
) -> None:
    db.flush()
    record_event(
        db,
        ctx,
        event_type=event_type,
        entity_type=_ENTITY,
        entity_id=version.id,
        details={
            "bank_id": version.bank_id,
            "version_number": version.version_number,
            "status": version.status,
            "effective_from": version.effective_from.isoformat(),
            "presets": version.presets,
            "change_note": version.change_note,
            "review_note": version.review_note,
        },
    )
