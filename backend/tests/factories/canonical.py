"""Compact canonical fixture for fact-derivation and activation tests.

Builds a few dozen canonical rows via the models directly (no file ingestion):
every position type, GL chart, products with regulatory categories, and every
reference dataset kind the derivation consumes. Deterministic amounts are
chosen so the derived facts have hand-checkable aggregates:

- loans_gross = 84.85M GHS across six GHS loans and one USD loan
- deposits    = 80.57M GHS across retail/wholesale/term/FX products
- securities  = 35M GHS (15M bill + 20M bond)
- capital     = 45M GHS (40M CET1 - 5M deduction + 10M T2)
- FX          = one long USD book (12.85M loan vs 2.57M deposit)

The GL asset residual (placements + fixed assets; the provision contra sits in
the loan GL range and is excluded with it) is 12M, leaving a deliberate funding
shortfall that exercises the balance plug.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
)

FIXTURE_AS_OF = date(2026, 6, 30)

# (code, class, balance)
_GL_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("1001", "ASSET", "5000000"),
    ("1002", "ASSET", "8000000"),
    ("1003", "ASSET", "4000000"),
    ("1101", "ASSET", "5000000"),
    ("1201", "ASSET", "8000000"),
    ("1204", "ASSET", "6000000"),
    ("1301", "ASSET", "60000000"),
    ("1399", "ASSET", "-2000000"),
    ("1401", "ASSET", "7000000"),
    ("2001", "LIABILITY", "80000000"),
    ("3001", "EQUITY", "45000000"),
)
_GL_NAMES = {
    "1001": "Cash on Hand",
    "1002": "Balances with BoG - Statutory Reserves",
    "1003": "Balances with BoG - Other",
    "1101": "Interbank Placements - Local",
    "1201": "Government of Ghana T-Bills (91d)",
    "1204": "Government of Ghana Bonds (2y)",
    "1301": "Loans to Customers",
    "1399": "Loan Loss Provisions (contra)",
    "1401": "Property, Plant and Equipment",
    "2001": "Customer Deposits",
    "3001": "Paid-in Share Capital",
}

# (product_code, regulatory_category)
_PRODUCTS: tuple[tuple[str, str | None], ...] = (
    ("LN.CORP.5Y", "CORPORATE_UNRATED"),
    ("LN.RET.PERS", "RETAIL_UNSECURED"),
    ("LN.RET.MORT", "RESIDENTIAL_MORTGAGE"),
    ("LN.SME.TERM", "SME_UNRATED"),
    ("DEP.RET.CUR", None),
    ("DEP.RET.SAV", None),
    ("DEP.RET.TRM", None),
    ("DEP.CORP.CUR", None),
    ("DEP.CORP.TRM", None),
    ("DEP.USD.SAV", None),
    ("SEC.TBILL.91", "SOVEREIGN_LOCAL_CCY"),
    ("SEC.BOND.5Y", "SOVEREIGN_LOCAL_CCY"),
)

# Expected hand-checked aggregates the tests assert against.
EXPECTED_LOANS_GROSS = Decimal("84850000")
EXPECTED_CAPITAL_TOTAL = Decimal("45000000")
EXPECTED_SECURITIES_BILLS = Decimal("15000000")
EXPECTED_SECURITIES_BONDS = Decimal("20000000")
EXPECTED_FX_NET_LONG = Decimal("10280000")
EXPECTED_FX_NET_SHORT = Decimal("0")
FX_HISTORY_DAYS = 150

# Hedge/swap overlay (seed_hedge_and_swap_positions): two hedges sell 700k USD
# (delta -8,995,000 GHS at the 12.85 spot), so the raw +10.28M USD long
# (29.4% of the fixture's 35M Tier 1 — a breach) lands at +1.285M (3.7%).
HEDGE_USD_SOLD = Decimal("700000")
EXPECTED_POST_HEDGE_USD_NET = Decimal("1285000")
SWAP_NOTIONAL_GHS = Decimal("20000000")


def seed_canonical_fixture(  # noqa: PLR0915 - one linear, readable fixture script
    session: Session, *, organization_id: UUID, bank_id: UUID, as_of: date = FIXTURE_AS_OF
) -> None:
    """Insert the full canonical fixture for one bank at ``as_of``."""
    bank = session.get(Bank, bank_id)
    assert bank is not None, "seed the bank before the canonical fixture"

    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=organization_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="canonical-fixture",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()

    common = {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "as_of_date": as_of,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }

    for code, account_class, balance in _GL_ACCOUNTS:
        session.add(
            CanonicalGlAccount(
                **common,
                source_reference=f"GL/{code}",
                account_code=code,
                name=_GL_NAMES[code],
                account_class=account_class,
                currency="GHS",
                balance=Decimal(balance),
            )
        )

    products: dict[str, CanonicalProduct] = {}
    for product_code, category in _PRODUCTS:
        product = CanonicalProduct(
            **common,
            source_reference=f"PRODUCT/{product_code}",
            product_code=product_code,
            name=product_code,
            regulatory_category=category,
        )
        session.add(product)
        products[product_code] = product

    retail_cp = CanonicalCounterparty(
        **common,
        source_reference="CP/RETAIL-1",
        name="Ama Mensah",
        counterparty_type="RETAIL_INDIVIDUAL",
    )
    corporate_cp = CanonicalCounterparty(
        **common,
        source_reference="CP/CORP-1",
        name="Volta Agro Ltd",
        counterparty_type="CORPORATE",
    )
    session.add_all([retail_cp, corporate_cp])
    session.flush()

    def position(  # noqa: PLR0913 - keyword-only fixture builder
        source_reference: str,
        position_type: str,
        currency: str,
        *,
        balance: str,
        balance_ghs: str | None = None,
        rate: str | None = None,
        rate_type: str | None = None,
        maturity: date | None = None,
        next_repricing: date | None = None,
        stage: int | None = None,
        product: str | None = None,
        counterparty: CanonicalCounterparty | None = None,
        branch: str | None = None,
        extra_attributes: dict[str, Any] | None = None,
        validation_status: str = "accepted",
        superseded_by: UUID | None = None,
    ) -> None:
        row = CanonicalPosition(
            **common,
            source_reference=source_reference,
            position_type=position_type,
            currency=currency,
        )
        session.add(row)
        session.flush()
        attributes: dict[str, Any] = {}
        if balance_ghs is not None:
            attributes["balance_ghs"] = balance_ghs
        if branch is not None:
            attributes["branch_id"] = branch
        if extra_attributes:
            attributes.update(extra_attributes)
        snapshot_fields = {**common, "validation_status": validation_status}
        session.add(
            CanonicalPositionSnapshot(
                **snapshot_fields,
                source_reference=source_reference,
                position_id=row.id,
                product_id=products[product].id if product else None,
                counterparty_id=counterparty.id if counterparty else None,
                balance=Decimal(balance),
                interest_rate=Decimal(rate) if rate else None,
                rate_type=rate_type,
                contractual_maturity=maturity,
                next_repricing_date=next_repricing,
                ifrs9_stage=stage,
                superseded_by=superseded_by,
                attributes=attributes,
            )
        )

    # --- loans (Σ balance_ghs = 84.85M) ---------------------------------
    position(
        "LOAN/1",
        "LOAN",
        "GHS",
        balance="30000000",
        balance_ghs="30000000",
        rate="0.26",
        rate_type="FIXED",
        maturity=date(2029, 6, 30),
        stage=1,
        product="LN.CORP.5Y",
        counterparty=corporate_cp,
        branch="BR-001",
        extra_attributes={"ecl_provision_ghs": "300000"},
    )
    position(
        "LOAN/2",
        "LOAN",
        "GHS",
        balance="10000000",
        balance_ghs="10000000",
        rate="0.28",
        rate_type="FLOATING",
        maturity=date(2028, 6, 30),
        next_repricing=date(2026, 8, 15),
        stage=1,
        product="LN.CORP.5Y",
        branch="BR-002",
        extra_attributes={"ecl_provision_ghs": "100000"},
    )
    position(
        "LOAN/3",
        "LOAN",
        "GHS",
        balance="8000000",
        balance_ghs="8000000",
        rate="0.31",
        rate_type="FIXED",
        maturity=date(2026, 7, 20),
        stage=1,
        product="LN.RET.PERS",
        counterparty=retail_cp,
        branch="BR-002",
        extra_attributes={"ecl_provision_ghs": "80000"},
    )
    position(
        "LOAN/4",
        "LOAN",
        "GHS",
        balance="12000000",
        balance_ghs="12000000",
        rate="0.25",
        rate_type="FLOATING",
        maturity=date(2040, 1, 1),
        next_repricing=date(2026, 10, 1),
        stage=1,
        product="LN.RET.MORT",
        branch="BR-001",
        extra_attributes={"ecl_provision_ghs": "60000"},
    )
    position(
        "LOAN/5",
        "LOAN",
        "GHS",
        balance="9000000",
        balance_ghs="9000000",
        rate="0.32",
        rate_type="FIXED",
        maturity=date(2027, 3, 31),
        stage=1,
        product="LN.SME.TERM",
        branch="BR-002",
        extra_attributes={"ecl_provision_ghs": "120000"},
    )
    position(
        "LOAN/6",
        "LOAN",
        "GHS",
        balance="3000000",
        balance_ghs="3000000",
        rate="0.31",
        rate_type="FIXED",
        maturity=date(2027, 1, 31),
        stage=3,
        product="LN.RET.PERS",
        branch="BR-001",
        extra_attributes={"ecl_provision_ghs": "900000"},
    )
    position(
        "LOAN/USD",
        "LOAN",
        "USD",
        balance="1000000",
        balance_ghs="12850000",
        rate="0.10",
        rate_type="FLOATING",
        maturity=date(2028, 6, 30),
        next_repricing=date(2026, 9, 30),
        stage=1,
        product="LN.CORP.5Y",
        counterparty=corporate_cp,
        branch="BR-001",
        extra_attributes={"ecl_provision_ghs": "0"},
    )
    # Superseded and error-status snapshots must be excluded from every total.
    position(
        "LOAN/OLD",
        "LOAN",
        "GHS",
        balance="999000000",
        balance_ghs="999000000",
        rate="0.30",
        rate_type="FIXED",
        maturity=date(2029, 1, 1),
        stage=1,
        product="LN.CORP.5Y",
        superseded_by=uuid4(),
    )
    position(
        "LOAN/BAD",
        "LOAN",
        "GHS",
        balance="888000000",
        balance_ghs="888000000",
        rate="0.30",
        rate_type="FIXED",
        maturity=date(2029, 1, 1),
        stage=1,
        product="LN.CORP.5Y",
        validation_status="error",
    )

    # --- deposits (Σ balance_ghs = 80.57M) ------------------------------
    position(
        "DEP/1",
        "DEPOSIT",
        "GHS",
        balance="25000000",
        balance_ghs="25000000",
        rate="0",
        product="DEP.RET.CUR",
        counterparty=retail_cp,
        branch="BR-001",
    )
    position(
        "DEP/2",
        "DEPOSIT",
        "GHS",
        balance="20000000",
        balance_ghs="20000000",
        rate="0.08",
        rate_type="FIXED",
        product="DEP.RET.SAV",
        branch="BR-002",
    )
    position(
        "DEP/3",
        "DEPOSIT",
        "GHS",
        balance="15000000",
        balance_ghs="15000000",
        rate="0.03",
        rate_type="FIXED",
        product="DEP.CORP.CUR",
        counterparty=corporate_cp,
        branch="BR-001",
    )
    position(
        "DEP/4",
        "DEPOSIT",
        "GHS",
        balance="10000000",
        balance_ghs="10000000",
        rate="0.19",
        rate_type="FIXED",
        maturity=date(2026, 9, 30),
        next_repricing=date(2026, 9, 30),
        product="DEP.CORP.TRM",
        branch="BR-002",
    )
    position(
        "DEP/5",
        "DEPOSIT",
        "GHS",
        balance="8000000",
        balance_ghs="8000000",
        rate="0.17",
        rate_type="FIXED",
        maturity=date(2026, 12, 31),
        next_repricing=date(2026, 12, 31),
        product="DEP.RET.TRM",
        branch="BR-001",
    )
    position(
        "DEP/USD",
        "DEPOSIT",
        "USD",
        balance="200000",
        balance_ghs="2570000",
        rate="0.03",
        rate_type="FIXED",
        product="DEP.USD.SAV",
        branch="BR-002",
    )

    # --- securities / interbank / off-balance ---------------------------
    position(
        "SEC/1",
        "SECURITY_HOLDING",
        "GHS",
        balance="15000000",
        balance_ghs="15000000",
        rate="0.18",
        rate_type="FIXED",
        maturity=date(2026, 8, 29),
        product="SEC.TBILL.91",
        extra_attributes={"issuer": "Government of Ghana"},
    )
    position(
        "SEC/2",
        "SECURITY_HOLDING",
        "GHS",
        balance="20000000",
        balance_ghs="20000000",
        rate="0.22",
        rate_type="FIXED",
        maturity=date(2029, 6, 30),
        product="SEC.BOND.5Y",
        extra_attributes={"issuer": "Government of Ghana"},
    )
    position(
        "IBP/1",
        "INTERBANK_PLACEMENT",
        "GHS",
        balance="5000000",
        balance_ghs="5000000",
        rate="0.14",
        rate_type="FIXED",
        maturity=date(2026, 7, 15),
    )
    position(
        "IBB/1",
        "INTERBANK_BORROWING",
        "GHS",
        balance="6000000",
        balance_ghs="6000000",
        rate="0.15",
        rate_type="FIXED",
        maturity=date(2026, 8, 29),
    )
    position(
        "LC/1",
        "LC_GUARANTEE",
        "USD",
        balance="100000",
        counterparty=corporate_cp,
        extra_attributes={
            "notional_ghs": "2000000",
            "credit_conversion_factor": "0.2",
            "credit_equivalent_ghs": "400000",
        },
    )

    _seed_reference_rows(session, common, batch.id, as_of)
    session.flush()


def seed_hedge_and_swap_positions(
    session: Session, *, organization_id: UUID, bank_id: UUID, as_of: date = FIXTURE_AS_OF
) -> None:
    """Overlay an FX hedge book and a pay-fixed IRS on the canonical fixture.

    Kept separate from ``seed_canonical_fixture`` so the base fixture's raw
    (unhedged) FX breach stays assertable; tests that want the hedged book
    call both.
    """
    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=organization_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="hedge-swap-fixture",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()

    common = {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "as_of_date": as_of,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }

    def hedge_or_swap(  # noqa: PLR0913 - keyword-only fixture builder
        source_reference: str,
        position_type: str,
        currency: str,
        *,
        balance: str,
        maturity: date,
        attributes: dict[str, Any],
    ) -> None:
        row = CanonicalPosition(
            **common,
            source_reference=source_reference,
            position_type=position_type,
            currency=currency,
        )
        session.add(row)
        session.flush()
        session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=source_reference,
                position_id=row.id,
                balance=Decimal(balance),
                notional=Decimal(balance),
                contractual_maturity=maturity,
                attributes=attributes,
            )
        )

    # Effective forward: sells 600k USD against GHS.
    hedge_or_swap(
        "HEDGE/1",
        "FX_HEDGE",
        "USD",
        balance="600000",
        maturity=as_of + timedelta(days=90),
        attributes={
            "hedge_id": "FXH-T-001",
            "instrument": "FORWARD",
            "currency_pair": "USD/GHS",
            "buy_currency": "GHS",
            "sell_currency": "USD",
            "notional_currency": "USD",
            "contract_rate": "13.0",
            "mtm_ghs": "250000",
            "prospective_r2": "0.94",
            "dollar_offset_ratio": "1.02",
        },
    )
    # Ineffective option (R^2 below 0.80): still sells 100k USD economically.
    hedge_or_swap(
        "HEDGE/2",
        "FX_HEDGE",
        "USD",
        balance="100000",
        maturity=as_of + timedelta(days=45),
        attributes={
            "hedge_id": "FXH-T-002",
            "instrument": "OPTION",
            "currency_pair": "USD/GHS",
            "buy_currency": "GHS",
            "sell_currency": "USD",
            "notional_currency": "USD",
            "contract_rate": "12.9",
            "mtm_ghs": "-20000",
            "prospective_r2": "0.72",
            "dollar_offset_ratio": "0.95",
        },
    )
    # Pay-fixed IRS: 20M GHS, remaining tenor 3y (1095 days -> the 1-3y bucket).
    hedge_or_swap(
        "SWAP/1",
        "INTEREST_RATE_SWAP",
        "GHS",
        balance="20000000",
        maturity=as_of + timedelta(days=1095),
        attributes={
            "swap_id": "IRS-T-001",
            "direction": "PAY_FIXED",
            "notional_ghs": "20000000",
            "pay_rate_pct": "25.3",
            "receive_index": "91D_TBILL",
            "tenor_years": "3",
            "mtm_ghs": "800000",
        },
    )
    session.flush()


def _seed_reference_rows(
    session: Session, common: dict[str, Any], batch_id: UUID, as_of: date
) -> None:
    rows: list[tuple[str, dict[str, Any]]] = [
        ("institution", {"institution_id": "FIX-GH-001", "institution_name": "Fixture Bank"}),
        (
            "business_units",
            {"business_unit_id": "BR-001", "business_unit_name": "Head Office"},
        ),
        ("business_units", {"business_unit_id": "BR-002", "business_unit_name": "Osu"}),
        (
            "capital_structure",
            {"capital_component": "CET1_SHARE_CAPITAL", "amount_ghs": "40000000", "tier": "CET1"},
        ),
        (
            "capital_structure",
            {
                "capital_component": "REGULATORY_ADJ_GOODWILL",
                "amount_ghs": "-5000000",
                "tier": "CET1_DEDUCTION",
            },
        ),
        (
            "capital_structure",
            {
                "capital_component": "TIER2_SUBORDINATED_DEBT",
                "amount_ghs": "10000000",
                "tier": "TIER2",
            },
        ),
    ]
    for product_code, stability in (
        ("DEP.RET.CUR", "0.7"),
        ("DEP.RET.SAV", "0.75"),
        ("DEP.CORP.CUR", "0.3"),
    ):
        rows.append(
            (
                "behavioral_assumptions",
                {
                    "assumption_type": "DEPOSIT_STABILITY",
                    "product_code": product_code,
                    "value": stability,
                    "unit": "FRACTION",
                },
            )
        )
    for product_code, months in (
        ("DEP.RET.CUR", "36"),
        ("DEP.RET.SAV", "48"),
        ("DEP.CORP.CUR", "12"),
    ):
        rows.append(
            (
                "behavioral_assumptions",
                {
                    "assumption_type": "NMD_DURATION",
                    "product_code": product_code,
                    "value": months,
                    "unit": "MONTHS",
                },
            )
        )
    for months, rate in (
        ("1", "0.14"),
        ("3", "0.155"),
        ("6", "0.17"),
        ("12", "0.185"),
        ("24", "0.195"),
        ("36", "0.205"),
        ("60", "0.22"),
        ("120", "0.24"),
    ):
        rows.append(
            (
                "yield_curve",
                {
                    "curve_name": "GHS_SOVEREIGN",
                    "currency": "GHS",
                    "tenor_months": months,
                    "rate": rate,
                },
            )
        )
    rows.append(
        ("fx_rates_current", {"currency": "USD", "quote_currency": "GHS", "spot_rate": "12.85"})
    )
    rows.append(
        ("fx_rates_current", {"currency": "GHS", "quote_currency": "GHS", "spot_rate": "1"})
    )
    # 150 business-ish days of USD/GHS spots: a gentle drift with a wiggle.
    spot = Decimal("11.00")
    for day_index in range(FX_HISTORY_DAYS):
        spot += Decimal("0.01") if day_index % 3 else Decimal("-0.005")
        rows.append(
            (
                "fx_rates_historical",
                {
                    "date": (as_of - timedelta(days=FX_HISTORY_DAYS - day_index)).isoformat(),
                    "currency": "USD",
                    "quote_currency": "GHS",
                    "spot_rate": str(spot),
                },
            )
        )
    # 36 month-end financials: 2M net interest + 0.5M fees per month, so every
    # trailing 12-month window sums to a 30M gross income.
    year, month = as_of.year, as_of.month
    ends: list[date] = []
    for _ in range(36):
        ends.append(date(year, month, monthrange(year, month)[1]))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    for period_end in reversed(ends):
        rows.append(
            (
                "historical_financials",
                {
                    "period_end": period_end.isoformat(),
                    "net_interest_income_ghs": "2000000",
                    "non_interest_income_ghs": "500000",
                },
            )
        )

    for index, (kind, payload) in enumerate(rows):
        session.add(
            CanonicalReferenceRow(
                organization_id=common["organization_id"],
                bank_id=common["bank_id"],
                ingestion_batch_id=batch_id,
                as_of_date=as_of,
                dataset_kind=kind,
                row_index=index,
                payload=payload,
                source_reference=f"fixture#{kind}!{index}",
                lineage_id=common["lineage_id"],
            )
        )
