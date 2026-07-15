from __future__ import annotations

from fastapi import APIRouter

from app.api.health import router as health_router
from app.features.bulk_update_cases import router as bulk_update_cases_router
from app.features.generate_case_reports import router as case_reports_router
from app.features.ingest_data import router as ingestion_router
from app.features.list_case_taxonomy import router as case_taxonomy_router
from app.features.list_taxonomy import router as taxonomy_router
from app.features.manage_banks import router as banks_router
from app.features.manage_capital import router as capital_router
from app.features.manage_documents import router as documents_router
from app.features.manage_scenarios import router as scenarios_router
from app.features.read_cashflow_forecast import router as cashflow_forecast_router
from app.features.read_financial_workspace import router as financial_workspace_router
from app.features.record_case_decisions import router as case_decisions_router
from app.features.review_cases import router as cases_router
from app.features.review_findings import router as findings_router
from app.features.review_liquidity import router as liquidity_router
from app.features.run_assessments import router as assessments_router
from app.features.run_calculations import router as calculations_router
from app.features.run_forecasting import router as forecasting_router
from app.features.run_regulatory_capital import router as regulatory_capital_router
from app.features.run_regulatory_ftp import router as regulatory_ftp_router
from app.features.run_regulatory_fx import router as regulatory_fx_router
from app.features.run_regulatory_irr import router as regulatory_irr_router
from app.features.run_regulatory_liquidity import router as regulatory_liquidity_router
from app.features.track_jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(health_router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(banks_router)
v1_router.include_router(ingestion_router)
v1_router.include_router(regulatory_liquidity_router)
v1_router.include_router(regulatory_capital_router)
v1_router.include_router(regulatory_irr_router)
v1_router.include_router(regulatory_fx_router)
v1_router.include_router(regulatory_ftp_router)
v1_router.include_router(forecasting_router)
v1_router.include_router(cashflow_forecast_router)
v1_router.include_router(bulk_update_cases_router)
v1_router.include_router(cases_router)
v1_router.include_router(case_decisions_router)
v1_router.include_router(case_reports_router)
v1_router.include_router(case_taxonomy_router)
v1_router.include_router(documents_router)
v1_router.include_router(financial_workspace_router)
v1_router.include_router(scenarios_router)
v1_router.include_router(calculations_router)
v1_router.include_router(capital_router)
v1_router.include_router(jobs_router)
v1_router.include_router(assessments_router)
v1_router.include_router(findings_router)
v1_router.include_router(liquidity_router)
v1_router.include_router(taxonomy_router)
api_router.include_router(v1_router)
