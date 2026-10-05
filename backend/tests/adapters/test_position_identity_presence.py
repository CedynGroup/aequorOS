from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from app.adapters.api_push.adapter import ApiPushAdapter
from app.adapters.database_direct.translate import translate as translate_database_direct
from app.adapters.excel_csv.adapter import ExcelCsvAdapter
from app.adapters.temenos_t24.translate import translate as translate_t24
from app.domain.ingestion.contracts import (
    AdapterIdentity,
    CanonicalRecords,
    EntityMapping,
    ExtractionResult,
    MappingConfig,
    RawRecord,
)
from app.domain.ingestion.validation import (
    PositionIdentity,
    ValidationContext,
    default_validation_config,
    run_validation,
)


@pytest.mark.parametrize(
    "translate",
    [ExcelCsvAdapter().translate, ApiPushAdapter().translate, translate_t24, translate_database_direct],
    ids=["excel", "push", "t24", "database_direct"],
)
@pytest.mark.parametrize("source_fields", ["Originated", ["Missing", "Originated"]])
@pytest.mark.parametrize(
    ("mapped", "date_input", "refused"),
    [
        (False, {"Originated": None}, False),
        (True, {}, False),
        (True, {"Originated": None}, True),
        (True, {"Originated": ""}, True),
        (True, {"Originated": "2024-01-10"}, False),
        (True, {"Originated": "2024-02-01"}, True),
    ],
)
def test_translated_identity_distinguishes_absence_from_explicit_date_changes(
    translate: Callable[[ExtractionResult, MappingConfig], CanonicalRecords],
    source_fields: str | list[str],
    mapped: bool,
    date_input: dict[str, Any],
    refused: bool,
) -> None:
    fields: dict[str, str | list[str]] = {
        "source_reference": "Reference",
        "position_type": "Type",
        "currency": "Currency",
        "balance": "Balance",
    }
    if mapped:
        fields["origination_date"] = source_fields
    extraction = ExtractionResult(
        identity=AdapterIdentity(name="test", version="1", source_system="EXCEL_CSV"),
        as_of_date=date(2026, 7, 31),
        extraction_mode="full",
        records=[
            RawRecord(
                entity_type="position",
                source_locator="positions#R1",
                data={
                    "Reference": "LN-0001",
                    "Type": "LOAN",
                    "Currency": "GHS",
                    "Balance": "1250",
                    **date_input,
                },
            )
        ],
    )
    records = translate(
        extraction,
        MappingConfig(
            field_mappings={"position": EntityMapping(source_table="Positions", fields=fields)}
        ),
    )
    assert records.failures == []
    assert len(records.positions) == 1
    outcome = run_validation(
        records,
        default_validation_config(),
        ValidationContext(
            as_of_date=extraction.as_of_date,
            settled_positions={"LN-0001": PositionIdentity("LOAN", "GHS", date(2024, 1, 10))},
        ),
    )
    assert outcome.record_statuses[("position", "LN-0001")] == ("error" if refused else "accepted")
    assert len(outcome.findings) == int(refused)
    if refused:
        assert outcome.findings[0].detail.startswith("origination_date is frozen")
