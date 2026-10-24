"""Reporting registry: shared policy definitions plus official form templates."""

from app.domain.policy.return_registry import (
    ACCRA_TZ,
    REGISTRY,
    REGULATOR_BOG,
    ChannelCode,
    FidelityGrade,
    FilingFormat,
    FilingRole,
    ReturnDefinition,
    ReturnFamily,
    ReturnFrequency,
    _add_months,
    _deadline_is_governed,
    _month_end,
    annual_month_day,
    daily_next_business_day,
    monthly_day,
    quarterly_days_after,
)
from app.reporting.service.bog_forms.registry_entries import build_definitions

REGISTRY.update({definition.code: definition for definition in build_definitions(ReturnDefinition)})


def get_definition(return_code: str) -> ReturnDefinition | None:
    return REGISTRY.get(return_code)


__all__ = [
    "ACCRA_TZ",
    "REGISTRY",
    "REGULATOR_BOG",
    "ChannelCode",
    "FidelityGrade",
    "FilingFormat",
    "FilingRole",
    "ReturnDefinition",
    "ReturnFamily",
    "ReturnFrequency",
    "_add_months",
    "_deadline_is_governed",
    "_month_end",
    "annual_month_day",
    "daily_next_business_day",
    "monthly_day",
    "quarterly_days_after",
    "get_definition",
]
