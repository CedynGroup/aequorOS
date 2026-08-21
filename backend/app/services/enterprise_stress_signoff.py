"""Enterprise-stress sign-off / Board attestation (docs/stress.md §3.8, Phase 5).

The governance spine the BoG Guideline on Stress Testing requires around every
enterprise-stress run before its results are submitted (Part II ¶10–29): the
Board attests it has reviewed and challenged both the framework and the results,
with a rationale for their credibility (¶20), and the run carries the analyst/CRO
narrative & assumptions rationale the annual ICAAP submission requires (¶67(b)(c)).

Maker-checker mirrors the macro-scenario library exactly:

- an analyst (maker) prepares the ``scenario_narrative`` + ``assumptions_rationale``
  (``draft``, freely editable);
- ``submit`` moves a draft to ``pending_attestation``;
- ``attest`` requires a DIFFERENT Board/approver officer (maker ≠ checker), records
  the credibility rationale + challenge, stamps ``attested_by``/``attested_at`` and
  bumps the version;
- ``withdraw`` retires a sign-off from any live state.

Only an ``attested`` sign-off makes its run eligible to feed the ICAAP Appendix II
submission — ``resolve_attested_run_for_period`` is the guard the reporting
generator (``regulatory_reporting.generation._generate_icaap_stress_appendix2``)
calls. Every mutation carries a non-empty reason and is audit-logged; all
reads/writes are org-scoped in code on top of the migration's RLS FORCE policies.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import Bank, EnterpriseStressSignoff, RegulatoryRun
from app.schemas.enterprise_stress_signoff import (
    StressSignoffAttestation,
    StressSignoffCreate,
    StressSignoffListRead,
    StressSignoffRead,
    StressSignoffSummary,
    StressSignoffTransition,
    StressSignoffUpdate,
)
from app.services.audit import record_event

MODULE_ENTERPRISE_STRESS = "enterprise_stress"


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_signoff_or_404(
    db: Session, ctx: TenantContext, bank_id: str, signoff_id: UUID
) -> EnterpriseStressSignoff:
    signoff = db.scalar(
        select(EnterpriseStressSignoff).where(
            EnterpriseStressSignoff.id == signoff_id,
            EnterpriseStressSignoff.organization_id == ctx.organization_id,
            EnterpriseStressSignoff.bank_id == bank_id,
        )
    )
    if signoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise-stress sign-off not found.",
        )
    return signoff


def _resolve_enterprise_run_or_404(
    db: Session, ctx: TenantContext, bank: Bank, run_id: UUID
) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.module == MODULE_ENTERPRISE_STRESS,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise-stress run not found for this bank.",
        )
    if run.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "run_not_succeeded",
                "message": (
                    "Only a succeeded enterprise-stress run can be signed off; this "
                    f"run is '{run.status}'."
                ),
            },
        )
    return run


def _headline_flags(run: RegulatoryRun) -> tuple[bool | None, bool | None]:
    """(stress stays-above-all-minima, with-actions stays-above-all-minima)."""
    metrics = run.metrics or {}
    projection = metrics.get("projection") or {}
    stays = projection.get("stress_stays_above_all_minima")
    management = metrics.get("management_actions")
    with_actions = (
        management.get("stays_above_all_minima") if isinstance(management, dict) else None
    )
    return (
        None if stays is None else bool(stays),
        None if with_actions is None else bool(with_actions),
    )


def _read(signoff: EnterpriseStressSignoff) -> StressSignoffRead:
    return StressSignoffRead(
        id=signoff.id,
        organization_id=signoff.organization_id,
        bank_id=signoff.bank_id,
        run_id=signoff.run_id,
        reporting_period_id=signoff.reporting_period_id,
        scenario_code=signoff.scenario_code,
        status=signoff.status,  # pyright: ignore[reportArgumentType]
        version=signoff.version,
        scenario_narrative=signoff.scenario_narrative,
        assumptions_rationale=signoff.assumptions_rationale,
        methodology_summary=signoff.methodology_summary,
        board_challenge=signoff.board_challenge,
        credibility_rationale=signoff.credibility_rationale,
        stays_above_all_minima=signoff.stays_above_all_minima,
        with_actions_stays_above_all_minima=signoff.with_actions_stays_above_all_minima,
        created_by=signoff.created_by,
        submitted_by=signoff.submitted_by,
        submitted_at=signoff.submitted_at,
        attested_by=signoff.attested_by,
        attested_at=signoff.attested_at,
        created_at=signoff.created_at,
        updated_at=signoff.updated_at,
    )


def _summary(signoff: EnterpriseStressSignoff) -> StressSignoffSummary:
    return StressSignoffSummary(
        id=signoff.id,
        run_id=signoff.run_id,
        reporting_period_id=signoff.reporting_period_id,
        scenario_code=signoff.scenario_code,
        status=signoff.status,  # pyright: ignore[reportArgumentType]
        version=signoff.version,
        stays_above_all_minima=signoff.stays_above_all_minima,
        attested_by=signoff.attested_by,
        attested_at=signoff.attested_at,
        created_at=signoff.created_at,
        updated_at=signoff.updated_at,
    )


def create_signoff(
    db: Session, ctx: TenantContext, bank_id: str, payload: StressSignoffCreate
) -> StressSignoffRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    run = _resolve_enterprise_run_or_404(db, ctx, bank, payload.run_id)
    existing = db.scalar(
        select(EnterpriseStressSignoff).where(
            EnterpriseStressSignoff.organization_id == ctx.organization_id,
            EnterpriseStressSignoff.run_id == run.id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "signoff_exists",
                "message": (
                    "A sign-off already exists for this enterprise-stress run "
                    f"(status '{existing.status}'); edit or withdraw it instead."
                ),
            },
        )
    stays, with_actions = _headline_flags(run)
    signoff = EnterpriseStressSignoff(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        run_id=run.id,
        reporting_period_id=run.reporting_period_id,
        scenario_code=run.scenario_code,
        status="draft",
        version=1,
        scenario_narrative=payload.scenario_narrative,
        assumptions_rationale=payload.assumptions_rationale,
        methodology_summary=payload.methodology_summary,
        stays_above_all_minima=stays,
        with_actions_stays_above_all_minima=with_actions,
        created_by=ctx.actor_user_id,
    )
    db.add(signoff)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="enterprise_stress_signoff.created",
        entity_type="enterprise_stress_signoff",
        entity_id=signoff.id,
        details={
            "bank_id": str(bank.id),
            "run_id": str(run.id),
            "scenario_code": run.scenario_code,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(signoff)


def list_signoffs(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID | None = None,
    status_filter: str | None = None,
) -> StressSignoffListRead:
    conditions: list[Any] = [
        EnterpriseStressSignoff.organization_id == ctx.organization_id,
        EnterpriseStressSignoff.bank_id == bank_id,
    ]
    if reporting_period_id is not None:
        conditions.append(EnterpriseStressSignoff.reporting_period_id == reporting_period_id)
    if status_filter is not None:
        conditions.append(EnterpriseStressSignoff.status == status_filter)
    signoffs = list(
        db.scalars(
            select(EnterpriseStressSignoff)
            .where(*conditions)
            .order_by(EnterpriseStressSignoff.created_at.desc())
        )
    )
    return StressSignoffListRead(
        signoffs=[_summary(signoff) for signoff in signoffs], total=len(signoffs)
    )


def get_signoff(
    db: Session, ctx: TenantContext, bank_id: str, signoff_id: UUID
) -> StressSignoffRead:
    return _read(_get_signoff_or_404(db, ctx, bank_id, signoff_id))


def update_signoff(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffUpdate,
) -> StressSignoffRead:
    signoff = _get_signoff_or_404(db, ctx, bank_id, signoff_id)
    if signoff.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_editable",
                "message": (
                    f"Only a draft sign-off can be edited; this one is '{signoff.status}'."
                ),
            },
        )
    for field in ("scenario_narrative", "assumptions_rationale", "methodology_summary"):
        value = getattr(payload, field)
        if value is not None:
            setattr(signoff, field, value)
    record_event(
        db,
        ctx,
        event_type="enterprise_stress_signoff.updated",
        entity_type="enterprise_stress_signoff",
        entity_id=signoff.id,
        details={"run_id": str(signoff.run_id), "reason": payload.reason},
    )
    db.commit()
    return _read(signoff)


def submit_signoff(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffTransition,
) -> StressSignoffRead:
    signoff = _get_signoff_or_404(db, ctx, bank_id, signoff_id)
    if signoff.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_submittable",
                "message": (
                    "Only a draft sign-off can be submitted for attestation; this one is "
                    f"'{signoff.status}'."
                ),
            },
        )
    signoff.status = "pending_attestation"
    signoff.submitted_by = ctx.actor_user_id
    signoff.submitted_at = utc_now()
    record_event(
        db,
        ctx,
        event_type="enterprise_stress_signoff.submitted",
        entity_type="enterprise_stress_signoff",
        entity_id=signoff.id,
        details={"run_id": str(signoff.run_id), "reason": payload.reason},
    )
    db.commit()
    return _read(signoff)


def attest_signoff(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffAttestation,
) -> StressSignoffRead:
    signoff = _get_signoff_or_404(db, ctx, bank_id, signoff_id)
    if signoff.status != "pending_attestation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_pending_attestation",
                "message": (
                    "Only a sign-off pending attestation can be attested; this one is "
                    f"'{signoff.status}'."
                ),
            },
        )
    # Maker ≠ checker (¶16, ¶20): the Board attester must differ from the preparer
    # and from the submitter — the officer who challenges the results cannot be the
    # officer who produced the narrative.
    preparers = {signoff.created_by, signoff.submitted_by}
    if ctx.actor_user_id in preparers and ctx.actor_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "maker_is_checker",
                "message": (
                    "The sign-off's preparer/submitter cannot attest it (maker ≠ checker)."
                ),
            },
        )
    signoff.status = "attested"
    signoff.credibility_rationale = payload.credibility_rationale
    if payload.board_challenge is not None:
        signoff.board_challenge = payload.board_challenge
    signoff.attested_by = ctx.actor_user_id
    signoff.attested_at = utc_now()
    signoff.version = signoff.version + 1
    record_event(
        db,
        ctx,
        event_type="enterprise_stress_signoff.attested",
        entity_type="enterprise_stress_signoff",
        entity_id=signoff.id,
        details={
            "run_id": str(signoff.run_id),
            "scenario_code": signoff.scenario_code,
            "version": signoff.version,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(signoff)


def withdraw_signoff(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffTransition,
) -> StressSignoffRead:
    signoff = _get_signoff_or_404(db, ctx, bank_id, signoff_id)
    if signoff.status == "withdrawn":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "already_withdrawn",
                "message": "This sign-off is already withdrawn.",
            },
        )
    signoff.status = "withdrawn"
    record_event(
        db,
        ctx,
        event_type="enterprise_stress_signoff.withdrawn",
        entity_type="enterprise_stress_signoff",
        entity_id=signoff.id,
        details={"run_id": str(signoff.run_id), "reason": payload.reason},
    )
    db.commit()
    return _read(signoff)


def resolve_attested_run_for_period(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID
) -> RegulatoryRun:
    """Return the enterprise-stress run of the latest ATTESTED sign-off, or refuse.

    The ICAAP Appendix II reporting generator calls this: a return that carries
    the directive's regulatory deliverable may be built ONLY from an enterprise-
    stress run the Board has attested (¶20, ¶67). No attested sign-off ⇒ 409.
    """
    signoff = db.scalar(
        select(EnterpriseStressSignoff)
        .where(
            EnterpriseStressSignoff.organization_id == ctx.organization_id,
            EnterpriseStressSignoff.bank_id == bank_id,
            EnterpriseStressSignoff.reporting_period_id == reporting_period_id,
            EnterpriseStressSignoff.status == "attested",
        )
        .order_by(EnterpriseStressSignoff.attested_at.desc())
        .limit(1)
    )
    if signoff is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_attested_stress_run",
                "message": (
                    "No Board-attested enterprise-stress run exists for this reporting "
                    "period. Run the enterprise stress test, then prepare and attest its "
                    "sign-off before generating the ICAAP Appendix II submission."
                ),
            },
        )
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == signoff.run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "attested_run_missing",
                "message": "The attested sign-off references a run that no longer exists.",
            },
        )
    return run


def latest_signoff_for_run(
    db: Session, ctx: TenantContext, run_id: UUID
) -> EnterpriseStressSignoff | None:
    return db.scalar(
        select(EnterpriseStressSignoff).where(
            EnterpriseStressSignoff.organization_id == ctx.organization_id,
            EnterpriseStressSignoff.run_id == run_id,
        )
    )
