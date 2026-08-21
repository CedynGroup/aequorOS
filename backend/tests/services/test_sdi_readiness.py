"""SDI module-readiness / data-quality diagnostics (docs/sdi.md §11, Phase I).

An SDI with no data sees every module BLOCKED with a specific reason; as the
canonical book + capital_structure land, modules move to READY/PARTIAL. Absent
data is never reported as compliant.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalReferenceRow, IngestionBatch, LineageRecord
from app.services import sdi_readiness
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db: Session, *, institution_type: str = "savings_and_loans") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="SDI readiness tenant",
        short_name="Rdy",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _statuses(db: Session, bank: Bank, as_of: date) -> dict[str, str]:
    return {m.module: m.status for m in sdi_readiness.assess_sdi_readiness(db, _CTX, bank, as_of)}


def test_no_data_blocks_every_module(db_session: Session) -> None:
    sdi = _bank(db_session)
    statuses = _statuses(db_session, sdi, date(2026, 6, 30))
    assert set(statuses) == {
        "liquidity_table1",
        "maturity_ladder",
        "funding_concentration",
        "capital",
        "exposures",
        "provisioning",
    }
    assert all(v == "blocked" for v in statuses.values())
    # Every blocked module explains WHY.
    for m in sdi_readiness.assess_sdi_readiness(db_session, _CTX, sdi, date(2026, 6, 30)):
        assert m.reasons


def test_capital_structure_alone_readies_capital_only(db_session: Session) -> None:
    sdi = _bank(db_session)
    as_of = date(2026, 6, 30)
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=sdi.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="readiness-test",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    db_session.add(
        CanonicalReferenceRow(
            organization_id=ORG_1,
            bank_id=sdi.id,
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
            dataset_kind="capital_structure",
            as_of_date=as_of,
            row_index=0,
            source_reference="CS/0",
            payload={
                "capital_component": "paid_up_capital",
                "amount_ghs": "20000000",
                "tier": "CET1",
            },
        )
    )
    db_session.flush()
    statuses = _statuses(db_session, sdi, as_of)
    assert statuses["capital"] == "ready"
    # No positions yet → the position-driven modules stay blocked.
    assert statuses["liquidity_table1"] == "blocked"
    assert statuses["provisioning"] == "blocked"


def test_full_canonical_book_lights_up_the_position_modules(db_session: Session) -> None:
    sdi = _bank(db_session)
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=sdi.id, as_of=FIXTURE_AS_OF)
    statuses = _statuses(db_session, sdi, FIXTURE_AS_OF)
    # The fixture seeds deposits, loans, securities and capital_structure — the
    # core SDI modules are no longer blocked.
    assert statuses["liquidity_table1"] != "blocked"
    assert statuses["capital"] == "ready"
    assert statuses["exposures"] == "ready"
    assert statuses["provisioning"] != "blocked"
