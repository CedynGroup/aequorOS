"""Test-only forecast assumptions for hermetic bank and governance fixtures."""

from collections.abc import Mapping
from typing import Final

FORECAST_PRESETS: Final[Mapping[str, Mapping[str, str]]] = {
    "base": {
        "loan_growth_pct": "18",
        "deposit_growth_pct": "16",
        "nim_pct": "4.8",
        "cost_to_income_pct": "48",
        "credit_loss_rate_pct": "1.0",
        "fx_depreciation_pct": "0",
        "dividend_payout_pct": "30",
    },
    "adverse": {
        "loan_growth_pct": "8",
        "deposit_growth_pct": "6",
        "nim_pct": "4.2",
        "cost_to_income_pct": "54",
        "credit_loss_rate_pct": "1.5",
        "fx_depreciation_pct": "15",
        "dividend_payout_pct": "0",
    },
    "severely_adverse": {
        "loan_growth_pct": "-2",
        "deposit_growth_pct": "-8",
        "nim_pct": "3.6",
        "cost_to_income_pct": "60",
        "credit_loss_rate_pct": "2.0",
        "fx_depreciation_pct": "40",
        "dividend_payout_pct": "0",
    },
}
