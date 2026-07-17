"""Registration + identity contract for the (now implemented) T24 adapter.

The end-to-end extract/translate/ingest behavior is covered by the per-mode
suites under ``tests/adapters/temenos_t24``; this module only pins the registry
wiring and the documented-tables surface.
"""

from __future__ import annotations

from app.adapters.temenos_t24 import TemenosT24Adapter
from app.adapters.temenos_t24.adapter import KNOWN_TABLES
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.contracts import AdapterConfig


class TestT24Registration:
    def test_registered_and_identifiable(self) -> None:
        assert get_adapter_class("T24") is TemenosT24Adapter
        identity = TemenosT24Adapter().identify()
        assert identity.source_system == "T24"
        assert identity.name == "temenos_t24"
        assert "skeleton" not in identity.version

    def test_validate_connection_rejects_missing_bundle_without_raising(self) -> None:
        status = TemenosT24Adapter().validate_connection(
            AdapterConfig(location="/no/such/t24-bundle.json")
        )
        assert not status.ok
        assert status.detail

    def test_known_tables_come_from_product_docs_only(self) -> None:
        assert "AA.ARRANGEMENT" in KNOWN_TABLES
        assert "CUSTOMER" in KNOWN_TABLES
        assert len(KNOWN_TABLES) == 21
