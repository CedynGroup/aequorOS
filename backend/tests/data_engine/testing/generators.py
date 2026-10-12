"""Canonical records and provenance graphs scoped to one synthetic bank."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import strategies as st

from app.domain.ingestion.contracts import (
    CanonicalRecords,
    CounterpartyData,
    PositionData,
    ProductData,
)
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
)


@dataclass(frozen=True)
class CanonicalBook:
    records: CanonicalRecords
    batch: IngestionBatch
    lineage: LineageRecord
    counterparty: CanonicalCounterparty
    product: CanonicalProduct
    positions: tuple[CanonicalPosition, ...]
    snapshots: tuple[CanonicalPositionSnapshot, ...]


@st.composite
def canonical_books(draw: st.DrawFn) -> CanonicalBook:
    """Referentially complete loan books, all IDs synthetic and reproducible.

    Sources: domain/ingestion/contracts.py PositionData; models/canonical.py
    bank-composite foreign keys and NUMERIC(28,6) amounts / NUMERIC(18,10)
    fractional rates. Dates are synthetic month ends; loan amounts 10k–10m
    and 5–35% fractional rates are fixture assumptions, not regulatory bounds.
    Zero balances and reporting-date maturities are intentional valid edges.
    No persistence or provisioning route is invoked by this generator.
    """
    as_of = draw(st.sampled_from((date(2026, 3, 31), date(2026, 6, 30), date(2026, 12, 31))))
    org, bank = "OR-000000000001", "BK-000000000001"
    batch_id, lineage_id, cp_id, product_id = draw(
        st.lists(st.uuids(version=4), min_size=4, max_size=4, unique=True)
    )
    batch = IngestionBatch(
        id=batch_id,
        organization_id=org,
        bank_id=bank,
        source_system="EXCEL_CSV",
        adapter_version="synthetic-1",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    lineage = LineageRecord(
        id=lineage_id,
        organization_id=org,
        bank_id=bank,
        ingestion_batch_id=batch_id,
        operation_type="VALIDATION",
        operation_ref="synthetic-property-book",
        input_lineage_ids=[],
    )
    stamp = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
    cp = CanonicalCounterparty(
        id=cp_id,
        organization_id=org,
        bank_id=bank,
        ingestion_batch_id=batch_id,
        lineage_id=lineage_id,
        source_system="EXCEL_CSV",
        source_reference="CP/1",
        as_of_date=as_of,
        ingested_at=stamp,
        validation_status="accepted",
        name="Synthetic corporate borrower",
        counterparty_type="CORPORATE",
    )
    product = CanonicalProduct(
        id=product_id,
        organization_id=org,
        bank_id=bank,
        ingestion_batch_id=batch_id,
        lineage_id=lineage_id,
        source_system="EXCEL_CSV",
        source_reference="PRODUCT/1",
        as_of_date=as_of,
        ingested_at=stamp,
        validation_status="accepted",
        product_code="SYNTHETIC.LOAN",
        name="Synthetic performing term loan",
        regulatory_category="CORPORATE_UNRATED",
    )
    count = draw(st.integers(1, 12))
    ids = draw(st.lists(st.uuids(version=4), min_size=2 * count, max_size=2 * count, unique=True))
    positions: list[CanonicalPosition] = []
    snapshots: list[CanonicalPositionSnapshot] = []
    records = CanonicalRecords(
        counterparties=[
            CounterpartyData(
                source_reference="CP/1",
                source_locator="synthetic/CP/1",
                name=cp.name,
                counterparty_type="CORPORATE",
            )
        ],
        products=[
            ProductData(
                source_reference="PRODUCT/1",
                source_locator="synthetic/PRODUCT/1",
                product_code=product.product_code,
                name=product.name,
                regulatory_category="CORPORATE_UNRATED",
            )
        ],
    )
    for index in range(len(ids) // 2):
        ref = f"LOAN/{index}"
        balance = draw(
            st.one_of(st.just(Decimal("0.000000")), st.decimals(10_000, 10_000_000, places=6))
        )
        rate = draw(st.decimals("0.05", "0.35", places=10))
        maturity = as_of + timedelta(days=draw(st.one_of(st.just(0), st.integers(30, 1825))))
        row = PositionData(
            source_reference=ref,
            source_locator=f"synthetic/{ref}",
            position_type="LOAN",
            currency="GHS",
            balance=balance,
            counterparty_reference="CP/1",
            product_code="SYNTHETIC.LOAN",
            origination_date=as_of - timedelta(days=365),
            contractual_maturity=maturity,
            next_repricing_date=maturity,
            interest_rate=rate,
            rate_type="FIXED",
            ifrs9_stage=1,
        )
        records.positions.append(row)
        positions.append(
            CanonicalPosition(
                id=ids[index * 2],
                organization_id=org,
                bank_id=bank,
                ingestion_batch_id=batch_id,
                lineage_id=lineage_id,
                as_of_date=as_of,
                ingested_at=stamp,
                source_system="EXCEL_CSV",
                source_reference=ref,
                validation_status="accepted",
                position_type=row.position_type,
                currency=row.currency,
                origination_date=row.origination_date,
            )
        )
        snapshots.append(
            CanonicalPositionSnapshot(
                id=ids[index * 2 + 1],
                organization_id=org,
                bank_id=bank,
                ingestion_batch_id=batch_id,
                lineage_id=lineage_id,
                as_of_date=as_of,
                ingested_at=stamp,
                source_system="EXCEL_CSV",
                source_reference=ref,
                validation_status="accepted",
                position_id=positions[-1].id,
                counterparty_id=cp_id,
                product_id=product_id,
                balance=balance,
                interest_rate=rate,
                rate_type="FIXED",
                contractual_maturity=maturity,
                next_repricing_date=maturity,
                ifrs9_stage=1,
            )
        )
    batch.records_extracted = records.record_count
    batch.records_translated = records.record_count
    batch.records_accepted = records.record_count
    batch.records_warning = batch.records_error = batch.records_blocked = 0
    batch.started_at = stamp
    batch.completed_at = stamp + timedelta(seconds=1)
    return CanonicalBook(records, batch, lineage, cp, product, tuple(positions), tuple(snapshots))
