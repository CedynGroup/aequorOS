"""A small, hand-readable banking book for the Standardised Framework tests.

Deliberately NOT the big canonical fixture: the SF service tests are about the
LOADER and the run lifecycle — which positions enter, which are excluded and
counted, which refuse the run — and a three-position book makes each of those
legible. The arithmetic itself is already pinned by the domain's eight golden
vectors, so nothing here asserts a magnitude the engine produced.

Everything is seeded at the sample bank's newest reporting date, because the
framework measures a position, not a period range.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

#: The newest period of the shared fixture's twelve-month spine.
AS_OF = date(2026, 3, 31)

#: A plain GHS zero curve, annually compounded decimal fractions as market data
#: carries them. Flat-ish and unremarkable: the curve's job in these tests is to
#: exist, not to produce a particular number.
GHS_CURVE: dict[int, str] = {
    1: "0.24",
    3: "0.245",
    6: "0.25",
    12: "0.255",
    24: "0.26",
    60: "0.265",
    120: "0.27",
}
USD_CURVE: dict[int, str] = {1: "0.05", 12: "0.055", 60: "0.06"}


def _meta(
    session: Session, *, as_of: date = AS_OF, source_system: str = "EXCEL_CSV"
) -> dict[str, Any]:
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="sf-book-fixture",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    return {
        "organization_id": DEMO_ORG_ID,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": as_of,
        "ingested_at": datetime(as_of.year, as_of.month, as_of.day, 18, 0, tzinfo=UTC),
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def seed_curve(
    session: Session, currency: str, rates: dict[int, str], *, as_of: date = AS_OF
) -> None:
    meta = _meta(session, as_of=as_of)
    curve = CanonicalYieldCurve(
        **meta,
        source_reference=f"SF/{currency}",
        currency=currency,
        curve_name=f"{currency}.SOVEREIGN",
        curve_type="sovereign",
    )
    session.add(curve)
    session.flush()
    for tenor_months, rate in rates.items():
        session.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"SF/{currency}/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    session.flush()


def seed_book(  # noqa: PLR0913 - one switch per condition the loader must handle
    session: Session,
    *,
    with_curve: bool = True,
    with_options: bool = False,
    with_usd: bool = False,
    with_usd_fx: bool = False,
    with_prepayment: bool = False,
    with_past_due: bool = False,
    with_wholesale_term_deposit: bool = False,
    as_of: date = AS_OF,
) -> UUID:
    """Seed the sample bank plus a three-position book. Returns the period id."""
    materialize_canonical_test_book(session)
    meta = _meta(session, as_of=as_of)
    # Reference rows are batch-scoped: they carry no supersession chain and no
    # validation status of their own, so they take a narrower column set than
    # the canonical entities.
    reference = {
        "organization_id": meta["organization_id"],
        "bank_id": meta["bank_id"],
        "as_of_date": meta["as_of_date"],
        "ingestion_batch_id": meta["ingestion_batch_id"],
        "lineage_id": meta["lineage_id"],
    }

    loan_product = CanonicalProduct(
        **meta,
        source_reference="PRODUCT/SF.LOAN",
        product_code="SF.LOAN",
        name="SF term loan",
        regulatory_category="CORPORATE_UNRATED",
    )
    deposit_product = CanonicalProduct(
        **meta,
        source_reference="PRODUCT/SF.CUR",
        product_code="SF.CUR",
        name="SF current account",
        regulatory_category=None,
    )
    session.add_all([loan_product, deposit_product])
    retail = CanonicalCounterparty(
        **meta,
        source_reference="CP/SF-RETAIL",
        name="Akosua Boateng",
        counterparty_type="RETAIL_INDIVIDUAL",
    )
    corporate = CanonicalCounterparty(
        **meta,
        source_reference="CP/SF-CORP",
        name="Sekondi Foods Ltd",
        counterparty_type="CORPORATE",
    )
    session.add_all([retail, corporate])
    session.flush()

    def position(  # noqa: PLR0913 - keyword-only fixture builder
        source_reference: str,
        position_type: str,
        currency: str,
        *,
        balance: str,
        rate: str | None = None,
        rate_type: str | None = None,
        maturity: date | None = None,
        product: CanonicalProduct | None = None,
        counterparty: CanonicalCounterparty | None = None,
        deposit_account_type: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        row = CanonicalPosition(
            **meta,
            source_reference=source_reference,
            position_type=position_type,
            currency=currency,
        )
        session.add(row)
        session.flush()
        session.add(
            CanonicalPositionSnapshot(
                **meta,
                source_reference=source_reference,
                position_id=row.id,
                product_id=None if product is None else product.id,
                counterparty_id=None if counterparty is None else counterparty.id,
                balance=Decimal(balance),
                interest_rate=None if rate is None else Decimal(rate),
                rate_type=rate_type,
                contractual_maturity=maturity,
                deposit_account_type=deposit_account_type,
                attributes=attributes or {},
            )
        )

    position(
        "SF/LOAN/1",
        "LOAN",
        "GHS",
        balance="1000000",
        rate="0.26",
        rate_type="FIXED",
        maturity=date(2031, 3, 31),
        product=loan_product,
        counterparty=corporate,
        attributes={"amortisation": "annuity", "payment_frequency_months": 12},
    )
    position(
        "SF/DEP/1",
        "DEPOSIT",
        "GHS",
        balance="800000",
        rate="0.08",
        rate_type="FIXED",
        product=deposit_product,
        counterparty=retail,
        deposit_account_type="CURRENT",
    )
    position(
        "SF/BORROW/1",
        "INTERBANK_BORROWING",
        "GHS",
        balance="200000",
        rate="0.22",
        rate_type="FIXED",
        maturity=date(2026, 9, 30),
    )
    # Always present, always excluded by rule and counted: the loader must be
    # able to say "we did not measure this" rather than drop it.
    position("SF/CASH/1", "CASH", "GHS", balance="50000")

    if with_past_due:
        # Matured six months before the reporting date and still on the book:
        # the projection has no remaining schedule to slot, so the principal
        # goes overnight. That placement must be TALLIED, not silent (R-3).
        position(
            "SF/LOAN/PASTDUE",
            "LOAN",
            "GHS",
            balance="300000",
            rate="0.29",
            rate_type="FIXED",
            maturity=as_of - timedelta(days=180),
            product=loan_product,
            counterparty=corporate,
        )
    if with_wholesale_term_deposit:
        # A corporate term deposit stating no early-withdrawal penalty: the
        # default kind for every non-retail term deposit, and the one the
        # engine prices as repayable on demand.
        position(
            "SF/DEPTERM/CORP",
            "DEPOSIT",
            "GHS",
            balance="600000",
            rate="0.19",
            rate_type="FIXED",
            maturity=date(as_of.year + 3, as_of.month, as_of.day),
            product=deposit_product,
            counterparty=corporate,
            deposit_account_type="FIXED",
        )
    if with_options:
        position(
            "SF/CAP/1",
            "DERIVATIVE",
            "GHS",
            balance="250000",
            attributes={"option_type": "cap"},
        )
    if with_usd:
        position(
            "SF/LOAN/USD",
            "LOAN",
            "USD",
            balance="100000",
            rate="0.07",
            rate_type="FIXED",
            maturity=date(2030, 3, 31),
            product=loan_product,
            counterparty=corporate,
        )
        seed_curve(session, "USD", USD_CURVE, as_of=as_of)
        if with_usd_fx:
            seed_fx(session, base="USD", quote="GHS", rate="15", as_of=as_of)
    if with_prepayment:
        session.add(
            CanonicalReferenceRow(
                **reference,
                source_reference="SF/BEHAVIOUR/1",
                dataset_kind="behavioral_assumptions",
                row_index=0,
                payload={
                    "assumption_type": "PREPAYMENT_RATE",
                    "product_code": "SF.LOAN",
                    "value": "0.10",
                },
            )
        )
    if with_curve:
        seed_curve(session, "GHS", GHS_CURVE, as_of=as_of)
    session.flush()

    period_id = session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == as_of,
        )
    )
    assert period_id is not None, f"the fixture spine has no period ending {as_of}"
    return period_id


def seed_fx(
    session: Session, *, base: str, quote: str, rate: str, as_of: date = AS_OF
) -> None:
    from app.models import CanonicalFxRate  # noqa: PLC0415 - fixture-local import

    meta = _meta(session, as_of=as_of)
    session.add(
        CanonicalFxRate(
            **meta,
            source_reference=f"SF/FX/{base}{quote}",
            base_currency=base,
            quote_currency=quote,
            rate=Decimal(rate),
            rate_type="spot",
        )
    )
    session.flush()
