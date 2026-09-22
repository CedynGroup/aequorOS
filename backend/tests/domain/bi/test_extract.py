"""``app.domain.bi.extract``: mart rows from plain objects, hand-computed.

Every expectation below is written out from the contract (``bi_contracts.md``)
and the rules the extractor mirrors — never echoed from the module. The
stand-ins are dataclasses, not ORM rows: the extractor is pure and must work on
anything that carries the fields. Generation is the CALLER's job (every
snapshot passed here is assumed current and included); that is asserted by
documentation, not by a check the extractor cannot make.

Currencies are fictional ISO-shaped codes: the reporting currency is whatever
the caller passes, never a literal the platform knows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain.bi import extract
from app.domain.bi.extract import (
    MATURITY_BUCKETS,
    EngineMetricFactRow,
    SnapshotMatch,
    engine_metric_row,
    live_metric_rows,
    loan_event_row,
    official_run_rows,
    position_row,
)
from app.domain.capital.loan_classification import ClassifiedLoan
from app.domain.liquidity.ladder import LADDER_HORIZON_DAYS

BASE = "XRC"  # the tenant's reporting currency (fictional)
FOREIGN = "XFC"  # a foreign currency with no ingested conversion (fictional)
AS_OF = date(2026, 6, 30)
ORG = "OR-TEST0001"
BANK = "BK-TEST0001"


@dataclass(frozen=True)
class Snapshot:
    id: UUID = field(default_factory=uuid4)
    organization_id: str = ORG
    bank_id: str = BANK
    as_of_date: date = AS_OF
    position_id: UUID = field(default_factory=uuid4)
    counterparty_id: UUID | None = None
    ingestion_batch_id: UUID | None = None
    balance: Decimal | None = Decimal("0")
    notional: Decimal | None = None
    interest_rate: Decimal | None = None
    rate_type: str | None = None
    rate_index: str | None = None
    contractual_maturity: date | None = None
    next_repricing_date: date | None = None
    ifrs9_stage: int | None = None
    encumbered: bool | None = None
    deposit_account_type: str | None = None
    behavioral_maturity_months: int | None = None
    attributes: dict[str, Any] | None = None


@dataclass(frozen=True)
class Position:
    position_type: str
    currency: str
    source_system: str = "CORE"
    source_reference: str = "ACC-1"
    origination_date: date | None = None


@dataclass(frozen=True)
class Counterparty:
    counterparty_type: str | None = "CORPORATE"
    group_reference: str | None = None


@dataclass(frozen=True)
class Product:
    product_code: str = "P-1"
    regulatory_category: str | None = None


@dataclass(frozen=True)
class GlAccount:
    account_code: str = "1100"


@dataclass(frozen=True)
class Event:
    id: UUID = field(default_factory=uuid4)
    organization_id: str = ORG
    bank_id: str = BANK
    source_system: str = "CORE"
    source_reference: str = "EVT-1"
    event_type: str = "REPAYMENT"
    event_subtype: str | None = None
    event_date: date = AS_OF
    position_source_reference: str = "ACC-1"
    amount: Decimal = Decimal("100")
    currency: str = BASE
    amount_ghs: Decimal | None = None


@dataclass(frozen=True)
class Live:
    module: str
    metrics: dict[str, Any]
    organization_id: str = ORG
    bank_id: str = BANK
    status: str = "green"
    source_as_of_date: date = AS_OF
    source_fact_period_id: UUID | None = None
    computed_from_input_hash: str | None = "abc123"
    engine_version: str = "engine-v1"
    pipeline_state: str = "ready"
    computed_at: datetime = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Run:
    module: str
    metrics: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    organization_id: str = ORG
    bank_id: str = BANK
    status: str = "succeeded"
    reporting_period_id: UUID = field(default_factory=uuid4)
    input_hash: str = "def456"
    engine_version: str = "engine-v1"
    completed_at: datetime | None = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


OLEM = ClassifiedLoan(
    grade="olem",
    exposure_ghs=Decimal("0"),
    provision_required_ghs=Decimal("0"),
    non_performing=False,
    classification_basis="days_past_due",
)
SUBSTANDARD = ClassifiedLoan(
    grade="substandard",
    exposure_ghs=Decimal("250000.5"),
    provision_required_ghs=Decimal("62500.125"),
    non_performing=True,
    classification_basis="days_past_due",
)


# --- vocabularies ------------------------------------------------------------------


def test_maturity_buckets_are_derived_from_the_ladder_horizons() -> None:
    assert LADDER_HORIZON_DAYS == (30, 91, 182, 365, None)
    assert [b.code for b in MATURITY_BUCKETS] == [
        "0_30d",
        "31_91d",
        "92_182d",
        "183_365d",
        "over_365d",
    ]
    assert [b.label for b in MATURITY_BUCKETS] == [
        "Up to 30 days",
        "31–91 days",
        "92–182 days",
        "183–365 days",
        "Over 365 days",
    ]
    assert [(b.minimum_days, b.maximum_days) for b in MATURITY_BUCKETS] == [
        (0, 30),
        (31, 91),
        (92, 182),
        (183, 365),
        (366, None),
    ]


# --- position rows: the two FX rules ------------------------------------------------


def test_foreign_currency_loan_without_conversion_follows_both_fx_rules() -> None:
    snapshot = Snapshot(
        balance=Decimal("1000000"),
        interest_rate=Decimal("0.185"),
        rate_type="FLOATING",
        rate_index="91d_tbill",
        next_repricing_date=date(2026, 7, 15),
        contractual_maturity=date(2028, 6, 30),
        ifrs9_stage=2,
        behavioral_maturity_months=6,
        counterparty_id=uuid4(),
        ingestion_batch_id=uuid4(),
        attributes={
            "days_past_due": "45",
            "ecl_provision_ghs": "20000",
            "interest_in_suspense_ghs": "",
            "sector": "  ",
            "industry": "Agriculture",
            "branch_id": " BR-01 ",
            "restructured": "yes",
            "crm_collateral_ghs": "500000",
            "crm_collateral_class": "RESIDENTIAL",
            "employer": "Acme Ltd",
        },
    )
    position = Position("LOAN", FOREIGN, origination_date=date(2024, 3, 15))
    row = position_row(
        snapshot,
        position,
        Counterparty("SME", "GRP-9"),
        Product("LN-SME", "SME_RETAIL"),
        GlAccount("1310"),
        base_currency=BASE,
        classified=OLEM,
    )

    # Derivation rule: no reporting-currency balance, counted.
    assert row.balance_native == Decimal("1000000")
    assert row.balance_rc is None
    assert row.fx_unconverted is True
    # Classification rule: in the book at zero.
    assert row.classification_exposure_rc == Decimal("0")
    assert row.notional_rc is None

    assert row.currency == FOREIGN
    assert row.days_past_due == 45
    assert row.dpd_band == "30_59"
    assert row.exposure_category == "sme_retail"
    assert row.product_family == "sme_loans"
    # FLOATING → next repricing date: 15 days from 30 June → 8-30d.
    assert row.repricing_bucket == "8-30d"
    # 30 Jun 2026 → 30 Jun 2028 is 731 days → the open-ended ladder bucket.
    assert row.maturity_bucket == "over_365d"
    assert row.vintage_month == date(2024, 3, 1)
    assert row.origination_date == date(2024, 3, 15)

    assert row.grade == "olem"
    assert row.non_performing is False
    assert row.classification_basis == "days_past_due"
    assert row.provision_required_rc == Decimal("0")
    assert row.provision_held_rc == Decimal("20000")
    assert row.interest_in_suspense_rc is None  # blank is unstated, not zero
    assert row.collateral_rc == Decimal("500000")
    assert row.collateral_type == "RESIDENTIAL"  # crm_collateral_class fallback
    assert row.restructured is True
    assert row.behavioral_maturity_months == Decimal("6")

    assert row.branch_code == " BR-01 "  # verbatim: the dimension resolves it
    assert row.sector == "Agriculture"  # blank sector falls through to industry
    assert row.employer == "Acme Ltd"
    assert row.product_code == "LN-SME"
    assert row.counterparty_id == snapshot.counterparty_id
    assert row.counterparty_type == "SME"
    assert row.counterparty_group == "GRP-9"
    assert row.gl_account_code == "1310"
    assert row.ingestion_batch_id == snapshot.ingestion_batch_id
    assert row.snapshot_id == snapshot.id
    assert row.position_id == snapshot.position_id
    assert (row.organization_id, row.bank_id, row.as_of_date) == (ORG, BANK, AS_OF)
    assert (row.source_system, row.source_reference) == ("CORE", "ACC-1")


def test_base_currency_loan_needs_no_conversion_and_stage_three_is_past_due() -> None:
    snapshot = Snapshot(
        balance=Decimal("250000.5"),
        notional=Decimal("300000"),
        rate_type="FIXED",
        next_repricing_date=date(2026, 7, 15),
        contractual_maturity=date(2026, 9, 30),
        ifrs9_stage=3,
        attributes={"days_past_due": 120, "restructured": "no", "sector": "Retail trade"},
    )
    position = Position("LOAN", BASE, origination_date=date(2023, 11, 2))
    row = position_row(
        snapshot,
        position,
        None,
        Product("LN-MTG", "RESIDENTIAL_MORTGAGE"),
        None,
        base_currency=BASE,
        classified=SUBSTANDARD,
    )
    assert row.balance_rc == Decimal("250000.5")
    assert row.fx_unconverted is False
    assert row.classification_exposure_rc == Decimal("250000.5")
    assert row.notional_rc == Decimal("300000")  # base currency: own notional
    assert row.days_past_due == 120
    assert row.dpd_band == "90_179"
    # Stage 3 overrides the product's class for the exposure category …
    assert row.exposure_category == "past_due_90"
    # … but the product family is the product's own, not the delinquency's.
    assert row.product_family == "mortgages"
    # FIXED → contractual maturity: 92 days → 3-6m, and the ladder's 92-182d.
    assert row.repricing_bucket == "3-6m"
    assert row.maturity_bucket == "92_182d"
    assert row.vintage_month == date(2023, 11, 1)
    assert row.grade == "substandard"
    assert row.non_performing is True
    assert row.provision_required_rc == Decimal("62500.125")
    assert row.restructured is False
    assert row.sector == "Retail trade"
    assert row.counterparty_type is None
    assert row.counterparty_group is None
    assert row.gl_account_code is None
    assert row.collateral_rc is None
    assert row.collateral_type is None
    assert row.employer is None


def test_foreign_currency_loan_with_ingested_conversion_uses_it_for_both_rules() -> None:
    snapshot = Snapshot(
        balance=Decimal("100"),
        attributes={"balance_ghs": "1250.75", "notional_ghs": "1300"},
    )
    row = position_row(
        snapshot,
        Position("LOAN", FOREIGN),
        None,
        Product("LN-C", "CORPORATE_UNRATED"),
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.balance_rc == Decimal("1250.75")
    assert row.fx_unconverted is False
    assert row.classification_exposure_rc == Decimal("1250.75")
    assert row.notional_rc == Decimal("1300")
    assert row.exposure_category == "corporate_unrated"
    assert row.product_family == "corporate_loans"
    # No classification passed: the four classification columns stay NULL.
    assert (row.grade, row.non_performing, row.classification_basis) == (None, None, None)
    assert row.provision_required_rc is None


def test_non_loan_position_has_no_classification_columns_even_when_one_is_passed() -> None:
    snapshot = Snapshot(
        balance=Decimal("5000"),
        deposit_account_type="CURRENT",
        interest_rate=Decimal("0.02"),
        attributes={"balance_ghs": "1500.25", "days_past_due": "3"},
    )
    row = position_row(
        snapshot,
        Position("DEPOSIT", FOREIGN, source_reference="DEP-7"),
        Counterparty("RETAIL_INDIVIDUAL"),
        Product("DEP-CUR"),
        None,
        base_currency=BASE,
        classified=SUBSTANDARD,
    )
    assert row.balance_rc == Decimal("1500.25")
    assert row.fx_unconverted is False
    assert row.classification_exposure_rc is None
    assert row.exposure_category is None
    assert (row.grade, row.non_performing, row.classification_basis) == (None, None, None)
    assert row.provision_required_rc is None
    assert row.product_family == "demand_deposits"
    # Undated demand-natured deposit → the shortest ladder bucket.
    assert row.maturity_bucket == "0_30d"
    assert row.repricing_bucket is None  # no maturity and no repricing date
    # DPD is read verbatim even on a deposit; the band is a fact about the number.
    assert row.days_past_due == 3
    assert row.dpd_band == "1_29"


@pytest.mark.parametrize(
    ("account_type", "family", "bucket"),
    [
        ("CALL", "demand_deposits", "0_30d"),
        ("SAVINGS", "savings_deposits", "0_30d"),
        ("FIXED", "term_deposits", "over_365d"),
        ("OTHER", "other_deposits", "over_365d"),
        (None, "other_deposits", "over_365d"),
    ],
)
def test_undated_deposits_are_placed_by_their_account_type(
    account_type: str | None, family: str, bucket: str
) -> None:
    row = position_row(
        Snapshot(balance=Decimal("1"), deposit_account_type=account_type),
        Position("DEPOSIT", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.product_family == family
    assert row.maturity_bucket == bucket


@pytest.mark.parametrize(
    ("position_type", "family", "laddered"),
    [
        ("SECURITY_HOLDING", "securities", True),
        ("INTERBANK_PLACEMENT", "interbank_placements", True),
        ("INTERBANK_BORROWING", "interbank_borrowings", True),
        ("CASH", "cash", True),
        ("DERIVATIVE", "derivatives", True),
        ("FX_HEDGE", "derivatives", True),
        ("INTEREST_RATE_SWAP", "derivatives", True),
        ("OTHER_ASSET", "other_assets", True),
        ("OTHER_LIABILITY", "other_liabilities", True),
        ("LC_GUARANTEE", "off_balance_sheet", False),
        ("COMMITMENT_UNDRAWN", "off_balance_sheet", False),
    ],
)
def test_every_other_position_type_has_a_family_and_a_ladder_rule(
    position_type: str, family: str, laddered: bool
) -> None:
    row = position_row(
        Snapshot(balance=Decimal("1"), contractual_maturity=date(2026, 7, 31)),
        Position(position_type, BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.product_family == family
    # 31 days out → the second ladder bucket for anything laddered.
    assert row.maturity_bucket == ("31_91d" if laddered else None)
    assert row.classification_exposure_rc is None


def test_undated_cash_is_demand_natured_and_other_undated_assets_are_long() -> None:
    cash = position_row(
        Snapshot(balance=Decimal("1")),
        Position("CASH", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    other = position_row(
        Snapshot(balance=Decimal("1")),
        Position("OTHER_ASSET", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert cash.maturity_bucket == "0_30d"
    assert other.maturity_bucket == "over_365d"


def test_floating_reprices_at_next_repricing_and_fixed_at_maturity() -> None:
    repricing, maturity = date(2026, 7, 1), date(2029, 6, 30)
    floating = position_row(
        Snapshot(
            balance=Decimal("1"),
            rate_type="FLOATING",
            next_repricing_date=repricing,
            contractual_maturity=maturity,
        ),
        Position("SECURITY_HOLDING", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    fixed = position_row(
        Snapshot(
            balance=Decimal("1"),
            rate_type="FIXED",
            next_repricing_date=repricing,
            contractual_maturity=maturity,
        ),
        Position("SECURITY_HOLDING", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert floating.repricing_bucket == "overnight"  # 1 day out
    # 30 Jun 2026 → 30 Jun 2029 spans the 2028 leap day: 1096 days, one past 1-3y.
    assert fixed.repricing_bucket == "3-5y"
    # The contractual ladder ignores the rate type: both mature in 1096 days.
    assert floating.maturity_bucket == fixed.maturity_bucket == "over_365d"


def test_unrecognised_regulatory_category_is_named_not_substituted() -> None:
    row = position_row(
        Snapshot(balance=Decimal("1")),
        Position("LOAN", BASE),
        None,
        Product("LN-X", "Loan Retail"),
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.exposure_category == "unclassified_loan_retail"
    assert row.product_family == "unclassified_loans"
    missing = position_row(
        Snapshot(balance=Decimal("1")),
        Position("LOAN", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert missing.exposure_category == "unclassified_unmapped"
    assert missing.product_code is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("-4", None), ("abc", None), ("12.0", 12), (7, 7), ("0", 0)],
)
def test_days_past_due_is_read_as_the_classification_service_reads_it(
    raw: Any, expected: int | None
) -> None:
    assert extract.coerce_days_past_due(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("true", True),
        ("YES", True),
        ("1", True),
        ("false", False),
        ("no", False),
        (True, True),
        (False, False),
    ],
)
def test_restructured_flag_spellings(raw: Any, expected: bool | None) -> None:
    row = position_row(
        Snapshot(balance=Decimal("1"), attributes={"restructured": raw}),
        Position("LOAN", BASE),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.restructured is expected


def test_attribute_keys_are_the_wire_keys_read_verbatim() -> None:
    """The ``_ghs`` suffix is a load-bearing wire key, read as-is into ``_rc``."""
    row = position_row(
        Snapshot(
            balance=Decimal("9"),
            attributes={
                "balance_ghs": "10",
                "ecl_provision_ghs": "2",
                "interest_in_suspense_ghs": "1",
                "crm_collateral_ghs": "4",
                "collateral_type": "CASH",
                "hqla_level": "L1",
            },
        ),
        Position("LOAN", FOREIGN),
        None,
        None,
        None,
        base_currency=BASE,
        classified=None,
    )
    assert row.balance_rc == Decimal("10")
    assert row.provision_held_rc == Decimal("2")
    assert row.interest_in_suspense_rc == Decimal("1")
    assert row.collateral_rc == Decimal("4")
    assert row.collateral_type == "CASH"  # the explicit key wins over the CRM class
    assert row.hqla_level == "L1"


# --- loan events (D-018) --------------------------------------------------------------


def _attributed_snapshot() -> extract.PositionFactRow:
    return position_row(
        Snapshot(
            balance=Decimal("1"),
            counterparty_id=uuid4(),
            attributes={"branch_id": "BR-02", "sector": "Mining"},
        ),
        Position("LOAN", BASE, source_reference="ACC-1"),
        None,
        Product("LN-C", "CORPORATE_UNRATED"),
        None,
        base_currency=BASE,
        classified=None,
    )


def test_event_attributed_to_the_facility_snapshot_on_or_before_its_date() -> None:
    snapshot = _attributed_snapshot()
    event = Event(currency=BASE, amount=Decimal("100"))
    row = loan_event_row(
        event,
        snapshot_match=SnapshotMatch(snapshot.position_id, snapshot),
        base_currency=BASE,
    )
    assert row.attribution_basis == "snapshot_on_or_before"
    assert row.position_id == snapshot.position_id
    assert row.snapshot_id == snapshot.snapshot_id
    assert row.branch_code == "BR-02"
    assert row.product_code == "LN-C"
    assert row.product_family == "corporate_loans"
    assert row.counterparty_id == snapshot.counterparty_id
    assert row.sector == "Mining"
    assert row.amount_native == Decimal("100")
    assert row.amount_rc == Decimal("100")  # already in the reporting currency
    assert row.fx_unconverted is False
    assert row.event_id == event.id
    assert (row.event_type, row.event_subtype, row.event_date) == ("REPAYMENT", None, AS_OF)
    assert (row.source_system, row.source_reference) == ("CORE", "EVT-1")
    assert row.position_source_reference == "ACC-1"


def test_event_whose_facility_has_no_snapshot_yet_keeps_the_position_only() -> None:
    position_id = uuid4()
    row = loan_event_row(
        Event(currency=FOREIGN, amount=Decimal("50"), amount_ghs=Decimal("600")),
        snapshot_match=SnapshotMatch(position_id, None),
        base_currency=BASE,
    )
    assert row.attribution_basis == "no_snapshot"
    assert row.position_id == position_id
    assert row.snapshot_id is None
    assert (row.branch_code, row.product_code, row.product_family) == (None, None, None)
    assert (row.counterparty_id, row.sector) == (None, None)
    assert row.amount_rc == Decimal("600")  # the ingested conversion
    assert row.fx_unconverted is False


def test_event_with_no_facility_in_its_own_source_system_is_unmatched() -> None:
    row = loan_event_row(
        Event(currency=FOREIGN, amount=Decimal("50"), event_type="WRITE_OFF"),
        snapshot_match=None,
        base_currency=BASE,
    )
    assert row.attribution_basis == "unmatched"
    assert row.position_id is None
    assert row.snapshot_id is None
    assert row.branch_code is None
    assert row.amount_rc is None  # unconverted, never invented
    assert row.fx_unconverted is True
    assert row.amount_native == Decimal("50")


# --- engine metrics --------------------------------------------------------------------


def _by_id(rows: tuple[EngineMetricFactRow, ...]) -> dict[str, EngineMetricFactRow]:
    return {row.metric_id: row for row in rows}


def test_live_capital_payload_for_a_bank_copies_scalars_and_flags_blocking() -> None:
    live = Live(
        module="capital",
        pipeline_state="blocked",
        metrics={
            "total_rwa_ghs": "1000",
            "car_pct": "14.25",
            "reconciliation_status": "blocked",
            "path": [{"year": 1}],
            "assumptions": {"growth": "0.1"},
        },
    )
    rows = live_metric_rows(live, regime="crd", institution_class="bank")
    assert [row.metric_id for row in rows] == ["car_pct", "total_rwa_ghs"]  # sorted, scalars only
    car = _by_id(rows)["car_pct"]
    assert car.value == Decimal("14.25")
    assert car.unit == "pct"
    assert car.tier == "live"
    assert car.regime == "crd"
    assert car.institution_class == "bank"
    assert car.advisory_designation == "filed"
    assert car.reconciliation_blocked is True
    assert car.pipeline_state == "blocked"
    assert car.input_hash == "abc123"
    assert car.engine_version == "engine-v1"
    assert car.run_id is None
    assert car.as_of_date == AS_OF
    assert car.computed_at == live.computed_at
    assert car.status == "green"
    rwa = _by_id(rows)["total_rwa_ghs"]
    assert rwa.unit == "ccy"
    assert rwa.value == Decimal("1000")


def test_reconciliation_blocked_is_read_from_the_payload_stamp_alone() -> None:
    live = Live(module="capital", metrics={"car_pct": "1", "reconciliation_status": "blocked"})
    (row,) = live_metric_rows(live, regime="crd", institution_class="bank")
    assert row.pipeline_state == "ready"
    assert row.reconciliation_blocked is True
    clean = Live(module="capital", metrics={"car_pct": "1"})
    (row,) = live_metric_rows(clean, regime="crd", institution_class="bank")
    assert row.reconciliation_blocked is False


def test_class_neutral_metrics_carry_their_own_regime() -> None:
    rows = _by_id(
        live_metric_rows(
            Live(
                module="rating",
                metrics={
                    "pit_pd_upper_pct": "3.5",
                    "pit_rating_grade": "BB",
                    "ddep_eligible": "true",
                },
            ),
            regime="crd",
            institution_class="bank",
        )
    )
    pd = rows["pit_pd_upper_pct"]
    assert pd.regime == "advisory_internal"
    assert pd.advisory_designation == "advisory_only"
    assert pd.value == Decimal("3.5")
    grade = rows["pit_rating_grade"]
    assert grade.value is None
    assert grade.unit == "text"
    assert grade.advisory_designation == "advisory_only"
    assert rows["ddep_eligible"].value is None
    ecl = _by_id(
        official_run_rows(
            Run(module="capital", metrics={"ecl_total_ghs": "42"}),
            as_of_date=AS_OF,
            regime="crd",
            institution_class="bank",
        )
    )["ecl_total_ghs"]
    assert ecl.regime == "ifrs9"
    assert ecl.advisory_designation == "filed"


def test_unregistered_figures_are_designated_unregistered_never_promoted() -> None:
    # H-009: an SDI's IRRBB figure has no s.29 authority and borrows nothing from CRD.
    (row,) = live_metric_rows(
        Live(module="irr", metrics={"worst_eve_change_pct_tier1": "-8.1"}),
        regime="s29",
        institution_class="sdi",
    )
    assert row.advisory_designation == "unregistered"
    assert row.regime == "s29"  # the tenant's regime, since nothing resolved
    assert row.value == Decimal("-8.1")
    # A limit stamped beside a metric is a parameter, not a registered metric.
    (limit,) = live_metric_rows(
        Live(module="credit", metrics={"npl_limit_pct": "10"}),
        regime="crd",
        institution_class="bank",
    )
    assert limit.advisory_designation == "unregistered"
    # A bank-only advisory figure does not resolve for an SDI either.
    (pd,) = live_metric_rows(
        Live(module="rating", metrics={"pit_pd_upper_pct": "3.5"}),
        regime="s29",
        institution_class="sdi",
    )
    assert pd.advisory_designation == "unregistered"


def test_multi_authority_metric_resolves_by_the_tenants_regime() -> None:
    bank = _by_id(
        live_metric_rows(
            Live(module="credit", metrics={"npl_ratio_pct": "5"}),
            regime="crd",
            institution_class="bank",
        )
    )["npl_ratio_pct"]
    sdi = _by_id(
        live_metric_rows(
            Live(module="credit", metrics={"npl_ratio_pct": "5"}),
            regime="s29",
            institution_class="sdi",
        )
    )["npl_ratio_pct"]
    assert (bank.regime, bank.advisory_designation) == ("crd", "filed")
    assert (sdi.regime, sdi.advisory_designation) == ("s29", "filed")
    car = _by_id(
        live_metric_rows(
            Live(module="capital", metrics={"car_pct": "20"}),
            regime="s29",
            institution_class="sdi",
        )
    )["car_pct"]
    assert (car.regime, car.advisory_designation) == ("s29", "supervisory_monitoring")


def test_official_run_rows_carry_the_run_identity_and_no_pipeline_state() -> None:
    run = Run(module="liquidity", metrics={"lcr_pct": "132.4", "hqla_total_ghs": "9000"})
    rows = _by_id(official_run_rows(run, as_of_date=AS_OF, regime="crd", institution_class="bank"))
    lcr = rows["lcr_pct"]
    assert lcr.tier == "official"
    assert lcr.run_id == run.id
    assert lcr.reporting_period_id == run.reporting_period_id
    assert lcr.pipeline_state is None
    assert lcr.reconciliation_blocked is False
    assert lcr.input_hash == "def456"
    assert lcr.computed_at == run.completed_at
    assert lcr.status == "succeeded"
    assert lcr.advisory_designation == "filed"
    assert rows["hqla_total_ghs"].unit == "ccy"


def test_engine_metric_row_keeps_a_blank_or_unparseable_value_as_null() -> None:
    row = engine_metric_row(
        organization_id=ORG,
        bank_id=BANK,
        as_of_date=AS_OF,
        module="capital",
        metric_id="car_pct",
        raw_value="",
        tier="live",
        regime="crd",
        institution_class="bank",
        status="na",
        input_hash=None,
        engine_version=None,
        pipeline_state="ready",
        reconciliation_blocked=False,
        run_id=None,
        reporting_period_id=None,
        computed_at=None,
    )
    assert row.value is None
    assert row.unit == "text"
    assert row.advisory_designation == "filed"


def test_unknown_regime_or_class_strings_resolve_to_unregistered() -> None:
    (row,) = live_metric_rows(
        Live(module="capital", metrics={"car_pct": "1"}),
        regime="not-a-regime",
        institution_class="bank",
    )
    assert row.advisory_designation == "unregistered"
    assert row.regime == "not-a-regime"
