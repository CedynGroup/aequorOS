"""Push translation accepts registered rows and reports malformed source evidence."""

from __future__ import annotations

from datetime import date

import pytest

from app.adapters.api_push.adapter import ApiPushAdapter, identity_mapping_config
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.contracts import CanonicalRecords, ExtractionResult, RawRecord
from app.domain.ingestion.reference_schemas import SCHEMAS
from tests.domain.ingestion.test_reference_row_enforcement import VALID_ROWS


def _translate(kind: str, payloads: list[dict[str, str | None]]) -> CanonicalRecords:
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
                source_locator=f"push.json#row{index}",
                data=payload,
            )
            for index, payload in enumerate(payloads, start=1)
        ],
    )
    return adapter.translate(extraction, identity_mapping_config())


@pytest.mark.parametrize("kind", REFERENCE_DATASET_KINDS)
def test_push_translation_keeps_valid_rows_and_reports_invalid_reference_rows(kind: str) -> None:
    result = _translate(kind, [VALID_ROWS[kind], {"source_evidence": "malformed row"}])
    assert result.reference_row_counts == {kind: 1}
    assert result.reference_rows[0].payload == VALID_ROWS[kind]
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.error_code == "invalid_reference_row"
    assert failure.source_locator == "push.json#row2"
    assert failure.raw_record == {"source_evidence": "malformed row"}
    assert f"{kind} row 2" in failure.error_message


@pytest.mark.parametrize("field", ["assumption_type", "assumption"])
@pytest.mark.parametrize(
    "assumption_type", ["DEPOSIT_STABILITY", "NMD_DURATION", "PREPAYMENT_RATE"]
)
def test_push_validates_behavioral_shapes_without_renaming_payloads(
    field: str, assumption_type: str
) -> None:
    valid: dict[str, str | None] = {
        "product_code": "DEP-SAV",
        field: assumption_type,
        "value": "0.82",
        "source_note": "retained",
        "source_null": None,
    }
    invalid = [
        {**valid, "product_code": None},
        {**valid, field: None},
        {**valid, field: ""},
        {**valid, "value": None},
        {**valid, "value": "NaN"},
    ]
    if field == "assumption_type":
        invalid[1]["assumption"] = assumption_type
        invalid[2]["assumption"] = assumption_type
    result = _translate("behavioral_assumptions", [valid, *invalid])
    assert result.reference_row_counts == {"behavioral_assumptions": 1}
    assert result.reference_rows[0].payload == valid
    assert len(result.failures) == len(invalid)
    for index, (failure, payload) in enumerate(zip(result.failures, invalid, strict=True), start=2):
        assert failure.error_code == "invalid_reference_row"
        assert failure.source_locator == f"push.json#row{index}"
        assert failure.raw_record == payload
        assert f"behavioral_assumptions row {index}" in failure.error_message


@pytest.mark.parametrize(
    ("kind", "field"),
    [(kind, field) for kind, schema in SCHEMAS.items() for field in schema.dates],
)
@pytest.mark.parametrize(
    "bad", ["2026-W23-2", "20260630", "2026-6-30", "2026-02-30", "2026-06-30T00:00:00"]
)
def test_push_refuses_noncanonical_dates_in_every_registered_date_field(
    kind: str, field: str, bad: str
) -> None:
    valid = {**VALID_ROWS[kind], field: "2026-06-30"}
    invalid = {**valid, field: bad}
    result = _translate(kind, [valid, invalid])
    assert result.reference_row_counts == {kind: 1}
    assert result.reference_rows[0].payload == valid
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.error_code == "invalid_reference_row"
    assert failure.source_locator == "push.json#row2"
    assert failure.raw_record == invalid
    assert field in failure.error_message
    assert "YYYY-MM-DD" in failure.error_message


@pytest.mark.parametrize("padding", [" ", "\t", "\n"])
def test_push_stringification_retains_only_canonical_dates(padding: str) -> None:
    payload = {**VALID_ROWS["fx_rates_historical"], "date": f"{padding}2026-06-30{padding}"}
    result = _translate("fx_rates_historical", [payload])
    assert not result.failures
    assert result.reference_rows[0].payload["date"] == "2026-06-30"
    assert payload["date"] == f"{padding}2026-06-30{padding}"


@pytest.mark.parametrize(
    ("kind", "field", "payload"),
    [
        (
            "fx_rates_current",
            "quote_date",
            {"pair": "USD/EUR", "rate": "0.9", "quote_date": "2026-06-30"},
        ),
        (
            "fx_rates_historical",
            "quote_date",
            {"pair": "USD/EUR", "rate": "0.9", "quote_date": "2026-06-30"},
        ),
        (
            "historical_cashflows",
            "date",
            {"date": "2026-06-30", "inflow": "100", "outflow": "80"},
        ),
        (
            "historical_financials",
            "month",
            {"month": "2026-06-30", "total_assets": "100", "net_income": "10"},
        ),
    ],
)
def test_push_validates_dates_in_documented_alternatives_without_rewriting(
    kind: str, field: str, payload: dict[str, str | None]
) -> None:
    invalid = {**payload, field: "2026-W23-2"}
    result = _translate(kind, [payload, invalid])
    assert result.reference_row_counts == {kind: 1}
    assert result.reference_rows[0].payload == payload
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.error_code == "invalid_reference_row"
    assert failure.source_locator == "push.json#row2"
    assert failure.raw_record == invalid
    assert field in failure.error_message
    assert "YYYY-MM-DD" in failure.error_message
