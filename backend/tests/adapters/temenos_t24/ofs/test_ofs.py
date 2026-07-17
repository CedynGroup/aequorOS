"""OFS mode: the SourceAdapter contract (offline) plus the T24 end-to-end
contract (staged pull -> ingestion spine -> canonical rows)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.temenos_t24.adapter import TemenosT24Adapter
from app.domain.ingestion.adapter import SourceAdapter
from tests.adapters.contract import AdapterContractSuite
from tests.adapters.temenos_t24.contract import TemenosContractSuite

from .conftest import FIXTURES_DIR


@pytest.fixture
def adapter() -> SourceAdapter:
    return TemenosT24Adapter()


class TestOfsSourceAdapterContract(AdapterContractSuite):
    """The generic SourceAdapter conformance suite, run against OFS fixtures."""


class TestOfsEndToEnd(TemenosContractSuite):
    """The T24 stage -> ingest -> persist journey, OFS mode."""

    @pytest.fixture
    def mode(self) -> str:
        return "OFS"

    @pytest.fixture
    def fixtures_dir(self) -> Path:
        return FIXTURES_DIR

    @pytest.fixture
    def enabled_domains(self) -> list[str] | None:
        return None  # all supported OFS domains
