"""Permission-aware response projection for immutable enterprise stress runs."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.models import Bank
from app.services import scoped_authorization

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def project_response(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    response: ResponseModel,
    *,
    sensitivity: Sensitivity,
) -> ResponseModel:
    decision = scoped_authorization.evaluate_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.FX,
        sensitivity=sensitivity,
        surface="enterprise_run_visibility",
    )
    if decision is not None and decision.allowed:
        return response

    payload = response.model_dump(mode="python")
    metrics = payload.get("metrics", payload)
    outcome = metrics.get("outcome", {})
    outcome.pop("fx", None)
    for row in metrics.get("appendix_ii", {}).get("table5_rwa", {}).get("rows", []):
        pillar2 = row.get("pillar2", {})
        if pillar2.pop("country_and_fx", None) is not None:
            pillar2.pop("total", None)
            row.pop("total_capital_requirement", None)
    risk_drivers = metrics.get("appendix_ii", {}).get("table6_risk_drivers", {})
    if "rows" in risk_drivers:
        risk_drivers["rows"] = [
            row for row in risk_drivers["rows"] if row.get("variable") != "fx_usd_ghs"
        ]
    metrics.get("plan_provenance", {}).get("fields", {}).pop("fx_depreciation_pct", None)
    defaults = metrics.get("plan_provenance", {}).get("platform_default_fields")
    if isinstance(defaults, list):
        metrics["plan_provenance"]["platform_default_fields"] = [
            field for field in defaults if field != "fx_depreciation_pct"
        ]
    inputs = payload.get("inputs", {})
    inputs.pop("include_fx", None)
    inputs.pop("fx_evaluated", None)
    inputs.get("plan", {}).pop("fx_depreciation_pct", None)
    scenario = inputs.get("scenario", {})
    if "paths" in scenario:
        scenario["paths"] = [
            point for point in scenario["paths"] if point.get("variable") != "fx_usd_ghs"
        ]
    return type(response).model_validate(payload)
