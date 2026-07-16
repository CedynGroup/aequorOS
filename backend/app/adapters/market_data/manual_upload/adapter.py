"""The Manual Upload market data adapter (market_data_adapter.md §8).

Architecturally a vendor with no external counterparty: the "credential" is a
resolved handle to the operator's staged upload
(``credentials={"staged_location": "temp://uploads/..."}``), consistent with
the AdapterConfig doctrine that adapters receive resolved handles, never raw
secrets. Authentication checks that the staged file exists; pulls parse it
and delegate persistence to the shared pull runner with
``vendor="manual_upload"`` and zero quota units (§8.3) — lineage, raw-tier
preservation, supersession, cache update, and the pipeline-refresh trigger
are identical to vendor pulls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    MarketDataPullResult,
    QuotaEstimate,
    TestPullResult,
    register_market_data_adapter,
)
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.manual_upload.parser import (
    ManualUploadParseError,
    ParsedUpload,
    detect_header,
    is_blank_row,
    is_comment_row,
    parse_upload,
    read_grids,
)
from app.adapters.market_data.pull_runner import ScopeExtraction, execute_pull
from app.adapters.market_data.quota_tracker import estimate
from app.adapters.market_data.scope_taxonomy import (
    DataScope,
    PullFrequency,
    ScopeCategory,
    category_of,
)
from app.adapters.market_data.scope_translator import Catalog
from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    EntityType,
    ExtractionResult,
    HealthStatus,
    MappingConfig,
    SourceColumn,
    SourceSchema,
    SourceTable,
)
from app.storage.client import StorageClient, StorageError, StorageLocation
from app.storage.factory import get_storage_client

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

    from app.models import Bank

VENDOR = "manual_upload"
ADAPTER_VERSION = "1.0"

_VENDOR_LABEL = "manual upload"
_TEMP_SCHEME = "temp://"
# CREDENTIAL_INVALID's template renders "last updated: {timestamp}"; manual
# uploads have no cached-vendor history to point at.
_NO_CACHE_TIMESTAMP = "not available"

# Manual pulls consume no vendor quota (§8.3): the estimator runs against an
# empty catalog, so every scope contributes zero units and no cap applies.
_EMPTY_CATALOG = Catalog(source_path="<manual_upload>", entries={})


class ManualUploadAdapter(MarketDataAdapter):
    """Serves every taxonomy scope the canonical market data model can hold,
    from operator-uploaded template files. Always available, never disabled
    (§8.4)."""

    def __init__(
        self,
        db: Session,
        bank: Bank,
        bank_slug: str,
        actor_user_id: UUID | None = None,
        storage: StorageClient | None = None,
    ) -> None:
        self._db = db
        self._bank = bank
        self._bank_slug = bank_slug
        self._actor_user_id = actor_user_id
        self._storage_client = storage

    # -- SourceAdapter (data_engine.md §5.1) ---------------------------------

    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name=VENDOR, version=ADAPTER_VERSION, source_system="MANUAL_UPLOAD")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        try:
            filename, content = self._resolve(config.location)
        except MarketDataError as exc:
            return ConnectionStatus(ok=False, detail=exc.bank_facing.message)
        return ConnectionStatus(ok=True, detail=f"{filename} ({len(content)} bytes)")

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        filename, content = self._resolve(config.location)
        try:
            grids = read_grids(content, filename)
        except ManualUploadParseError as exc:
            raise self._unreadable_file_error(str(exc)) from exc
        tables: list[SourceTable] = []
        for sheet_name, grid in grids:
            header = next(
                (row for row in grid if not is_blank_row(row) and not is_comment_row(row)),
                None,
            )
            if header is None:
                continue
            detected = detect_header(header)
            columns = (
                tuple(sorted(detected[1], key=detected[1].__getitem__))
                if detected is not None
                else tuple(str(cell).strip() for cell in header if cell is not None)
            )
            tables.append(
                SourceTable(
                    name=sheet_name,
                    columns=tuple(SourceColumn(name=column) for column in columns),
                )
            )
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        raise NotImplementedError("market data adapters ingest via pull()")

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        raise NotImplementedError("market data adapters ingest via pull()")

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="manual upload adapter ready")

    # -- MarketDataAdapter (market_data_adapter.md §4.1) ----------------------

    def vendor_name(self) -> str:
        return VENDOR

    def authenticate(self, credentials: CredentialSet) -> AuthResult:
        """There is no vendor to authenticate against: validity means the
        staged upload handle resolves to a readable file."""
        try:
            self._resolve_staged(credentials)
        except MarketDataError as exc:
            return AuthResult(
                success=False,
                session_token=None,
                expires_at=None,
                error_code=exc.bank_facing.code.value,
                error_message=exc.bank_facing.message,
            )
        return AuthResult(
            success=True,
            session_token=None,
            expires_at=None,
            error_code=None,
            error_message=None,
        )

    def validate_credentials(self, credentials: CredentialSet) -> AuthResult:
        return self.authenticate(credentials)

    def list_available_scopes(self) -> list[DataScope]:
        """Manual upload covers every scope the canonical market data model
        can represent (§5.4). Security master scopes are excluded: the
        persistence spine has no security-master record type yet, and faking
        support is forbidden (§16.9)."""
        return sorted(
            (
                scope
                for scope in DataScope
                if category_of(scope) is not ScopeCategory.SECURITY_MASTER
            ),
            key=lambda scope: scope.value,
        )

    def estimate_quota_cost(
        self,
        scopes: list[DataScope],
        frequency: PullFrequency,
        institution_id: str,
    ) -> QuotaEstimate:
        _ = institution_id  # no per-institution vendor consumption to look up
        return estimate(_EMPTY_CATALOG, scopes, frequency, current_consumption=0, cap=None)

    def test_pull(
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
    ) -> TestPullResult:
        """Parse the staged upload and surface human-readable sample values
        for the requested scopes, persisting nothing."""
        try:
            filename, content = self._resolve_staged(credentials)
            parsed = parse_upload(content, filename)
        except MarketDataError as exc:
            return TestPullResult(success=False, sample_values={}, error=exc.bank_facing.message)
        except ManualUploadParseError as exc:
            error = self._unreadable_file_error(str(exc))
            return TestPullResult(success=False, sample_values={}, error=error.bank_facing.message)
        missing = [scope for scope in scopes if scope not in parsed.scopes]
        if missing:
            message = render_bank_facing(
                BankFacingErrorCode.UNKNOWN_INSTRUMENT,
                vendor=_VENDOR_LABEL,
                scope=", ".join(scope.value for scope in missing),
            ).message
            return TestPullResult(success=False, sample_values={}, error=message)
        sample_values: dict[str, str] = {}
        for scope in scopes:
            sample_values.update(parsed.scopes[scope].bundle.sample_values)
        return TestPullResult(success=True, sample_values=sample_values, error=None)

    def pull(  # noqa: PLR0913 - spec §4.1 signature
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        """Parse the staged upload and hand the bundles to the pull runner.

        ``institution_id``/``batch_id`` come with the spec signature; the
        adapter is already bound to its bank and the runner mints the
        authoritative batch id, so both are informational here.
        """
        _ = institution_id, batch_id
        filename, content = self._resolve_staged(credentials)
        try:
            parsed = parse_upload(content, filename, expected_as_of=as_of_date)
        except ManualUploadParseError as exc:
            raise self._unreadable_file_error(str(exc)) from exc

        result = execute_pull(
            self._db,
            organization_id=self._bank.organization_id,
            bank=self._bank,
            bank_slug=self._bank_slug,
            vendor=VENDOR,
            adapter_version=ADAPTER_VERSION,
            scopes=scopes,
            as_of_date=as_of_date,
            extract=lambda scope: self._extract_scope(parsed, filename, as_of_date, scope),
            quota_units=0,
            actor_user_id=self._actor_user_id,
        )
        if parsed.problems:
            notes = [
                f"{problem.sheet} row {problem.row_number}: {problem.message}"
                for problem in parsed.problems
            ]
            result = replace(result, warnings=[*result.warnings, *notes])
        return result

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _extract_scope(
        parsed: ParsedUpload, filename: str, as_of_date: date, scope: DataScope
    ) -> ScopeExtraction:
        scope_rows = parsed.scopes.get(scope)
        if scope_rows is None:
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.UNKNOWN_INSTRUMENT,
                    vendor=_VENDOR_LABEL,
                    scope=scope.value,
                ),
                internal_detail=f"{filename} contains no rows for {scope.value}",
            )
        return ScopeExtraction(
            raw_payload={
                "filename": filename,
                "scope": scope.value,
                "as_of_date": as_of_date.isoformat(),
                "rows": scope_rows.raw_rows,
            },
            bundle=scope_rows.bundle,
        )

    def _storage(self) -> StorageClient:
        return self._storage_client if self._storage_client is not None else get_storage_client()

    def _resolve_staged(self, credentials: CredentialSet) -> tuple[str, bytes]:
        raw = credentials.credentials
        location = raw.get("staged_location") if isinstance(raw, dict) else None
        if not isinstance(location, str) or not location.strip():
            keys = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
            raise self._staged_file_error(
                f"credentials are missing a 'staged_location' handle (got: {keys})"
            )
        return self._resolve(location)

    def _resolve(self, location: str) -> tuple[str, bytes]:
        """Resolve a staged-upload handle to (filename, bytes).

        ``temp://{object_path}`` locations are fetched from the bank's temp
        tier (mirroring ingestion's source materialization); plain paths are
        read from the local filesystem.
        """
        if location.startswith(_TEMP_SCHEME):
            object_path = location[len(_TEMP_SCHEME) :]
            storage_location = StorageLocation(
                institution_slug=self._bank_slug, tier="temp", object_path=object_path
            )
            try:
                _, stream = self._storage().read(storage_location)
            except StorageError as exc:
                raise self._staged_file_error(
                    f"staged upload could not be read from {location}: {exc}"
                ) from exc
            return Path(object_path).name or "upload", stream.read()
        path = Path(location)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise self._staged_file_error(
                f"staged upload could not be read from {location}: {exc}"
            ) from exc
        return path.name, content

    @staticmethod
    def _staged_file_error(internal_detail: str) -> MarketDataError:
        """The staged-upload handle IS this adapter's credential; a handle
        that does not resolve is a credential failure."""
        return MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.CREDENTIAL_INVALID,
                vendor=_VENDOR_LABEL,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail=internal_detail,
        )

    @staticmethod
    def _unreadable_file_error(internal_detail: str) -> MarketDataError:
        return MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.CREDENTIAL_INVALID,
                vendor=_VENDOR_LABEL,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail=f"staged upload is not a readable template file: {internal_detail}",
        )


register_market_data_adapter(VENDOR, ManualUploadAdapter)
