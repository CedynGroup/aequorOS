"""The ICAAP risk register: which risks the institution runs, and which are material.

¶49(a) and ¶75 ask a bank to identify its risks and say which are material, with
a concise reason. The framework supplies the categories — the bank cannot
quietly drop one — and the bank supplies the scores, the rationale and the
treatment.

Rows are created LAZILY, on the first save. A framework that gains a category
therefore needs no data migration, and an untouched cycle carries no rows
pretending to be assessments. The register a reader sees is the framework's
categories merged with whatever has been stored.

The verdict comes from the governed matrix, never from code: the thresholds and
the rating bands are control-plane rows (D-024), and the digest of the rows that
produced a verdict is stored with it, so a later console change shows up as
"assessed under earlier thresholds" instead of silently restating the answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import materiality
from app.domain.icaap.frameworks.schema import Framework, RiskCategoryDef
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import IcaapPillar2Item, IcaapRiskAssessment
from app.schemas.icaap_risk_capital import (
    IcaapCustomRiskCreate,
    IcaapMaterialityBandRead,
    IcaapMaterialityCellRead,
    IcaapMaterialityLevelRead,
    IcaapMaterialityMatrixRead,
    IcaapParameterUseRead,
    IcaapRetire,
    IcaapRiskComponentRead,
    IcaapRiskPut,
    IcaapRiskRead,
    IcaapRiskRegisterRead,
    IcaapRiskRegisterSummaryRead,
)
from app.services.audit import record_event
from app.services.icaap import digests, guards, params

MATERIALITY_CODES: tuple[str, ...] = (
    materiality.PARAM_MIN_SCORE,
    materiality.PARAM_MIN_IMPACT,
    materiality.PARAM_RATING_BANDS,
)

#: Derived from the column, so a slug can never be written that the row
#: cannot hold, and the two cannot drift apart.
_RISK_KEY_MAX = int(getattr(IcaapRiskAssessment.__table__.c.risk_key.type, "length", 0) or 0)
_UNASSESSED = "unassessed"


@dataclass(frozen=True)
class RegisterRow:
    """One register line: the framework's category and the bank's assessment."""

    category: RiskCategoryDef
    risk_key: str
    title: str
    is_custom: bool
    stored: IcaapRiskAssessment | None


@dataclass(frozen=True)
class RegisterState:
    """What readiness and the register block read."""

    rows: tuple[RegisterRow, ...]
    policy: materiality.MaterialityPolicy
    items_by_component: Mapping[str, IcaapPillar2Item]


def _slug(value: str, *, limit: int) -> str:
    cleaned = [char if char.isalnum() else "_" for char in value.casefold()]
    collapsed = "".join(cleaned).strip("_")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed[:limit] or "risk"


def materiality_policy(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> tuple[materiality.MaterialityPolicy, params.P2Parameters]:
    """The governed matrix at the cycle's as-of date, or a typed refusal.

    The framework names parameter CODES for the thresholds and bands (D-024
    §3); nothing about the matrix is decided here.
    """
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=MATERIALITY_CODES)
    bands_body = resolved.body(framework.materiality.rating_bands_param)
    raw_bands = bands_body.get("bands")
    if not isinstance(raw_bands, Sequence) or isinstance(raw_bands, str) or not raw_bands:
        raise params.parameter_malformed(
            framework.materiality.rating_bands_param, "bands", "no bands"
        )
    bands = [
        materiality.RatingBand(
            key=str(entry["key"]),
            label=str(entry.get("label", entry["key"])),
            min_score=int(entry["min_score"]),
            max_score=int(entry["max_score"]),
        )
        for entry in raw_bands
        if isinstance(entry, Mapping)
    ]
    digest = digests.register_digest(
        {
            "codes": [
                {
                    "param_code": code,
                    "value": row.printable,
                    "value_json": row.value_json,
                    "parameter_id": row.parameter_id,
                }
                for code in MATERIALITY_CODES
                if (row := resolved.optional(code)) is not None
            ]
        }
    )
    try:
        policy = materiality.build_policy(
            [
                materiality.Level(score=level.score, key=level.key, label=level.label)
                for level in framework.materiality.likelihood_levels
            ],
            [
                materiality.Level(score=level.score, key=level.key, label=level.label)
                for level in framework.materiality.impact_levels
            ],
            bands,
            min_score=resolved.integer(framework.materiality.material_min_score_param),
            min_impact=resolved.integer(framework.materiality.material_min_impact_param),
            thresholds_digest=digest,
        )
    except materiality.MaterialityPolicyError as exc:
        raise params.parameter_malformed(
            framework.materiality.rating_bands_param, "bands", str(exc)
        ) from exc
    return policy, resolved


def _stored_rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapRiskAssessment]:
    return list(
        db.scalars(
            select(IcaapRiskAssessment)
            .where(
                IcaapRiskAssessment.organization_id == access.ctx.organization_id,
                IcaapRiskAssessment.cycle_id == cycle.id,
            )
            .order_by(IcaapRiskAssessment.risk_key.asc())
        )
    )


def _live_items(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, IcaapPillar2Item]:
    return {
        item.component_key: item
        for item in db.scalars(
            select(IcaapPillar2Item).where(
                IcaapPillar2Item.organization_id == access.ctx.organization_id,
                IcaapPillar2Item.cycle_id == cycle.id,
                IcaapPillar2Item.retired_at.is_(None),
            )
        )
    }


def register_rows(
    framework: Framework, stored: Sequence[IcaapRiskAssessment]
) -> tuple[RegisterRow, ...]:
    """Framework categories merged with stored rows, custom rows appended."""
    by_key = {row.risk_key: row for row in stored}
    rows: list[RegisterRow] = []
    for category in framework.risk_categories:
        row = by_key.get(category.key)
        rows.append(
            RegisterRow(
                category=category,
                risk_key=category.key,
                title=row.title if row is not None else category.title,
                is_custom=False,
                stored=row,
            )
        )
        for custom in sorted(
            (
                entry
                for entry in stored
                if entry.is_custom
                and entry.category_key == category.key
                and entry.retired_at is None
            ),
            key=lambda entry: entry.risk_key,
        ):
            rows.append(
                RegisterRow(
                    category=category,
                    risk_key=custom.risk_key,
                    title=custom.title,
                    is_custom=True,
                    stored=custom,
                )
            )
    return tuple(rows)


def register_state(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> RegisterState:
    framework = guards.require_framework(cycle)
    policy, _resolved = materiality_policy(db, access, cycle, framework)
    return RegisterState(
        rows=register_rows(framework, _stored_rows(db, access, cycle)),
        policy=policy,
        items_by_component=_live_items(db, access, cycle),
    )


def _matrix_read(
    policy: materiality.MaterialityPolicy,
    framework: Framework,
    resolved: params.P2Parameters,
) -> IcaapMaterialityMatrixRead:
    return IcaapMaterialityMatrixRead(
        likelihood_levels=[
            IcaapMaterialityLevelRead(score=level.score, key=level.key, label=level.label)
            for level in policy.likelihood
        ],
        impact_levels=[
            IcaapMaterialityLevelRead(score=level.score, key=level.key, label=level.label)
            for level in policy.impact
        ],
        bands=[
            IcaapMaterialityBandRead(
                key=band.key,
                label=band.label,
                min_score=band.min_score,
                max_score=band.max_score,
            )
            for band in policy.bands
        ],
        material_min_score=policy.material_min_score,
        material_min_impact=policy.material_min_impact,
        cells=[
            IcaapMaterialityCellRead(
                likelihood=cell.likelihood,
                impact=cell.impact,
                score=cell.score,
                rating_key=cell.rating_key,
                material=cell.material,
            )
            for cell in materiality.matrix_cells(policy)
        ],
        thresholds_digest=policy.thresholds_digest,
        parameters=[
            IcaapParameterUseRead(**use)
            for use in resolved.uses(
                (
                    framework.materiality.material_min_score_param,
                    framework.materiality.material_min_impact_param,
                    framework.materiality.rating_bands_param,
                )
            )
        ],
    )


def _risk_read(
    row: RegisterRow,
    policy: materiality.MaterialityPolicy,
    items: Mapping[str, IcaapPillar2Item],
) -> IcaapRiskRead:
    stored = row.stored
    components = [
        IcaapRiskComponentRead(
            component_key=component.key,
            table5_row=component.table5_row,
            p29_class=component.p29_class,
            allowed_methods=list(component.allowed_methods),
            item_id=item.id if (item := items.get(component.key)) is not None else None,
            item_key=item.item_key if item is not None else None,
            method=item.method if item is not None else None,
            method_status=item.method_status if item is not None else None,
            baseline_amount=item.baseline_amount if item is not None else None,
            stressed_amount=item.stressed_amount if item is not None else None,
        )
        for component in row.category.components
    ]
    if stored is None:
        return IcaapRiskRead(
            risk_key=row.risk_key,
            category_key=row.category.key,
            title=row.title,
            is_custom=row.is_custom,
            matrix_verdict=_UNASSESSED,
            verdict=_UNASSESSED,
            verdict_source="matrix",
            pillar1_coverage=row.category.pillar1_coverage,
            p29_class=_category_p29(row.category),
            pillar2_treatment="undecided",
            row_rev=0,
            thresholds_current=True,
            components=components,
            stored=False,
        )
    return IcaapRiskRead(
        risk_key=stored.risk_key,
        category_key=stored.category_key,
        title=stored.title,
        is_custom=stored.is_custom,
        description=stored.description,
        likelihood_score=stored.likelihood_score,
        impact_score=stored.impact_score,
        controls_summary=stored.controls_summary,
        materiality_score=stored.materiality_score,
        rating_key=stored.rating_key,
        matrix_verdict=stored.matrix_verdict,  # pyright: ignore[reportArgumentType]
        verdict=stored.verdict,  # pyright: ignore[reportArgumentType]
        verdict_source=stored.verdict_source,  # pyright: ignore[reportArgumentType]
        override_reason=stored.override_reason,
        materiality_rationale=stored.materiality_rationale,
        pillar1_coverage=stored.pillar1_coverage,
        p29_class=stored.p29_class,
        pillar2_treatment=stored.pillar2_treatment,  # pyright: ignore[reportArgumentType]
        pillar1_coverage_rationale=stored.pillar1_coverage_rationale,
        owner_function=stored.owner_function,
        row_rev=stored.row_rev,
        thresholds_current=(
            stored.thresholds_digest is None or stored.thresholds_digest == policy.thresholds_digest
        ),
        components=components,
        stored=True,
        retired_at=stored.retired_at,
        updated_at=stored.updated_at,
    )


def _category_p29(category: RiskCategoryDef) -> str:
    if category.components:
        return category.components[0].p29_class
    return "not_classified"  # pragma: no cover - every category has a component


def get_register(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapRiskRegisterRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    policy, resolved = materiality_policy(db, access, cycle, framework)
    rows = register_rows(framework, _stored_rows(db, access, cycle))
    items = _live_items(db, access, cycle)
    reads = [_risk_read(row, policy, items) for row in rows]
    assessed = [read for read in reads if read.materiality_score is not None]
    return IcaapRiskRegisterRead(
        cycle_id=cycle.id,
        risks=reads,
        matrix=_matrix_read(policy, framework, resolved),
        summary=IcaapRiskRegisterSummaryRead(
            category_count=len(framework.risk_categories),
            assessed_risk_count=len(assessed),
            material_risk_count=len([read for read in reads if read.verdict == "material"]),
            unassessed_risk_count=len([read for read in reads if read.verdict == _UNASSESSED]),
        ),
    )


def _row_for(
    framework: Framework, stored: Sequence[IcaapRiskAssessment], risk_key: str
) -> RegisterRow:
    for row in register_rows(framework, stored):
        if row.risk_key == risk_key:
            return row
    raise guards.unprocessable(
        "unknown_risk_key", "That risk is not in this ICAAP's register.", risk_key=risk_key
    )


def _apply(  # noqa: PLR0912 - one branch per field the payload may carry
    row: RegisterRow,
    stored: IcaapRiskAssessment,
    payload: IcaapRiskPut,
    policy: materiality.MaterialityPolicy,
) -> None:
    if payload.likelihood_score is not None:
        _check_level(policy.likelihood, payload.likelihood_score, "likelihood_score")
        stored.likelihood_score = payload.likelihood_score
    if payload.impact_score is not None:
        _check_level(policy.impact, payload.impact_score, "impact_score")
        stored.impact_score = payload.impact_score
    if payload.description is not None:
        stored.description = payload.description
    if payload.controls_summary is not None:
        stored.controls_summary = payload.controls_summary
    if payload.materiality_rationale is not None:
        stored.materiality_rationale = payload.materiality_rationale
    if payload.owner_function is not None:
        stored.owner_function = payload.owner_function
    if payload.pillar2_treatment is not None:
        stored.pillar2_treatment = payload.pillar2_treatment
    if payload.pillar1_coverage_rationale is not None:
        stored.pillar1_coverage_rationale = payload.pillar1_coverage_rationale

    assessment = materiality.assess(
        policy,
        stored.likelihood_score,
        stored.impact_score,
        override=payload.override,
    )
    stored.materiality_score = assessment.score
    stored.rating_key = assessment.rating
    stored.matrix_verdict = assessment.matrix_verdict
    stored.verdict = assessment.verdict
    stored.verdict_source = assessment.verdict_source
    if assessment.verdict_source == "override":
        if not (payload.override_reason or stored.override_reason):
            raise guards.unprocessable(
                "override_reason_required",
                "Say why the matrix verdict is being overridden.",
                risk_key=row.risk_key,
            )
        stored.override_reason = payload.override_reason or stored.override_reason
    else:
        stored.override_reason = None
    stored.thresholds_digest = policy.thresholds_digest
    if stored.materiality_score is not None and not stored.materiality_rationale:
        raise guards.unprocessable(
            "rationale_required",
            "A scored risk needs a short statement of why it is or is not material.",
            risk_key=row.risk_key,
        )
    if (
        stored.pillar2_treatment == "fully_covered_by_pillar1"
        and not stored.pillar1_coverage_rationale
    ):
        raise guards.unprocessable(
            "rationale_required",
            "Say why Pillar 1 already covers this risk in full.",
            risk_key=row.risk_key,
        )


def _check_level(levels: Sequence[materiality.Level], score: int, field: str) -> None:
    if score not in {level.score for level in levels}:
        raise guards.unprocessable(
            "unknown_risk_key" if field == "risk_key" else "score_out_of_scale",
            "That score is not on this framework's scale.",
            field=field,
            score=str(score),
        )


def put_risk(
    db: Session, access: IcaapAccess, cycle_id: UUID, risk_key: str, payload: IcaapRiskPut
) -> IcaapRiskRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    policy, _resolved = materiality_policy(db, access, cycle, framework)
    stored_rows = _stored_rows(db, access, cycle)
    row = _row_for(framework, stored_rows, risk_key)
    stored = row.stored
    if stored is None:
        if payload.base_rev is not None:
            raise guards.conflict(
                "row_rev_conflict",
                "This risk has not been assessed yet, so there is no version to update.",
                current_rev=0,
            )
        stored = IcaapRiskAssessment(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            risk_key=row.risk_key,
            category_key=row.category.key,
            title=row.title,
            is_custom=row.is_custom,
            matrix_verdict=_UNASSESSED,
            verdict=_UNASSESSED,
            verdict_source="matrix",
            pillar1_coverage=row.category.pillar1_coverage,
            p29_class=_category_p29(row.category),
            pillar2_treatment="undecided",
            row_rev=0,
            created_by=guards.actor_id(access),
            updated_by=guards.actor_id(access),
        )
        db.add(stored)
        row = RegisterRow(
            category=row.category,
            risk_key=row.risk_key,
            title=row.title,
            is_custom=row.is_custom,
            stored=stored,
        )
    elif payload.base_rev is None or payload.base_rev != stored.row_rev:
        raise guards.conflict(
            "row_rev_conflict",
            "Somebody else changed this risk while you were editing it.",
            current_rev=stored.row_rev,
        )
    if stored.retired_at is not None:
        raise guards.conflict("risk_retired", "This risk has been retired.")

    _apply(row, stored, payload, policy)
    stored.row_rev += 1
    stored.updated_by = guards.actor_id(access)
    try:
        db.flush()
    except IntegrityError as exc:  # pragma: no cover - the unique is on (cycle, key)
        db.rollback()
        raise guards.conflict("risk_exists", "That risk is already in the register.") from exc
    record_event(
        db,
        access.ctx,
        event_type="icaap.risk.assessed",
        entity_type="icaap_risk_assessment",
        entity_id=stored.id,
        details={
            "cycle_id": str(cycle.id),
            "risk_key": stored.risk_key,
            "verdict": stored.verdict,
            "verdict_source": stored.verdict_source,
            "materiality_score": stored.materiality_score,
            "row_rev": stored.row_rev,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(stored)
    return _risk_read(
        RegisterRow(
            category=row.category,
            risk_key=stored.risk_key,
            title=stored.title,
            is_custom=stored.is_custom,
            stored=stored,
        ),
        policy,
        _live_items(db, access, cycle),
    )


def create_custom_risk(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapCustomRiskCreate
) -> IcaapRiskRead:
    """An emerging risk the framework does not name (category 14)."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    policy, _resolved = materiality_policy(db, access, cycle, framework)
    category = next(
        (entry for entry in framework.risk_categories if entry.key == payload.category_key), None
    )
    if category is None or not category.custom:
        raise guards.unprocessable(
            "unknown_risk_key",
            "New risks can only be added under a category the framework marks as open.",
            category_key=payload.category_key,
        )
    risk_key = f"{category.key}_{_slug(payload.title, limit=_RISK_KEY_MAX - len(category.key) - 1)}"
    stored = IcaapRiskAssessment(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        risk_key=risk_key,
        category_key=category.key,
        title=payload.title,
        is_custom=True,
        description=payload.description,
        owner_function=payload.owner_function,
        matrix_verdict=_UNASSESSED,
        verdict=_UNASSESSED,
        verdict_source="matrix",
        pillar1_coverage=category.pillar1_coverage,
        p29_class=_category_p29(category),
        pillar2_treatment="undecided",
        row_rev=0,
        created_by=guards.actor_id(access),
        updated_by=guards.actor_id(access),
    )
    db.add(stored)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "risk_exists", "A risk with that name is already in the register.", risk_key=risk_key
        ) from exc
    record_event(
        db,
        access.ctx,
        event_type="icaap.risk.custom_created",
        entity_type="icaap_risk_assessment",
        entity_id=stored.id,
        details={
            "cycle_id": str(cycle.id),
            "risk_key": risk_key,
            "category_key": category.key,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(stored)
    return _risk_read(
        RegisterRow(
            category=category,
            risk_key=risk_key,
            title=stored.title,
            is_custom=True,
            stored=stored,
        ),
        policy,
        _live_items(db, access, cycle),
    )


def retire_custom_risk(
    db: Session, access: IcaapAccess, cycle_id: UUID, risk_key: str, payload: IcaapRetire
) -> IcaapRiskRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    policy, _resolved = materiality_policy(db, access, cycle, framework)
    stored = db.scalar(
        select(IcaapRiskAssessment).where(
            IcaapRiskAssessment.organization_id == access.ctx.organization_id,
            IcaapRiskAssessment.cycle_id == cycle.id,
            IcaapRiskAssessment.risk_key == risk_key,
        )
    )
    if stored is None:
        guards.not_found()
    if not stored.is_custom:
        raise guards.conflict(
            "risk_not_custom",
            "The framework's own risk categories cannot be removed from the register.",
            risk_key=risk_key,
        )
    items = _live_items(db, access, cycle)
    category = next(
        entry for entry in framework.risk_categories if entry.key == stored.category_key
    )
    live = [component.key for component in category.components if component.key in items]
    if live and any(items[key].risk_key == risk_key for key in live):
        raise guards.conflict(
            "risk_has_live_items",
            "This risk still carries a Pillar 2 figure. Retire the figure first.",
            risk_key=risk_key,
        )
    stored.retired_at = utc_now()
    stored.retired_by = guards.actor_id(access)
    stored.retire_reason = payload.reason
    stored.row_rev += 1
    stored.updated_by = guards.actor_id(access)
    record_event(
        db,
        access.ctx,
        event_type="icaap.risk.retired",
        entity_type="icaap_risk_assessment",
        entity_id=stored.id,
        details={"cycle_id": str(cycle.id), "risk_key": risk_key, "reason": payload.reason},
    )
    db.commit()
    db.refresh(stored)
    return _risk_read(
        RegisterRow(
            category=category,
            risk_key=risk_key,
            title=stored.title,
            is_custom=True,
            stored=stored,
        ),
        policy,
        items,
    )


def register_payload(state: RegisterState) -> dict[str, Any]:
    """The register as a value-based body — what the data block digests."""
    return {
        "risks": [
            {
                "risk_key": row.risk_key,
                "category_key": row.category.key,
                "title": row.title,
                "likelihood_score": None if row.stored is None else row.stored.likelihood_score,
                "impact_score": None if row.stored is None else row.stored.impact_score,
                "materiality_score": (None if row.stored is None else row.stored.materiality_score),
                "rating_key": None if row.stored is None else row.stored.rating_key,
                "verdict": _UNASSESSED if row.stored is None else row.stored.verdict,
                "verdict_source": ("matrix" if row.stored is None else row.stored.verdict_source),
                "pillar1_coverage": row.category.pillar1_coverage,
                "pillar2_treatment": (
                    "undecided" if row.stored is None else row.stored.pillar2_treatment
                ),
                "owner_function": None if row.stored is None else row.stored.owner_function,
                "retired": row.stored is not None and row.stored.retired_at is not None,
            }
            for row in state.rows
        ],
        "thresholds_digest": state.policy.thresholds_digest,
    }


def material_risk_keys(state: RegisterState) -> tuple[str, ...]:
    return tuple(
        row.risk_key
        for row in state.rows
        if row.stored is not None
        and row.stored.retired_at is None
        and row.stored.verdict == "material"
    )


def as_of_of(cycle: IcaapCycle) -> date:
    return cycle.as_of_date


__all__ = [
    "MATERIALITY_CODES",
    "RegisterRow",
    "RegisterState",
    "create_custom_risk",
    "get_register",
    "material_risk_keys",
    "materiality_policy",
    "put_risk",
    "register_payload",
    "register_rows",
    "register_state",
    "retire_custom_risk",
]
