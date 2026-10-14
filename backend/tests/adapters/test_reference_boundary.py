"""Push translation accepts registered rows and reports malformed source evidence."""

from __future__ import annotations

from datetime import date

import pytest

from app.adapters.api_push.adapter import ApiPushAdapter, identity_mapping_config
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.contracts import ExtractionResult, RawRecord
from tests.domain.ingestion.test_reference_row_enforcement import VALID_ROWS


@pytest.mark.parametrize("kind", REFERENCE_DATASET_KINDS)
def test_push_translation_keeps_valid_rows_and_reports_invalid_reference_rows(kind: str) -> None:
    adapter = ApiPushAdapter()
    extraction = ExtractionResult(
        identity=adapter.identify(),
        as_of_date=date(2026, 6, 30),
        extraction_mode="full",
        records=[
            RawRecord(
                entity_type="reference",
                dataset_kind=kind,
                source_table=kind,
                source_locator="push.json#row1",
                data=VALID_ROWS[kind],
            ),
            RawRecord(
                entity_type="reference",
                dataset_kind=kind,
                source_table=kind,
                source_locator="push.json#row2",
                data={"source_evidence": "malformed row"},
            ),
        ],
    )
    result = adapter.translate(extraction, identity_mapping_config())
    assert result.reference_row_counts == {kind: 1}
    assert result.reference_rows[0].payload == VALID_ROWS[kind]
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.error_code == "invalid_reference_row"
    assert failure.source_locator == "push.json#row2"
    assert failure.raw_record == {"source_evidence": "malformed row"}
    assert f"{kind} row 2" in failure.error_message
