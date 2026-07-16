from __future__ import annotations

import pytest

from app.adapters.market_data.scope_taxonomy import (
    DEFAULT_FREQUENCY_BY_CATEGORY,
    STANDARD_CURVE_TENORS_MONTHS,
    DataScope,
    PullFrequency,
    ScopeCategory,
    category_of,
)


def test_taxonomy_contains_full_scope_list() -> None:
    names = {scope.name for scope in DataScope}
    expected = {
        "YIELD_CURVE_GHS",
        "YIELD_CURVE_USD",
        "YIELD_CURVE_EUR",
        "YIELD_CURVE_GBP",
        "YIELD_CURVE_NGN",
        "YIELD_CURVE_KES",
        "YIELD_CURVE_ZAR",
        "FX_SPOT_USD_GHS",
        "FX_SPOT_EUR_GHS",
        "FX_SPOT_GBP_GHS",
        "FX_SPOT_USD_NGN",
        "FX_FORWARD_USD_GHS_1M",
        "FX_FORWARD_USD_GHS_3M",
        "FX_FORWARD_USD_GHS_6M",
        "FX_FORWARD_USD_GHS_12M",
        "SECURITY_MASTER_GOG_BONDS",
        "SECURITY_MASTER_GOG_TBILLS",
        "CREDIT_RATING_GHANA_SOVEREIGN",
        "CREDIT_RATING_NIGERIA_SOVEREIGN",
        "MACRO_GHANA_GDP_FORECAST",
        "MACRO_GHANA_INFLATION_FORECAST",
        "MACRO_GHANA_POLICY_RATE_PATH",
    }
    assert names == expected


def test_scope_values_equal_names() -> None:
    for scope in DataScope:
        assert scope.value == scope.name


def test_pull_frequency_members() -> None:
    assert {f.name for f in PullFrequency} == {
        "ON_DEMAND",
        "HOURLY",
        "END_OF_DAY",
        "WEEKLY",
        "MONTHLY",
    }


@pytest.mark.parametrize(
    ("scope", "category"),
    [
        (DataScope.YIELD_CURVE_GHS, ScopeCategory.YIELD_CURVE),
        (DataScope.YIELD_CURVE_ZAR, ScopeCategory.YIELD_CURVE),
        (DataScope.FX_SPOT_USD_GHS, ScopeCategory.FX_SPOT),
        (DataScope.FX_FORWARD_USD_GHS_1M, ScopeCategory.FX_FORWARD),
        (DataScope.FX_FORWARD_USD_GHS_12M, ScopeCategory.FX_FORWARD),
        (DataScope.SECURITY_MASTER_GOG_TBILLS, ScopeCategory.SECURITY_MASTER),
        (DataScope.CREDIT_RATING_GHANA_SOVEREIGN, ScopeCategory.CREDIT_RATING),
        (DataScope.MACRO_GHANA_POLICY_RATE_PATH, ScopeCategory.MACRO_FORECAST),
    ],
)
def test_category_of_representatives(scope: DataScope, category: ScopeCategory) -> None:
    assert category_of(scope) is category


def test_every_scope_has_a_category() -> None:
    for scope in DataScope:
        assert isinstance(category_of(scope), ScopeCategory)


def test_fx_forward_is_not_categorized_as_fx_spot() -> None:
    for scope in DataScope:
        if scope.value.startswith("FX_FORWARD_"):
            assert category_of(scope) is ScopeCategory.FX_FORWARD


def test_default_frequency_covers_every_category() -> None:
    assert set(DEFAULT_FREQUENCY_BY_CATEGORY) == set(ScopeCategory)


def test_default_frequencies_match_spec_defaults() -> None:
    # market_data_adapter.md §9.2 step 6.
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.YIELD_CURVE] is PullFrequency.END_OF_DAY
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.FX_SPOT] is PullFrequency.HOURLY
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.CREDIT_RATING] is PullFrequency.WEEKLY
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.MACRO_FORECAST] is PullFrequency.MONTHLY
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.SECURITY_MASTER] is PullFrequency.WEEKLY
    assert DEFAULT_FREQUENCY_BY_CATEGORY[ScopeCategory.FX_FORWARD] is PullFrequency.END_OF_DAY


def test_standard_curve_tenors() -> None:
    # §5.2: 1M, 3M, 6M, 12M, 24M, 36M, 60M, 84M, 120M.
    assert STANDARD_CURVE_TENORS_MONTHS == (1, 3, 6, 12, 24, 36, 60, 84, 120)
    assert list(STANDARD_CURVE_TENORS_MONTHS) == sorted(STANDARD_CURVE_TENORS_MONTHS)
