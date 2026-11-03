from __future__ import annotations

from fastapi import APIRouter

from app.case.schemas.taxonomy import AssessmentTypesRead, RiskTypesRead
from app.domain.risk_constants import ASSESSMENT_TYPES, RISK_TYPES

router = APIRouter(tags=["taxonomy"])


@router.get("/risk-types", response_model=RiskTypesRead)
def risk_types() -> RiskTypesRead:
    return RiskTypesRead(risk_types=RISK_TYPES)


@router.get("/assessment-types", response_model=AssessmentTypesRead)
def assessment_types() -> AssessmentTypesRead:
    return AssessmentTypesRead(assessment_types=ASSESSMENT_TYPES)
