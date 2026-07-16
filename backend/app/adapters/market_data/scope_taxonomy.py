"""Canonical business-scope taxonomy (market_data_adapter.md §5).

The taxonomy is the vocabulary the UI, calculation modules, and validation
layer all speak. The bank Treasury operator selects "Ghana yield curve", never
"GHGGB3M Index PX_LAST via BVAL" — vendor field names live only inside adapter
catalogs (§6.2 / §7.2), translated by ``scope_translator``.
"""

from __future__ import annotations

from enum import Enum


class DataScope(Enum):
    """Canonical business-scope taxonomy. Vendor-agnostic.

    Every vendor adapter must be able to translate these to its own field
    references (market_data_adapter.md §5). Adding a value is a coordinated
    change per §5.3: enum here, catalog entries per vendor, contract tests,
    onboarding UI.
    """

    YIELD_CURVE_GHS = "YIELD_CURVE_GHS"
    YIELD_CURVE_USD = "YIELD_CURVE_USD"
    YIELD_CURVE_EUR = "YIELD_CURVE_EUR"
    YIELD_CURVE_GBP = "YIELD_CURVE_GBP"
    YIELD_CURVE_NGN = "YIELD_CURVE_NGN"
    YIELD_CURVE_KES = "YIELD_CURVE_KES"
    YIELD_CURVE_ZAR = "YIELD_CURVE_ZAR"

    FX_SPOT_USD_GHS = "FX_SPOT_USD_GHS"
    FX_SPOT_EUR_GHS = "FX_SPOT_EUR_GHS"
    FX_SPOT_GBP_GHS = "FX_SPOT_GBP_GHS"
    FX_SPOT_USD_NGN = "FX_SPOT_USD_NGN"

    FX_FORWARD_USD_GHS_1M = "FX_FORWARD_USD_GHS_1M"
    FX_FORWARD_USD_GHS_3M = "FX_FORWARD_USD_GHS_3M"
    FX_FORWARD_USD_GHS_6M = "FX_FORWARD_USD_GHS_6M"
    FX_FORWARD_USD_GHS_12M = "FX_FORWARD_USD_GHS_12M"

    SECURITY_MASTER_GOG_BONDS = "SECURITY_MASTER_GOG_BONDS"
    SECURITY_MASTER_GOG_TBILLS = "SECURITY_MASTER_GOG_TBILLS"

    CREDIT_RATING_GHANA_SOVEREIGN = "CREDIT_RATING_GHANA_SOVEREIGN"
    CREDIT_RATING_NIGERIA_SOVEREIGN = "CREDIT_RATING_NIGERIA_SOVEREIGN"

    MACRO_GHANA_GDP_FORECAST = "MACRO_GHANA_GDP_FORECAST"
    MACRO_GHANA_INFLATION_FORECAST = "MACRO_GHANA_INFLATION_FORECAST"
    MACRO_GHANA_POLICY_RATE_PATH = "MACRO_GHANA_POLICY_RATE_PATH"


class PullFrequency(Enum):
    ON_DEMAND = "ON_DEMAND"
    HOURLY = "HOURLY"
    END_OF_DAY = "END_OF_DAY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class ScopeCategory(Enum):
    """The scope families of market_data_adapter.md §5.2.

    Freshness rules (§11.4), default pull frequencies (§9.2 step 6), and
    manual-upload templates (§8.2) are keyed by category, not by scope.
    """

    YIELD_CURVE = "YIELD_CURVE"
    FX_SPOT = "FX_SPOT"
    FX_FORWARD = "FX_FORWARD"
    SECURITY_MASTER = "SECURITY_MASTER"
    CREDIT_RATING = "CREDIT_RATING"
    MACRO_FORECAST = "MACRO_FORECAST"


# Ordered longest-prefix-first so FX_FORWARD_* never matches FX_SPOT's prefix.
_CATEGORY_PREFIXES: tuple[tuple[str, ScopeCategory], ...] = (
    ("YIELD_CURVE_", ScopeCategory.YIELD_CURVE),
    ("FX_FORWARD_", ScopeCategory.FX_FORWARD),
    ("FX_SPOT_", ScopeCategory.FX_SPOT),
    ("SECURITY_MASTER_", ScopeCategory.SECURITY_MASTER),
    ("CREDIT_RATING_", ScopeCategory.CREDIT_RATING),
    ("MACRO_", ScopeCategory.MACRO_FORECAST),
)


def category_of(scope: DataScope) -> ScopeCategory:
    """Return the §5.2 category a scope belongs to."""
    for prefix, category in _CATEGORY_PREFIXES:
        if scope.value.startswith(prefix):
            return category
    msg = f"DataScope {scope.value!r} does not match any known category prefix."
    raise ValueError(msg)


# Default pull frequencies per market_data_adapter.md §9.2 step 6: yield
# curves end-of-day, FX spot hourly during market hours, credit ratings
# weekly, macro forecasts monthly, security master weekly. FX forwards are
# not listed in step 6; END_OF_DAY follows §11.4 which gives forwards the
# same until-next-business-day freshness as curves.
DEFAULT_FREQUENCY_BY_CATEGORY: dict[ScopeCategory, PullFrequency] = {
    ScopeCategory.YIELD_CURVE: PullFrequency.END_OF_DAY,
    ScopeCategory.FX_SPOT: PullFrequency.HOURLY,
    ScopeCategory.FX_FORWARD: PullFrequency.END_OF_DAY,
    ScopeCategory.SECURITY_MASTER: PullFrequency.WEEKLY,
    ScopeCategory.CREDIT_RATING: PullFrequency.WEEKLY,
    ScopeCategory.MACRO_FORECAST: PullFrequency.MONTHLY,
}

# Standard curve tenors per §5.2: 1M, 3M, 6M, 12M, 24M, 36M, 60M, 84M, 120M.
# Vendors may natively provide additional tenors; these are the guaranteed set.
STANDARD_CURVE_TENORS_MONTHS: tuple[int, ...] = (1, 3, 6, 12, 24, 36, 60, 84, 120)
