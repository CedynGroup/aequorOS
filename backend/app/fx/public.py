"""FX's interface for other features: re-exports only, no logic."""

from app.fx import service as regulatory_fx
from app.fx.service import FX_RUN_SCENARIO_CODES, SHOCK_DEPRECIATION

__all__ = [
    "FX_RUN_SCENARIO_CODES",
    "SHOCK_DEPRECIATION",
    "regulatory_fx",
]
