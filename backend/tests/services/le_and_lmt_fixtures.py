"""LE/LMT owning-suite fixtures: canonical seeder, return generation and Table 1 book.

Lives outside the test module so other suites can reuse these helpers without a
cross-suite test import.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
    RegulatoryPackage,
)
from app.reporting.schemas.packages import RegulatoryPackageCreate
from app.reporting.service import generation
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, DEMO_USER_ID, SAMPLE_BANK_ID

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
REPORTING_DATE = date(2026, 3, 31)


class _CanonicalSeeder:
    """Minimal canonical scaffold (batch + lineage + row builders) at as-of."""

    def __init__(
        self, db: Session, as_of: date = REPORTING_DATE, *, validation_status: str = "accepted"
    ) -> None:
        self.db = db
        batch = IngestionBatch(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=as_of,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=DEMO_ORG_ID,
            bank_id=batch.bank_id,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="w6-fixture",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        self.common: dict[str, Any] = {
            "organization_id": DEMO_ORG_ID,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": as_of,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": validation_status,
        }

    def counterparty(  # noqa: PLR0913 - keyword-only fixture builder
        self,
        ref: str,
        name: str,
        counterparty_type: str,
        *,
        group_reference: str | None = None,
        tin: str | None = None,
        rating: str | None = None,
        resident: bool | None = None,
    ) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            source_reference=ref,
            name=name,
            counterparty_type=counterparty_type,
            group_reference=group_reference,
            rating=rating,
            resident=resident,
            external_identifiers={"tin": tin} if tin else {},
        )
        self.db.add(row)
        self.db.flush()
        return row

    def product(self, code: str, regulatory_category: str | None) -> CanonicalProduct:
        row = CanonicalProduct(
            **self.common,
            source_reference=f"PRODUCT/{code}",
            product_code=code,
            name=code,
            regulatory_category=regulatory_category,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def position(  # noqa: PLR0913 - keyword-only fixture builder
        self,
        ref: str,
        position_type: str,
        balance_ghs: Decimal | str,
        *,
        counterparty: CanonicalCounterparty | None = None,
        product: CanonicalProduct | None = None,
        maturity: date | None = None,
        ifrs9_stage: int | None = None,
        currency: str = "GHS",
        interest_rate: Decimal | None = None,
        deposit_account_type: str | None = None,
        encumbered: bool | None = None,
        pledged_as_collateral: bool | None = None,
        operational_purpose: bool | None = None,
        redeemable_within_two_days: bool | None = None,
        extra_attributes: dict[str, Any] | None = None,
    ) -> None:
        position = CanonicalPosition(
            **self.common,
            source_reference=ref,
            position_type=position_type,
            currency=currency,
        )
        self.db.add(position)
        self.db.flush()
        attributes: dict[str, Any] = {"balance_ghs": str(balance_ghs)}
        if extra_attributes:
            attributes.update(extra_attributes)
        self.db.add(
            CanonicalPositionSnapshot(
                **self.common,
                source_reference=ref,
                position_id=position.id,
                counterparty_id=counterparty.id if counterparty is not None else None,
                product_id=product.id if product is not None else None,
                balance=Decimal(str(balance_ghs)),
                interest_rate=interest_rate,
                contractual_maturity=maturity,
                ifrs9_stage=ifrs9_stage,
                deposit_account_type=deposit_account_type,
                encumbered=encumbered,
                pledged_as_collateral=pledged_as_collateral,
                operational_purpose=operational_purpose,
                redeemable_within_two_days=redeemable_within_two_days,
                attributes=attributes,
            )
        )
        self.db.flush()


def _generate(db: Session, return_code: str) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=return_code, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _seed_table1_book(db: Session) -> None:
    """A book exercising every LMTD ¶5 Narrow/Broad classification leg.

    Narrow = 2.0 vault + 3.0 BoG + 1.0 operational correspondent + 4.0 AAA
    non-resident placement + 2.5 domestic-bank claim + 5.0 sovereign bill
    ≤1yr + 0.8 two-day redeemable = 18.3M. Broad adds the 6.0M GoG bond >1yr
    and equities capped at other-broad/9 (3.0M holding → 2.7M) = 27.0M.
    The encumbered 1.0M bill counts in neither. Volatile 10.0M; total
    deposits 27.0M; short-term 26.5M (by-nature 18.0 + 4.0 short fixed +
    3.0 interbank + 1.5 contingent); total assets 28.3M.
    """
    seeder = _CanonicalSeeder(db)
    bog = seeder.counterparty("CP/BOG", "Central Bank", "CENTRAL_BANK")
    corr_de = seeder.counterparty("CP/CORR-DE", "Frankfurt Corr Bank", "BANK_OECD", resident=False)
    aaa_bank = seeder.counterparty(
        "CP/AAA", "Zurich Prime Bank", "BANK_OECD", rating="AAA", resident=False
    )
    local_bank = seeder.counterparty("CP/GH-BANK", "Volta Bank", "BANK_NON_OECD", resident=True)
    tbill = seeder.product("SEC.GOG.TBILL", "SOVEREIGN_GOG_TBILL_0RW")
    bond = seeder.product("SEC.GOG.BOND", "SOVEREIGN_GOG_BOND_0RW")
    equity = seeder.product("SEC.GSE.EQ", "EQUITY_GSE_LISTED")

    seeder.position("CASH/VAULT", "CASH", Decimal("2000000"))  # leg (a)
    seeder.position("CASH/BOG", "CASH", Decimal("3000000"), counterparty=bog)  # leg (d)
    seeder.position(
        "CASH/CORR", "CASH", Decimal("1000000"), counterparty=corr_de, operational_purpose=True
    )  # leg (b)
    seeder.position("IBP/AAA", "INTERBANK_PLACEMENT", Decimal("4000000"), counterparty=aaa_bank)
    seeder.position("IBP/GH", "INTERBANK_PLACEMENT", Decimal("2500000"), counterparty=local_bank)
    seeder.position(
        "SEC/TBILL",
        "SECURITY_HOLDING",
        Decimal("5000000"),
        product=tbill,
        maturity=date(2026, 9, 30),
    )  # leg (e)
    seeder.position(
        "SEC/TBILL-ENC",
        "SECURITY_HOLDING",
        Decimal("1000000"),
        product=tbill,
        maturity=date(2026, 9, 30),
        encumbered=True,
    )  # excluded: encumbered
    seeder.position(
        "SEC/MDB",
        "SECURITY_HOLDING",
        Decimal("800000"),
        maturity=date(2026, 12, 31),
        redeemable_within_two_days=True,
    )  # leg (f)
    seeder.position(
        "SEC/GOGBOND",
        "SECURITY_HOLDING",
        Decimal("6000000"),
        product=bond,
        maturity=date(2030, 6, 30),
    )  # broad only
    seeder.position("SEC/EQ", "SECURITY_HOLDING", Decimal("3000000"), product=equity)
    seeder.position(
        "DEP/CUR", "DEPOSIT", Decimal("10000000"), deposit_account_type="CURRENT"
    )  # volatile + by-nature short-term
    seeder.position(
        "DEP/SAV", "DEPOSIT", Decimal("8000000"), deposit_account_type="SAVINGS"
    )  # stable, by-nature short-term
    seeder.position(
        "DEP/FIX-LONG",
        "DEPOSIT",
        Decimal("5000000"),
        deposit_account_type="FIXED",
        maturity=date(2028, 3, 31),
    )  # NOT short-term
    seeder.position(
        "DEP/FIX-SHORT",
        "DEPOSIT",
        Decimal("4000000"),
        deposit_account_type="FIXED",
        maturity=date(2026, 6, 30),
    )  # short-term by maturity
    seeder.position("IBB/1", "INTERBANK_BORROWING", Decimal("3000000"), maturity=date(2026, 4, 30))
    seeder.position(
        "LCG/ST",
        "LC_GUARANTEE",
        Decimal("0"),
        maturity=date(2026, 8, 31),
        extra_attributes={"notional_ghs": "1500000"},
    )  # contingent ≤ 1 yr
