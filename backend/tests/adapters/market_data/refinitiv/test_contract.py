"""Contract conformance for the Refinitiv adapter (§4.3).

The shared :class:`MarketDataContractSuite` runs verbatim against the
Refinitiv adapter wired to FixtureTransport + SimulatedTokenProvider; all
fixtures live in ``conftest.py``. A Refinitiv-only failure here means the
adapter leaks implementation details through the interface.
"""

from __future__ import annotations

from tests.adapters.market_data.contract import MarketDataContractSuite


class TestRefinitivContract(MarketDataContractSuite):
    pass
