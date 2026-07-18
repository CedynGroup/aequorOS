"""End-to-end ``run_etl`` orchestration tests and cross-cutting invariants.

Proves the build-brief invariants over the whole pass:
  * cleaned extraction has the same record COUNT as the input (fields transformed, never
    records added/dropped);
  * no regulatory-critical value is silently modified — a malformed critical value is
    FLAGGED (guard fires even under a raw alias like ``balance_ghs``);
  * every operation carries confidence + provenance;
  * deduplication emits :class:`LinkageRecord` s and never drops source records;
  * a Sample-Bank-shaped batch produces a cross-source counterparty linkage and at least one
    FLAG on a malformed critical field.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain.ingestion.contracts import (
    AdapterIdentity,
    ExtractionResult,
    MappingConfig,
    RawRecord,
)
from app.etl import run_etl
from app.etl.contracts import (
    REGULATORY_CRITICAL_FIELDS,
    Disposition,
    ETLOperationType,
    MatchType,
)
from app.etl.pipeline import EtlConfig, etl_summary
from app.etl.resolve import resolve_concept


def _identity() -> AdapterIdentity:
    return AdapterIdentity(name="excel_csv", version="1", source_system="EXCEL_CSV")


def _extraction(records: list[RawRecord]) -> ExtractionResult:
    return ExtractionResult(
        identity=_identity(),
        as_of_date=date(2026, 4, 30),
        extraction_mode="full",
        records=records,
    )


def _sample_bank_batch() -> ExtractionResult:
    """A small batch resembling Sample Bank Limited counterparties + loan positions.

    Includes the same real-world counterparty spelled two ways across two sources (the
    build-brief ``ACME TRADING LTD`` / ``Acme Trading Limited`` variants, sharing a national
    id) plus loan positions carrying deliberate imperfections (a thousands-separated balance,
    a percent rate, an Excel-serial date, and a missing regulatory-critical balance).
    """
    records = [
        RawRecord(
            entity_type="counterparty",
            source_locator="t24#CP-001",
            source_table="t24_customers",
            data={
                "counterparty_id": "CP-001",
                "counterparty_name": "  ACME TRADING LTD ",
                "national_id": "GHA-000111222",
                "country": "Ghana",
                "counterparty_type": "CORPORATE",
            },
        ),
        RawRecord(
            entity_type="counterparty",
            source_locator="upload#UPL-77",
            source_table="onboarding_upload",
            data={
                "counterparty_id": "UPL-77",
                "counterparty_name": "Acme Trading Limited",
                "national_id": "GHA-000111222",
                "country": "GHA",
                "counterparty_type": "CORPORATE",
            },
        ),
        RawRecord(
            entity_type="counterparty",
            source_locator="los#LOS-5",
            source_table="loan_origination",
            data={"counterparty_id": "LOS-5", "counterparty_name": "Kofi Mensah Ventures"},
        ),
        RawRecord(
            entity_type="position",
            source_locator="loans#LN-100",
            source_table="loans",
            data={
                "position_id": "LN-100",
                "source_reference": "ARR/LN-100",
                "as_of_date": "2026-04-30",
                "balance_ghs": "1,250,000.00",
                "interest_rate_pct": "24.5%",
                "maturity_date": 46142,
                "product_code": "LN-SME-01",
                "ccy": "ghs",
            },
        ),
        RawRecord(
            entity_type="position",
            source_locator="loans#LN-101",
            source_table="loans",
            data={
                "position_id": "LN-101",
                "source_reference": "ARR/LN-101",
                "as_of_date": "2026-04-30",
                "balance_ghs": "N/A",  # deliberate missing regulatory-critical value
                "product_code": "LN-MORT-01",
            },
        ),
    ]
    return _extraction(records)


def _mapping() -> MappingConfig:
    return MappingConfig(
        product_mappings={"LN-SME-01": "SME_TERM_LOAN", "LN-MORT-01": "RETAIL_MORTGAGE"}
    )


# -- required Sample-Bank scenario ---------------------------------------------------


def test_sample_bank_batch_links_counterparty_and_flags_malformed_critical_field() -> None:
    result = run_etl(_sample_bank_batch(), _mapping())

    # (a) A cross-source linkage over the ACME variants sharing a national id.
    cross_source = [lk for lk in result.linkages if lk.match_type is MatchType.CROSS_SOURCE]
    assert cross_source, "the two ACME spellings must link as one counterparty"
    link = cross_source[0]
    assert set(link.linked_source_ids) == {"CP-001", "UPL-77"}
    assert link.canonical_winner_id in link.linked_source_ids

    # (b) At least one FLAG on a malformed regulatory-critical field (balance under an alias).
    critical_flags = [
        f for f in result.flags if resolve_concept(f.field_name) == "balance"
    ]
    assert critical_flags, "the 'N/A' balance must be flagged, not coerced to null"
    assert all(f.after is None for f in critical_flags)


# -- invariants ----------------------------------------------------------------------


def test_cleaned_extraction_preserves_record_count() -> None:
    batch = _sample_bank_batch()
    result = run_etl(batch, _mapping())
    assert len(result.cleaned.records) == len(batch.records)
    # Identity/as-of metadata is carried through untouched.
    assert result.cleaned.identity == batch.identity
    assert result.cleaned.as_of_date == batch.as_of_date


def test_no_regulatory_critical_value_is_silently_modified() -> None:
    result = run_etl(_sample_bank_batch(), _mapping())
    for op in result.operations:
        if op.disposition is Disposition.SANCTIONED:
            # No sanctioned rewrite ever lands on a column literally named as a critical
            # concept: those are only admitted value-preservingly through a raw alias.
            assert op.field_name not in REGULATORY_CRITICAL_FIELDS
    # The malformed critical balance survives untouched in the cleaned record.
    ln101 = next(r for r in result.cleaned.records if r.source_locator == "loans#LN-101")
    assert ln101.data["balance_ghs"] == "N/A"


def test_every_operation_carries_confidence_and_provenance() -> None:
    result = run_etl(_sample_bank_batch(), _mapping())
    assert result.operations
    for op in result.operations:
        assert op.provenance is not None
        assert op.provenance.operation_type in ETLOperationType
        assert op.provenance.operation_ref
        assert op.provenance.confidence is not None
        assert 0.0 <= op.provenance.confidence <= 1.0


def test_dedup_emits_linkages_and_never_drops_source_records() -> None:
    batch = _sample_bank_batch()
    result = run_etl(batch, _mapping())
    source_ids = set()
    for record in batch.records:
        source_ids.add(record.data.get("counterparty_id") or record.data.get("position_id"))
    for link in result.linkages:
        assert link.canonical_winner_id in source_ids
        for sid in link.linked_source_ids:
            assert sid in source_ids  # every linked id remains a real, retrievable source row


def test_sanctioned_transforms_land_in_cleaned_record() -> None:
    result = run_etl(_sample_bank_batch(), _mapping())
    ln100 = next(r for r in result.cleaned.records if r.source_locator == "loans#LN-100")
    assert ln100.data["balance_ghs"] == "1250000"  # thousands stripped (value-preserving)
    assert ln100.data["interest_rate_pct"] == "0.245"  # percent -> fraction
    assert ln100.data["maturity_date"] == "2026-04-30"  # excel serial -> ISO
    assert ln100.data["ccy"] == "GHS"  # ISO 4217
    assert ln100.data["resolved_product_category"] == "SME_TERM_LOAN"  # reference resolution


def test_config_toggles_disable_stages() -> None:
    batch = _sample_bank_batch()
    cfg = EtlConfig(
        normalize=False,
        coerce_types=False,
        resolve_references=False,
        deduplicate=False,
        detect_anomalies=False,
    )
    result = run_etl(batch, _mapping(), config=cfg)
    assert result.operations == []
    assert result.linkages == []
    assert result.flags == []
    # With every stage off the cleaned record is a faithful copy (count + values).
    assert len(result.cleaned.records) == len(batch.records)


def test_dedup_on_by_default() -> None:
    assert EtlConfig().deduplicate is True


# -- summary -------------------------------------------------------------------------


def test_etl_summary_is_compact_and_json_serialisable() -> None:
    result = run_etl(_sample_bank_batch(), _mapping())
    summary = etl_summary(result, sample_limit=3)

    assert summary["record_count"] == 5
    assert summary["sanctioned_count"] >= 1
    assert summary["flagged_count"] >= 1
    assert summary["linkage_count"] >= 1
    assert summary["operations_by_type"]["NORMALIZE"] >= 1
    assert len(summary["sample_operations"]) <= 3
    assert summary["linkages_by_match_type"]["CROSS_SOURCE"] >= 1
    # Round-trips through JSON without a custom encoder.
    assert json.loads(json.dumps(summary)) == summary


def test_run_etl_is_pure_no_input_mutation() -> None:
    batch = _sample_bank_batch()
    before = batch.model_dump()
    run_etl(batch, _mapping())
    assert batch.model_dump() == before  # the input extraction is never mutated in place


_SOLO = RawRecord(
    entity_type="counterparty",
    source_locator="x",
    data={"counterparty_id": "1", "counterparty_name": "Solo"},
)


@pytest.mark.parametrize("records", [[], [_SOLO]])
def test_run_etl_handles_trivial_batches(records: list[RawRecord]) -> None:
    result = run_etl(_extraction(records), _mapping())
    assert len(result.cleaned.records) == len(records)
    assert result.linkages == []  # nothing to link with < 2 comparable records
