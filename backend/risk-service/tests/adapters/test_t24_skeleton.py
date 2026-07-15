from __future__ import annotations

from datetime import date

import pytest

from app.adapters.temenos_t24 import TemenosT24Adapter
from app.adapters.temenos_t24.adapter import KNOWN_TABLES
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.contracts import AdapterConfig


class TestT24Skeleton:
    def test_registered_and_identifiable(self) -> None:
        assert get_adapter_class("T24") is TemenosT24Adapter
        identity = TemenosT24Adapter().identify()
        assert identity.source_system == "T24"
        assert "skeleton" in identity.version

    def test_connection_reports_pending_portal_access_without_raising(self) -> None:
        status = TemenosT24Adapter().validate_connection(AdapterConfig(location="tafj://bank"))
        assert not status.ok
        assert "EXCEL_CSV" in status.detail  # points operators at the working path

    def test_extraction_paths_are_explicit_todos(self) -> None:
        adapter = TemenosT24Adapter()
        config = AdapterConfig(location="tafj://bank")
        with pytest.raises(NotImplementedError):
            adapter.extract(config, date(2026, 6, 30), ["position"])
        with pytest.raises(NotImplementedError):
            adapter.discover_schema(config)

    def test_known_tables_come_from_product_docs_only(self) -> None:
        assert "AA.ARRANGEMENT" in KNOWN_TABLES
        assert "CUSTOMER" in KNOWN_TABLES
        assert len(KNOWN_TABLES) == 21
