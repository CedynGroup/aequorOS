"""Tests for test_identity_corrections."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.data_engine.adapters.temenos_t24.adapter import TemenosT24Adapter
from app.data_engine.adapters.temenos_t24.catalog import load_mode_catalog
from app.data_engine.adapters.temenos_t24.domains import CoreBankingDomain
from app.data_engine.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.data_engine.domain.ingestion.contracts import AdapterConfig
from app.data_engine.domain.ingestion.validation import (
    SETTLED_IDENTITY_RULE,
    PositionIdentity,
    ValidationContext,
    default_validation_config,
    run_validation,
)

REFERENCE = "T24-CORRECTION-1"
HELD_DATE = date(2042, 1, 1)


def stage_bundle(path: Path, mode: str, domain: CoreBankingDomain, values: dict[str, Any]) -> Path:
    entry = load_mode_catalog(mode).entries[domain]
    native_fields = {
        native: values[canonical]
        for native, canonical in entry.field_map.items()
        if canonical in values
    }
    path.write_text(
        json.dumps(
            {
                "mode": mode,
                "domains": [
                    {"domain": domain.name, "records": [{"id": REFERENCE, **native_fields}]}
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("mode", ["IRIS", "OPEN_API"])
@pytest.mark.parametrize(
    "domain",
    [
        CoreBankingDomain.POSITIONS_LOANS,
        CoreBankingDomain.POSITIONS_DEPOSITS,
        CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS,
        CoreBankingDomain.POSITIONS_MM_PLACEMENTS,
        CoreBankingDomain.POSITIONS_MM_BORROWINGS,
        CoreBankingDomain.POSITIONS_SWAPS,
    ],
)
@pytest.mark.parametrize(
    ("date_input", "refused"),
    [
        ({}, False),
        ({"origination_date": None}, True),
        ({"origination_date": ""}, True),
        ({"origination_date": "20420101"}, False),
        ({"origination_date": "20240110"}, True),
    ],
)
def test_t24_bundle_preserves_origination_date_presence_through_validation(
    tmp_path: Path,
    mode: str,
    domain: CoreBankingDomain,
    date_input: dict[str, Any],
    refused: bool,
) -> None:
    path = stage_bundle(
        tmp_path / "t24.json", mode, domain, {"currency": "GHS", "balance": "1250", **date_input}
    )
    adapter = TemenosT24Adapter()
    extraction = adapter.extract(AdapterConfig(location=str(path)), date(2026, 7, 31), ["position"])
    assert extraction.warnings == []
    records = adapter.translate(extraction, default_t24_mapping_config(mode))
    assert records.failures == []
    (position,) = records.positions
    assert ("origination_date" in position.model_fields_set) == bool(date_input)
    outcome = run_validation(
        records,
        default_validation_config(),
        ValidationContext(
            as_of_date=extraction.as_of_date,
            settled_positions={
                REFERENCE: PositionIdentity(position.position_type, "GHS", HELD_DATE)
            },
        ),
    )
    findings = [finding for finding in outcome.findings if finding.rule == SETTLED_IDENTITY_RULE]
    assert len(findings) == int(refused)
    if refused:
        assert findings[0].detail.startswith("origination_date is frozen")
        assert outcome.record_statuses[("position", REFERENCE)] == "error"
