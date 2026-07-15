from __future__ import annotations

from pydantic import BaseModel


class AssessmentTypesRead(BaseModel):
    assessment_types: list[str]


class RiskTypesRead(BaseModel):
    risk_types: list[str]
