"""The ``capital_structure`` tier is a closed vocabulary, end to end.

Audit evidence 12.1: the tier parser read every value it did not recognise —
"Tier 2", "TIER_2", "Additional Tier 1", "" and "garbage" — as CET1, so a
subordinated loan tagged "Tier 2" landed in CET1, uncapped. And the SDI sample
booked the BoG Credit Risk Reserve as CET1, which the reserve's own cited rule
excludes from the capital base.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import OutcomeState
from app.domain.capital.engine import CAPITAL_REGISTER_REFUSED_CATEGORY
from app.domain.ingestion.capital_tiers import parse_capital_tier
from app.domain.ingestion.constants import SourceSystem
from app.domain.ingestion.contracts import MappingConfig, ReferenceMapping, ReferenceRowData
from app.models import (
    Bank,
    BankFinancialFact,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    TranslationFailure,
)
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services import ingestion, sdi_capital, sdi_capital_checks
from app.services.fact_derivation import derive_facts
from app.services.sdi_capital import net_own_funds
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.canonical import seed_canonical_fixture
from tests.support.inmemory_storage import InMemoryStorageClient

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
REPORTING_DATE = date(2026, 3, 31)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CET1", ("CET1", False)),
        ("AT1", ("AT1", False)),
        ("TIER2", ("T2", False)),
        ("T2", ("T2", False)),
        ("Tier 2", ("T2", False)),
        ("TIER_2", ("T2", False)),
        ("Tier-2", ("T2", False)),
        ("Additional Tier 1", ("AT1", False)),
        ("AT 1", ("AT1", False)),
        ("CET1_DEDUCTION", ("CET1", True)),
        ("TIER2_DEDUCTION", ("T2", True)),
        ("t2_deduction", ("T2", True)),
        ("T1", None),
        ("Tier 1", None),
        ("", None),
        ("garbage", None),
        ("DEDUCTION", None),
        (None, None),
    ],
)
def test_capital_tier_vocabulary_is_closed(raw: str | None, expected: object) -> None:
    """BoG CRD June 2018 ¶32 (repo-cited): a component's tier decides the ratio it
    counts toward, so an unrecognised or ambiguous tier is refused, never CET1."""
    assert parse_capital_tier(raw) == expected


def _row(tier: str, amount: str = "20000000") -> ReferenceRowData:
    return ReferenceRowData(
        dataset_kind="capital_structure",
        source_locator="capital_structure.csv!A2",
        row_index=1,
        payload={"capital_component": "subordinated_debt", "amount_ghs": amount, "tier": tier},
    )


def test_unknown_capital_tier_is_rejected_at_ingestion() -> None:
    """BoG CRD June 2018 ¶32 (repo-cited): an unrecognised tier is refused at the door."""
    assert _row("Tier 2").row_index == 1
    with pytest.raises(ValueError, match="field 'tier' must be one of"):
        _row("garbage")
    with pytest.raises(ValueError, match="missing required field 'tier'"):
        _row("")


@pytest.mark.parametrize(
    "amount",
    ["-20,000,000", "-20%", "NaN", "sNaN", "Infinity", "-Infinity", float("nan"), float("inf")],
)
def test_capital_amount_requires_an_unchanged_finite_decimal(amount: object) -> None:
    """BoG CRD 2018 ¶32: refused capital amounts cannot reach Decimal consumers."""
    with pytest.raises(ValueError, match="amount_ghs.*finite Decimal"):
        _row("CET1_DEDUCTION", str(amount))


@pytest.mark.parametrize(
    "amount", ["-20000000", "0", "20000000.123456", "2e7", Decimal("-20.25"), 20, 20.25]
)
def test_finite_capital_amounts_reach_consumers_without_value_changes(amount: object) -> None:
    """BoG CRD 2018 ¶32: finite Decimal representations retain their signed amount."""
    row = _row("CET1_DEDUCTION", str(amount))
    assert sdi_capital.signed_component_amount(row.payload) == -abs(Decimal(str(amount)))


def _push_register(db: Session, *rows: tuple[str, str, str]) -> None:
    """Store a capital_structure generation directly, as a pre-fix push would have."""
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=REPORTING_DATE,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="capital-structure-tiers",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    for index, (component, amount, tier) in enumerate(rows, start=1):
        db.add(
            CanonicalReferenceRow(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=REPORTING_DATE,
                row_index=index,
                source_reference=f"CAPITAL/{index}",
                payload={"capital_component": component, "amount_ghs": amount, "tier": tier},
            )
        )
    db.flush()


def _seed_book(db: Session) -> None:
    materialize_canonical_test_book(db)
    seed_canonical_fixture(
        db, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID, as_of=REPORTING_DATE
    )


def _capital_facts(db: Session, period_id: UUID) -> dict[str, tuple[Decimal, str | None]]:
    rows = db.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.organization_id == DEMO_ORG_ID,
            BankFinancialFact.bank_id == SAMPLE_BANK_ID,
            BankFinancialFact.reporting_period_id == period_id,
            BankFinancialFact.fact_group == "capital_component",
            BankFinancialFact.category != CAPITAL_REGISTER_REFUSED_CATEGORY,
        )
    ).all()
    return {row.category: (Decimal(str(row.amount)), row.capital_tier) for row in rows}


def test_spelled_out_tier_lands_in_its_own_tier_and_reserve_in_none(db_session: Session) -> None:
    """IAS 32 ¶15-16 input, BoG CRD 2018 placement: sub-debt tagged "Tier 2" is Tier 2.

    Guide for Financial Publication BSD/2017 §2.2.1 (repo-cited): the Credit Risk
    Reserve is excluded from the adjusted capital base, whatever tier it is tagged.
    """
    _seed_book(db_session)
    _push_register(
        db_session,
        ("paid_up_capital", "100000000", "CET1"),
        ("credit_risk_reserve", "5000000", "CET1"),
        ("subordinated_debt", "20000000", "Tier 2"),
    )

    result = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)

    facts = _capital_facts(db_session, result.reporting_period_id)
    assert facts == {
        "paid_up_capital": (Decimal("100000000"), "CET1"),
        "subordinated_debt": (Decimal("20000000"), "T2"),
    }
    group = next(group for group in result.groups if group.group == "capital_component")
    assert any("credit_risk_reserve" in warning for warning in group.warnings)


def test_unknown_capital_tier_fails_the_register_closed(db_session: Session) -> None:
    """BoG CRD June 2018 ¶32 (repo-cited): a stored row with an unrecognised tier is
    never counted as CET1; the register derives no capital until it is corrected."""
    _seed_book(db_session)
    _push_register(
        db_session,
        ("paid_up_capital", "100000000", "CET1"),
        ("subordinated_debt", "20000000", "garbage"),
    )

    result = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)

    assert _capital_facts(db_session, result.reporting_period_id) == {}
    group = next(group for group in result.groups if group.group == "capital_component")
    assert group.status == "skipped"
    assert group.note is not None and "subordinated_debt ('garbage')" in group.note


def test_credit_risk_reserve_is_not_in_sdi_net_own_funds(db_session: Session) -> None:
    """Guide for Financial Publication BSD/2017 §2.2.1 (repo-cited): the reserve is not
    own funds, so the SDI Net Own Funds is the register without it."""
    materialize_canonical_test_book(db_session)
    _push_register(
        db_session,
        ("paid_up_capital", "22000000", "CET1"),
        ("credit_risk_reserve", "157887.95", "CET1"),
        ("intangible_assets", "-1200000", "CET1_DEDUCTION"),
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None

    assert net_own_funds(db_session, MAKER, bank, REPORTING_DATE) == Decimal("20800000")


@pytest.mark.parametrize("source_system", ["EXCEL_CSV", "API_PUSH"])
@pytest.mark.parametrize(
    ("refused_tier", "refused_amount"),
    [
        ("garbage", "-20000000"),
        ("CET1_DEDUCTION", "-20,000,000"),
        ("CET1_DEDUCTION", "-20%"),
        ("CET1_DEDUCTION", "NaN"),
        ("CET1_DEDUCTION", "Infinity"),
    ],
)
def test_refused_capital_row_rejects_the_batch_without_publishing_a_partial_register(
    db_session: Session,
    tmp_path: Path,
    source_system: SourceSystem,
    refused_tier: str,
    refused_amount: str,
) -> None:
    """BoG CRD 2018 ¶32: a refused deduction must never leave a partial capital register."""
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    ingestion.create_mapping_config(
        db_session,
        MAKER,
        bank.id,
        MappingConfigCreate(
            source_system=source_system,
            name="Capital register",
            config=MappingConfig(
                reference_mappings={
                    "bank_capital": ReferenceMapping(
                        source_table="capital_structure", dataset_kind="capital_structure"
                    )
                }
            ),
            activate=True,
            reason="Test capital register publication.",
        ),
        commit=False,
    )
    storage = InMemoryStorageClient()

    def ingest(tier: str, amount: str) -> IngestionBatch:
        rows = [
            {"capital_component": "paid_up_capital", "amount_ghs": "100000000", "tier": "CET1"},
            {"capital_component": "intangible_assets", "amount_ghs": amount, "tier": tier},
        ]
        if source_system == "API_PUSH":
            source = tmp_path / "register.json"
            source.write_text(json.dumps({"reference": {"capital_structure": rows}}))
        else:
            source = tmp_path / "capital_structure.csv"
            with source.open("w", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file, fieldnames=["capital_component", "amount_ghs", "tier"]
                )
                writer.writeheader()
                writer.writerows(rows)
        result = ingestion.start_ingestion(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            IngestionBatchCreate(
                source_system=source_system,
                as_of_date=REPORTING_DATE,
                location=str(source),
                reason="Test register replacement.",
            ),
            storage,
            commit=False,
        )
        batch = db_session.get(IngestionBatch, result.batch.id)
        assert batch is not None
        return batch

    rejected = ingest(refused_tier, refused_amount)
    assert rejected.status == "rejected"
    assert rejected.records_translated == 1
    assert rejected.records_accepted == 0
    assert not sdi_capital.latest_capital_structure_rows(db_session, MAKER, bank, REPORTING_DATE)
    failure = db_session.scalar(
        select(TranslationFailure).where(TranslationFailure.ingestion_batch_id == rejected.id)
    )
    assert failure is not None
    raw_record = cast(dict[str, object], failure.raw_record)
    assert raw_record["capital_component"] == "intangible_assets"
    findings = cast(list[dict[str, str]], rejected.validation_report["failures"])
    finding = findings[0]
    assert finding["severity"] == "BLOCKER"
    assert finding["source_locator"] == failure.source_locator
    assert (refused_tier if refused_tier == "garbage" else refused_amount) in finding["detail"]

    accepted = ingest("CET1_DEDUCTION", "-10000000")
    assert accepted.status == "accepted"
    assert net_own_funds(db_session, MAKER, bank, REPORTING_DATE) == Decimal("90000000")
    rejected = ingest(refused_tier, refused_amount)
    assert rejected.status == "rejected"
    current = sdi_capital.latest_capital_structure_rows(db_session, MAKER, bank, REPORTING_DATE)
    assert {row.ingestion_batch_id for row in current} == {accepted.id}
    assert net_own_funds(db_session, MAKER, bank, REPORTING_DATE) == Decimal("90000000")

    corrected = ingest("CET1_DEDUCTION", "-20000000")
    assert corrected.status == "accepted"
    assert net_own_funds(db_session, MAKER, bank, REPORTING_DATE) == Decimal("80000000")


@pytest.mark.parametrize("consumer", ["net_own_funds", "prefetch", "capital_checks", "summary"])
@pytest.mark.parametrize("amount", ["-20000000", "20000000"])
def test_stored_unknown_tier_refuses_sdi_capital_consumers(
    db_session: Session, consumer: str, amount: str
) -> None:
    """BoG CRD 2018 ¶32: SDI capital refuses stored unknown tiers, including deductions."""
    materialize_canonical_test_book(db_session)
    _push_register(
        db_session,
        ("paid_up_capital", "100000000", "CET1"),
        ("intangible_assets", amount, "garbage"),
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as refused:
        if consumer == "net_own_funds":
            net_own_funds(db_session, MAKER, bank, REPORTING_DATE)
        elif consumer == "prefetch":
            sdi_capital.prefetch_net_own_funds(db_session, MAKER, bank, [REPORTING_DATE])
        elif consumer == "capital_checks":
            sdi_capital_checks.capital_components(db_session, MAKER, bank, REPORTING_DATE)
        else:
            sdi_capital.compute_sdi_capital_summary(db_session, MAKER, bank, REPORTING_DATE)

    assert refused.value.state == OutcomeState.DATA_QUALITY_BLOCK
    assert refused.value.blocks_filing
    assert refused.value.status_code == 409
    detail = cast(str, refused.value.detail)
    assert "intangible_assets" in detail
    assert "garbage" in detail
