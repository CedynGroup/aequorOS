"""IRIS mode: SourceAdapter contract (offline) + T24 end-to-end contract.

This module's other-suite cases live in ``test_iris_integration.py``.
"""

from __future__ import annotations

import pytest

from app.data_engine.adapters.temenos_t24.adapter import TemenosT24Adapter
from app.data_engine.domain.ingestion.adapter import SourceAdapter
from tests.data_engine.adapters.contract import AdapterContractSuite


@pytest.fixture
def adapter() -> SourceAdapter:
    return TemenosT24Adapter()


class TestIrisSourceAdapterContract(AdapterContractSuite):
    """The generic SourceAdapter conformance suite, run against IRIS fixtures."""
