"""Refinitiv (LSEG) market data adapter (market_data_adapter.md §7).

Refinitiv Data Platform (RDP) integration: OAuth 2.0 client-credentials auth
(§7.1), the scope-to-RIC catalog (§7.2), extractors and translators (§7.3),
and fixture-based testing without live RDP access (§7.4). Importing this
package registers :class:`RefinitivAdapter` under the vendor name
``"refinitiv"`` in the market-data adapter registry.
"""

from app.adapters.market_data.refinitiv.adapter import (
    ADAPTER_VERSION,
    RefinitivAdapter,
)
from app.adapters.market_data.refinitiv.auth import (
    SimulatedTokenProvider,
    TokenProvider,
)
from app.adapters.market_data.refinitiv.transport import (
    VENDOR_LABEL,
    VENDOR_NAME,
    FixtureTransport,
    RdpTransport,
    UnconfiguredTransport,
)

__all__ = [
    "ADAPTER_VERSION",
    "VENDOR_LABEL",
    "VENDOR_NAME",
    "FixtureTransport",
    "RdpTransport",
    "RefinitivAdapter",
    "SimulatedTokenProvider",
    "TokenProvider",
    "UnconfiguredTransport",
]
