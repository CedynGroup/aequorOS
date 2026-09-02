from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import require_module_access
from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.database_connections import router as database_direct_connections_router
from app.features.bulk_update_cases import router as bulk_update_cases_router
from app.features.examiner_surfaces import router as examiner_router
from app.features.generate_case_reports import router as case_reports_router
from app.features.ingest_data import router as ingestion_router
from app.features.list_case_taxonomy import router as case_taxonomy_router
from app.features.list_organization_users import router as organization_users_router
from app.features.list_taxonomy import router as taxonomy_router
from app.features.manage_attestation import router as attestation_router
from app.features.manage_authorization import router as authorization_router
from app.features.manage_banks import router as banks_router
from app.features.manage_capital import router as capital_router
from app.features.manage_capital_plan import router as capital_plan_router
from app.features.manage_credit_params import router as credit_params_router
from app.features.manage_documents import router as documents_router
from app.features.manage_enterprise_stress import router as enterprise_stress_router
from app.features.manage_enterprise_stress_signoff import (
    router as enterprise_stress_signoff_router,
)
from app.features.manage_institution_profile import router as institution_profile_router
from app.features.manage_integration_keys import router as integration_keys_router
from app.features.manage_liquidity_cfp import router as liquidity_cfp_router
from app.features.manage_liquidity_thresholds import router as liquidity_thresholds_router
from app.features.manage_live_engine import router as live_engine_router
from app.features.manage_macro_scenarios import router as macro_scenarios_router
from app.features.manage_management_actions import router as management_actions_router
from app.features.manage_market_data_connections import router as market_data_connections_router
from app.features.manage_market_data_overlays import router as market_data_overlays_router
from app.features.manage_market_data_uploads import router as market_data_uploads_router
from app.features.manage_notifications import router as notifications_router
from app.features.manage_reconciliation import router as reconciliation_router
from app.features.manage_regulatory_reporting import router as regulatory_reporting_router
from app.features.manage_scenarios import router as scenarios_router
from app.features.manage_stress_scenarios import router as stress_scenarios_router
from app.features.manage_system_of_record import router as system_of_record_router
from app.features.manage_temenos_connections import router as temenos_connections_router
from app.features.market_data_sources import router as market_data_sources_router
from app.features.push_data import router as push_router
from app.features.read_behavioral_models import router as behavioral_models_router
from app.features.read_cashflow_forecast import router as cashflow_forecast_router
from app.features.read_cashflow_window import router as cashflow_window_router
from app.features.read_financial_workspace import router as financial_workspace_router
from app.features.read_liquidity_monitoring import router as liquidity_monitoring_router
from app.features.read_market_data_views import router as market_data_views_router
from app.features.read_sdi_diagnostics import router as sdi_diagnostics_router
from app.features.read_window_analytics import router as window_analytics_router
from app.features.record_case_decisions import router as case_decisions_router
from app.features.review_cases import router as cases_router
from app.features.review_findings import router as findings_router
from app.features.review_liquidity import router as liquidity_router
from app.features.run_assessments import router as assessments_router
from app.features.run_calculations import router as calculations_router
from app.features.run_forecasting import router as forecasting_router
from app.features.run_implied_rating import router as implied_rating_router
from app.features.run_regulatory_capital import router as regulatory_capital_router
from app.features.run_regulatory_credit import router as regulatory_credit_router
from app.features.run_regulatory_ftp import router as regulatory_ftp_router
from app.features.run_regulatory_fx import router as regulatory_fx_router
from app.features.run_regulatory_irr import router as regulatory_irr_router
from app.features.run_regulatory_liquidity import router as regulatory_liquidity_router
from app.features.run_reverse_stress import router as reverse_stress_router
from app.features.run_scenario_analysis import router as scenario_analysis_router
from app.features.track_jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(health_router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(auth_router)
v1_router.include_router(attestation_router)
v1_router.include_router(authorization_router)
v1_router.include_router(banks_router)
v1_router.include_router(ingestion_router)
v1_router.include_router(system_of_record_router)
v1_router.include_router(push_router)
v1_router.include_router(database_direct_connections_router)
v1_router.include_router(regulatory_liquidity_router)
v1_router.include_router(liquidity_thresholds_router)
v1_router.include_router(liquidity_cfp_router)
v1_router.include_router(credit_params_router)
v1_router.include_router(capital_plan_router)
v1_router.include_router(examiner_router)
v1_router.include_router(stress_scenarios_router)
v1_router.include_router(macro_scenarios_router)
v1_router.include_router(management_actions_router)
v1_router.include_router(scenario_analysis_router)
v1_router.include_router(reverse_stress_router)
v1_router.include_router(enterprise_stress_router)
v1_router.include_router(enterprise_stress_signoff_router)
v1_router.include_router(regulatory_capital_router)
# Server-side module scoping (docs/sdi.md §14): IRRBB, FX and FTP are scoped out
# of the SDI module set, so an SDI tenant is rejected at the API, not merely in
# the nav. A universal bank has all modules and is unaffected.
v1_router.include_router(regulatory_irr_router, dependencies=[require_module_access("irrbb")])
v1_router.include_router(regulatory_credit_router, dependencies=[require_module_access("credit")])
v1_router.include_router(regulatory_fx_router, dependencies=[require_module_access("fx")])
v1_router.include_router(regulatory_ftp_router, dependencies=[require_module_access("ftp")])
v1_router.include_router(regulatory_reporting_router)
# The reconciliation escape valve (audit 2026-08-22 D-20): the fail-closed
# balance-sheet identity control had no product path to record an approved,
# bounded exception, so a blocked tenant could only be unblocked by a database
# write. Class-agnostic — the control applies to every institution.
v1_router.include_router(reconciliation_router)
v1_router.include_router(organization_users_router)
v1_router.include_router(institution_profile_router)
v1_router.include_router(integration_keys_router)
v1_router.include_router(notifications_router)
v1_router.include_router(forecasting_router, dependencies=[require_module_access("forecasting")])
v1_router.include_router(implied_rating_router)
v1_router.include_router(live_engine_router)
v1_router.include_router(market_data_uploads_router)
v1_router.include_router(market_data_connections_router)
v1_router.include_router(market_data_overlays_router)
v1_router.include_router(market_data_sources_router)
v1_router.include_router(temenos_connections_router)
v1_router.include_router(cashflow_forecast_router)
v1_router.include_router(
    behavioral_models_router, dependencies=[require_module_access("behavioral")]
)
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
v1_router.include_router(market_data_views_router)
v1_router.include_router(window_analytics_router)
v1_router.include_router(cashflow_window_router)
v1_router.include_router(liquidity_monitoring_router)
v1_router.include_router(sdi_diagnostics_router)
api_router.include_router(v1_router)
