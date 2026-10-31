"""FTP's interface for other features: re-exports only, no logic."""

from app.ftp import service as regulatory_ftp
from app.ftp.service import (
    FTP_RUN_SCENARIO_CODES,
    SHOCK_CURVE_SHIFT_BP,
    SHOCK_FUNDING_ADD_BP,
    SHOCK_MIN_MARGIN_PCT,
    SHOCK_NMD_CORE_MAX_PCT,
    SHOCK_NMD_CORE_MIN_PCT,
)

__all__ = [
    "FTP_RUN_SCENARIO_CODES",
    "SHOCK_CURVE_SHIFT_BP",
    "SHOCK_FUNDING_ADD_BP",
    "SHOCK_MIN_MARGIN_PCT",
    "SHOCK_NMD_CORE_MAX_PCT",
    "SHOCK_NMD_CORE_MIN_PCT",
    "regulatory_ftp",
]
