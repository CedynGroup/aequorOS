"""The loan exposure taxonomy BI reuses from fact derivation (P0-8).

Every mapping is written out by hand from the literals ``fact_derivation``
carried before the lift — never echoed from the module — so a changed row is a
changed regulatory category or family, and fails here before it reaches a fact.
"""

from __future__ import annotations

import pytest

from app.domain.positions.families import (
    LOAN_CATEGORY_MAP,
    LOAN_FAMILY,
    PAST_DUE_CATEGORY,
    RETAIL_LOAN_CATEGORIES,
    UNCLASSIFIED_FAMILY,
    UNCLASSIFIED_PREFIX,
    loan_family,
    unclassified_category,
)
from app.services import fact_derivation

EXPECTED_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "CORPORATE_UNRATED": ("corporate_unrated", "RW100"),
    "CORPORATE_LOAN_UNRATED_100RW": ("corporate_unrated", "RW100"),
    "AGRICULTURE": ("corporate_unrated", "RW100"),
    "SME_UNRATED": ("sme_retail", "RW100"),
    "SME_RETAIL": ("sme_retail", "RW100"),
    "RETAIL_UNSECURED": ("retail_other", "RW75"),
    "RETAIL_OTHER": ("retail_other", "RW75"),
    "RESIDENTIAL_MORTGAGE": ("residential_mortgage", "RW35"),
    "COMMERCIAL_REAL_ESTATE": ("commercial_real_estate", "RW100"),
}

EXPECTED_FAMILY: dict[str, str] = {
    "corporate_unrated": "corporate_loans",
    "sme_retail": "sme_loans",
    "retail_other": "retail_loans",
    "residential_mortgage": "mortgages",
    "commercial_real_estate": "cre_loans",
    "past_due_90": "corporate_loans",
}


def test_the_category_map_is_exactly_the_nine_regulatory_classes() -> None:
    assert LOAN_CATEGORY_MAP == EXPECTED_CATEGORY_MAP
    assert PAST_DUE_CATEGORY == ("past_due_90", "RW150")
    assert RETAIL_LOAN_CATEGORIES == ("retail_other", "residential_mortgage")


def test_every_exposure_category_has_a_family_and_nothing_else_does() -> None:
    assert LOAN_FAMILY == EXPECTED_FAMILY
    categories = {category for category, _ in LOAN_CATEGORY_MAP.values()} | {PAST_DUE_CATEGORY[0]}
    assert set(LOAN_FAMILY) == categories
    assert set(RETAIL_LOAN_CATEGORIES) <= categories


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("corporate_unrated", "corporate_loans"),
        ("sme_retail", "sme_loans"),
        ("retail_other", "retail_loans"),
        ("residential_mortgage", "mortgages"),
        ("commercial_real_estate", "cre_loans"),
        ("past_due_90", "corporate_loans"),
        ("unclassified_micro_finance", "unclassified_loans"),
        ("", "unclassified_loans"),
    ],
)
def test_loan_family(category: str, expected: str) -> None:
    assert loan_family(category) == expected
    assert UNCLASSIFIED_FAMILY == "unclassified_loans"


@pytest.mark.parametrize(
    ("regulatory_category", "expected"),
    [
        (None, "unclassified_unmapped"),
        ("", "unclassified_unmapped"),
        ("   ", "unclassified_unmapped"),
        ("!!!", "unclassified_unmapped"),
        ("MICRO_FINANCE", "unclassified_micro_finance"),
        ("  Micro-Finance / Group ", "unclassified_micro_finance_group"),
        ("Trade Finance", "unclassified_trade_finance"),
    ],
)
def test_unclassified_category_slugs_the_banks_own_token(
    regulatory_category: str | None, expected: str
) -> None:
    assert unclassified_category(regulatory_category) == expected
    assert expected.startswith(UNCLASSIFIED_PREFIX)


def test_an_unclassified_category_never_collides_with_a_recognised_one() -> None:
    recognised = {category for category, _ in LOAN_CATEGORY_MAP.values()} | {PAST_DUE_CATEGORY[0]}
    for regulatory_category in LOAN_CATEGORY_MAP:
        assert unclassified_category(regulatory_category) not in recognised


def test_fact_derivation_reads_the_domain_definition() -> None:
    """The names it still uses are the domain objects; the rest moved outright."""
    assert fact_derivation._LOAN_CATEGORY_MAP is LOAN_CATEGORY_MAP
    assert fact_derivation._PAST_DUE_CATEGORY is PAST_DUE_CATEGORY
    assert fact_derivation._RETAIL_LOAN_CATEGORIES is RETAIL_LOAN_CATEGORIES
    assert fact_derivation._loan_family is loan_family
    assert fact_derivation._unclassified_category is unclassified_category
    for moved in ("_LOAN_FAMILY", "_UNCLASSIFIED_FAMILY", "_UNCLASSIFIED_PREFIX", "_NON_SLUG_RE"):
        assert not hasattr(fact_derivation, moved), moved
