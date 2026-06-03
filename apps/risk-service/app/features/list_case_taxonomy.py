from __future__ import annotations

from fastapi import APIRouter

from app.domain.risk_constants import (
    CASE_DECISIONS,
    CASE_SORT_OPTIONS,
    CASE_STATUSES,
    RISK_LEVELS,
)
from app.schemas.cases import CaseTaxonomyRead

router = APIRouter(tags=["taxonomy"])


@router.get("/taxonomies/cases", response_model=CaseTaxonomyRead)
def case_taxonomy_resource() -> CaseTaxonomyRead:
    return CaseTaxonomyRead(
        statuses=sorted(CASE_STATUSES),
        decisions=sorted(CASE_DECISIONS),
        risk_levels=sorted(RISK_LEVELS),
        sort_options=sorted(CASE_SORT_OPTIONS),
    )
