"""IRIS mode: SourceAdapter contract (offline) + T24 end-to-end contract.

This module's other-suite cases live in ``test_iris.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.data_engine.adapters.temenos_t24.contract import TemenosContractSuite

from .conftest import FIXTURES_DIR, MODE


class TestIrisEndToEnd(TemenosContractSuite):
    """The T24 stage -> ingest -> persist journey, IRIS mode."""

    @pytest.fixture
    def mode(self) -> str:
        return MODE

    @pytest.fixture
    def fixtures_dir(self) -> Path:
        return FIXTURES_DIR

    @pytest.fixture
    def enabled_domains(self) -> list[str] | None:
        return None
