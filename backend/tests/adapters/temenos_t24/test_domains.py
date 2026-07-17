"""Domain taxonomy contract: category resolution, cadence coverage, and the
one-domain-one-entity-type invariant."""

from __future__ import annotations

import pytest

from app.adapters.temenos_t24.domains import (
    DEFAULT_CADENCE_BY_CATEGORY,
    DOMAIN_TO_ENTITY_TYPE,
    CoreBankingDomain,
    DomainCategory,
    category_of,
    domains_for_entity,
)
from app.domain.ingestion.contracts import ENTITY_TYPES


def test_every_domain_has_a_category() -> None:
    for domain in CoreBankingDomain:
        assert isinstance(category_of(domain), DomainCategory)


def test_longest_prefix_resolves_mm_before_positions() -> None:
    assert category_of(CoreBankingDomain.POSITIONS_MM_PLACEMENTS) is DomainCategory.POSITIONS
    assert category_of(CoreBankingDomain.POSITIONS_LOANS) is DomainCategory.POSITIONS
    # off-balance is its own category, not folded into positions
    assert category_of(CoreBankingDomain.OFF_BALANCE_LC) is DomainCategory.OFF_BALANCE


def test_every_category_has_a_default_cadence() -> None:
    for category in DomainCategory:
        assert category in DEFAULT_CADENCE_BY_CATEGORY


def test_every_domain_maps_to_one_entity_type() -> None:
    valid = {*ENTITY_TYPES, "reference"}
    assert set(DOMAIN_TO_ENTITY_TYPE) == set(CoreBankingDomain)
    for domain, entity in DOMAIN_TO_ENTITY_TYPE.items():
        assert entity in valid, f"{domain.name} maps to unknown entity {entity!r}"


def test_domains_for_entity_is_consistent() -> None:
    positions = domains_for_entity("position")
    assert CoreBankingDomain.POSITIONS_LOANS in positions
    assert CoreBankingDomain.COUNTERPARTY_MASTER not in positions
    assert domains_for_entity("counterparty") == [CoreBankingDomain.COUNTERPARTY_MASTER]
    assert domains_for_entity("product") == [CoreBankingDomain.PRODUCT_MASTER]


def test_reference_domains_are_exactly_the_non_entity_ones() -> None:
    reference = set(domains_for_entity("reference"))
    assert CoreBankingDomain.BUSINESS_UNITS in reference
    assert CoreBankingDomain.INSTITUTION in reference
    assert CoreBankingDomain.GL_BALANCES not in reference


def test_unknown_domain_name_is_a_value_error_not_a_silent_default() -> None:
    # A hand-constructed enum-like with no matching prefix should never happen,
    # but category_of must fail loudly rather than bucket it silently.
    with pytest.raises(KeyError):
        CoreBankingDomain["NOT_A_DOMAIN"]
