from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.regulatory_instruments import forecast_status
from app.identity.public import Bank
from app.models import ParamCapitalThreshold
from app.services import regulatory_parameters
from app.services.params import get_active_params


def rule_in_force(
    db: Session, ctx: TenantContext, bank: Bank, code: str, *, as_of: date | None = None
) -> bool:
    from app.reporting.public import REGISTRY, resolve_eligibility  # noqa: PLC0415

    when = as_of or date.today()
    return (
        resolve_eligibility(db, ctx, bank, as_of=when).instrument_status(REGISTRY[code], when)
        == "in_force"
    )


def forecast_legacy_thresholds(db: Session, bank: Bank, as_of: date) -> dict[str, Decimal]:
    rows = get_active_params(
        db, bank.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
    return regulatory_parameters.clamp_overrides(
        db, bank, {row.threshold_code: Decimal(str(row.value_pct)) for row in rows}, as_of=as_of
    ).values


def forecast_view_status(
    db: Session,
    bank: Bank,
    metrics: Mapping[str, object],
    as_of: date | None,
    *,
    liquidity_in_force: bool,
) -> Literal["green", "amber", "red", "na"]:
    thresholds: dict[str, Decimal] | None = None
    if "year5_car_status" not in metrics and as_of is not None:
        thresholds = forecast_legacy_thresholds(db, bank, as_of)
    return forecast_status(metrics, liquidity_in_force, thresholds)
