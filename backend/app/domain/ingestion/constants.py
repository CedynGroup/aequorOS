"""Data Engine domain value sets.

Canonical enum values are UPPER_SNAKE because they are translation *targets*:
adapters map source-system codes (for example T24 ``"F"``/``"V"``) onto these
values via per-institution enum mappings. Lifecycle statuses are lower_snake to
match the rest of the service.
"""

from __future__ import annotations

from typing import Literal

SOURCE_SYSTEMS: tuple[str, ...] = (
    "EXCEL_CSV",
    "T24",
    "FINACLE",
    "FLEXCUBE",
    "DB_DIRECT",
    "SFTP_DROP",
    "API_GENERIC",
    "MANUAL",
)
SourceSystem = Literal[
    "EXCEL_CSV",
    "T24",
    "FINACLE",
    "FLEXCUBE",
    "DB_DIRECT",
    "SFTP_DROP",
    "API_GENERIC",
    "MANUAL",
]

POSITION_TYPES: tuple[str, ...] = (
    "LOAN",
    "DEPOSIT",
    "SECURITY_HOLDING",
    "DERIVATIVE",
    "FX_HEDGE",
    "INTEREST_RATE_SWAP",
    "CASH",
    "INTERBANK_PLACEMENT",
    "INTERBANK_BORROWING",
    "LC_GUARANTEE",
    "COMMITMENT_UNDRAWN",
    "OTHER_ASSET",
    "OTHER_LIABILITY",
)
PositionType = Literal[
    "LOAN",
    "DEPOSIT",
    "SECURITY_HOLDING",
    "DERIVATIVE",
    "FX_HEDGE",
    "INTEREST_RATE_SWAP",
    "CASH",
    "INTERBANK_PLACEMENT",
    "INTERBANK_BORROWING",
    "LC_GUARANTEE",
    "COMMITMENT_UNDRAWN",
    "OTHER_ASSET",
    "OTHER_LIABILITY",
]

COUNTERPARTY_TYPES: tuple[str, ...] = (
    "RETAIL_INDIVIDUAL",
    "SME",
    "CORPORATE",
    "BANK_OECD",
    "BANK_NON_OECD",
    "CENTRAL_BANK",
    "SOVEREIGN",
    "GOVERNMENT_ENTITY",
    "MULTILATERAL_DEV_BANK",
    "NBFI",
    "OTHER",
)
CounterpartyType = Literal[
    "RETAIL_INDIVIDUAL",
    "SME",
    "CORPORATE",
    "BANK_OECD",
    "BANK_NON_OECD",
    "CENTRAL_BANK",
    "SOVEREIGN",
    "GOVERNMENT_ENTITY",
    "MULTILATERAL_DEV_BANK",
    "NBFI",
    "OTHER",
]

RATE_TYPES: tuple[str, ...] = ("FIXED", "FLOATING")
RateType = Literal["FIXED", "FLOATING"]

GL_ACCOUNT_CLASSES: tuple[str, ...] = (
    "ASSET",
    "LIABILITY",
    "EQUITY",
    "INCOME",
    "EXPENSE",
    "OFF_BALANCE",
)
GlAccountClass = Literal["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE", "OFF_BALANCE"]

VALIDATION_STATUSES: tuple[str, ...] = ("pending", "accepted", "warning", "error", "blocked")
ValidationStatus = Literal["pending", "accepted", "warning", "error", "blocked"]

BATCH_STATUSES: tuple[str, ...] = (
    "created",
    "extracting",
    "translating",
    "validating",
    "accepted",
    "accepted_with_warnings",
    "rejected",
    "failed",
)
BatchStatus = Literal[
    "created",
    "extracting",
    "translating",
    "validating",
    "accepted",
    "accepted_with_warnings",
    "rejected",
    "failed",
]
BATCH_TERMINAL_STATUSES: tuple[str, ...] = (
    "accepted",
    "accepted_with_warnings",
    "rejected",
    "failed",
)
BATCH_ACCEPTED_STATUSES: tuple[str, ...] = ("accepted", "accepted_with_warnings")

EXTRACTION_MODES: tuple[str, ...] = ("full", "incremental")
ExtractionMode = Literal["full", "incremental"]

# Reference datasets the modules consume as-is (curves, assumptions, history).
# Unlike the entity types, these have no per-field canonical schema: rows are
# preserved as payload dicts under a dataset kind and interpreted downstream.
REFERENCE_DATASET_KINDS: tuple[str, ...] = (
    "capital_structure",
    "behavioral_assumptions",
    "yield_curve",
    "fx_rates_current",
    "fx_rates_historical",
    "historical_cashflows",
    "historical_financials",
    "business_units",
    "institution",
)
ReferenceDatasetKind = Literal[
    "capital_structure",
    "behavioral_assumptions",
    "yield_curve",
    "fx_rates_current",
    "fx_rates_historical",
    "historical_cashflows",
    "historical_financials",
    "business_units",
    "institution",
]

LINEAGE_OPERATION_TYPES: tuple[str, ...] = (
    "ADAPTER_EXTRACT",
    "ADAPTER_TRANSLATE",
    "VALIDATION",
    "ENRICHMENT",
    "ML_ENRICHMENT",
    "HUMAN_OVERRIDE",
    "MANUAL_ENTRY",
    "SUPERSESSION",
)
LineageOperationType = Literal[
    "ADAPTER_EXTRACT",
    "ADAPTER_TRANSLATE",
    "VALIDATION",
    "ENRICHMENT",
    "ML_ENRICHMENT",
    "HUMAN_OVERRIDE",
    "MANUAL_ENTRY",
    "SUPERSESSION",
]

MAPPING_CONFIG_STATUSES: tuple[str, ...] = ("draft", "active", "retired")
MappingConfigStatus = Literal["draft", "active", "retired"]
