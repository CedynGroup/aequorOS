"""Core-banking data-domain taxonomy for the Temenos T24 adapter.

Analogous to the market-data ``DataScope`` taxonomy: a flat, vendor-agnostic
vocabulary of the business data domains AequorOS needs from a core banking
system for ALM/Treasury. A T24 catalog (per connection mode) maps each domain
to concrete T24 applications/enquiries/endpoints; the domain names never carry
Temenos field vocabulary.

Adding a domain is a coordinated change: enum here, catalog entries per mode,
contract tests, and the connection UI. A domain maps to exactly one canonical
entity type (or ``"reference"``) so extraction dispatch and the default mapping
config can be derived from the taxonomy.
"""

from __future__ import annotations

from enum import Enum

from app.domain.ingestion.contracts import EntityType


class CoreBankingDomain(Enum):
    """The business data domains the T24 adapter can extract."""

    GL_BALANCES = "GL_BALANCES"
    POSITIONS_LOANS = "POSITIONS_LOANS"
    POSITIONS_DEPOSITS = "POSITIONS_DEPOSITS"
    POSITIONS_CURRENT_ACCOUNTS = "POSITIONS_CURRENT_ACCOUNTS"
    POSITIONS_MM_PLACEMENTS = "POSITIONS_MM_PLACEMENTS"
    POSITIONS_MM_BORROWINGS = "POSITIONS_MM_BORROWINGS"
    POSITIONS_FX_DEALS = "POSITIONS_FX_DEALS"
    POSITIONS_SWAPS = "POSITIONS_SWAPS"
    SECURITIES_HOLDINGS = "SECURITIES_HOLDINGS"
    OFF_BALANCE_LC = "OFF_BALANCE_LC"
    OFF_BALANCE_GUARANTEES = "OFF_BALANCE_GUARANTEES"
    OFF_BALANCE_COMMITMENTS = "OFF_BALANCE_COMMITMENTS"
    COUNTERPARTY_MASTER = "COUNTERPARTY_MASTER"
    PRODUCT_MASTER = "PRODUCT_MASTER"
    LIMITS = "LIMITS"
    CASHFLOWS_SCHEDULED = "CASHFLOWS_SCHEDULED"
    HISTORICAL_BALANCES = "HISTORICAL_BALANCES"
    BUSINESS_UNITS = "BUSINESS_UNITS"
    INSTITUTION = "INSTITUTION"


class DomainCategory(Enum):
    """Coarse families used for scheduling defaults and dispatch."""

    GL = "GL"
    POSITIONS = "POSITIONS"
    SECURITIES = "SECURITIES"
    OFF_BALANCE = "OFF_BALANCE"
    MASTER_DATA = "MASTER_DATA"
    LIMITS = "LIMITS"
    CASHFLOWS = "CASHFLOWS"
    HISTORICAL = "HISTORICAL"
    REFERENCE = "REFERENCE"


class PullCadence(Enum):
    ON_DEMAND = "ON_DEMAND"
    END_OF_DAY = "END_OF_DAY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


# Longest-prefix-first so POSITIONS_MM_ / OFF_BALANCE_ resolve before the
# shorter POSITIONS_ / OFF_BALANCE bucket. Matched by name prefix.
_CATEGORY_PREFIXES: tuple[tuple[str, DomainCategory], ...] = (
    ("GL_", DomainCategory.GL),
    ("POSITIONS_MM_", DomainCategory.POSITIONS),
    ("POSITIONS_", DomainCategory.POSITIONS),
    ("SECURITIES_", DomainCategory.SECURITIES),
    ("OFF_BALANCE_", DomainCategory.OFF_BALANCE),
    ("COUNTERPARTY_", DomainCategory.MASTER_DATA),
    ("PRODUCT_", DomainCategory.MASTER_DATA),
    ("LIMITS", DomainCategory.LIMITS),
    ("CASHFLOWS_", DomainCategory.CASHFLOWS),
    ("HISTORICAL_", DomainCategory.HISTORICAL),
    ("BUSINESS_UNITS", DomainCategory.REFERENCE),
    ("INSTITUTION", DomainCategory.REFERENCE),
)


def category_of(domain: CoreBankingDomain) -> DomainCategory:
    """The category a domain belongs to (longest-prefix match)."""
    for prefix, category in sorted(_CATEGORY_PREFIXES, key=lambda t: -len(t[0])):
        if domain.name.startswith(prefix):
            return category
    raise ValueError(f"No category prefix matches domain {domain.name!r}.")


DEFAULT_CADENCE_BY_CATEGORY: dict[DomainCategory, PullCadence] = {
    DomainCategory.GL: PullCadence.END_OF_DAY,
    DomainCategory.POSITIONS: PullCadence.END_OF_DAY,
    DomainCategory.SECURITIES: PullCadence.END_OF_DAY,
    DomainCategory.OFF_BALANCE: PullCadence.END_OF_DAY,
    DomainCategory.MASTER_DATA: PullCadence.WEEKLY,
    DomainCategory.LIMITS: PullCadence.END_OF_DAY,
    DomainCategory.CASHFLOWS: PullCadence.END_OF_DAY,
    DomainCategory.HISTORICAL: PullCadence.MONTHLY,
    DomainCategory.REFERENCE: PullCadence.WEEKLY,
}

# Each domain maps to exactly one canonical entity type, or "reference" for
# domains preserved as reference-dataset rows.
DOMAIN_TO_ENTITY_TYPE: dict[CoreBankingDomain, EntityType | str] = {
    CoreBankingDomain.GL_BALANCES: "gl_account",
    CoreBankingDomain.POSITIONS_LOANS: "position",
    CoreBankingDomain.POSITIONS_DEPOSITS: "position",
    CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS: "position",
    CoreBankingDomain.POSITIONS_MM_PLACEMENTS: "position",
    CoreBankingDomain.POSITIONS_MM_BORROWINGS: "position",
    CoreBankingDomain.POSITIONS_FX_DEALS: "position",
    CoreBankingDomain.POSITIONS_SWAPS: "position",
    CoreBankingDomain.SECURITIES_HOLDINGS: "position",
    CoreBankingDomain.OFF_BALANCE_LC: "position",
    CoreBankingDomain.OFF_BALANCE_GUARANTEES: "position",
    CoreBankingDomain.OFF_BALANCE_COMMITMENTS: "position",
    CoreBankingDomain.COUNTERPARTY_MASTER: "counterparty",
    CoreBankingDomain.PRODUCT_MASTER: "product",
    CoreBankingDomain.LIMITS: "reference",
    CoreBankingDomain.CASHFLOWS_SCHEDULED: "reference",
    CoreBankingDomain.HISTORICAL_BALANCES: "reference",
    CoreBankingDomain.BUSINESS_UNITS: "reference",
    CoreBankingDomain.INSTITUTION: "reference",
}


def domains_for_entity(entity_type: str) -> list[CoreBankingDomain]:
    """All domains that produce a given canonical entity type."""
    return [d for d, et in DOMAIN_TO_ENTITY_TYPE.items() if et == entity_type]
