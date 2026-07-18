"""BloombergAdapter — the Bloomberg market data adapter (market_data_adapter.md §6).

Wires the §6 pieces together: the field catalog is ground truth for coverage
and quota, :class:`SimulatedSessionProvider` validates §6.1 credentials
(live B-PIPE / Data License cert auth is Phase 2 behind
:class:`BloombergSessionProvider`), the transport seam replays recorded
fixtures (§6.5), extractors and translators keep vendor vocabulary inside
this package, and the shared pull runner performs all persistence. Every
vendor fault surfaces as a classified :class:`BankFacingErrorCode`; raw
vendor messages never leak (§12.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.adapters.market_data import quota_tracker
from app.adapters.market_data.base import (
    AuthResult,
    MarketDataAdapter,
    TestPullResult,
    register_market_data_adapter,
)
from app.adapters.market_data.bloomberg.auth import (
    VENDOR_DISPLAY_NAME,
    SimulatedSessionProvider,
)
from app.adapters.market_data.bloomberg.extractors.credit_data import extract_ratings
from app.adapters.market_data.bloomberg.extractors.curves import extract_curve
from app.adapters.market_data.bloomberg.extractors.fx import extract_fx_forward, extract_fx_spot
from app.adapters.market_data.bloomberg.extractors.macro_series import extract_macro
from app.adapters.market_data.bloomberg.translators.curve_to_canonical import curve_bundle
from app.adapters.market_data.bloomberg.translators.fx_to_canonical import (
    fx_forward_bundle,
    fx_spot_bundle,
)
from app.adapters.market_data.bloomberg.translators.macro_to_canonical import macro_bundle
from app.adapters.market_data.bloomberg.translators.rating_to_canonical import rating_bundle
from app.adapters.market_data.bloomberg.transport import UnavailableTransport
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.pull_runner import ScopeExtraction, execute_pull
from app.adapters.market_data.scope_taxonomy import (
    DataScope,
    ScopeCategory,
    category_of,
)
from app.adapters.market_data.scope_translator import (
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)
from app.db.base import utc_now
from app.domain.ingestion.contracts import (
    AdapterIdentity,
    ConnectionStatus,
    HealthStatus,
    SourceColumn,
    SourceSchema,
    SourceTable,
)

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.orm import Session

    from app.adapters.market_data.base import (
        CredentialSet,
        MarketDataPullResult,
        QuotaEstimate,
    )
    from app.adapters.market_data.bloomberg.auth import (
        BloombergSession,
        BloombergSessionProvider,
    )
    from app.adapters.market_data.bloomberg.transport import BlpTransport
    from app.adapters.market_data.scope_taxonomy import PullFrequency
    from app.domain.ingestion.contracts import (
        AdapterConfig,
        CanonicalRecords,
        EntityType,
        ExtractionResult,
        MappingConfig,
    )
    from app.models import Bank

ADAPTER_NAME = "bloomberg_market_data"
ADAPTER_VERSION = "1.0"
VENDOR = "bloomberg"

CATALOG_PATH = Path(__file__).with_name("field_catalog.yaml")

# Loaded once at import: the catalog is ground truth (§6.2) and a malformed
# catalog must fail the deploy, never a bank's pull.
_CATALOG = load_catalog(CATALOG_PATH)

_INGESTS_VIA_PULL = "market data adapters ingest via pull()"


class BloombergAdapter(MarketDataAdapter):
    """Concrete :class:`MarketDataAdapter` for Bloomberg (§6).

    ``institution_id`` / ``batch_id`` arguments on the spec §4.1 methods are
    correlation inputs: institution identity is bound at construction (the
    ``bank`` row) and the persistence spine assigns the authoritative batch
    id, which the returned :class:`MarketDataPullResult` reports.
    """

    def __init__(  # noqa: PLR0913 - constructor carries the full pull context
        self,
        db: Session,
        bank: Bank,
        bank_slug: str,
        session_provider: BloombergSessionProvider | None = None,
        transport: BlpTransport | None = None,
        actor_user_id: UUID | None = None,
    ) -> None:
        self._db = db
        self._bank = bank
        self._bank_slug = bank_slug
        self._session_provider = session_provider or SimulatedSessionProvider()
        self._transport = transport or UnavailableTransport()
        self._actor_user_id = actor_user_id
        self._catalog = _CATALOG

    # -- SourceAdapter contract (data_engine.md §5.1) -------------------------

    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(
            name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="BLOOMBERG"
        )

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        if isinstance(self._transport, UnavailableTransport):
            return ConnectionStatus(
                ok=False, detail="live Bloomberg transport not configured (Phase 2)."
            )
        return ConnectionStatus(ok=True, detail="Bloomberg transport configured.")

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        """The field catalog is the schema: one table per supported scope."""
        tables = []
        for scope in supported_scopes(self._catalog):
            requests = requests_for(self._catalog, scope)
            columns = tuple(
                SourceColumn(name=f"{request['security']} {request['field']}")
                for request in requests
            )
            tables.append(SourceTable(name=scope.value, columns=columns, row_count=None))
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        raise NotImplementedError(_INGESTS_VIA_PULL)

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        raise NotImplementedError(_INGESTS_VIA_PULL)

    def health_check(self) -> HealthStatus:
        if isinstance(self._transport, UnavailableTransport):
            return HealthStatus(
                healthy=False, detail="live Bloomberg transport not configured (Phase 2)."
            )
        supported = len(supported_scopes(self._catalog))
        return HealthStatus(
            healthy=True,
            detail=f"catalog serves {supported} scopes; transport configured.",
        )

    # -- MarketDataAdapter contract (market_data_adapter.md §4.1) -------------

    def vendor_name(self) -> str:
        return VENDOR

    def authenticate(self, credentials: CredentialSet) -> AuthResult:
        try:
            self._session_provider.open_session(credentials)
        except MarketDataError as exc:
            return AuthResult(
                success=False,
                session_token=None,
                expires_at=None,
                error_code=exc.bank_facing.code.value,
                error_message=exc.bank_facing.message,
            )
        # Simulated sessions carry no vendor token; live token handling is the
        # Phase 2 session provider's concern (tokens stay within a pull cycle).
        return AuthResult(
            success=True,
            session_token=None,
            expires_at=None,
            error_code=None,
            error_message=None,
        )

    def list_available_scopes(self) -> list[DataScope]:
        return supported_scopes(self._catalog)

    def estimate_quota_cost(
        self,
        scopes: list[DataScope],
        frequency: PullFrequency,
        institution_id: str,
    ) -> QuotaEstimate:
        current = quota_tracker.current_month_usage(
            self._db, self._bank.organization_id, self._bank.id, VENDOR
        )
        # No monthly cap store exists in MVP (tracking only, §16.5): estimates
        # are advisory and never block.
        return quota_tracker.estimate(
            self._catalog, scopes, frequency, current_consumption=current, cap=None
        )

    def test_pull(
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
    ) -> TestPullResult:
        try:
            session = self._session_provider.open_session(credentials)
            samples: dict[str, str] = {}
            # Nothing here persists: sample values exist only for the
            # onboarding operator to eyeball (§9.2 test step).
            for scope in scopes:
                extraction = self._extract_scope(session, scope, utc_now().date())
                samples.update(extraction.bundle.sample_values)
        except MarketDataError as exc:
            return TestPullResult(success=False, sample_values={}, error=exc.bank_facing.message)
        return TestPullResult(success=True, sample_values=samples, error=None)

    def pull(  # noqa: PLR0913 - spec §4.1 signature
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        # An auth failure aborts the whole pull as a classified, bank-safe
        # MarketDataError; per-scope vendor faults are collected by the runner.
        session = self._session_provider.open_session(credentials)
        return execute_pull(
            self._db,
            organization_id=self._bank.organization_id,
            bank=self._bank,
            bank_slug=self._bank_slug,
            vendor=VENDOR,
            adapter_version=ADAPTER_VERSION,
            scopes=scopes,
            as_of_date=as_of_date,
            extract=lambda scope: self._extract_scope(session, scope, as_of_date),
            quota_units=quota_units(self._catalog, scopes),
            actor_user_id=self._actor_user_id,
        )

    def validate_credentials(self, credentials: CredentialSet) -> AuthResult:
        # Lightweight by construction: the simulated provider validates shape
        # only and consumes no quota. Phase 2 points this at Bloomberg's
        # session-open endpoint, which is also quota-free.
        return self.authenticate(credentials)

    # -- Vendor-specific extraction/translation dispatch -----------------------

    def _extract_scope(
        self,
        session: BloombergSession,
        scope: DataScope,
        as_of_date: date,
    ) -> ScopeExtraction:
        entry = self._catalog.entries.get(scope)
        if entry is None or not entry.supported:
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.SCOPE_NOT_PERMITTED,
                    vendor=VENDOR_DISPLAY_NAME,
                    scope=scope.value,
                ),
                internal_detail=f"scope {scope.value} is not supported by the Bloomberg catalog",
            )
        requests = requests_for(self._catalog, scope)
        category = category_of(scope)
        if category is ScopeCategory.YIELD_CURVE:
            curve = extract_curve(session, self._transport, scope, requests)
            return ScopeExtraction(
                raw_payload=curve.raw_response,
                bundle=curve_bundle(scope, curve.observations),
            )
        if category is ScopeCategory.FX_SPOT:
            fx = extract_fx_spot(session, self._transport, scope, requests)
            return ScopeExtraction(
                raw_payload=fx.raw_response,
                bundle=fx_spot_bundle(scope, fx.observation),
            )
        if category is ScopeCategory.FX_FORWARD:
            forward = extract_fx_forward(session, self._transport, scope, requests)
            return ScopeExtraction(
                raw_payload=forward.raw_response,
                bundle=fx_forward_bundle(scope, forward.observation),
            )
        if category is ScopeCategory.MACRO_FORECAST:
            raw_macro, macro_observations = extract_macro(session, self._transport, scope, requests)
            return ScopeExtraction(
                raw_payload=raw_macro,
                bundle=macro_bundle(scope, macro_observations),
            )
        if category is ScopeCategory.CREDIT_RATING:
            ratings = extract_ratings(session, self._transport, scope, requests)
            return ScopeExtraction(
                raw_payload=ratings.raw_response,
                bundle=rating_bundle(scope, ratings.observations, as_of_date),
            )
        # A supported catalog entry in a category without an extractor is a
        # catalog authoring error; classified so nothing internal leaks.
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.SCOPE_NOT_PERMITTED,
                vendor=VENDOR_DISPLAY_NAME,
                scope=scope.value,
            ),
            internal_detail=f"category {category.value} has no Bloomberg extractor",
        )


register_market_data_adapter(VENDOR, BloombergAdapter)
