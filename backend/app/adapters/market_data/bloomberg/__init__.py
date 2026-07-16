"""Bloomberg market data adapter (market_data_adapter.md §6).

Importing this package registers :class:`BloombergAdapter` under the
``"bloomberg"`` vendor name in the market-data adapter registry. Vendor
vocabulary — security tickers, field mnemonics, response shapes — never
leaves this package; the rest of AequorOS speaks ``DataScope``.
"""

from app.adapters.market_data.bloomberg.adapter import (
    ADAPTER_NAME,
    ADAPTER_VERSION,
    CATALOG_PATH,
    VENDOR,
    BloombergAdapter,
)
from app.adapters.market_data.bloomberg.auth import (
    REQUIRED_CREDENTIAL_FIELDS,
    VENDOR_DISPLAY_NAME,
    BloombergSession,
    BloombergSessionProvider,
    SimulatedSessionProvider,
    ensure_scope_permitted,
)
from app.adapters.market_data.bloomberg.transport import (
    BlpTransport,
    FixtureTransport,
    UnavailableTransport,
)

__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "CATALOG_PATH",
    "REQUIRED_CREDENTIAL_FIELDS",
    "VENDOR",
    "VENDOR_DISPLAY_NAME",
    "BloombergAdapter",
    "BloombergSession",
    "BloombergSessionProvider",
    "BlpTransport",
    "FixtureTransport",
    "SimulatedSessionProvider",
    "UnavailableTransport",
    "ensure_scope_permitted",
]
