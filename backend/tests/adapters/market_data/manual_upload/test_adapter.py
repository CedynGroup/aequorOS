"""Adapter pull behavior against the real persistence spine: canonical rows
with full mandatory metadata, supersession on re-pull, raw-tier artifacts,
pipeline-refresh enqueue, and non-persisting test pulls."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.market_data.manual_upload.adapter import ManualUploadAdapter
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency
from app.models import (
    Bank,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    Job,
)
from app.storage.client import StorageLocation
from tests.adapters.market_data.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_full_coverage_workbook,
    build_yield_curve_workbook,
    count_current_canonical,
    credentials_for,
    produced_batch_records,
    stage_upload,
)
from tests.storage.inmemory import InMemoryStorageClient

PULL_SCOPES = [
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.FX_FORWARD_USD_GHS_3M,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
    DataScope.MACRO_GHANA_GDP_FORECAST,
]


def _staged_credentials(storage: InMemoryStorageClient, slug: str, bank: Bank):
    location = stage_upload(storage, slug, build_full_coverage_workbook(), "full.xlsx")
    return credentials_for(bank, location)


def test_pull_persists_canonical_rows_with_full_mandatory_metadata(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    result = adapter.pull(credentials, PULL_SCOPES, FIXTURE_AS_OF, str(bank.id), "b-1")

    assert result.errors == []
    assert result.quota_consumed == 0  # manual pulls consume no vendor quota (§8.3)
    # GHS curve (1 header + 3 points) + spot + forward + rating + macro index.
    assert result.canonical_records_produced == 8
    records = produced_batch_records(db_session, UUID(result.batch_id))
    assert len(records) == 8
    for record in records:
        assert record.organization_id == bank.organization_id
        assert record.bank_id == bank.id
        assert record.as_of_date == FIXTURE_AS_OF
        assert record.source_system == "MANUAL_UPLOAD"
        assert record.source_reference
        assert record.ingestion_batch_id == UUID(result.batch_id)
        assert record.lineage_id is not None
        assert record.validation_status == "accepted"
        assert record.ingested_at is not None


def test_percent_rates_are_persisted_as_decimal_fractions(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    adapter.pull(credentials, [DataScope.YIELD_CURVE_GHS], FIXTURE_AS_OF, str(bank.id), "b-1")
    rates = set(
        db_session.scalars(
            select(CanonicalYieldCurvePoint.rate).where(CanonicalYieldCurvePoint.bank_id == bank.id)
        )
    )
    assert rates == {Decimal("0.152"), Decimal("0.158"), Decimal("0.164")}


def test_repull_same_file_and_as_of_supersedes_not_duplicates(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    adapter.pull(credentials, PULL_SCOPES, FIXTURE_AS_OF, str(bank.id), "b-1")
    first = {
        scope: count_current_canonical(db_session, bank, scope, FIXTURE_AS_OF)
        for scope in PULL_SCOPES
    }
    assert all(count > 0 for count in first.values())

    adapter.pull(credentials, PULL_SCOPES, FIXTURE_AS_OF, str(bank.id), "b-2")
    for scope in PULL_SCOPES:
        assert count_current_canonical(db_session, bank, scope, FIXTURE_AS_OF) == first[scope]
    # The superseded generation remains as history.
    total_curves = db_session.scalar(
        select(func.count())
        .select_from(CanonicalYieldCurve)
        .where(CanonicalYieldCurve.bank_id == bank.id, CanonicalYieldCurve.currency == "GHS")
    )
    assert total_curves == 2


def test_accepted_pull_enqueues_debounced_pipeline_refresh(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    result = adapter.pull(
        credentials, [DataScope.YIELD_CURVE_GHS], FIXTURE_AS_OF, str(bank.id), "b-1"
    )
    batch = db_session.get(IngestionBatch, UUID(result.batch_id))
    assert batch is not None
    assert batch.status == "accepted"
    jobs = list(
        db_session.scalars(
            select(Job).where(Job.job_type == "pipeline_refresh", Job.bank_id == bank.id)
        )
    )
    assert len(jobs) == 1
    assert jobs[0].coalesce_key == f"refresh:{bank.id}:{FIXTURE_AS_OF.isoformat()}"
    assert jobs[0].run_after is not None  # debounced, not immediate


def test_raw_artifact_preserved_per_scope(
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    result = adapter.pull(
        credentials, [DataScope.YIELD_CURVE_GHS], FIXTURE_AS_OF, str(bank.id), "b-1"
    )
    location = StorageLocation(
        institution_slug=slug,
        tier="raw",
        object_path=(
            f"market_data/manual_upload/{FIXTURE_AS_OF.isoformat()}/"
            f"{result.batch_id}/YIELD_CURVE_GHS.json"
        ),
    )
    metadata, stream = storage.read(location)
    payload = json.loads(stream.read().decode("utf-8"))
    assert payload["scope"] == "YIELD_CURVE_GHS"
    assert payload["filename"] == "full.xlsx"
    assert len(payload["rows"]) == 3
    assert metadata.metadata.source_system == "MANUAL_UPLOAD"
    assert result.raw_storage_location.endswith(
        f"market_data/manual_upload/{FIXTURE_AS_OF.isoformat()}/{result.batch_id}"
    )


def test_row_problems_surface_as_pull_warnings(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    content = build_yield_curve_workbook(
        [
            ["GHS", "GHS_GOV_BOND", FIXTURE_AS_OF.isoformat(), 3, 15.80],
            ["XXX", "XXX_GOV_BOND", FIXTURE_AS_OF.isoformat(), 3, 9.10],
        ]
    )
    credentials = credentials_for(bank, stage_upload(storage, slug, content, "curves.xlsx"))
    result = adapter.pull(
        credentials, [DataScope.YIELD_CURVE_GHS], FIXTURE_AS_OF, str(bank.id), "b-1"
    )
    assert result.canonical_records_produced == 2  # header + one point
    assert any("unsupported currency" in warning for warning in result.warnings)
    assert any("row 3" in warning for warning in result.warnings)


def test_scope_missing_from_file_is_a_scope_error_others_proceed(
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    content = build_yield_curve_workbook(
        [["GHS", "GHS_GOV_BOND", FIXTURE_AS_OF.isoformat(), 3, 15.80]]
    )
    credentials = credentials_for(bank, stage_upload(storage, slug, content, "curves.xlsx"))
    result = adapter.pull(
        credentials,
        [DataScope.YIELD_CURVE_GHS, DataScope.CREDIT_RATING_NIGERIA_SOVEREIGN],
        FIXTURE_AS_OF,
        str(bank.id),
        "b-1",
    )
    assert result.scopes_pulled == [DataScope.YIELD_CURVE_GHS]
    assert len(result.errors) == 1
    assert "CREDIT_RATING_NIGERIA_SOVEREIGN" in result.errors[0]


def test_test_pull_returns_samples_without_persisting(
    db_session: Session,
    bank: Bank,
    slug: str,
    storage: InMemoryStorageClient,
    adapter: ManualUploadAdapter,
) -> None:
    credentials = _staged_credentials(storage, slug, bank)
    result = adapter.test_pull(credentials, [DataScope.YIELD_CURVE_GHS])
    assert result.success
    assert result.sample_values["GHS 3M"] == "15.80%"
    batches = db_session.scalar(select(func.count()).select_from(IngestionBatch))
    assert batches == 0


def test_zero_quota_estimate_within_cap(bank: Bank, adapter: ManualUploadAdapter) -> None:
    estimate = adapter.estimate_quota_cost(
        [DataScope.YIELD_CURVE_GHS, DataScope.FX_SPOT_USD_GHS],
        PullFrequency.END_OF_DAY,
        str(bank.id),
    )
    assert estimate.estimated_units_per_pull == 0
    assert estimate.estimated_monthly_units == 0
    assert estimate.within_cap is True


def test_available_scopes_exclude_security_master(adapter: ManualUploadAdapter) -> None:
    scopes = adapter.list_available_scopes()
    assert scopes
    assert all(not scope.value.startswith("SECURITY_MASTER_") for scope in scopes)
    assert DataScope.YIELD_CURVE_GHS in scopes
    assert DataScope.MACRO_GHANA_POLICY_RATE_PATH in scopes


def test_source_adapter_surface(adapter: ManualUploadAdapter) -> None:
    identity = adapter.identify()
    assert identity.name == "manual_upload"
    assert identity.source_system == "MANUAL_UPLOAD"
    assert adapter.vendor_name() == "manual_upload"
    assert adapter.health_check().healthy
