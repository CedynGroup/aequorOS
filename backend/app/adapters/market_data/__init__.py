"""Market Data Adapter framework (docs/market_data_adapter.md).

Market data adapters are Layer 1 source adapters specialized for vendor
market data (Bloomberg, Refinitiv/LSEG, manual upload): credential-based
auth, business-scope taxonomy, quota tracking, freshness-bounded caching,
and bank-facing error classification. Vendor concepts never leak past the
adapter boundary — the rest of AequorOS speaks :class:`DataScope`.
"""

from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    MarketDataPullResult,
    QuotaEstimate,
    TestPullResult,
    get_market_data_adapter_class,
    register_market_data_adapter,
    registered_vendors,
)
from app.adapters.market_data.cache import (
    FRESHNESS_BY_CATEGORY,
    StalenessTag,
    cache_location,
    fresh_until,
    is_fresh,
    next_business_day,
    read_cache_entry,
    staleness_tag,
    write_cache_entry,
)
from app.adapters.market_data.credential_manager import (
    EXPIRING_SOON_THRESHOLD,
    CredentialVault,
    CredentialVaultError,
    EncryptedDbVault,
    build_vault_path,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    derive_status,
    encrypt_credential_envelope,
)
from app.adapters.market_data.errors import (
    MESSAGE_TEMPLATES,
    BankFacingError,
    BankFacingErrorCode,
    MarketDataError,
    MessageTemplate,
    render_bank_facing,
)
from app.adapters.market_data.quota_tracker import (
    PULLS_PER_MONTH_BY_FREQUENCY,
    current_month_usage,
    estimate,
    month_key,
    record_consumption,
)
from app.adapters.market_data.scheduler import due_scopes, next_pull_due
from app.adapters.market_data.scope_taxonomy import (
    DEFAULT_FREQUENCY_BY_CATEGORY,
    STANDARD_CURVE_TENORS_MONTHS,
    DataScope,
    PullFrequency,
    ScopeCategory,
    category_of,
)
from app.adapters.market_data.scope_translator import (
    Catalog,
    CatalogEntry,
    CatalogError,
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)

__all__ = [
    "DEFAULT_FREQUENCY_BY_CATEGORY",
    "EXPIRING_SOON_THRESHOLD",
    "FRESHNESS_BY_CATEGORY",
    "MESSAGE_TEMPLATES",
    "PULLS_PER_MONTH_BY_FREQUENCY",
    "STANDARD_CURVE_TENORS_MONTHS",
    "AuthResult",
    "BankFacingError",
    "BankFacingErrorCode",
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "CredentialSet",
    "CredentialVault",
    "CredentialVaultError",
    "DataScope",
    "EncryptedDbVault",
    "MarketDataAdapter",
    "MarketDataError",
    "MarketDataPullResult",
    "MessageTemplate",
    "PullFrequency",
    "QuotaEstimate",
    "ScopeCategory",
    "StalenessTag",
    "TestPullResult",
    "build_vault_path",
    "cache_location",
    "category_of",
    "credential_fingerprint",
    "current_month_usage",
    "decrypt_credential_envelope",
    "derive_master_key",
    "derive_status",
    "due_scopes",
    "encrypt_credential_envelope",
    "estimate",
    "fresh_until",
    "get_market_data_adapter_class",
    "is_fresh",
    "load_catalog",
    "month_key",
    "next_business_day",
    "next_pull_due",
    "quota_units",
    "read_cache_entry",
    "record_consumption",
    "register_market_data_adapter",
    "registered_vendors",
    "render_bank_facing",
    "requests_for",
    "staleness_tag",
    "supported_scopes",
    "write_cache_entry",
]
