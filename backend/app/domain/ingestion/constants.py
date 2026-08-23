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
    "API_PUSH",
    "BLOOMBERG",
    "REFINITIV",
    "MANUAL_UPLOAD",
    "MANUAL",
    "AEQUOR_DESK",
)
SourceSystem = Literal[
    "EXCEL_CSV",
    "T24",
    "FINACLE",
    "FLEXCUBE",
    "DB_DIRECT",
    "SFTP_DROP",
    "API_GENERIC",
    "API_PUSH",
    "BLOOMBERG",
    "REFINITIV",
    "MANUAL_UPLOAD",
    "MANUAL",
    "AEQUOR_DESK",
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

# Deposit account taxonomy for the BoG liquidity directives (LMTD 2026 ¶5):
# "Volatile Liabilities" = all demand deposits (CURRENT and CALL accounts),
# and current/call/savings accounts are deemed by their nature to mature
# within one year regardless of any stated maturity. The classification is
# carried on the position snapshot so both rules derive from data, not from
# product-code heuristics.
DEPOSIT_ACCOUNT_TYPES: tuple[str, ...] = ("CURRENT", "CALL", "SAVINGS", "FIXED", "OTHER")
DepositAccountType = Literal["CURRENT", "CALL", "SAVINGS", "FIXED", "OTHER"]

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

#: The canonical rows a CALCULATION may read: what the validator accepted, plus
#: what it accepted with warnings. Everything else — ``pending`` (a record the
#: validator never enumerated, P0-11), ``error``, ``blocked`` — must not reach a
#: regulatory number.
#:
#: One spelling of the scope ``ingestion.status_of`` promises holds "in every
#: engine and every filed return". It was previously copied into eighteen
#: module-private constants under fifteen different names, and the filed-return
#: layer simply omitted it: on 2026-08-22 only 2 of 14 ``bog_forms/sources_ext``
#: modules filtered on it, and the shared ``positions.sum`` resolver behind BSD2
#: and BSD5A did not, so the capital-adequacy return read rows the capital
#: engine excludes. New readers must use THIS constant.
INCLUDED_VALIDATION_STATUSES: tuple[str, ...] = ("accepted", "warning")

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

# ``IngestionBatch.etl_report["dedup_status"]`` — the ML-ETL dedup pass's own
# lifecycle, written by ``app.services.ingestion`` (inline) and
# ``app.services.etl_dedup_jobs`` (out of band) and READ by the operator backlog
# board. It lives here, with the other batch lifecycle vocabularies, because
# three modules in two layers share it and the operator control plane must be
# able to name a stuck pass without importing the ETL stack.
DEDUP_STATUS_DEFERRED = "deferred"
DEDUP_STATUS_COMPLETED = "completed"
DEDUP_STATUS_FAILED = "failed"
#: A pass in one of these states has NOT produced its linkage/anomaly metadata:
#: ``deferred`` never ran, ``failed`` ran and could not finish. Neither blocks a
#: filing (see ``etl_dedup_jobs.DEDUP_STATUS_FAILED``) — they are backlog, and
#: whether the backlog is recoverable depends on the job behind it.
STUCK_DEDUP_STATUSES: tuple[str, ...] = (DEDUP_STATUS_DEFERRED, DEDUP_STATUS_FAILED)

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
    # --- BoG return data-gap datasets (2026-08-16; migration 202608160014) ---
    # Each closes lines the official BSD returns need that no canonical entity
    # carried: uploaded/pushed like every other reference dataset (no fixed
    # schema; rows preserved verbatim under the kind), documented per kind in
    # docs/data_engine/datasets/<kind>.md and consumed by bog_forms resolvers.
    "gl_mapping_bsd7",  # bank CoA → BSD7A/7B P&L item mapping (BSD7A/7B/11)
    "subsidiaries",  # subsidiary register (BSD3B, 5B, 7B, 9)
    "tariff_schedule",  # charges/tariff register keyed by official BSD15 row (BSD15A/B, 14)
    "capital_expenditure",  # capex movements by asset class (BSD10, BSD2 rows 115–123)
    "atm_operations",  # monthly ATM/card operations (BSD16)
    "remittance_flows",  # foreign inward remittances (BSD17)
    "teller_withdrawals",  # over-the-counter cash withdrawals (BSD1A)
    "interest_accruals",  # accrued-interest sub-ledger (BSD2/BSD6 accrual rows)
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
    "gl_mapping_bsd7",
    "subsidiaries",
    "tariff_schedule",
    "capital_expenditure",
    "atm_operations",
    "remittance_flows",
    "teller_withdrawals",
    "interest_accruals",
]

LINEAGE_OPERATION_TYPES: tuple[str, ...] = (
    "ADAPTER_EXTRACT",
    "ML_ETL_PREPROCESS",
    "ML_ETL_DEDUP",
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
    "ML_ETL_PREPROCESS",
    "ML_ETL_DEDUP",
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

# Market data (market_data_adapter.md sections 10 and 13): canonical entity
# value sets plus the vendor/credential-lifecycle vocabulary the operational
# connection tables enforce.
# ``aequor_desk`` is the AequorOS market research desk publishing as an
# internal vendor (AequorOS_Market_Data_and_Curve_Platform.md §2): a valid
# vendor for canonical provenance and the connection-table CHECK, but NOT
# bank-onboardable — the bank-facing connection API's vendor Literal
# (app/schemas/market_data_connections.py) deliberately excludes it, because
# desk data is pushed centrally at publication; there is nothing for a bank
# to configure and no credential to hold.
MARKET_DATA_VENDORS: tuple[str, ...] = ("bloomberg", "refinitiv", "manual_upload", "aequor_desk")
MarketDataVendor = Literal["bloomberg", "refinitiv", "manual_upload", "aequor_desk"]

MARKET_DATA_CONNECTION_STATUSES: tuple[str, ...] = (
    "TESTING",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "REVOKED",
    "INVALID",
    "REPLACED_PENDING_DELETION",
    "DISABLED",
)
MarketDataConnectionStatus = Literal[
    "TESTING",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "REVOKED",
    "INVALID",
    "REPLACED_PENDING_DELETION",
    "DISABLED",
]

# 'zero' / 'forward' / 'discount' carry the desk-constructed curve family
# (AEQ.GHS.SOV.ZERO / AEQ.GHS.SOV.FWD / AEQ.GHS.OIS — spec §8): a bootstrapped
# zero curve, its derived forward curve, and the synthetic OIS discounting
# proxy are typed distinctly from vendor-sourced observed curves.
YIELD_CURVE_TYPES: tuple[str, ...] = (
    "sovereign",
    "interbank",
    "swap",
    "credit_spread",
    "zero",
    "forward",
    "discount",
)
YieldCurveType = Literal[
    "sovereign",
    "interbank",
    "swap",
    "credit_spread",
    "zero",
    "forward",
    "discount",
]

FX_RATE_TYPES: tuple[str, ...] = ("spot", "forward")
FxRateType = Literal["spot", "forward"]

MARKET_INDEX_SCENARIOS: tuple[str, ...] = ("base", "adverse", "severely_adverse")
MarketIndexScenario = Literal["base", "adverse", "severely_adverse"]

RATING_AGENCIES: tuple[str, ...] = ("moodys", "sp", "fitch", "internal")
RatingAgency = Literal["moodys", "sp", "fitch", "internal"]

RATING_WATCH_STATUSES: tuple[str, ...] = ("positive", "negative", "stable", "developing")
RatingWatchStatus = Literal["positive", "negative", "stable", "developing"]

# Temenos T24 core-banking adapter (docs/temenos_adapter.md): the connection
# modes (transport channels), the core systems a connection can target, and the
# credential-lifecycle statuses the operational connection table enforces.
# These mirror the market-data connection lifecycle vocabulary.
TEMENOS_CONNECTION_MODES: tuple[str, ...] = ("OFS", "IRIS", "OPEN_API")
TemenosConnectionMode = Literal["OFS", "IRIS", "OPEN_API"]

TEMENOS_CORE_SYSTEMS: tuple[str, ...] = ("T24", "FINACLE", "FLEXCUBE")
TemenosCoreSystem = Literal["T24", "FINACLE", "FLEXCUBE"]

TEMENOS_CONNECTION_STATUSES: tuple[str, ...] = (
    "TESTING",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "REVOKED",
    "INVALID",
    "REPLACED_PENDING_DELETION",
    "DISABLED",
)
TemenosConnectionStatus = Literal[
    "TESTING",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "REVOKED",
    "INVALID",
    "REPLACED_PENDING_DELETION",
    "DISABLED",
]
