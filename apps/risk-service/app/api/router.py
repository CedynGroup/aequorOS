from __future__ import annotations

from fastapi import APIRouter

from app.api.health import router as health_router
from app.features.list_taxonomy import router as taxonomy_router
from app.features.manage_documents import router as documents_router
from app.features.read_financial_workspace import router as financial_workspace_router
from app.features.review_cases import router as cases_router
from app.features.review_findings import router as findings_router
from app.features.run_assessments import router as assessments_router
from app.features.track_jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(health_router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(cases_router)
v1_router.include_router(documents_router)
v1_router.include_router(financial_workspace_router)
v1_router.include_router(jobs_router)
v1_router.include_router(assessments_router)
v1_router.include_router(findings_router)
v1_router.include_router(taxonomy_router)
api_router.include_router(v1_router)
