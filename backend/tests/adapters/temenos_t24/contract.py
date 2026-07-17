"""Reusable end-to-end contract suite for the Temenos T24 adapter.

A per-mode test module (OFS / IRIS / Open API) subclasses
:class:`TemenosContractSuite` and provides three fixtures — ``mode``,
``fixtures_dir``, and ``enabled_domains`` — and inherits the full stage ->
``start_ingestion`` -> persisted-canonical journey plus the guarantees the T24
integration must hold for every mode:

- a fixture pull produces an accepted batch with canonical rows,
- every canonical row carries mandatory metadata (``source_system == "T24"``),
- re-staging the identical pull reuses or supersedes rather than duplicating,
- no core-internal text ever reaches a bank-facing surface (leak canary).

The suite drives the real ingestion spine over an in-memory storage client and
a :class:`FixtureTransport`, so no live core is touched.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.temenos_t24.auth import SimulatedSessionProvider, TemenosCredentials
from app.adapters.temenos_t24.catalog import load_mode_catalog
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.errors import (
    BankFacingError,
    TemenosError,
    TemenosErrorCode,
    render_bank_facing,
)
from app.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.adapters.temenos_t24.pull import build_bundle, fetch_domains, pull_and_ingest
from app.adapters.temenos_t24.transport import FixtureTransport
from app.api.deps import TenantContext
from app.domain.ingestion.constants import BATCH_ACCEPTED_STATUSES
from app.domain.ingestion.contracts import AdapterConfig
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
)
from app.schemas.ingestion import MappingConfigCreate
from app.services.ingestion import create_mapping_config
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

# A sentinel that must NEVER appear on a bank-facing surface. It stands in for
# the raw OFS/core diagnostic text an error carries internally.
TEMENOS_INTERNAL_MARKER = "X-T24-INTERNAL-DO-NOT-SURFACE"

_AS_OF = date(2026, 6, 30)
_COMPANY = "GH0010001"
_ENDPOINT = "fixture://sample-bank"

# Entity domains for the base SourceAdapter contract (its extract test asserts
# every record's entity_type is one of the four canonical types; reference
# domains are exercised only by the end-to-end suite).
_ENTITY_DOMAIN_NAMES = (
    "GL_BALANCES",
    "POSITIONS_LOANS",
    "POSITIONS_DEPOSITS",
    "POSITIONS_CURRENT_ACCOUNTS",
    "COUNTERPARTY_MASTER",
    "PRODUCT_MASTER",
)


def staged_entity_bundle(mode: str, fixtures_dir, tmp_path) -> AdapterConfig:
    """Build an entity-only staged bundle file for the SourceAdapter contract."""
    catalog = load_mode_catalog(mode)
    domains = [CoreBankingDomain[name] for name in _ENTITY_DOMAIN_NAMES]
    transport = FixtureTransport(fixtures_dir)
    session = SimulatedSessionProvider().sign_on(
        mode, _ENDPOINT, TemenosCredentials(username="SVC"), company=_COMPANY
    )
    responses = fetch_domains(
        catalog, domains, transport, session, as_of=_AS_OF, company=_COMPANY, mode=mode
    )
    bundle = build_bundle(mode=mode, as_of=_AS_OF, company=_COMPANY, responses=responses)
    path = tmp_path / f"t24-{mode.lower()}.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return AdapterConfig(location=str(path))


class TemenosContractSuite:
    # --- fixtures a per-mode subclass MUST provide ------------------------

    @pytest.fixture
    def mode(self) -> str:
        raise NotImplementedError

    @pytest.fixture
    def fixtures_dir(self) -> Path:
        raise NotImplementedError

    @pytest.fixture
    def enabled_domains(self) -> list[str] | None:
        return None

    # --- shared machinery -------------------------------------------------

    @pytest.fixture
    def ctx(self) -> TenantContext:
        return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)

    @pytest.fixture
    def storage(self) -> InMemoryStorageClient:
        return InMemoryStorageClient()

    @pytest.fixture
    def transport(self, fixtures_dir: Path) -> FixtureTransport:
        return FixtureTransport(fixtures_dir)

    @pytest.fixture
    def bank(self, db_session: Session) -> Bank:
        bank = Bank(
            organization_id=ORG_1,
            name="Sample Bank Ltd",
            short_name="sample-t24",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
        )
        db_session.add(bank)
        db_session.commit()
        return bank

    @pytest.fixture
    def mapping_id(self, db_session: Session, ctx: TenantContext, bank: Bank, mode: str):
        created = create_mapping_config(
            db_session,
            ctx,
            bank.id,
            MappingConfigCreate(
                source_system="T24",
                name=f"Default T24 ({mode})",
                config=default_t24_mapping_config(mode),
                activate=True,
                reason="test seed",
            ),
        )
        return created.id

    @pytest.fixture
    def run_pull(  # noqa: PLR0913 - a pull binds several test fixtures
        self,
        db_session: Session,
        ctx: TenantContext,
        bank: Bank,
        storage: InMemoryStorageClient,
        transport: FixtureTransport,
        mode: str,
        enabled_domains: list[str] | None,
        mapping_id,
    ):
        def _run(as_of: date = _AS_OF):
            return pull_and_ingest(
                db_session,
                ctx,
                bank.id,
                storage,
                mode=mode,
                as_of=as_of,
                company=_COMPANY,
                transport=transport,
                session_provider=SimulatedSessionProvider(),
                credentials=TemenosCredentials(username="SVC.AEQUOROS"),
                endpoint=_ENDPOINT,
                reason="fixture pull",
                mapping_config_id=mapping_id,
                domains=enabled_domains,
            )

        return _run

    # --- the contract -----------------------------------------------------

    def test_pull_produces_accepted_batch(self, run_pull) -> None:
        result = run_pull()
        assert not result.reused
        assert result.batch.status in BATCH_ACCEPTED_STATUSES
        assert result.batch.records_accepted > 0

    def test_canonical_rows_persist_with_t24_metadata(
        self, run_pull, db_session: Session, bank: Bank
    ) -> None:
        run_pull()
        for model in (
            CanonicalGlAccount,
            CanonicalCounterparty,
            CanonicalProduct,
            CanonicalPosition,
        ):
            rows = db_session.scalars(
                select(model).where(
                    model.organization_id == ORG_1,
                    model.bank_id == bank.id,
                    model.superseded_by.is_(None),
                )
            ).all()
            assert rows, f"{model.__name__} produced no rows"
            for row in rows:
                assert row.source_system == "T24"
                assert row.ingestion_batch_id is not None

    def test_reference_rows_persist(self, run_pull, db_session: Session, bank: Bank) -> None:
        # Reference rows are batch-scoped (no supersession chain); one pull
        # yields exactly this batch's rows.
        run_pull()
        refs = db_session.scalars(
            select(CanonicalReferenceRow).where(
                CanonicalReferenceRow.organization_id == ORG_1,
                CanonicalReferenceRow.bank_id == bank.id,
            )
        ).all()
        kinds = {row.dataset_kind for row in refs}
        assert "business_units" in kinds
        assert "institution" in kinds

    def test_position_snapshots_carry_lcy_balance_for_the_engines(
        self, run_pull, db_session: Session, bank: Bank
    ) -> None:
        # fact_derivation reads balance_ghs off the snapshot attributes; the LCY
        # binding must survive extract -> translate -> persist. This is the
        # load-bearing link that lets the ALM engines run on the T24 book.
        run_pull()
        snapshots = db_session.scalars(
            select(CanonicalPositionSnapshot).where(
                CanonicalPositionSnapshot.organization_id == ORG_1,
                CanonicalPositionSnapshot.bank_id == bank.id,
                CanonicalPositionSnapshot.superseded_by.is_(None),
            )
        ).all()
        assert snapshots
        # On-balance positions carry balance_ghs; derivatives and off-balance
        # exposures carry notional_ghs. Every position must land at least one
        # LCY measure the engines read.
        assert all(
            {"balance_ghs", "notional_ghs"} & set(snap.attributes or {}) for snap in snapshots
        )

    def test_derivative_and_off_balance_positions_carry_engine_attributes(
        self, run_pull, db_session: Session, bank: Bank
    ) -> None:
        # The IRRBB/FX/Basel engines read specific attribute keys off each
        # exposure. Prove the treasury and off-balance book lands them.
        run_pull()
        positions = db_session.scalars(
            select(CanonicalPosition).where(
                CanonicalPosition.organization_id == ORG_1,
                CanonicalPosition.bank_id == bank.id,
                CanonicalPosition.superseded_by.is_(None),
            )
        ).all()
        by_type: dict[str, dict] = {}
        for position in positions:
            snapshot = db_session.scalars(
                select(CanonicalPositionSnapshot).where(
                    CanonicalPositionSnapshot.position_id == position.id,
                    CanonicalPositionSnapshot.superseded_by.is_(None),
                )
            ).first()
            if snapshot is not None:
                by_type[position.position_type] = snapshot.attributes or {}

        required = {
            "FX_HEDGE": {"buy_currency", "contract_rate", "notional_ghs"},
            "INTEREST_RATE_SWAP": {"direction", "pay_rate_pct", "receive_index", "notional_ghs"},
            "LC_GUARANTEE": {"credit_conversion_factor", "notional_ghs"},
            "COMMITMENT_UNDRAWN": {"credit_conversion_factor", "notional_ghs"},
        }
        for position_type, keys in required.items():
            assert position_type in by_type, f"no {position_type} position persisted"
            assert keys <= set(by_type[position_type]), (
                f"{position_type} snapshot missing {keys - set(by_type[position_type])}"
            )

    def test_restaging_identical_pull_does_not_duplicate(
        self, run_pull, db_session: Session, bank: Bank
    ) -> None:
        run_pull()
        before = db_session.scalars(
            select(CanonicalPosition).where(
                CanonicalPosition.organization_id == ORG_1,
                CanonicalPosition.bank_id == bank.id,
                CanonicalPosition.superseded_by.is_(None),
            )
        ).all()
        run_pull()  # identical content
        after = db_session.scalars(
            select(CanonicalPosition).where(
                CanonicalPosition.organization_id == ORG_1,
                CanonicalPosition.bank_id == bank.id,
                CanonicalPosition.superseded_by.is_(None),
            )
        ).all()
        assert len(after) == len(before)

    def test_leak_canary_absent_from_bank_facing_error(self) -> None:
        bank_facing: BankFacingError = render_bank_facing(
            TemenosErrorCode.CORE_UNAVAILABLE,
            core_system="Temenos T24",
            timestamp=_AS_OF.isoformat(),
        )
        err = TemenosError(
            bank_facing,
            internal_detail=f"OFS sign-on rejected {TEMENOS_INTERNAL_MARKER} pw=deadbeef",
        )
        assert TEMENOS_INTERNAL_MARKER not in str(err)
        assert TEMENOS_INTERNAL_MARKER not in err.bank_facing.message
        assert TEMENOS_INTERNAL_MARKER in err.internal_detail
