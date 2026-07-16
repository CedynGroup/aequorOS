"""Reusable contract-conformance suite for market data adapters.

Per market_data_adapter.md §4.3: a single contract test suite runs against
every ``MarketDataAdapter`` implementation. Any test that passes for one
adapter but fails for another indicates the interface is leaking
implementation details; fix the interface, not the test.

Every adapter test module subclasses :class:`MarketDataContractSuite`
(named ``Test...`` so pytest collects it) and provides these fixtures:

- ``adapter``: the MarketDataAdapter instance under test
- ``valid_credentials``: a CredentialSet the adapter accepts
- ``invalid_credentials``: a CredentialSet the vendor rejects. Its vendor
  fixture must embed :data:`VENDOR_INTERNAL_MARKER` in the raw vendor error
  so the leak tests can prove raw vendor messages never surface.
- ``pull_scopes``: a non-empty list of DataScope values the adapter supports
- ``as_of``: the business date the fixtures represent
- ``produced_records``: hook ``(MarketDataPullResult) -> Sequence`` returning
  the canonical records the pull persisted (dicts or objects), for the
  mandatory-metadata assertions
- ``count_current_records``: hook ``(DataScope, date) -> int`` returning the
  number of CURRENT-generation canonical records for a scope/as-of, for the
  supersede-not-duplicate assertion

This module intentionally contains no ``Test``-prefixed class, so pytest
collects zero tests from it standalone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any
from uuid import uuid4

from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    MarketDataPullResult,
    QuotaEstimate,
    TestPullResult,
)
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency

# Canary string vendor fixtures embed in their raw error payloads. If this
# ever appears in a bank-facing surface (AuthResult.error_message,
# TestPullResult.error, MarketDataPullResult.errors/warnings, or a
# MarketDataError's bank_facing message), the adapter is leaking raw vendor
# internals (§12.3).
VENDOR_INTERNAL_MARKER = "X-VENDOR-INTERNAL-DO-NOT-SURFACE"

# data_engine.md §4.3 mandatory metadata every canonical record must carry.
MANDATORY_METADATA_FIELDS = (
    "source_system",
    "source_reference",
    "ingestion_batch_id",
    "lineage_id",
    "as_of_date",
    "validation_status",
)

_BANK_FACING_CODES = frozenset(code.value for code in BankFacingErrorCode)

ProducedRecordsHook = Callable[[MarketDataPullResult], Sequence[Any]]
CountCurrentRecordsHook = Callable[[DataScope, date], int]


def _new_batch_id() -> str:
    return f"contract-{uuid4().hex}"


def _metadata_value(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field)
    return getattr(record, field, None)


class MarketDataContractSuite:
    # -- Authentication behavior (§4.3: invalid credentials produce
    # AuthResult(success=False) with a bank-facing error, not exceptions) ----

    def test_authenticate_invalid_credentials_fails_without_raising(
        self, adapter: MarketDataAdapter, invalid_credentials: CredentialSet
    ) -> None:
        result = adapter.authenticate(invalid_credentials)
        assert isinstance(result, AuthResult)
        assert not result.success
        assert result.error_code in _BANK_FACING_CODES
        assert result.error_message
        assert VENDOR_INTERNAL_MARKER not in result.error_message

    def test_validate_credentials_invalid_fails_without_raising(
        self, adapter: MarketDataAdapter, invalid_credentials: CredentialSet
    ) -> None:
        result = adapter.validate_credentials(invalid_credentials)
        assert isinstance(result, AuthResult)
        assert not result.success
        assert result.error_code in _BANK_FACING_CODES
        assert result.error_message
        assert VENDOR_INTERNAL_MARKER not in result.error_message

    def test_authenticate_valid_credentials_succeeds(
        self, adapter: MarketDataAdapter, valid_credentials: CredentialSet
    ) -> None:
        result = adapter.authenticate(valid_credentials)
        assert isinstance(result, AuthResult)
        assert result.success
        assert result.error_code is None

    # -- Scope coverage (§4.3: every scope returned by list_available_scopes
    # is pullable by test_pull and pull) ------------------------------------

    def test_every_listed_scope_is_accepted_by_test_pull(
        self, adapter: MarketDataAdapter, valid_credentials: CredentialSet
    ) -> None:
        scopes = adapter.list_available_scopes()
        assert scopes, "an adapter serving no scopes is useless"
        for scope in scopes:
            result = adapter.test_pull(valid_credentials, [scope])
            assert isinstance(result, TestPullResult)
            assert result.success, f"test_pull rejected advertised scope {scope.value}"
            assert result.sample_values

    def test_every_listed_scope_is_accepted_by_pull(
        self,
        adapter: MarketDataAdapter,
        valid_credentials: CredentialSet,
        as_of: date,
    ) -> None:
        scopes = adapter.list_available_scopes()
        result = adapter.pull(
            valid_credentials,
            scopes,
            as_of,
            valid_credentials.institution_id,
            _new_batch_id(),
        )
        assert isinstance(result, MarketDataPullResult)
        assert set(result.scopes_pulled) == set(scopes)
        assert not result.errors

    # -- Quota accounting (§4.3: pre-flight estimate, honest post-pull
    # reporting) -------------------------------------------------------------

    def test_quota_estimate_shape(
        self,
        adapter: MarketDataAdapter,
        valid_credentials: CredentialSet,
        pull_scopes: list[DataScope],
    ) -> None:
        estimate = adapter.estimate_quota_cost(
            pull_scopes, PullFrequency.ON_DEMAND, valid_credentials.institution_id
        )
        assert isinstance(estimate, QuotaEstimate)
        assert isinstance(estimate.within_cap, bool)
        assert estimate.estimated_units_per_pull >= 0
        assert estimate.estimated_monthly_units >= 0
        assert estimate.current_monthly_consumption >= 0

    def test_pull_reports_quota_consumption_honestly(
        self,
        adapter: MarketDataAdapter,
        valid_credentials: CredentialSet,
        pull_scopes: list[DataScope],
        as_of: date,
    ) -> None:
        estimate = adapter.estimate_quota_cost(
            pull_scopes, PullFrequency.ON_DEMAND, valid_credentials.institution_id
        )
        result = adapter.pull(
            valid_credentials,
            pull_scopes,
            as_of,
            valid_credentials.institution_id,
            _new_batch_id(),
        )
        assert result.quota_consumed >= 0
        # Actual consumption may exceed the estimate, but not wildly: beyond
        # 1.5x the estimator is misleading the bank's budgeting (§11.1). An
        # adapter with a documented reason to exceed this must fix its
        # estimate, not this test.
        assert result.quota_consumed <= max(estimate.estimated_units_per_pull, 1) * 1.5

    # -- Canonical output (§4.3: every canonical record has the mandatory
    # metadata columns populated) --------------------------------------------

    def test_pull_produces_records_with_mandatory_metadata(
        self,
        adapter: MarketDataAdapter,
        valid_credentials: CredentialSet,
        pull_scopes: list[DataScope],
        as_of: date,
        produced_records: ProducedRecordsHook,
    ) -> None:
        result = adapter.pull(
            valid_credentials,
            pull_scopes,
            as_of,
            valid_credentials.institution_id,
            _new_batch_id(),
        )
        assert result.canonical_records_produced > 0
        assert result.raw_storage_location
        assert result.canonical_storage_location
        records = produced_records(result)
        assert len(records) == result.canonical_records_produced
        for record in records:
            for field in MANDATORY_METADATA_FIELDS:
                value = _metadata_value(record, field)
                assert value not in (None, ""), (
                    f"canonical record missing mandatory metadata {field!r}: {record!r}"
                )

    # -- Idempotency (§4.3: re-running the same pull with the same as-of-date
    # supersedes rather than duplicates) ---------------------------------------

    def test_rerun_same_pull_supersedes_rather_than_duplicates(
        self,
        adapter: MarketDataAdapter,
        valid_credentials: CredentialSet,
        pull_scopes: list[DataScope],
        as_of: date,
        count_current_records: CountCurrentRecordsHook,
    ) -> None:
        adapter.pull(
            valid_credentials,
            pull_scopes,
            as_of,
            valid_credentials.institution_id,
            _new_batch_id(),
        )
        first_counts = {scope: count_current_records(scope, as_of) for scope in pull_scopes}
        assert any(count > 0 for count in first_counts.values())
        adapter.pull(
            valid_credentials,
            pull_scopes,
            as_of,
            valid_credentials.institution_id,
            _new_batch_id(),
        )
        for scope in pull_scopes:
            assert count_current_records(scope, as_of) == first_counts[scope], (
                f"re-pull duplicated current-generation records for {scope.value}"
            )

    # -- Error surfacing (§4.3: vendor errors map to BankFacingErrorCode; raw
    # vendor messages never leak) ----------------------------------------------

    def test_vendor_errors_never_leak_raw_messages(
        self,
        adapter: MarketDataAdapter,
        invalid_credentials: CredentialSet,
        pull_scopes: list[DataScope],
        as_of: date,
    ) -> None:
        test_result: TestPullResult | None = None
        try:
            test_result = adapter.test_pull(invalid_credentials, pull_scopes)
        except MarketDataError as exc:
            self._assert_market_data_error_is_clean(exc)
        if test_result is not None:
            assert not test_result.success
            assert VENDOR_INTERNAL_MARKER not in (test_result.error or "")

        pull_result: MarketDataPullResult | None = None
        try:
            pull_result = adapter.pull(
                invalid_credentials,
                pull_scopes,
                as_of,
                invalid_credentials.institution_id,
                _new_batch_id(),
            )
        except MarketDataError as exc:
            self._assert_market_data_error_is_clean(exc)
        if pull_result is not None:
            assert pull_result.errors, "a failed pull must surface bank-facing errors"
            for message in (*pull_result.errors, *pull_result.warnings):
                assert VENDOR_INTERNAL_MARKER not in message

    @staticmethod
    def _assert_market_data_error_is_clean(exc: MarketDataError) -> None:
        assert exc.bank_facing.code in BankFacingErrorCode
        assert VENDOR_INTERNAL_MARKER not in exc.bank_facing.message
        assert VENDOR_INTERNAL_MARKER not in str(exc)
