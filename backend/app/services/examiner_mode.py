"""Examiner mode v1 (product.md §Phase 2 item 7; ai_engine.md §10.6).

Read-only supervisory surfaces over state that already exists:

- run reproduction: every regulatory module hashes its input snapshot with
  the same canonical JSON form, so an examiner can ask "does the stored
  snapshot still hash to the stored input_hash?" — recomputed live, per run.
- the documentation package: one bundle answering "show me everything" —
  runs per module (latest succeeded, hash, engine version), filed packages
  with their content seals, the Board registers (thresholds, haircuts, ECL,
  CRM), plan/CFP posture and audit-trail volume for the period.

Nothing here mutates; the endpoints sit behind the read-only tenant guard
and the examiner role clears them by construction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AuditEvent,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.schemas.examiner import (
    ExaminerDocumentationRead,
    ExaminerPackageRead,
    ExaminerRunRead,
    ExaminerRunsRead,
    RunReproductionRead,
)
from app.services.liquidity_cfp import get_cfp
from app.services.liquidity_ewi import (
    _get_bank_or_404,  # noqa: PLC2701 - shared tenant guards, one definition
    _get_period_or_404,  # noqa: PLC2701
)


def _canonical_hash(snapshot: dict[str, Any]) -> str:
    """The one canonical snapshot-hash form every module uses."""
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _run_read(run: RegulatoryRun) -> ExaminerRunRead:
    return ExaminerRunRead(
        run_id=run.id,
        module=run.module,
        scenario_code=run.scenario_code,
        status=run.status,
        engine_version=run.engine_version,
        input_schema_version=run.input_schema_version,
        input_hash=run.input_hash,
        created_at=run.created_at,
    )


def list_runs(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID,
    module: str | None = None,
) -> ExaminerRunsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    conditions = [
        RegulatoryRun.organization_id == ctx.organization_id,
        RegulatoryRun.bank_id == bank.id,
        RegulatoryRun.reporting_period_id == period.id,
    ]
    if module is not None:
        conditions.append(RegulatoryRun.module == module)
    runs = db.scalars(
        select(RegulatoryRun)
        .where(*conditions)
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    ).all()
    return ExaminerRunsRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        runs=[_run_read(run) for run in runs],
    )


def reproduce_run(
    db: Session, ctx: TenantContext, bank_id: str, run_id: UUID
) -> RunReproductionRead:
    """Recompute the canonical hash over the stored snapshot, live."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory run not found."
        )
    recomputed = _canonical_hash(run.inputs)
    return RunReproductionRead(
        run_id=run.id,
        module=run.module,
        scenario_code=run.scenario_code,
        engine_version=run.engine_version,
        input_schema_version=run.input_schema_version,
        stored_input_hash=run.input_hash,
        recomputed_input_hash=recomputed,
        reproducible=recomputed == run.input_hash,
        fact_count=len(run.inputs.get("facts", []))
        if isinstance(run.inputs.get("facts"), list)
        else None,
        created_at=run.created_at,
    )


def documentation_package(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID
) -> ExaminerDocumentationRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)

    runs = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    ).all()
    latest: dict[tuple[str, str], RegulatoryRun] = {}
    for run in runs:
        latest.setdefault((run.module, run.scenario_code), run)
    latest_runs = [
        _run_read(latest[key]) for key in sorted(latest, key=lambda item: (item[0], item[1]))
    ]

    packages = db.scalars(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.reporting_date == period.period_end,
        )
        .order_by(RegulatoryPackage.return_code, RegulatoryPackage.version.desc())
    ).all()
    package_reads = [
        ExaminerPackageRead(
            package_id=row.id,
            return_code=row.return_code,
            version=row.version,
            status=row.status,
            basis=row.basis,
            content_digest=row.content_digest,
        )
        for row in packages
    ]

    audit_count = (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.organization_id == ctx.organization_id)
        )
        or 0
    )
    cfp = get_cfp(db, ctx, bank_id)
    # Register reads are import-light here: the examiner package cites their
    # dedicated endpoints rather than duplicating every row inline.
    return ExaminerDocumentationRead(
        bank_id=bank.id,
        bank_name=bank.name,
        jurisdiction_code=bank.jurisdiction_code,
        reporting_period_id=period.id,
        period_label=period.label,
        as_of_date=period.period_end,
        latest_runs=latest_runs,
        packages=package_reads,
        cfp_approved_version=cfp.approved.version if cfp.approved else None,
        cfp_active=bool(cfp.approved and cfp.approved.active),
        audit_event_count=int(audit_count),
        register_endpoints=[
            f"/api/v1/banks/{bank.id}/liquidity-thresholds",
            f"/api/v1/banks/{bank.id}/liquidity-haircuts",
            f"/api/v1/banks/{bank.id}/ecl-assumptions",
            f"/api/v1/banks/{bank.id}/crm-haircuts",
            f"/api/v1/banks/{bank.id}/liquidity/ewis",
            f"/api/v1/banks/{bank.id}/capital-plan",
        ],
    )
