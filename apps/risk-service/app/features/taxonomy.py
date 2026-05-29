from __future__ import annotations

from fastapi import APIRouter

from app.features.constants import ASSESSMENT_TYPES, RISK_TYPES

router = APIRouter(tags=["taxonomy"])


@router.get("/risk-types")
def risk_types() -> dict[str, list[str]]:
    return {"risk_types": RISK_TYPES}


@router.get("/assessment-types")
def assessment_types() -> dict[str, list[str]]:
    return {"assessment_types": ASSESSMENT_TYPES}
