from __future__ import annotations

from fastapi import APIRouter

from app.api.health import router as health_router
from app.features.assessments import router as assessments_router
from app.features.cases import router as cases_router
from app.features.documents import router as documents_router
from app.features.findings import router as findings_router
from app.features.jobs import router as jobs_router
from app.features.taxonomy import router as taxonomy_router

api_router = APIRouter()
api_router.include_router(health_router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(cases_router)
v1_router.include_router(documents_router)
v1_router.include_router(jobs_router)
v1_router.include_router(assessments_router)
v1_router.include_router(findings_router)
v1_router.include_router(taxonomy_router)
api_router.include_router(v1_router)
