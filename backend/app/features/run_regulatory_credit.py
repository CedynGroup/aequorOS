"""Credit / Loan Book module routes (credit PR-2; scoped-binding cutover P4-C).

Thin delegation into ``app.services.regulatory_credit``. Mounted with
``require_module_access("credit")`` in the API router — the module is in every
institution class's default set, so that gate is about per-tenant CONFIGURATION,
not authority, and it stays because entitlement is orthogonal to a sentence.

Authority is per route, and the permission tuple is chosen from what the response
DISCLOSES rather than once for the module
(``backend/docs/credit_enforcement_rollout.md`` is the contract):

* ``CreditAggregatedView`` — dashboard, migration, vintages, PD: the
  institution's portfolio figures, whole institution required.
* ``CreditConcentrationView`` — the monitor names obligors (``cp:<name>``), so it
  is ``restricted``, and it measures against institution capital, so it is whole
  institution too.
* ``CreditBlotterView`` — the loan blotter and its facets: named obligor rows,
  each with its own branch, so the reader's data scope is APPLIED here.
* ``CreditActivityView`` — the record-level event grid: ``confidential``, scope
  applied through the facility each event names.
* ``CreditRun`` — minting regulatory runs is ``run``, never ``view``.

Each dependency resolves the institution first (the router's
``resolve_tenant_bank``), so a bank of another tenant is 404 before any sentence
is evaluated and a refusal never confirms that an institution exists.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CreditActivityView,
    CreditAggregatedView,
    CreditBlotterView,
    CreditConcentrationView,
    CreditRun,
    DbSession,
)
from app.schemas.regulatory_credit import (
    CreditActivityRead,
    CreditConcentrationRead,
    CreditDashboardRead,
    CreditLoanFacetsRead,
    CreditLoansPageRead,
    CreditMigrationRead,
    CreditPdRead,
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
    access: CreditRun,
) -> RegulatoryRunBatchRead:
    """Seal the baseline credit run for one reporting period.

    The dependency requires the whole institution, so the sealed snapshot is the
    institution's book — the run has no way to record that it covered a slice, and
    a filing record that claims more than it measured is worse than no record.
    """
    return regulatory_credit.run_all_credit_scenarios(db, access.ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/credit/dashboard",
    response_model=CreditDashboardRead,
    operation_id="getCreditDashboard",
)
def get_credit_dashboard(
    bank_id: str,
    db: DbSession,
    access: CreditAggregatedView,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> CreditDashboardRead:
    return regulatory_credit.get_credit_dashboard(db, access.ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/credit/loans",
    response_model=CreditLoansPageRead,
    operation_id="listCreditLoans",
)
def list_credit_loans(  # noqa: PLR0913 - one query parameter per blotter filter
    bank_id: str,
    db: DbSession,
    access: CreditBlotterView,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    grade: Annotated[str | None, Query()] = None,
    product: Annotated[str | None, Query()] = None,
    branch: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query(max_length=120)] = None,
    stage: Annotated[int | None, Query(ge=1, le=3)] = None,
    dpd_band: Annotated[str | None, Query(max_length=32)] = None,
    as_of: Annotated[date | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> CreditLoansPageRead:
    """One page of the classified loan book, narrowed to one slice.

    ``sector``, ``stage``, ``dpd_band`` and ``as_of`` exist so a reader who
    followed a figure into the rows behind it lands on the SAME slice. The
    service refuses a stage or band outside the platform's vocabulary and a date
    with no computed position; the bounds declared here are the same refusals
    stated in the contract, so a client learns them without asking.

    ``branch`` is the client's filter; ``access.data_scope`` is the server's, and
    the two intersect. A branch outside the reader's scope therefore answers an
    empty page with the scope disclosed, never that branch's rows and never an
    error that would confirm the branch exists.
    """
    return regulatory_credit.list_credit_loans(
        db,
        access.ctx,
        bank_id,
        data_scope=access.data_scope,
        limit=limit,
        offset=offset,
        grade=grade,
        product=product,
        branch=branch,
        sector=sector,
        stage=stage,
        dpd_band=dpd_band,
        as_of=as_of,
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
    access: CreditBlotterView,
) -> CreditLoanFacetsRead:
    return regulatory_credit.get_credit_loan_facets(
        db, access.ctx, bank_id, data_scope=access.data_scope
    )


@router.get(
    "/banks/{bank_id}/credit/concentration",
    response_model=CreditConcentrationRead,
    operation_id="getCreditConcentration",
)
def get_credit_concentration(
    bank_id: str,
    db: DbSession,
    access: CreditConcentrationView,
) -> CreditConcentrationRead:
    """The standing concentration monitor over the current credit book."""
    return regulatory_credit.get_credit_concentration(db, access.ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/activity",
    response_model=CreditActivityRead,
    operation_id="getCreditActivity",
)
def get_credit_activity(
    bank_id: str,
    db: DbSession,
    access: CreditActivityView,
) -> CreditActivityRead:
    """Restructures, write-offs, recoveries and cures over the trailing year."""
    return regulatory_credit.get_credit_activity(
        db, access.ctx, bank_id, data_scope=access.data_scope
    )


@router.get(
    "/banks/{bank_id}/credit/migration",
    response_model=CreditMigrationRead,
    operation_id="getCreditMigration",
)
def get_credit_migration(
    bank_id: str,
    db: DbSession,
    access: CreditAggregatedView,
) -> CreditMigrationRead:
    """Month-over-month state migration and DPD roll rates."""
    return regulatory_credit.get_credit_migration(db, access.ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/vintages",
    response_model=CreditVintagesRead,
    operation_id="getCreditVintages",
)
def get_credit_vintages(
    bank_id: str,
    db: DbSession,
    access: CreditAggregatedView,
) -> CreditVintagesRead:
    """Cohort PAR30+ curves by origination month and months on book."""
    return regulatory_credit.get_credit_vintages(db, access.ctx, bank_id)


@router.get(
    "/banks/{bank_id}/credit/pd",
    response_model=CreditPdRead,
    operation_id="getCreditPd",
)
def get_credit_pd(
    bank_id: str,
    db: DbSession,
    access: CreditAggregatedView,
) -> CreditPdRead:
    """Migration-implied 12-month PDs (ADVISORY - never filed, never adopted
    into any register by the platform)."""
    return regulatory_credit.get_credit_pd(db, access.ctx, bank_id)
