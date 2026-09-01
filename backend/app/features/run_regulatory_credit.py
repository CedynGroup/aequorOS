"""Credit / Loan Book module routes (credit PR-2).

Thin delegation into ``app.services.regulatory_credit``. Mounted with
``require_module_access("credit")`` in the API router — the module is in every
institution class's default set, so the gate is about per-tenant configuration,
not class scoping.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_credit import (
    CreditActivityRead,
    CreditConcentrationRead,
    CreditDashboardRead,
    CreditLoanFacetsRead,
    CreditLoansPageRead,
    CreditMigrationRead,
    CreditScenarioBatchCreate,
    CreditVintagesRead,
)
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_credit

router = APIRouter(tags=["regulatory-credit"])


@router.post(
    "/banks/{bank_id}/credit/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllCreditScenarios",
)
def run_all_credit_scenarios(
    bank_id: str,
    payload: CreditScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_credit.run_all_credit_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/credit/dashboard",
    response_model=CreditDashboardRead,
    operation_id="getCreditDashboard",
)
def get_credit_dashboard(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> CreditDashboardRead:
    return regulatory_credit.get_credit_dashboard(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/credit/loans",
    response_model=CreditLoansPageRead,
    operation_id="listCreditLoans",
)
def list_credit_loans(  # noqa: PLR0913 - one query parameter per blotter filter
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    grade: Annotated[str | None, Query()] = None,
    product: Annotated[str | None, Query()] = None,
    branch: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> CreditLoansPageRead:
    return regulatory_credit.list_credit_loans(
        db,
        ctx,
        bank_id,
        limit=limit,
        offset=offset,
        grade=grade,
        product=product,
        branch=branch,
        q=q,
    )


@router.get(
    "/banks/{bank_id}/credit/loans/facets",
    response_model=CreditLoanFacetsRead,
    operation_id="getCreditLoanFacets",
)
def get_credit_loan_facets(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> CreditLoanFacetsRead:
    return regulatory_credit.get_credit_loan_facets(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/concentration",
    response_model=CreditConcentrationRead,
    operation_id="getCreditConcentration",
)
def get_credit_concentration(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> CreditConcentrationRead:
    """The standing concentration monitor over the current credit book."""
    return regulatory_credit.get_credit_concentration(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/activity",
    response_model=CreditActivityRead,
    operation_id="getCreditActivity",
)
def get_credit_activity(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> CreditActivityRead:
    """Restructures, write-offs, recoveries and cures over the trailing year."""
    return regulatory_credit.get_credit_activity(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/migration",
    response_model=CreditMigrationRead,
    operation_id="getCreditMigration",
)
def get_credit_migration(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> CreditMigrationRead:
    """Month-over-month state migration and DPD roll rates."""
    return regulatory_credit.get_credit_migration(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/vintages",
    response_model=CreditVintagesRead,
    operation_id="getCreditVintages",
)
def get_credit_vintages(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> CreditVintagesRead:
    """Cohort PAR30+ curves by origination month and months on book."""
    return regulatory_credit.get_credit_vintages(db, ctx, bank_id)
