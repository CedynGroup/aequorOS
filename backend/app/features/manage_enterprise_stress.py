"""Enterprise-wide stress test endpoints (docs/stress.md §3.3–3.4, Phase 2).

POST runs one enterprise stress against an APPROVED macro scenario and persists
it as an immutable RegulatoryRun (analyst+ — it mints an official run); GET
retrieves the latest run (with its Appendix II tables) for a period + scenario.
Governed (approved-scenario-only) and RLS-scoped.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.enterprise_stress import (
    EnterpriseStressRead,
    EnterpriseStressRunCreate,
    EnterpriseStressRunSummary,
)
from app.services import enterprise_stress

router = APIRouter(tags=["enterprise-stress"])


@router.post(
    "/banks/{bank_id}/enterprise-stress/runs",
    response_model=EnterpriseStressRead,
    status_code=201,
    operation_id="runEnterpriseStress",
)
def run_enterprise_stress(
    bank_id: str,
    payload: EnterpriseStressRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> EnterpriseStressRead:
    return enterprise_stress.run_enterprise_stress_test(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/enterprise-stress/latest",
    response_model=EnterpriseStressRead,
    operation_id="getLatestEnterpriseStress",
)
def get_latest_enterprise_stress(
    bank_id: str,
    reporting_period_id: UUID,
    scenario_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> EnterpriseStressRead:
    return enterprise_stress.get_latest_enterprise_stress(
        db, ctx, bank_id, reporting_period_id, scenario_id
    )


@router.get(
    "/banks/{bank_id}/enterprise-stress/runs",
    response_model=list[EnterpriseStressRunSummary],
    operation_id="listEnterpriseStressRuns",
)
def list_enterprise_stress_runs(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: UUID | None = None,
) -> list[EnterpriseStressRunSummary]:
    """The run registry: full enterprise-stress run history (docs/stress.md §4.5)."""
    return enterprise_stress.list_enterprise_stress_runs(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/enterprise-stress/runs/{run_id}",
    response_model=EnterpriseStressRead,
    operation_id="getEnterpriseStressRun",
)
def get_enterprise_stress_run(
    bank_id: str,
    run_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> EnterpriseStressRead:
    """Re-open one immutable enterprise-stress run by id (docs/stress.md §4.5)."""
    return enterprise_stress.get_enterprise_stress_run(db, ctx, bank_id, run_id)
