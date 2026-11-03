"""Examiner mode v1 (product.md §Phase 2 item 7; ai_engine.md §10.6).

Read-only supervisory surfaces over state that already exists:

- run reproduction: every regulatory module hashes its input snapshot with
  the same canonical JSON form, so an examiner can ask "does the stored
  snapshot still hash to the stored input_hash?" — recomputed live, per run.
- the documentation package: one bundle answering "show me everything" —
  runs per module (latest succeeded, hash, engine version), filed packages
  with their content seals, the Board registers (thresholds, haircuts, ECL,
  CRM), plan/CFP posture and audit-trail volume for the period.

Nothing here mutates.

Authority is two independent sentences (issue 180, see ``examiner_access``):
the bundle's AUDIT/restricted ``view`` entry gate, and the exact source-module
``view`` for every embedded module. Sources the reader cannot open are filtered
out before the response is composed, and a by-id read of a run whose source
module is not held answers the same opaque 404 a foreign run does. Operator
inspection (act-as-examiner staff sessions) remains a separate lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.identity.public import Bank, count_attributable_events
from app.liquidity.public import get_bank_or_404 as _get_bank_or_404
from app.liquidity.public import get_cfp
from app.liquidity.public import get_period_or_404 as _get_period_or_404
from app.live.public import RegulatoryRun
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.schemas.examiner import (
    ExaminerDocumentationRead,
    ExaminerIcaapCycleRead,
    ExaminerPackageRead,
    ExaminerRunRead,
    ExaminerRunsRead,
    RunReproductionRead,
)
from app.reporting.service import examiner_access, family_access

#: One sentence for both run refusals, so a principal that cannot see a run's
#: source module cannot tell that run apart from one that does not exist.
_RUN_NOT_FOUND = "Regulatory run not found."


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
    examiner_access.require_evidence_view(db, ctx, bank)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    authority = examiner_access.EvidenceAuthority(db, ctx, bank)
    conditions = [
        RegulatoryRun.organization_id == ctx.organization_id,
        RegulatoryRun.bank_id == bank.id,
        RegulatoryRun.reporting_period_id == period.id,
        # Filtered in SQL, before the row is read: a run of a module this
        # principal cannot open is not in the result set at all.
        RegulatoryRun.module.in_(sorted(authority.run_modules_visible())),
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
    examiner_access.require_evidence_view(db, ctx, bank)
    authority = examiner_access.EvidenceAuthority(db, ctx, bank)
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
        )
    )
    # An unauthorized run module is indistinguishable from a missing run: the
    # reproduction answer discloses the snapshot's shape, so its authority is
    # the run's own, not the bundle's.
    if run is None or not authority.run_module_visible(run.module):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_RUN_NOT_FOUND)
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
    examiner_access.require_evidence_view(db, ctx, bank)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    authority = examiner_access.EvidenceAuthority(db, ctx, bank)

    runs = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.status == "succeeded",
            RegulatoryRun.module.in_(sorted(authority.run_modules_visible())),
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
    # A package of a GATED family is served to an impersonated examiner (a
    # supervisor may read what was filed) but hidden from a tenant principal
    # without the binding — the same decision the package routes make, taken
    # through the same authority so the two cannot disagree.
    hidden = family_access.hidden_families(db, ctx, bank)
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
        if row.return_family not in hidden
    ]

    cfp_visible = authority.cfp_visible()
    cfp = get_cfp(db, ctx, bank_id) if cfp_visible else None
    icaap_cycles = _examiner_icaap_cycles(db, ctx, bank, period.period_end, hidden=hidden)
    withheld: set[str] = {f"run:{module}" for module in authority.withheld_run_modules()}
    withheld.update(f"package:{family}" for family in hidden)
    withheld.update(
        f"register:{name}"
        for name, _module, _sensitivity in examiner_access.SOURCE_REGISTERS
        if not authority.register_visible(name)
    )
    if not cfp_visible:
        withheld.add("cfp")
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
        cfp_approved_version=(cfp.approved.version if cfp and cfp.approved else None),
        cfp_active=bool(cfp and cfp.approved and cfp.approved.active),
        audit_event_count=count_attributable_events(db, ctx, bank),
        register_endpoints=[
            f"/api/v1/banks/{bank.id}/{name}"
            for name, _module, _sensitivity in examiner_access.SOURCE_REGISTERS
            if authority.register_visible(name)
        ]
        + ([f"/api/v1/banks/{bank.id}/icaap/cycles"] if icaap_cycles else []),
        icaap_cycles=icaap_cycles,
        withheld_sources=sorted(withheld),
    )


def _examiner_icaap_cycles(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period_end: date,
    *,
    hidden: frozenset[str],
) -> list[ExaminerIcaapCycleRead]:
    """The ICAAP cycles for this reporting date that a supervisor may read.

    Frozen and later only — the same rule ``icaap.guards.get_cycle_or_404``
    applies to every other examiner ICAAP read, resolved from that module so
    there is one definition of "what a supervisor may see" rather than two.
    """
    from app.icaap.models.workspace import IcaapCycle  # noqa: PLC0415 - avoid an import cycle
    from app.icaap.service.guards import EXAMINER_VISIBLE_STATUSES  # noqa: PLC0415

    if "icaap" in hidden:
        return []
    rows = db.scalars(
        select(IcaapCycle)
        .where(
            IcaapCycle.organization_id == ctx.organization_id,
            IcaapCycle.bank_id == bank.id,
            IcaapCycle.as_of_date == period_end,
            IcaapCycle.status.in_(sorted(EXAMINER_VISIBLE_STATUSES)),
        )
        .order_by(IcaapCycle.as_of_date.desc(), IcaapCycle.created_at.desc())
    ).all()
    return [
        ExaminerIcaapCycleRead(
            cycle_id=row.id,
            cycle_kind=row.cycle_kind,
            basis=row.basis,
            status=row.status,
            round=getattr(row, "review_round", 1) or 1,
            package_id=getattr(row, "package_id", None),
            framework_code=row.framework_code,
            framework_version=row.framework_version,
            framework_digest=row.framework_sha256,
            frozen_at=row.frozen_at,
            board_approved_at=getattr(row, "board_approved_at", None),
            submitted_at=getattr(row, "submitted_at", None),
        )
        for row in rows
    ]
