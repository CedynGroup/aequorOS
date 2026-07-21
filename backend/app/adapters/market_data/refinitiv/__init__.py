"""LSEG (formerly Refinitiv) market data adapter (market_data_adapter.md §7).

Brand note (2026): Refinitiv is a retired brand — the Eikon desktop was withdrawn
2025-06-30 and replaced by **LSEG Workspace**; the platform REST APIs this adapter
targets ("Refinitiv Data Platform"/RDP) now ship as the **LSEG Data Platform**
(client libraries: LSEG Data Library). The internal vendor name ``"refinitiv"``
is kept for wire/DB stability.

LSEG Data Platform (RDP) integration: OAuth 2.0 client-credentials auth
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
    CachingTokenProvider,
    RdpTokenProvider,
    SimulatedTokenProvider,
    TokenProvider,
)
from app.adapters.market_data.refinitiv.resilience import (
    ConnectionPoolConfig,
    RetryPolicy,
    TokenBucketRateLimiter,
    retry_with_backoff,
)
from app.adapters.market_data.refinitiv.transport import (
    VENDOR_LABEL,
    VENDOR_NAME,
    FixtureTransport,
    LiveRdpTransport,
    RdpTransport,
    UnconfiguredTransport,
)

__all__ = [
    "ADAPTER_VERSION",
    "VENDOR_LABEL",
    "VENDOR_NAME",
    "CachingTokenProvider",
    "ConnectionPoolConfig",
    "FixtureTransport",
    "LiveRdpTransport",
    "RdpTokenProvider",
    "RdpTransport",
    "RefinitivAdapter",
    "RetryPolicy",
    "SimulatedTokenProvider",
    "TokenBucketRateLimiter",
    "TokenProvider",
    "UnconfiguredTransport",
    "retry_with_backoff",
]
