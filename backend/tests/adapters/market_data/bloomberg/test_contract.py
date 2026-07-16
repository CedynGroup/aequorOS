"""Runs the shared §4.3 contract suite against :class:`BloombergAdapter`.

All fixtures (adapter, credentials, scopes, hooks) come from this package's
``conftest.py``; the assertions live in the vendor-agnostic
:class:`MarketDataContractSuite`. Any test failing here but passing for
another adapter means the interface is leaking implementation details — fix
the interface, not the test.
"""

from __future__ import annotations

from tests.adapters.market_data.contract import MarketDataContractSuite


class TestBloombergContract(MarketDataContractSuite):
    pass
