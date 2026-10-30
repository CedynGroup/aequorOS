"""Irrbb's interface for other features: re-exports only, no logic."""

from app.irrbb.service import regulatory_irr, regulatory_irr_sf
from app.irrbb.service.regulatory_irr import IRR_RUN_SCENARIO_CODES
from app.irrbb.service.regulatory_irr_sf import CODE_MANDATORY_FROM, SfMandate

__all__ = [
    "CODE_MANDATORY_FROM",
    "IRR_RUN_SCENARIO_CODES",
    "SfMandate",
    "regulatory_irr",
    "regulatory_irr_sf",
]
