"""The BI compiler, executed against a small hand-built mart on SQLite.

The fixture below is the whole of the arithmetic: four loans (one unconverted
foreign-currency), one deposit, three dates, two branches, two obligors, three
loan events and two engine copies. Every expected number in this file is
derived by hand from those rows, so a test that fails names the rule that
broke — a ratio of sums, a NULL denominator, the month-end scoping of a stock
measure — rather than a golden that drifted.

The aggregate table is built HERE from the same fact rows by the definition in
``bi_contracts.md`` (additive sums per grain), so the "aggregate on / off give
the same numbers" proof is a proof about the compiler, not about the builder.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue, ColumnRef, MeasureDef, catalogue
from app.domain.bi.catalogue.dimensions import POSITION_DIMENSION_IDS
from app.models import Bank
from app.models.bi import (
    TARGET_BANK_WIDE_SCOPE,
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactLoanEvent,
    BiFactPositionDaily,
)
from app.schemas.bi import (
    BI_PIVOT_MAX_COLUMNS,
    BI_TOP_N_OTHER_LABEL,
    MEMBER_ID_MAX_LENGTH,
    BiDateRange,
    BiFilter,
    BiPivot,
    BiQuery,
    BiSort,
    BiTime,
    BiTopN,
)
from app.services.bi import alerts, compiler, mart_builder, reconciliation
from app.services.bi.compiler import CompiledQuery, compile_query
from app.services.bi.errors import BiQueryError, InvalidQuery, UnknownMember, is_member_id
from app.services.bi.execution import execute
from tests.api.helpers import ORG_1, ORG_2

AUG_31 = date(2026, 8, 31)
SEP_15 = date(2026, 9, 15)
SEP_18 = date(2026, 9, 18)
BUILT_AT = datetime(2026, 9, 19, 2, tzinfo=UTC)

CP_1 = UUID("11111111-1111-4111-8111-111111111111")
CP_2 = UUID("22222222-2222-4222-8222-222222222222")


def _bank(db: Session, organization_id: str, name: str) -> Bank:
    bank = Bank(
        organization_id=organization_id,
        name=name,
        short_name=name.lower(),
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _position(bank: Bank, as_of: date, **overrides: Any) -> BiFactPositionDaily:
    row: dict[str, Any] = {
        "as_of_date": as_of,
        "snapshot_id": uuid4(),
        "position_id": uuid4(),
        "organization_id": bank.organization_id,
        "bank_id": bank.id,
        "source_system": "API_PUSH",
        "source_reference": f"ref-{uuid4().hex[:8]}",
        "position_type": "LOAN",
        "currency": bank.currency,
        "balance_native": Decimal("0"),
        "balance_rc": Decimal("0"),
        "fx_unconverted": False,
        "classification_exposure_rc": None,
        "non_performing": None,
        "product_family": "retail_loans",
        "builder_version": 1,
        "built_at": BUILT_AT,
    }
    row.update(overrides)
    return BiFactPositionDaily(**row)


def _loan(  # noqa: PLR0913 - one keyword per fact column the tests reason about
    bank: Bank,
    as_of: date,
    *,
    position_id: UUID,
    branch: str,
    sector: str,
    balance: Decimal | None,
    non_performing: bool,
    counterparty: UUID | None,
    rate: Decimal | None,
    dpd_band: str = "current",
    grade: str = "standard",
    stage: int = 1,
    currency: str | None = None,
    provision_held: Decimal = Decimal("0"),
    restructured: bool = False,
    product_code: str = "P-RETAIL",
) -> BiFactPositionDaily:
    unconverted = balance is None
    return _position(
        bank,
        as_of,
        position_id=position_id,
        branch_code=branch,
        sector=sector,
        balance_native=balance if balance is not None else Decimal("50"),
        balance_rc=balance,
        fx_unconverted=unconverted,
        classification_exposure_rc=Decimal("0") if unconverted else balance,
        non_performing=non_performing,
        counterparty_id=counterparty,
        interest_rate=rate,
        dpd_band=dpd_band,
        grade=grade,
        ifrs9_stage=stage,
        currency=currency or bank.currency,
        provision_held_rc=provision_held,
        restructured=restructured,
        product_code=product_code,
    )


def _event(bank: Bank, day: date, event_type: str, amount: Decimal, branch: str) -> BiFactLoanEvent:
    return BiFactLoanEvent(
        event_date=day,
        event_id=uuid4(),
        organization_id=bank.organization_id,
        bank_id=bank.id,
        event_type=event_type,
        source_system="API_PUSH",
        source_reference=f"evt-{uuid4().hex[:8]}",
        position_source_reference="L1",
        amount_native=amount,
        currency=bank.currency,
        amount_rc=amount,
        fx_unconverted=False,
        attribution_basis="snapshot_on_or_before",
        branch_code=branch,
        product_code="P-RETAIL",
        product_family="retail_loans",
        builder_version=1,
        built_at=BUILT_AT,
    )


def _engine(
    bank: Bank, as_of: date, value: Decimal, *, tier: str, regime: str
) -> BiFactEngineMetric:
    return BiFactEngineMetric(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        as_of_date=as_of,
        module="capital",
        metric_id="car_pct",
        tier=tier,
        value=value,
        unit="pct",
        status="green",
        regime=regime,
        institution_class="bank",
        advisory_designation="filed",
        input_hash="a" * 64,
        engine_version="1",
        pipeline_state="ready",
        reconciliation_blocked=False,
        computed_at=BUILT_AT,
        builder_version=1,
        built_at=BUILT_AT,
    )


def _calendar(bank: Bank, day: date, *, has_data: bool, last_in_month: bool) -> BiDimDate:
    quarter_month = 3 * ((day.month - 1) // 3) + 1
    return BiDimDate(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        date=day,
        has_data=has_data,
        is_last_in_month=last_in_month,
        is_last_in_quarter=last_in_month and day.month in (3, 6, 9, 12),
        is_last_in_year=False,
        calendar_month=day.replace(day=1),
        calendar_quarter=date(day.year, quarter_month, 1),
        calendar_year=day.year,
        fiscal_year=day.year,
        fiscal_quarter=(day.month - 1) // 3 + 1,
        builder_version=1,
        built_at=BUILT_AT,
    )


_AGG_GRAIN = (
    "position_type",
    "product_family",
    "branch_code",
    "currency",
    "ifrs9_stage",
    "dpd_band",
    "grade",
    "deposit_account_type",
)


def _aggregate_rows(facts: list[BiFactPositionDaily]) -> list[BiAggPositionDaily]:
    """``bi_agg_position_daily`` by the contract's definition: additive sums per grain."""
    groups: dict[tuple[Any, ...], list[BiFactPositionDaily]] = defaultdict(list)
    for fact in facts:
        key = (
            fact.organization_id,
            fact.bank_id,
            fact.as_of_date,
            *(getattr(fact, column) for column in _AGG_GRAIN),
        )
        groups[key].append(fact)

    def _sql_sum(values: list[Decimal | None]) -> Decimal | None:
        """SQL's ``SUM``: a NULL input is skipped, and a sum over nothing but
        NULLs IS NULL. This used to be ``getattr(r, attr) or Decimal("0")``
        (audit A360 H1): the fixture re-implemented the builder's zero-fill, so
        an all-NULL cell could not exist in it and
        ``test_aggregate_and_fact_paths_agree`` was blind to the one class of
        disagreement the aggregate table can produce — a stored 0 where the
        fact rows say nothing at all."""
        present = [value for value in values if value is not None]
        return sum(present, Decimal("0")) if present else None

    def _sum(rows: list[BiFactPositionDaily], attr: str) -> Decimal | None:
        return _sql_sum([getattr(r, attr) for r in rows])

    out: list[BiAggPositionDaily] = []
    for key, rows in groups.items():
        organization_id, bank_id, as_of, *grain = key
        out.append(
            BiAggPositionDaily(
                as_of_date=as_of,
                id=uuid4(),
                organization_id=organization_id,
                bank_id=bank_id,
                **dict(zip(_AGG_GRAIN, grain, strict=True)),
                row_count=len(rows),
                balance_rc_sum=_sum(rows, "balance_rc"),
                classification_exposure_rc_sum=_sum(rows, "classification_exposure_rc"),
                # ``SUM(CASE WHEN non_performing THEN exposure ELSE 0 END)``: a
                # performing row contributes a literal 0 (the selection did not
                # hold), so this is NULL only when every row is non-performing
                # with no exposure — the compiler's own ``_summed`` rule.
                non_performing_exposure_rc_sum=_sql_sum(
                    [
                        r.classification_exposure_rc if r.non_performing else Decimal("0")
                        for r in rows
                    ]
                ),
                provision_required_rc_sum=_sum(rows, "provision_required_rc"),
                provision_held_rc_sum=_sum(rows, "provision_held_rc"),
                collateral_rc_sum=_sum(rows, "collateral_rc"),
                # ``SUM(interest_rate * balance_rc)``: NULL for a row missing
                # either factor, so NULL when no row carries both.
                rate_x_balance_rc_sum=_sql_sum(
                    [
                        (
                            r.interest_rate * r.balance_rc
                            if r.interest_rate is not None and r.balance_rc is not None
                            else None
                        )
                        for r in rows
                    ]
                ),
                fx_unconverted_count=sum(1 for r in rows if r.fx_unconverted),
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    return out


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


def seed_compiler_mart(db_session: Session) -> Bank:
    """One bank's mart plus a sibling tenant's, which must never show through.

    A plain function as well as a fixture so another suite can reuse the same
    hand-derived rows (``test_data_scope.py`` does) rather than build a second
    mart whose numbers a reader would have to check against these.
    """
    bank = _bank(db_session, ORG_1, "Mart Bank")
    other = _bank(db_session, ORG_2, "Other Bank")
    l1, l2, l3, l4 = uuid4(), uuid4(), uuid4(), uuid4()
    facts = [
        # 18 September: the book every as-of test reads.
        _loan(
            bank,
            SEP_18,
            position_id=l1,
            branch="B1",
            sector="agri",
            balance=Decimal("100"),
            non_performing=False,
            counterparty=CP_1,
            rate=Decimal("0.10"),
        ),
        _loan(
            bank,
            SEP_18,
            position_id=l2,
            branch="B1",
            sector="agri",
            balance=Decimal("300"),
            non_performing=True,
            counterparty=CP_2,
            rate=Decimal("0.20"),
            dpd_band="90_179",
            grade="substandard",
            stage=3,
            provision_held=Decimal("150"),
        ),
        _loan(
            bank,
            SEP_18,
            position_id=l3,
            branch="B2",
            sector="trade",
            balance=Decimal("600"),
            non_performing=False,
            counterparty=CP_1,
            rate=None,
            product_code="P-SME",
        ),
        # Unconverted foreign-currency loan: NULL under the derivation rule,
        # zero under the classification rule, counted either way.
        _loan(
            bank,
            SEP_18,
            position_id=l4,
            branch="B2",
            sector="trade",
            balance=None,
            non_performing=False,
            counterparty=None,
            rate=Decimal("0.30"),
            currency="USD",
        ),
        _position(
            bank,
            SEP_18,
            position_type="DEPOSIT",
            branch_code="B3",
            balance_native=Decimal("1000"),
            balance_rc=Decimal("1000"),
            deposit_account_type="CURRENT",
            product_family=None,
        ),
        # 15 September: an earlier book in the same month.
        _loan(
            bank,
            SEP_15,
            position_id=l1,
            branch="B1",
            sector="agri",
            balance=Decimal("90"),
            non_performing=False,
            counterparty=CP_1,
            rate=Decimal("0.10"),
        ),
        _loan(
            bank,
            SEP_15,
            position_id=l3,
            branch="B2",
            sector="trade",
            balance=Decimal("610"),
            non_performing=False,
            counterparty=CP_1,
            rate=None,
            product_code="P-SME",
        ),
        # 31 August: the prior month-end.
        _loan(
            bank,
            AUG_31,
            position_id=l1,
            branch="B1",
            sector="agri",
            balance=Decimal("80"),
            non_performing=False,
            counterparty=CP_1,
            rate=Decimal("0.10"),
        ),
        _loan(
            bank,
            AUG_31,
            position_id=l2,
            branch="B1",
            sector="agri",
            balance=Decimal("250"),
            non_performing=True,
            counterparty=CP_2,
            rate=Decimal("0.20"),
            dpd_band="90_179",
            grade="substandard",
            stage=3,
        ),
        # Another tenant's book on the same date.
        _loan(
            other,
            SEP_18,
            position_id=uuid4(),
            branch="B1",
            sector="agri",
            balance=Decimal("9999"),
            non_performing=True,
            counterparty=CP_1,
            rate=Decimal("0.50"),
        ),
    ]
    db_session.add_all(facts)
    db_session.add_all(_aggregate_rows(facts))
    db_session.add_all(
        [
            _calendar(bank, AUG_31, has_data=True, last_in_month=True),
            _calendar(bank, SEP_15, has_data=True, last_in_month=False),
            _calendar(bank, SEP_18, has_data=True, last_in_month=True),
            _calendar(bank, date(2026, 9, 30), has_data=False, last_in_month=False),
            _calendar(other, SEP_18, has_data=True, last_in_month=True),
        ]
    )
    db_session.add_all(
        [
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code="B1",
                name="North branch",
                region="North",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code="B2",
                name="South branch",
                region="South",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code="B3",
                name="Deposit hub",
                region="South",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimBranch(
                organization_id=ORG_2,
                bank_id=other.id,
                branch_code="B1",
                name="Elsewhere",
                region="Elsewhere",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimProduct(
                organization_id=ORG_1,
                bank_id=bank.id,
                product_code="P-RETAIL",
                name="Retail loan",
                product_family="retail_loans",
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimProduct(
                organization_id=ORG_1,
                bank_id=bank.id,
                product_code="P-SME",
                name="SME loan",
                product_family="retail_loans",
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimCounterparty(
                organization_id=ORG_1,
                bank_id=bank.id,
                counterparty_id=CP_1,
                source_reference="C1",
                name="Obligor One",
                counterparty_type="CORPORATE",
                builder_version=1,
                built_at=BUILT_AT,
            ),
            BiDimCounterparty(
                organization_id=ORG_1,
                bank_id=bank.id,
                counterparty_id=CP_2,
                source_reference="C2",
                name="Obligor Two",
                counterparty_type="SME",
                builder_version=1,
                built_at=BUILT_AT,
            ),
            _event(bank, date(2026, 9, 1), "DISBURSEMENT", Decimal("50"), "B1"),
            _event(bank, date(2026, 9, 10), "REPAYMENT", Decimal("20"), "B1"),
            _event(bank, date(2026, 8, 20), "WRITE_OFF", Decimal("5"), "B2"),
            _engine(bank, SEP_18, Decimal("14.5"), tier="official", regime="crd"),
            _engine(bank, SEP_18, Decimal("14.9"), tier="live", regime="crd"),
            _engine(bank, AUG_31, Decimal("13.0"), tier="official", regime="crd"),
        ]
    )
    db_session.flush()
    return bank


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_compiler_mart(db_session)


def _query(**overrides: Any) -> BiQuery:
    payload: dict[str, Any] = {"measures": ["loans.balance_rc"], "time": {"as_of": SEP_18}}
    payload.update(overrides)
    return BiQuery.model_validate(payload)


def _run(  # noqa: PLR0913 - one keyword per knob the tests turn
    db: Session,
    cat: Catalogue,
    bank: Bank,
    q: BiQuery,
    *,
    injected: list[BiFilter] | None = None,
    row_cap: int = 100,
) -> tuple[CompiledQuery, list[tuple[Any, ...]]]:
    compiled = compile_query(
        db,
        cat,
        q,
        organization_id=bank.organization_id,
        bank_id=bank.id,
        injected_filters=injected or [],
    )
    result = execute(db, compiled, timeout_ms=5_000, row_cap=row_cap)
    return compiled, result.rows


def _as_dict(compiled: CompiledQuery, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    ids = [column.id for column in compiled.columns]
    return [dict(zip(ids, row, strict=True)) for row in rows]


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


# --- aggregation kinds ---------------------------------------------------------------------


def test_sum_excludes_unconverted_balances(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    compiled, rows = _run(db_session, cat, mart, _query())
    assert [column.id for column in compiled.columns] == ["loans.balance_rc"]
    assert _num(rows[0][0]) == 1000.0  # 100 + 300 + 600; the USD loan is NULL


def test_count_counts_every_row_including_unconverted(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _, rows = _run(
        db_session, cat, mart, _query(measures=["loans.count", "loans.unconverted_count"])
    )
    assert rows == [(4, 1)]


def test_weighted_average_is_over_rows_that_carry_a_rate(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """(100·0.10 + 300·0.20) / (100 + 300): the 600 loan has no rate and the
    USD loan has no reporting-currency balance, so neither dilutes the average."""
    _, rows = _run(db_session, cat, mart, _query(measures=["loans.weighted_average_rate"]))
    assert _num(rows[0][0]) == pytest.approx(0.175)


def test_ratio_of_sums_in_percentage_points(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _, rows = _run(db_session, cat, mart, _query(measures=["loans.npl_ratio_pct"]))
    # NPL 300 over classified exposure 100 + 300 + 600 + 0 (USD counts at zero).
    assert _num(rows[0][0]) == pytest.approx(30.0)


def test_ratio_of_sums_is_not_an_average_of_ratios(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Two branches at 75 % and 0 % roll up to 30 %, not 37.5 %."""
    compiled, rows = _run(
        db_session, cat, mart, _query(measures=["loans.npl_ratio_pct"], dimensions=["branch.code"])
    )
    by_branch = {
        row["branch.code"]: _num(row["loans.npl_ratio_pct"]) for row in _as_dict(compiled, rows)
    }
    assert by_branch["B1"] == pytest.approx(75.0)
    assert by_branch["B2"] == pytest.approx(0.0)
    _, total = _run(db_session, cat, mart, _query(measures=["loans.npl_ratio_pct"]))
    assert _num(total[0][0]) == pytest.approx(30.0)
    assert _num(total[0][0]) != pytest.approx((75.0 + 0.0) / 2)


def test_null_denominator_gives_null(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    """Provision coverage = held on NPL / NPL exposure: B2 has no NPL, so NULL, never 0."""
    compiled, rows = _run(
        db_session,
        cat,
        mart,
        _query(measures=["loans.provision_coverage_pct"], dimensions=["branch.code"]),
    )
    by_branch = {
        row["branch.code"]: row["loans.provision_coverage_pct"] for row in _as_dict(compiled, rows)
    }
    assert _num(by_branch["B1"]) == pytest.approx(50.0)
    assert by_branch["B2"] is None
    # A branch with no loans at all: every loan sum is NULL, so the ratio is NULL.
    assert by_branch["B3"] is None


def test_share(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    _, rows = _run(db_session, cat, mart, _query(measures=["deposits.demand_share_pct"]))
    assert _num(rows[0][0]) == pytest.approx(100.0)
    _, rows = _run(db_session, cat, mart, _query(measures=["positions.unconverted_share_pct"]))
    assert _num(rows[0][0]) == pytest.approx(20.0)  # 1 of 5 positions


def test_top_n_share_is_the_largest_obligor(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Obligor One holds 100 + 600 of 1 000 classified exposure; the USD loan names no obligor."""
    _, rows = _run(db_session, cat, mart, _query(measures=["loans.largest_single_name_share_pct"]))
    assert _num(rows[0][0]) == pytest.approx(70.0)


def test_hhi_over_sectors(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    """(400² + 600²) / 1 000² over classified exposure by sector."""
    _, rows = _run(db_session, cat, mart, _query(measures=["loans.sector_hhi"]))
    assert _num(rows[0][0]) == pytest.approx(0.52)


def test_concentration_measure_beside_an_additive_one_by_branch(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    compiled, rows = _run(
        db_session,
        cat,
        mart,
        _query(measures=["loans.sector_hhi", "loans.balance_rc"], dimensions=["branch.code"]),
    )
    by_branch = {row["branch.code"]: row for row in _as_dict(compiled, rows)}
    assert _num(by_branch["B1"]["loans.sector_hhi"]) == pytest.approx(1.0)  # one sector
    assert _num(by_branch["B1"]["loans.balance_rc"]) == 400.0
    assert _num(by_branch["B2"]["loans.balance_rc"]) == 600.0


def test_flow_sum_over_a_range(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    q = _query(
        measures=["events.amount_rc", "events.disbursement_rc", "events.count"],
        time={"range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)}},
    )
    _, rows = _run(db_session, cat, mart, q)
    assert (_num(rows[0][0]), _num(rows[0][1]), rows[0][2]) == (70.0, 50.0, 2)


def test_last_value_reads_the_engine_copy_for_its_module_metric_tier_and_regime(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _, rows = _run(
        db_session,
        cat,
        mart,
        _query(measures=["engine.car_pct.crd.official", "engine.car_pct.crd.live"]),
    )
    assert (_num(rows[0][0]), _num(rows[0][1])) == (14.5, 14.9)
    # The same metric under another regime is a different figure: none was copied.
    _, rows = _run(db_session, cat, mart, _query(measures=["engine.car_pct.s29.official"]))
    assert rows[0][0] is None


# --- time -------------------------------------------------------------------------------------


def test_stock_range_reads_the_last_date_with_data(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """A balance over September is the 18 September book, not 15 + 18."""
    q = _query(time={"range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)}})
    _, rows = _run(db_session, cat, mart, q)
    assert _num(rows[0][0]) == 1000.0


def test_stock_range_by_month_is_one_month_end_book_per_month(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["time.calendar_month"],
        time={"range": {"start": date(2026, 8, 1), "end": date(2026, 9, 30)}},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    by_month = {
        row["time.calendar_month"]: _num(row["loans.balance_rc"])
        for row in _as_dict(compiled, rows)
    }
    assert by_month == {date(2026, 8, 1): 330.0, date(2026, 9, 1): 1000.0}


def test_stock_range_by_date_lists_every_date(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["time.date"],
        time={"range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)}},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    by_date = {row["time.date"]: _num(row["loans.balance_rc"]) for row in _as_dict(compiled, rows)}
    assert by_date == {SEP_15: 700.0, SEP_18: 1000.0}


def test_flow_range_sums_every_day(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    q = _query(
        measures=["events.amount_rc"],
        time={"range": {"start": date(2026, 8, 1), "end": date(2026, 9, 30)}},
    )
    _, rows = _run(db_session, cat, mart, q)
    assert _num(rows[0][0]) == 75.0


def test_stock_and_flow_measures_cannot_share_a_query(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, _query(measures=["loans.balance_rc", "events.amount_rc"]))


# --- comparison -------------------------------------------------------------------------------


def test_compare_to_emits_current_prior_delta_and_delta_pct(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(dimensions=["loan.sector"], time={"as_of": SEP_18, "compare_to": SEP_15})
    compiled, rows = _run(db_session, cat, mart, q)
    assert [c.id for c in compiled.columns] == [
        "loan.sector",
        "loans.balance_rc",
        "loans.balance_rc|prior",
        "loans.balance_rc|delta",
        "loans.balance_rc|delta_pct",
    ]
    assert [c.role for c in compiled.columns[1:]] == ["current", "prior", "delta", "delta_pct"]
    by_sector = {row["loan.sector"]: row for row in _as_dict(compiled, rows)}
    agri = by_sector["agri"]
    assert (_num(agri["loans.balance_rc"]), _num(agri["loans.balance_rc|prior"])) == (400.0, 90.0)
    assert _num(agri["loans.balance_rc|delta"]) == 310.0
    assert _num(agri["loans.balance_rc|delta_pct"]) == pytest.approx(310.0 / 90.0 * 100)


def test_compare_to_with_a_missing_prior_group_is_null(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The deposit exists only on 18 September: prior, delta and delta % are NULL, not 0."""
    q = _query(
        measures=["deposits.balance_rc"],
        dimensions=["branch.code"],
        time={"as_of": SEP_18, "compare_to": SEP_15},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    hub = next(row for row in _as_dict(compiled, rows) if row["branch.code"] == "B3")
    assert _num(hub["deposits.balance_rc"]) == 1000.0
    assert hub["deposits.balance_rc|prior"] is None
    assert hub["deposits.balance_rc|delta"] is None
    assert hub["deposits.balance_rc|delta_pct"] is None


def test_compare_to_with_a_zero_prior_gives_null_delta_pct(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """B2 had no non-performing exposure on either date; B1 went 250 → 300."""
    q = _query(
        measures=["loans.npl_exposure_rc"],
        dimensions=["branch.code"],
        time={"as_of": SEP_18, "compare_to": AUG_31},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    by_branch = {row["branch.code"]: row for row in _as_dict(compiled, rows)}
    assert _num(by_branch["B1"]["loans.npl_exposure_rc|delta"]) == 50.0
    assert _num(by_branch["B1"]["loans.npl_exposure_rc|delta_pct"]) == pytest.approx(20.0)
    assert by_branch["B2"]["loans.npl_exposure_rc|delta_pct"] is None


def test_compare_to_keeps_groups_that_only_exist_in_the_prior_period(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """A FULL OUTER JOIN: a group that vanished is reported with a NULL current value."""
    q = _query(dimensions=["loan.sector"], time={"as_of": AUG_31, "compare_to": SEP_18})
    compiled, rows = _run(db_session, cat, mart, q)
    by_sector = {row["loan.sector"]: row for row in _as_dict(compiled, rows)}
    assert by_sector["trade"]["loans.balance_rc"] is None
    assert _num(by_sector["trade"]["loans.balance_rc|prior"]) == 600.0


def test_compare_to_over_a_range_shifts_an_equal_window(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        measures=["events.amount_rc"],
        time={
            "range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)},
            "compare_to": date(2026, 8, 31),
        },
    )
    _, rows = _run(db_session, cat, mart, q)
    assert (_num(rows[0][0]), _num(rows[0][1])) == (70.0, 5.0)


# --- Top-N ------------------------------------------------------------------------------------


def test_top_n_with_other_collapses_the_rest(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(dimensions=["loan.sector"], top_n={"dimension": "loan.sector", "n": 1})
    compiled, rows = _run(db_session, cat, mart, q)
    assert compiled.columns[0].format == "text"
    by_sector = {
        row["loan.sector"]: _num(row["loans.balance_rc"]) for row in _as_dict(compiled, rows)
    }
    # The deposit has no sector: a NULL key is "no value", never folded into Other.
    assert by_sector == {"trade": 600.0, BI_TOP_N_OTHER_LABEL: 400.0, None: None}


def test_top_n_larger_than_the_groups_has_no_other_row(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(dimensions=["loan.sector"], top_n={"dimension": "loan.sector", "n": 5})
    compiled, rows = _run(db_session, cat, mart, q)
    by_sector = {
        row["loan.sector"]: _num(row["loans.balance_rc"]) for row in _as_dict(compiled, rows)
    }
    assert by_sector == {"agri": 400.0, "trade": 600.0, None: None}
    assert BI_TOP_N_OTHER_LABEL not in by_sector


def test_top_n_without_other_is_a_restriction(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["loan.sector"], top_n={"dimension": "loan.sector", "n": 1, "other": False}
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert _as_dict(compiled, rows) == [
        {"loan.sector": "trade", "loans.balance_rc": Decimal("600")}
    ]


def test_top_n_ranks_by_the_first_measure_and_recomputes_ratios_for_other(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Ranked by NPL exposure (agri 300 > trade 0); Other's ratio is a ratio of sums over trade."""
    q = _query(
        measures=["loans.npl_exposure_rc", "loans.npl_ratio_pct"],
        dimensions=["loan.sector"],
        top_n={"dimension": "loan.sector", "n": 1},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    by_sector = {row["loan.sector"]: row for row in _as_dict(compiled, rows)}
    assert _num(by_sector["agri"]["loans.npl_ratio_pct"]) == pytest.approx(75.0)
    assert _num(by_sector[BI_TOP_N_OTHER_LABEL]["loans.npl_ratio_pct"]) == pytest.approx(0.0)


def test_top_n_must_name_a_requested_dimension(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, _query(top_n={"dimension": "loan.sector", "n": 1}))


# --- pivot ------------------------------------------------------------------------------------


def test_pivot_emits_one_column_per_value(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    q = _query(
        measures=["loans.balance_rc", "loans.count"],
        dimensions=["branch.code"],
        pivot={"dimension": "loan.sector"},
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert [c.id for c in compiled.columns] == [
        "branch.code",
        "loans.balance_rc|agri",
        "loans.balance_rc|trade",
        "loans.count|agri",
        "loans.count|trade",
    ]
    assert compiled.columns[1].pivot_value == "agri"
    by_branch = {row["branch.code"]: row for row in _as_dict(compiled, rows)}
    assert _num(by_branch["B1"]["loans.balance_rc|agri"]) == 400.0
    assert by_branch["B1"]["loans.balance_rc|trade"] is None
    assert by_branch["B2"]["loans.count|trade"] == 2


def test_pivot_wider_than_the_cap_is_refused(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    db_session.add_all(
        _loan(
            mart,
            SEP_18,
            position_id=uuid4(),
            branch="B1",
            sector=f"sector-{i:03d}",
            balance=Decimal("1"),
            non_performing=False,
            counterparty=None,
            rate=None,
        )
        for i in range(BI_PIVOT_MAX_COLUMNS + 1)
    )
    db_session.flush()
    q = _query(
        dimensions=["branch.code"], pivot={"dimension": "loan.sector", "max_columns": 10_000}
    )
    with pytest.raises(InvalidQuery, match=str(BI_PIVOT_MAX_COLUMNS)):
        _run(db_session, cat, mart, q)


def test_pivot_client_cap_below_the_server_cap_is_honoured(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(dimensions=["branch.code"], pivot={"dimension": "loan.sector", "max_columns": 1})
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, q)


def test_pivot_axis_cannot_also_be_a_row_dimension(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery):
        _run(
            db_session,
            cat,
            mart,
            _query(dimensions=["loan.sector"], pivot={"dimension": "loan.sector"}),
        )


def test_pivoted_result_cannot_be_sorted_by_a_measure(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["branch.code"],
        pivot={"dimension": "loan.sector"},
        sort=[{"member": "loans.balance_rc", "direction": "desc"}],
    )
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, q)


def test_concentration_measures_cannot_be_pivoted(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        measures=["loans.sector_hhi"], dimensions=["branch.code"], pivot={"dimension": "loan.grade"}
    )
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, q)


# --- subtotals --------------------------------------------------------------------------------


def test_subtotals_roll_up_over_the_dimension_order(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(dimensions=["branch.region", "branch.code"], subtotals=True)
    compiled, rows = _run(db_session, cat, mart, q)
    assert [c.id for c in compiled.columns] == [
        "branch.region",
        "branch.code",
        "__level",
        "loans.balance_rc",
    ]
    assert compiled.columns[2].kind == "marker"
    table = [
        (r["branch.region"], r["branch.code"], r["__level"], _num(r["loans.balance_rc"]))
        for r in _as_dict(compiled, rows)
    ]
    assert table[0] == (None, None, 0, 1000.0)
    assert (None, None, 0, 1000.0) in table
    assert ("North", None, 1, 400.0) in table
    assert ("South", None, 1, 600.0) in table
    assert ("North", "B1", 2, 400.0) in table
    assert ("South", "B2", 2, 600.0) in table
    assert ("South", "B3", 2, None) in table
    assert len(table) == 6
    # Levels come first, then the hierarchical order within a level.
    assert [row[2] for row in table] == sorted(row[2] for row in table)


def test_subtotals_without_dimensions_is_the_plain_total(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    compiled, rows = _run(db_session, cat, mart, _query(subtotals=True))
    assert [c.id for c in compiled.columns] == ["loans.balance_rc"]
    assert _num(rows[0][0]) == 1000.0


def test_subtotals_with_comparison_join_on_the_level(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["loan.sector"], subtotals=True, time={"as_of": SEP_18, "compare_to": SEP_15}
    )
    compiled, rows = _run(db_session, cat, mart, q)
    total = next(r for r in _as_dict(compiled, rows) if r["__level"] == 0)
    assert (_num(total["loans.balance_rc"]), _num(total["loans.balance_rc|prior"])) == (
        1000.0,
        700.0,
    )


# --- aggregate table ----------------------------------------------------------------------------


def test_additive_position_measures_over_the_grain_use_the_aggregate(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        measures=[
            "loans.balance_rc",
            "loans.npl_ratio_pct",
            "loans.count",
            "loans.unconverted_count",
        ],
        dimensions=["branch.region", "product.family", "loan.grade"],
        filters=[{"member": "position.currency", "op": "in", "values": ["GHS", "USD"]}],
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert compiled.used_aggregate is True
    assert compiled.fact_table == "bi_agg_position_daily"
    assert "bi_fact_position_daily" not in str(compiled.select)


@pytest.mark.parametrize(
    "overrides",
    [
        {"measures": ["loans.weighted_average_rate"]},
        {"measures": ["loans.sector_hhi"]},
        {"measures": ["loans.restructured_exposure_rc"]},
        {"dimensions": ["loan.sector"]},
        {"dimensions": ["counterparty.type"]},
        {"filters": [{"member": "loan.restructured", "op": "eq", "values": [True]}]},
        {
            "measures": ["events.amount_rc"],
            "time": {"range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)}},
        },
    ],
)
def test_anything_outside_the_grain_reads_the_fact(
    db_session: Session, cat: Catalogue, mart: Bank, overrides: dict[str, Any]
) -> None:
    compiled, _ = _run(db_session, cat, mart, _query(**overrides))
    assert compiled.used_aggregate is False


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "measures": ["loans.balance_rc", "loans.npl_exposure_rc", "loans.npl_ratio_pct"],
            "dimensions": ["branch.region"],
        },
        {"measures": ["loans.count", "loans.unconverted_count", "positions.unconverted_share_pct"]},
        {
            "measures": ["loans.par_90_exposure_rc", "deposits.demand_balance_rc"],
            "dimensions": ["loan.grade", "product.family"],
        },
        {
            "dimensions": ["time.calendar_month"],
            "time": {"range": {"start": AUG_31, "end": SEP_18}},
        },
        {
            "dimensions": ["branch.code"],
            "subtotals": True,
            "time": {"as_of": SEP_18, "compare_to": AUG_31},
        },
        # Audit A360 H1 — the cases the zero-filling fixture could never hold.
        # A KPI over a value NO row states: every loan's ``collateral_rc`` is
        # NULL, so the whole aggregate cell is NULL, and the answer must be too.
        {"measures": ["loans.collateral_rc"]},
        # A grain cell whose whole population is unconverted FX: the USD loan is
        # alone in its (LOAN, retail_loans, B2, USD, …) cell, so ``balance_rc_sum``
        # is NULL there. By currency it is its own group; filtered to USD it is
        # the whole KPI.
        {"measures": ["loans.balance_rc", "loans.count"], "dimensions": ["position.currency"]},
        {
            "measures": ["loans.balance_rc", "loans.collateral_rc", "loans.count"],
            "filters": [{"member": "position.currency", "op": "in", "values": ["USD"]}],
        },
    ],
)
def test_aggregate_and_fact_paths_agree(
    db_session: Session,
    cat: Catalogue,
    mart: Bank,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
) -> None:
    q = _query(**overrides)
    on, rows_on = _run(db_session, cat, mart, q)
    assert on.used_aggregate is True
    monkeypatch.setattr(compiler, "aggregate_table_covers", lambda *_: False)
    off, rows_off = _run(db_session, cat, mart, q)
    assert off.used_aggregate is False
    assert [c.id for c in on.columns] == [c.id for c in off.columns]
    normalised = [
        tuple(
            _num(v) if isinstance(v, Decimal | float | int) and not isinstance(v, bool) else v
            for v in row
        )
        for row in rows_on
    ]
    assert normalised == [
        tuple(
            _num(v) if isinstance(v, Decimal | float | int) and not isinstance(v, bool) else v
            for v in row
        )
        for row in rows_off
    ]


# --- tenant and injected predicates --------------------------------------------------------------


def test_tenant_predicates_come_from_the_caller(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    compiled, rows = _run(db_session, cat, mart, _query(measures=["loans.npl_exposure_rc"]))
    assert _num(rows[0][0]) == 300.0  # the sibling tenant's 9 999 never shows
    params = compiled.params
    assert mart.organization_id in params.values()
    assert mart.id in params.values()
    assert ORG_2 not in params.values()


def test_injected_filters_apply_and_cannot_be_widened(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    scope = [BiFilter(member="branch.code", op="eq", values=["B1"])]
    compiled, rows = _run(db_session, cat, mart, _query(), injected=scope)
    assert _num(rows[0][0]) == 400.0
    assert compiled.injected_member_ids == ("branch.code",)
    assert "branch.code" in compiled.member_ids
    widen = _query(filters=[{"member": "branch.code", "op": "in", "values": ["B1", "B2"]}])
    _, rows = _run(db_session, cat, mart, widen, injected=scope)
    assert _num(rows[0][0]) == 400.0
    negate = _query(filters=[{"member": "branch.code", "op": "ne", "values": ["B1"]}])
    _, rows = _run(db_session, cat, mart, negate, injected=scope)
    assert rows[0][0] is None


def test_injected_filters_apply_to_the_top_n_ranking_and_the_pivot_probe(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    scope = [BiFilter(member="branch.code", op="eq", values=["B1"])]
    q = _query(dimensions=["loan.sector"], top_n={"dimension": "loan.sector", "n": 1})
    compiled, rows = _run(db_session, cat, mart, q, injected=scope)
    assert _as_dict(compiled, rows) == [{"loan.sector": "agri", "loans.balance_rc": Decimal("400")}]
    q = _query(dimensions=["branch.code"], pivot={"dimension": "loan.sector"})
    compiled, _ = _run(db_session, cat, mart, q, injected=scope)
    assert [c.id for c in compiled.columns] == ["branch.code", "loans.balance_rc|agri"]


# --- resolution and shape ----------------------------------------------------------------------


def test_unknown_member_is_refused_before_anything_else(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """An unknown id wins over every other defect in the same request."""
    q = _query(
        measures=["engine.car_pct.crd.official", "no.such_measure"],
        dimensions=["branch.region"],  # not allowed for an engine measure
        sort=[{"member": "loans.count"}],  # not requested
        top_n={"dimension": "loan.sector", "n": 1},  # not a requested dimension
    )
    with pytest.raises(UnknownMember) as excinfo:
        _run(db_session, cat, mart, q)
    assert excinfo.value.member_id == "no.such_measure"
    assert excinfo.value.status_code == 422
    assert isinstance(excinfo.value, BiQueryError)


@pytest.mark.parametrize(
    "overrides",
    [
        {"dimensions": ["no.such_dimension"]},
        {"filters": [{"member": "no.such_dimension", "op": "eq", "values": ["x"]}]},
        {"dimensions": ["loan.sector"], "sort": [{"member": "no.such"}]},
        {"dimensions": ["loan.sector"], "top_n": {"dimension": "no.such", "n": 2}},
        {"pivot": {"dimension": "no.such"}},
    ],
)
def test_every_member_slot_refuses_an_unknown_id(
    db_session: Session, cat: Catalogue, mart: Bank, overrides: dict[str, Any]
) -> None:
    with pytest.raises(UnknownMember):
        _run(db_session, cat, mart, _query(**overrides))


def test_a_member_whose_column_is_not_mapped_is_unknown(db_session: Session, mart: Bank) -> None:
    """The catalogue may only name mapped columns; a stray one is refused, not rendered."""
    real = catalogue()
    stray = MeasureDef(
        id="loans.stray",
        module="credit",
        sensitivity="aggregated",
        label="Stray",
        source=ColumnRef("bi_fact_position_daily", "no_such_column"),
        allowed_dimensions=POSITION_DIMENSION_IDS,
    )
    elsewhere = MeasureDef(
        id="loans.elsewhere",
        module="credit",
        sensitivity="aggregated",
        label="Elsewhere",
        source=ColumnRef("bank_financial_facts", "value"),
        allowed_dimensions=POSITION_DIMENSION_IDS,
    )
    cat = Catalogue(
        real.version,
        {**{m.id: m for m in real.measures()}, stray.id: stray, elsewhere.id: elsewhere},
        {d.id: d for d in real.dimensions()},
        real.hierarchies(),
    )
    for member_id in ("loans.stray", "loans.elsewhere"):
        with pytest.raises(UnknownMember) as excinfo:
            _run(db_session, cat, mart, _query(measures=[member_id]))
        assert excinfo.value.member_id == member_id


def test_hierarchy_ids_expand_to_their_levels(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    compiled, rows = _run(db_session, cat, mart, _query(dimensions=["geography"]))
    assert [c.id for c in compiled.columns] == ["branch.region", "branch.code", "loans.balance_rc"]
    assert len(rows) == 3


def test_dimension_not_allowed_by_a_measure_is_refused(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery, match="cannot be sliced"):
        _run(
            db_session,
            cat,
            mart,
            _query(measures=["engine.car_pct.crd.official"], dimensions=["branch.region"]),
        )


def test_measures_from_two_facts_are_refused(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery, match="more than one fact"):
        _run(
            db_session,
            cat,
            mart,
            _query(measures=["loans.balance_rc", "engine.car_pct.crd.official"]),
        )


def test_a_dimension_in_the_measure_list_is_refused(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, _query(measures=["branch.region"]))
    with pytest.raises(InvalidQuery):
        _run(db_session, cat, mart, _query(dimensions=["loans.count"]))
    with pytest.raises(InvalidQuery):
        _run(
            db_session,
            cat,
            mart,
            _query(filters=[{"member": "loans.count", "op": "gt", "values": [1]}]),
        )


def test_sort_is_a_whitelist_of_requested_members(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    q = _query(
        dimensions=["branch.code"], sort=[{"member": "loans.balance_rc", "direction": "desc"}]
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert [row["branch.code"] for row in _as_dict(compiled, rows)] == ["B2", "B1", "B3"]
    with pytest.raises(InvalidQuery, match="Sort by"):
        _run(
            db_session,
            cat,
            mart,
            _query(dimensions=["branch.code"], sort=[{"member": "loans.count"}]),
        )


# --- filters ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("member", "op", "values", "expected"),
    [
        ("branch.code", "eq", ["B1"], 400.0),
        ("branch.code", "ne", ["B1"], 600.0),
        ("branch.code", "in", ["B1", "B3"], 400.0),
        ("branch.code", "not_in", ["B1", "B3"], 600.0),
        ("loan.ifrs9_stage", "gt", [1], 300.0),
        ("loan.ifrs9_stage", "gte", [3], 300.0),
        ("loan.ifrs9_stage", "lt", [3], 700.0),
        ("loan.ifrs9_stage", "lte", [1], 700.0),
        ("loan.ifrs9_stage", "between", [2, 3], 300.0),
        ("counterparty.id", "is_null", [], None),
        ("counterparty.id", "not_null", [], 1000.0),
        ("loan.sector", "contains", ["gri"], 400.0),
        ("loan.non_performing", "eq", [True], 300.0),
        ("loan.non_performing", "eq", ["false"], 700.0),
        ("time.date", "eq", ["2026-09-18"], 1000.0),
        ("counterparty.id", "eq", [str(CP_2)], 300.0),
        ("loan.vintage_month", "is_null", [], 1000.0),
    ],
)
def test_every_whitelisted_operator(  # noqa: PLR0913 - one parametrised argument per axis
    db_session: Session,
    cat: Catalogue,
    mart: Bank,
    member: str,
    op: str,
    values: list[Any],
    expected: float | None,
) -> None:
    q = _query(filters=[{"member": member, "op": op, "values": values}])
    _, rows = _run(db_session, cat, mart, q)
    assert _num(rows[0][0]) == expected


def test_contains_escapes_like_wildcards(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    q = _query(filters=[{"member": "loan.sector", "op": "contains", "values": ["%"]}])
    _, rows = _run(db_session, cat, mart, q)
    assert rows[0][0] is None  # no sector contains a literal percent sign


@pytest.mark.parametrize(
    "flt",
    [
        {"member": "loan.ifrs9_stage", "op": "eq", "values": ["three"]},
        {"member": "loan.ifrs9_stage", "op": "eq", "values": [True]},
        {"member": "time.date", "op": "eq", "values": ["not-a-date"]},
        {"member": "counterparty.id", "op": "eq", "values": ["not-a-uuid"]},
        {"member": "counterparty.id", "op": "gt", "values": [str(CP_1)]},
        {"member": "loan.non_performing", "op": "eq", "values": ["maybe"]},
        {"member": "loan.non_performing", "op": "gt", "values": [True]},
        {"member": "loan.ifrs9_stage", "op": "contains", "values": ["1"]},
        {"member": "branch.code", "op": "eq", "values": [1]},
    ],
)
def test_values_that_do_not_fit_the_column_are_refused(
    db_session: Session, cat: Catalogue, mart: Bank, flt: dict[str, Any]
) -> None:
    with pytest.raises(InvalidQuery) as excinfo:
        _run(db_session, cat, mart, _query(filters=[flt]))
    assert excinfo.value.status_code == 422
    assert "SELECT" not in str(excinfo.value)


@pytest.mark.parametrize(
    "flt",
    [
        {"member": "branch.code", "op": "in", "values": [f"B{i}" for i in range(501)]},
        {"member": "branch.code", "op": "in", "values": []},
        {"member": "branch.code", "op": "between", "values": ["A"]},
        {"member": "branch.code", "op": "is_null", "values": ["A"]},
        {"member": "branch.code", "op": "eq", "values": ["A", "B"]},
        {"member": "branch.code", "op": "like", "values": ["A"]},
        {"member": "branch.code", "op": "eq", "values": ["A"], "sql": "1=1"},
    ],
)
def test_filter_arity_and_operator_whitelist_are_pydantic_errors(flt: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _query(filters=[flt])


def test_time_needs_exactly_one_window() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        BiTime()
    with pytest.raises(ValidationError, match="exactly one"):
        BiTime(as_of=SEP_18, range=BiDateRange(start=AUG_31, end=SEP_18))


def test_a_range_must_start_on_or_before_it_ends() -> None:
    """The ordering rule belongs to the range itself, so it is refused at
    construction and the message names the field that is wrong."""
    with pytest.raises(ValidationError, match="start must be on or before end"):
        BiDateRange(start=SEP_18, end=AUG_31)
    with pytest.raises(ValidationError, match="start must be on or before end"):
        BiTime.model_validate({"range": {"start": SEP_18, "end": AUG_31}})
    same_day = BiDateRange(start=SEP_18, end=SEP_18)
    assert (same_day.start, same_day.end) == (SEP_18, SEP_18)


def test_a_range_is_named_at_both_ends_on_the_wire() -> None:
    """``start``/``end``, never a positional pair: a client reading
    ``["2026-08-31", "2026-09-18"]`` would have to know which end is which, and
    the tuple form was also untypeable by the generated client (it rendered as
    an OpenAPI array with ``prefixItems`` and no ``items``)."""
    window = BiTime.model_validate({"range": {"start": AUG_31, "end": SEP_18}})
    assert window.range is not None
    assert (window.range.start, window.range.end) == (AUG_31, SEP_18)
    assert window.model_dump(mode="json")["range"] == {
        "start": AUG_31.isoformat(),
        "end": SEP_18.isoformat(),
    }
    for positional in ([AUG_31, SEP_18], (AUG_31, SEP_18)):
        with pytest.raises(ValidationError):
            BiTime.model_validate({"range": positional})
    with pytest.raises(ValidationError):
        BiTime.model_validate({"range": {"start": AUG_31, "end": SEP_18, "middle": SEP_15}})


def test_query_shape_is_closed_and_bounded() -> None:
    with pytest.raises(ValidationError):
        BiQuery.model_validate(
            {"measures": ["loans.balance_rc"], "time": {"as_of": SEP_18}, "raw_sql": "x"}
        )
    with pytest.raises(ValidationError):
        BiQuery.model_validate({"measures": [], "time": {"as_of": SEP_18}})
    with pytest.raises(ValidationError):
        BiTopN(dimension="loan.sector", n=51)
    with pytest.raises(ValidationError):
        BiPivot(dimension="loan.sector", max_columns=0)
    with pytest.raises(ValidationError):
        BiSort.model_validate({"member": "loan.sector", "direction": "sideways"})
    with pytest.raises(ValidationError):
        _query(limit=0)


# --- limits -----------------------------------------------------------------------------------


def test_limit_is_clamped_to_the_row_cap(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    compiled = compile_query(
        db_session, cat, _query(limit=999_999, offset=3), organization_id=ORG_1, bank_id=mart.id
    )
    statement, limit = compiled.with_row_cap(5_000)
    assert limit == 5_000
    params = statement.compile(compile_kwargs={"render_postcompile": True}).params
    assert 5_001 in params.values()
    assert 3 in params.values()
    _, below = compiled.with_row_cap(10)
    assert below == 10
    small = compile_query(db_session, cat, _query(limit=7), organization_id=ORG_1, bank_id=mart.id)
    assert small.with_row_cap(5_000)[1] == 7


# --- A5-01: sorting by a dimension ---------------------------------------------------------
#
# ``key_by_member.get(m) or measure_by_member[m]`` called ``bool()`` on a
# SQLAlchemy Label, which RAISES, so every query that sorted by a dimension
# crashed with a TypeError out of ``compile_query`` — including the only sort a
# pivoted result allows, which the pivot refusal message itself advertises.
# These tests fail on the ``or`` form and pass on the membership test.


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("asc", ["B1", "B2", "B3"]), ("desc", ["B3", "B2", "B1"])],
)
def test_sort_by_a_dimension(
    db_session: Session, cat: Catalogue, mart: Bank, direction: str, expected: list[str]
) -> None:
    q = _query(dimensions=["branch.code"], sort=[{"member": "branch.code", "direction": direction}])
    compiled, rows = _run(db_session, cat, mart, q)
    assert [row["branch.code"] for row in _as_dict(compiled, rows)] == expected


def test_sort_by_a_dimension_under_a_pivot(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    """The one sort a pivoted result allows — its own 422 names it."""
    q = _query(
        dimensions=["branch.code"],
        pivot={"dimension": "loan.sector"},
        sort=[{"member": "branch.code", "direction": "desc"}],
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert [row["branch.code"] for row in _as_dict(compiled, rows)] == ["B3", "B2", "B1"]


def test_sort_by_a_hierarchy_level(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    """``geography`` expands to region then code; either level may be sorted on."""
    q = _query(dimensions=["geography"], sort=[{"member": "branch.region", "direction": "desc"}])
    compiled, rows = _run(db_session, cat, mart, q)
    regions = [row["branch.region"] for row in _as_dict(compiled, rows)]
    assert regions == ["South", "South", "North"]


def test_sort_by_a_dimension_and_a_measure_together(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Region ascending, then the largest branch first within each region."""
    q = _query(
        dimensions=["branch.region", "branch.code"],
        sort=[
            {"member": "branch.region", "direction": "asc"},
            {"member": "loans.balance_rc", "direction": "desc"},
        ],
    )
    compiled, rows = _run(db_session, cat, mart, q)
    assert [(r["branch.region"], r["branch.code"]) for r in _as_dict(compiled, rows)] == [
        ("North", "B1"),
        ("South", "B2"),
        ("South", "B3"),
    ]


def test_sort_by_a_dimension_with_subtotals_keeps_levels_first(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """A rolled-up key sorts NULLS FIRST, so a subtotal precedes its children."""
    q = _query(
        dimensions=["branch.region", "branch.code"],
        subtotals=True,
        sort=[{"member": "branch.region", "direction": "asc"}],
    )
    compiled, rows = _run(db_session, cat, mart, q)
    table = [(r["__level"], r["branch.region"]) for r in _as_dict(compiled, rows)]
    assert table[0] == (0, None)
    assert [level for level, _ in table] == sorted(level for level, _ in table)


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sort_by_a_dimension_that_is_null_for_some_rows_puts_nulls_last(
    db_session: Session, cat: Catalogue, mart: Bank, direction: str
) -> None:
    """NULL placement is explicit both ways: Postgres and SQLite disagree by
    default, and "no value" belongs after the named groups either way."""
    q = _query(
        measures=["positions.count"],
        dimensions=["loan.sector"],
        sort=[{"member": "loan.sector", "direction": direction}],
    )
    compiled, rows = _run(db_session, cat, mart, q)
    sectors = [row["loan.sector"] for row in _as_dict(compiled, rows)]
    assert sectors[-1] is None, "the deposit has no sector and must sort last"
    assert sectors[:-1] == (["agri", "trade"] if direction == "asc" else ["trade", "agri"])


# --- A5-02 / D-042: a selection column that was never supplied ------------------------------
#
# "Not selected" and "never told" are different facts. A bank that sent no
# ``days_past_due`` has ``dpd_band`` NULL on every loan, and PAR-90 read
# ``0.00 %`` — a clean book. Under D-042 such a measure is NULL; a POPULATED
# column that simply matches nothing is still a genuine 0.

#: measure id → (position type, selection column, a matching value, a non-matching value).
#: ``test_every_measure_that_selects_on_a_nullable_column_has_a_case`` derives the
#: affected set from the catalogue and fails if a new measure is missing here.
_D042_CASES: dict[str, tuple[str, str, Any, Any]] = {
    "loans.par_30_exposure_rc": ("LOAN", "dpd_band", "30_59", "current"),
    "loans.par_60_exposure_rc": ("LOAN", "dpd_band", "60_89", "current"),
    "loans.par_90_exposure_rc": ("LOAN", "dpd_band", "90_179", "current"),
    "loans.restructured_exposure_rc": ("LOAN", "restructured", True, False),
    "deposits.demand_balance_rc": ("DEPOSIT", "deposit_account_type", "CURRENT", "FIXED"),
    "positions.encumbered_balance_rc": ("LOAN", "encumbered", True, False),
}

#: Measures that select on a column which CANNOT be missing, or on one the daily
#: aggregate pre-computes as its own column (so the question is unanswerable on
#: that source and asking it on the fact alone would make the two disagree).
#: The NPL pair is the second kind, and R1 reconciles it to the engine instead.
_D042_EXEMPT: frozenset[str] = frozenset(
    {
        "loans.npl_exposure_rc",
        "loans.specific_provision_held_rc",
        "loans.unconverted_count",
        "deposits.unconverted_count",
        "positions.unconverted_count",
        "events.disbursement_rc",
        "events.repayment_rc",
        "events.write_off_rc",
        "events.recovery_rc",
        "events.unconverted_count",
        "events.unattributed_count",
    }
)


def _selecting_measures(cat: Catalogue) -> dict[str, list[str]]:
    """Every measure with a selection filter → the columns it selects on."""
    population = compiler._POPULATION_COLUMNS
    out: dict[str, list[str]] = {}
    for measure in cat.measures():
        selection = [f for f in measure.row_filters if f.column not in population]
        if selection:
            out[measure.id] = [f.column for f in selection]
    return out


def test_every_measure_that_selects_on_a_nullable_column_has_a_case(cat: Catalogue) -> None:
    """The affected set comes from the CATALOGUE, so a new measure cannot slip
    past this suite: it lands in neither table and the test fails."""
    affected: set[str] = set()
    for member_id, columns in _selecting_measures(cat).items():
        table = compiler._TABLES[cat.measure(member_id).table]
        if any(
            column in table.c
            and table.c[column].nullable
            and column not in compiler._PRECOMPUTED_SELECTION_COLUMNS
            for column in columns
        ):
            affected.add(member_id)
    assert affected == set(_D042_CASES)
    assert set(_selecting_measures(cat)) - affected == _D042_EXEMPT


def _d042_bank(db: Session, spec: list[tuple[str, str, Any, str]]) -> Bank:
    """A bank whose rows set one column to one value each (fact + aggregate)."""
    bank = _bank(db, ORG_1, f"D042 {uuid4().hex[:6]}")
    facts = [
        _position(
            bank,
            SEP_18,
            position_type=position_type,
            balance_native=Decimal(amount),
            balance_rc=Decimal(amount),
            classification_exposure_rc=Decimal(amount),
            non_performing=False if position_type == "LOAN" else None,
            **{column: value},
        )
        for position_type, column, value, amount in spec
    ]
    db.add_all(facts)
    db.add_all(_aggregate_rows(facts))
    db.add_all([_calendar(bank, SEP_18, has_data=True, last_in_month=True)])
    db.flush()
    return bank


def _value(db: Session, cat: Catalogue, bank: Bank, member_id: str, **overrides: Any) -> Any:
    _, rows = _run(db, cat, bank, _query(measures=[member_id], **overrides))
    return rows[0][0]


@pytest.fixture(params=["auto", "fact"])
def path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run each D-042 case on whichever source the compiler picks AND on the
    fact table, because the two must never disagree about missing data."""
    if request.param == "fact":
        monkeypatch.setattr(compiler, "aggregate_table_covers", lambda *_: False)
    return cast(str, request.param)


@pytest.mark.parametrize("member_id", sorted(_D042_CASES))
def test_a_selection_column_null_across_the_population_gives_null(
    db_session: Session, cat: Catalogue, member_id: str, path: str
) -> None:
    position_type, column, _, _ = _D042_CASES[member_id]
    bank = _d042_bank(db_session, [(position_type, column, None, "100")] * 2)
    assert _value(db_session, cat, bank, member_id) is None, path


@pytest.mark.parametrize("member_id", sorted(_D042_CASES))
def test_a_populated_selection_column_that_matches_nothing_gives_zero(
    db_session: Session, cat: Catalogue, member_id: str, path: str
) -> None:
    position_type, column, _, absent = _D042_CASES[member_id]
    bank = _d042_bank(db_session, [(position_type, column, absent, "100")] * 2)
    assert _num(_value(db_session, cat, bank, member_id)) == 0.0, path


@pytest.mark.parametrize("member_id", sorted(_D042_CASES))
def test_a_matching_selection_column_measures_the_matching_rows(
    db_session: Session, cat: Catalogue, member_id: str, path: str
) -> None:
    position_type, column, present, absent = _D042_CASES[member_id]
    bank = _d042_bank(
        db_session,
        [(position_type, column, present, "100"), (position_type, column, absent, "300")],
    )
    assert _num(_value(db_session, cat, bank, member_id)) == 100.0, path


def test_a_ratio_whose_numerator_needs_missing_data_is_null(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """No loan has a DPD band: PAR-90 is unknown, not 0.00 % (the A5-02 defect)."""
    bank = _d042_bank(db_session, [("LOAN", "dpd_band", None, "100")] * 2)
    assert _value(db_session, cat, bank, "loans.par_90_pct") is None, path
    # The denominator on its own is still the whole book — the restriction
    # belongs to the ratio, not to the measure it composes.
    assert _num(_value(db_session, cat, bank, "loans.classification_exposure_rc")) == 200.0


def test_a_ratio_over_a_populated_column_with_no_matches_is_zero(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """Every loan has a band and none is 90+: the book really is 0 %."""
    bank = _d042_bank(db_session, [("LOAN", "dpd_band", "current", "100")] * 2)
    assert _num(_value(db_session, cat, bank, "loans.par_90_pct")) == 0.0, path


def test_a_ratio_with_partial_coverage_measures_only_the_rows_it_can(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """100 at 90+, 300 known-current, 600 with no band at all.

    The rate is 100/400 over the loans whose arrears are known — not 100/1000,
    which would read "we were never told" as "current" and understate PAR by
    more than half.
    """
    bank = _d042_bank(
        db_session,
        [
            ("LOAN", "dpd_band", "90_179", "100"),
            ("LOAN", "dpd_band", "current", "300"),
            ("LOAN", "dpd_band", None, "600"),
        ],
    )
    assert _num(_value(db_session, cat, bank, "loans.par_90_pct")) == pytest.approx(25.0), path
    assert _num(_value(db_session, cat, bank, "loans.par_90_exposure_rc")) == 100.0
    assert _num(_value(db_session, cat, bank, "loans.classification_exposure_rc")) == 1000.0


def test_the_same_rule_holds_for_the_deposit_mix(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    bank = _d042_bank(db_session, [("DEPOSIT", "deposit_account_type", None, "500")] * 2)
    assert _value(db_session, cat, bank, "deposits.demand_share_pct") is None, path
    known = _d042_bank(
        db_session,
        [
            ("DEPOSIT", "deposit_account_type", "CURRENT", "300"),
            ("DEPOSIT", "deposit_account_type", "FIXED", "100"),
            ("DEPOSIT", "deposit_account_type", None, "600"),
        ],
    )
    assert _num(_value(db_session, cat, known, "deposits.demand_share_pct")) == pytest.approx(75.0)


def test_the_npl_pair_is_deliberately_exempt_and_both_sources_agree(
    db_session: Session, cat: Catalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``non_performing`` keeps its 0, and the reason is structural.

    ``bi_agg_position_daily`` pre-computes ``non_performing_exposure_rc_sum``
    and carries no ``non_performing`` attribute, so "was the loan ever
    classified" cannot be asked on that source. Applying D-042 to the fact path
    alone would make the aggregate and the fact answer one query differently —
    the single invariant aggregate selection rests on. Completeness here is R1's
    job, which compares the mart's non-performing exposure to the engine's and
    reads the fact table directly rather than through this compiler.
    """
    bank = _d042_bank(db_session, [("LOAN", "dpd_band", "current", "100")] * 2)
    for row in db_session.query(BiFactPositionDaily).filter_by(bank_id=bank.id):
        row.non_performing = None
    db_session.flush()
    auto = _value(db_session, cat, bank, "loans.npl_exposure_rc")
    auto_ratio = _value(db_session, cat, bank, "loans.npl_ratio_pct")
    monkeypatch.setattr(compiler, "aggregate_table_covers", lambda *_: False)
    assert _num(_value(db_session, cat, bank, "loans.npl_exposure_rc")) == _num(auto) == 0.0
    assert _num(_value(db_session, cat, bank, "loans.npl_ratio_pct")) == _num(auto_ratio) == 0.0


def test_an_answerable_count_is_not_added_when_the_column_cannot_be_null(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """A measure selecting on a NOT NULL column compiles exactly as before: no
    answerable count, so no measure pays for a question that cannot arise."""
    compiled = compile_query(
        db_session,
        cat,
        _query(
            measures=["events.disbursement_rc"],
            time={"range": {"start": date(2026, 9, 1), "end": date(2026, 9, 30)}},
        ),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    sql = str(compiled.select.compile(dialect=sqlite.dialect()))
    assert "count(" not in sql


def _graded_loan(bank: Bank, as_of: date, grade: str, band: str | None, amount: str):
    """A loan whose GRADE is the grouping key, so no dimension table is needed."""
    return _position(
        bank,
        as_of,
        grade=grade,
        dpd_band=band,
        balance_native=Decimal(amount),
        balance_rc=Decimal(amount),
        classification_exposure_rc=Decimal(amount),
        non_performing=False,
    )


def test_coverage_is_judged_per_group_not_for_the_whole_result(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """One grade has arrears data and one does not: the second is NULL while the
    first still reports, so a partly-loaded book is not blanked wholesale."""
    bank = _bank(db_session, ORG_1, f"PerGroup {uuid4().hex[:6]}")
    facts = [
        _graded_loan(bank, SEP_18, "standard", "90_179", "100"),
        _graded_loan(bank, SEP_18, "standard", "current", "300"),
        _graded_loan(bank, SEP_18, "substandard", None, "600"),
    ]
    db_session.add_all(facts)
    db_session.add_all(_aggregate_rows(facts))
    db_session.add(_calendar(bank, SEP_18, has_data=True, last_in_month=True))
    db_session.flush()
    compiled, rows = _run(
        db_session,
        cat,
        bank,
        _query(measures=["loans.par_90_pct"], dimensions=["loan.grade"]),
    )
    by_grade = {r["loan.grade"]: r["loans.par_90_pct"] for r in _as_dict(compiled, rows)}
    assert _num(by_grade["standard"]) == pytest.approx(25.0), path
    assert by_grade["substandard"] is None, path


def test_coverage_is_judged_per_period_in_a_comparison(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """The prior period is judged on its OWN rows: a book that gained arrears
    data this month shows a figure now and "not supplied" before, never 0 %."""
    bank = _bank(db_session, ORG_1, f"PerPeriod {uuid4().hex[:6]}")
    facts = [
        _graded_loan(bank, SEP_18, "standard", "90_179", "100"),
        _graded_loan(bank, SEP_18, "standard", "current", "300"),
        _graded_loan(bank, AUG_31, "standard", None, "400"),
    ]
    db_session.add_all(facts)
    db_session.add_all(_aggregate_rows(facts))
    for day in (AUG_31, SEP_18):
        db_session.add(_calendar(bank, day, has_data=True, last_in_month=True))
    db_session.flush()
    compiled, rows = _run(
        db_session,
        cat,
        bank,
        _query(measures=["loans.par_90_pct"], time={"as_of": SEP_18, "compare_to": AUG_31}),
    )
    row = _as_dict(compiled, rows)[0]
    assert _num(row["loans.par_90_pct"]) == pytest.approx(25.0), path
    assert row["loans.par_90_pct|prior"] is None, path
    assert row["loans.par_90_pct|delta"] is None
    assert row["loans.par_90_pct|delta_pct"] is None


# --- A6-03: a member id is client text, and is treated as such --------------------------------


_HOSTILE_ID = "<script>alert('x')</script>"
_OVERSIZED_ID = "a" * (MEMBER_ID_MAX_LENGTH + 1)


@pytest.mark.parametrize(
    "payload",
    [
        {"measures": [_OVERSIZED_ID]},
        {"measures": ["loans.balance_rc"], "dimensions": [_OVERSIZED_ID]},
        {
            "measures": ["loans.balance_rc"],
            "filters": [{"member": _OVERSIZED_ID, "op": "not_null"}],
        },
        {"measures": ["loans.balance_rc"], "sort": [{"member": _OVERSIZED_ID}]},
        {"measures": ["loans.balance_rc"], "top_n": {"dimension": _OVERSIZED_ID, "n": 1}},
        {"measures": ["loans.balance_rc"], "pivot": {"dimension": _OVERSIZED_ID}},
    ],
)
def test_an_oversized_member_id_is_refused_in_every_member_field(payload: dict[str, Any]) -> None:
    """Every field that names a member carries the same bound.

    ``measures`` and ``dimensions`` had only a LIST-length cap, so one 427-char
    id rode in inside a list while the four scalar fields rejected it (A6-03).
    All six now share one type, so the bound cannot be present on some and
    missing on others.
    """
    with pytest.raises(ValidationError, match="string_too_long|at most"):
        BiQuery.model_validate({**payload, "time": {"as_of": SEP_18}})
    # One character shorter is a normal (if unknown) id: the bound is a bound,
    # not a rejection of long-but-plausible names.
    assert (
        len(
            BiQuery.model_validate(
                {"measures": ["a" * MEMBER_ID_MAX_LENGTH], "time": {"as_of": SEP_18}}
            ).measures
        )
        == 1
    )


@pytest.mark.parametrize("field", ["measures", "dimensions", "filters"])
def test_a_malformed_member_id_is_never_reflected_into_the_error_body(
    db_session: Session, cat: Catalogue, mart: Bank, field: str
) -> None:
    """The id the caller sent does not come back — in the message OR in ``members``.

    ``members`` is serialised straight into the response body, so echoing raw
    client bytes there made the module's stated property false (A6-03: the
    message withheld the id and ``members`` did not).
    """
    payloads: dict[str, dict[str, Any]] = {
        "measures": {"measures": [_HOSTILE_ID]},
        "dimensions": {"measures": ["loans.balance_rc"], "dimensions": [_HOSTILE_ID]},
        "filters": {
            "measures": ["loans.balance_rc"],
            "filters": [{"member": _HOSTILE_ID, "op": "not_null"}],
        },
    }
    query = BiQuery.model_validate({**payloads[field], "time": {"as_of": SEP_18}})
    with pytest.raises(UnknownMember) as excinfo:
        compile_query(db_session, cat, query, organization_id=mart.organization_id, bank_id=mart.id)
    error = excinfo.value
    assert error.members == ()
    assert _HOSTILE_ID not in error.message
    assert "script" not in error.message
    # The three fields the API serialises into the 422 body (`read_bi._query_error`).
    body = json.dumps({"error_code": error.code, "message": error.message, "members": []})
    assert "script" not in body
    # The raw id stays available internally, for the caller that raised it.
    assert error.member_id == _HOSTILE_ID


def test_a_well_formed_unknown_member_is_still_named(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Withholding malformed ids must not cost a legitimate client its typo.

    ``loans.balnce_rc`` is shaped like a member id, so it is repeated in both
    the message and ``members`` — that is the field's whole purpose.
    """
    typo = "loans.balnce_rc"
    with pytest.raises(UnknownMember) as excinfo:
        compile_query(
            db_session,
            cat,
            _query(measures=[typo]),
            organization_id=mart.organization_id,
            bank_id=mart.id,
        )
    assert excinfo.value.members == (typo,)
    assert typo in excinfo.value.message


def test_the_echo_bound_is_the_schema_bound() -> None:
    """One number, so what the API accepts and what an error repeats cannot drift."""
    assert is_member_id("a" * MEMBER_ID_MAX_LENGTH)
    assert not is_member_id("a" * (MEMBER_ID_MAX_LENGTH + 1))
    assert not is_member_id(_HOSTILE_ID)
    assert is_member_id("loans.balance_rc")


# --- audit A360: missing data is never zero, on the KPI path too ----------------------------------
#
# Every measure that names no dimension is served from ``bi_agg_position_daily``,
# so a KPI tile reads whatever the builder stored per grain cell. H1: the builder
# stored 0 where the fact rows carry nothing, and the fixture above re-implemented
# that zero-fill, so no test could see it. The bank below has two branches that
# each leave a whole cell with NULL in a value column — and rows in it, so this
# is not the empty-population case: HOME's loans state no collateral, and FX's
# whole book is unconverted foreign currency. Both must read the SAME on the
# aggregate path as on the fact path, and what they read must be nothing.


def _h1_bank(db: Session) -> Bank:
    bank = _bank(db, ORG_1, f"H1 {uuid4().hex[:6]}")
    facts = [
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="HOME",
            sector="agri",
            balance=Decimal("400"),
            non_performing=False,
            counterparty=None,
            rate=Decimal("0.10"),
        ),
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="HOME",
            sector="agri",
            balance=Decimal("600"),
            non_performing=True,
            counterparty=None,
            rate=None,
            dpd_band="90_179",
            grade="substandard",
            stage=3,
        ),
        # FX: two loans, neither with a reporting-currency conversion.
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="FX",
            sector="trade",
            balance=None,
            non_performing=False,
            counterparty=None,
            rate=Decimal("0.30"),
            currency="USD",
        ),
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="FX",
            sector="trade",
            balance=None,
            non_performing=False,
            counterparty=None,
            rate=None,
            currency="USD",
        ),
    ]
    db.add_all(facts)
    db.add_all(_aggregate_rows(facts))
    db.add(_calendar(bank, SEP_18, has_data=True, last_in_month=True))
    db.add_all(
        [
            BiDimBranch(
                organization_id=bank.organization_id,
                bank_id=bank.id,
                branch_code=code,
                name=name,
                region="North",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            )
            for code, name in (("HOME", "Home branch"), ("FX", "Foreign-currency desk"))
        ]
    )
    db.flush()
    # The fixture must be ABLE to hold the state under test, or the tests below
    # prove nothing: at least one aggregate cell is all-NULL in ``collateral_rc``
    # and one in ``balance_rc``.
    cells = db.scalars(
        select(BiAggPositionDaily).where(BiAggPositionDaily.bank_id == bank.id)
    ).all()
    assert cells and all(cell.collateral_rc_sum is None for cell in cells)
    assert any(cell.balance_rc_sum is None and cell.row_count > 0 for cell in cells)
    return bank


_FX_ONLY = {"member": "branch.code", "op": "in", "values": ["FX"]}


def _kpi(  # noqa: PLR0913 - the path is asserted, not assumed
    db: Session, cat: Catalogue, bank: Bank, member_id: str, path: str, **overrides: Any
) -> Any:
    compiled, rows = _run(db, cat, bank, _query(measures=[member_id], **overrides))
    # A case that never reached the aggregate source would prove nothing about it.
    assert compiled.used_aggregate is (path == "auto"), (member_id, path)
    assert len(rows) == 1
    return rows[0][0]


def test_a_book_that_states_no_collateral_has_no_collateral_figure(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    bank = _h1_bank(db_session)
    assert _kpi(db_session, cat, bank, "loans.collateral_rc", path) is None
    # Not an empty population — the same book answers its other questions.
    assert _num(_kpi(db_session, cat, bank, "loans.balance_rc", path)) == 1000.0
    assert _kpi(db_session, cat, bank, "loans.count", path) == 4


def test_a_branch_whose_whole_book_is_unconverted_has_no_reporting_currency_balance(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    bank = _h1_bank(db_session)
    # As a KPI narrowed to the branch: nothing, never 0.00.
    assert _kpi(db_session, cat, bank, "loans.balance_rc", path, filters=[_FX_ONLY]) is None
    # It IS a population — both loans are there, both unconverted.
    _, rows = _run(
        db_session,
        cat,
        bank,
        _query(measures=["loans.count", "loans.unconverted_count"], filters=[_FX_ONLY]),
    )
    assert rows == [(2, 2)]
    # And as a group beside a branch that does carry a balance.
    compiled, rows = _run(
        db_session,
        cat,
        bank,
        _query(measures=["loans.balance_rc", "loans.count"], dimensions=["branch.code"]),
    )
    assert compiled.used_aggregate is (path == "auto")
    by_branch = {
        row["branch.code"]: (_num(row["loans.balance_rc"]), row["loans.count"])
        for row in _as_dict(compiled, rows)
    }
    assert by_branch == {"HOME": (1000.0, 2), "FX": (None, 2)}


def test_an_absent_figure_is_no_figure_to_an_alert_never_a_breach_of_a_floor(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """``alerts._observed_value`` is the one read an alert makes. A stored 0 here
    would satisfy ``observed < threshold`` for every floor the bank has — a
    "collateral below X" alert firing on a book that stated no collateral — so
    the answer has to be the typed absence, which ``_evaluate_one`` records as
    ``not_evaluated`` and never judges."""
    bank = _h1_bank(db_session)
    observed, refusal = alerts._observed_value(
        db_session,
        cat,
        _query(measures=["loans.collateral_rc"]),
        bank=bank,
        measure=cat.measure("loans.collateral_rc"),
    )
    assert (observed, refusal) == (None, alerts.REASON_NO_FIGURE), path
    # The same for a branch-scoped reader whose whole slice is unconverted: the
    # data-scope filter is injected exactly as ``_evaluate_one`` injects it.
    observed, refusal = alerts._observed_value(
        db_session,
        cat,
        _query(measures=["loans.balance_rc"]),
        bank=bank,
        measure=cat.measure("loans.balance_rc"),
        injected_filters=(BiFilter.model_validate(_FX_ONLY),),
    )
    assert (observed, refusal) == (None, alerts.REASON_NO_FIGURE), path


def test_target_attainment_is_not_computed_against_an_absent_figure(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """``mart_builder._measure_actuals`` is the builder's read of the actual a
    target is compared with; a 0 there is "0 % attained" on the board pack.
    ``_target_values`` leaves variance blank for a ``None`` actual, so the
    absence has to arrive here as ``None``."""
    bank = _h1_bank(db_session)
    collateral = mart_builder._measure_actuals(
        db_session,
        bank.organization_id,
        bank.id,
        SEP_18,
        cat.measure("loans.collateral_rc"),
        scope_dimension="",
        window_start=SEP_18,
    )
    assert collateral == {TARGET_BANK_WIDE_SCOPE: None}, path
    by_branch = mart_builder._measure_actuals(
        db_session,
        bank.organization_id,
        bank.id,
        SEP_18,
        cat.measure("loans.balance_rc"),
        scope_dimension="branch.code",
        window_start=SEP_18,
    )
    assert by_branch == {"HOME": Decimal(1000), "FX": None}, path


# --- audit A360 M3: a count on a date that was never built is not a count of zero -----------------
#
# ``SUM`` over nothing is NULL and says "no data"; ``COUNT`` over nothing is 0 and
# says "measured: none". A select with no row dimensions produces a row from no
# input, so on a date with no mart rows its count was 0 — and the dashboard reads
# 0 as a measurement. The compiler now gates an ungrouped COUNT on having seen a
# row (``_when_any_row``); a grouped select needs no gate because a group exists
# only where rows do, and its SQL is unchanged.

#: No calendar row and no fact row.
UNBUILT_DAY = date(2026, 9, 25)
#: A calendar row (``has_data=False``) and no fact row.
NO_DATA_DAY = date(2026, 9, 30)


@pytest.mark.parametrize("day", [UNBUILT_DAY, NO_DATA_DAY])
def test_a_count_on_a_date_that_was_never_built_is_null_not_zero(
    db_session: Session, cat: Catalogue, mart: Bank, path: str, day: date
) -> None:
    compiled, rows = _run(
        db_session,
        cat,
        mart,
        _query(
            measures=[
                "loans.count",
                "positions.count",
                "positions.unconverted_count",
                "loans.balance_rc",
            ],
            time={"as_of": day},
        ),
    )
    assert compiled.used_aggregate is (path == "auto")
    assert rows == [(None, None, None, None)], (day, path)


def test_a_built_date_whose_book_has_no_loans_counts_zero_loans(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """The control: rows were seen, the LOAN population is empty, and that is a
    measured zero — the distinction the gate exists to draw."""
    bank = _d042_bank(db_session, [("DEPOSIT", "deposit_account_type", "CURRENT", "1000")])
    assert _value(db_session, cat, bank, "loans.count") == 0, path
    assert _value(db_session, cat, bank, "positions.count") == 1, path
    assert _value(db_session, cat, bank, "loans.balance_rc") is None, path


def test_a_grouped_count_on_an_unbuilt_date_has_no_rows_and_its_rollup_total_is_null(
    db_session: Session, cat: Catalogue, mart: Bank, path: str
) -> None:
    _, rows = _run(
        db_session,
        cat,
        mart,
        _query(measures=["loans.count"], dimensions=["branch.code"], time={"as_of": NO_DATA_DAY}),
    )
    assert rows == [], path
    # The grand-total grouping set always yields a row, so it is gated like a KPI.
    compiled, rows = _run(
        db_session,
        cat,
        mart,
        _query(
            measures=["loans.count"],
            dimensions=["branch.code"],
            subtotals=True,
            time={"as_of": NO_DATA_DAY},
        ),
    )
    table = [
        (row["branch.code"], row["__level"], row["loans.count"]) for row in _as_dict(compiled, rows)
    ]
    assert table == [(None, 0, None)], path


def test_a_comparison_against_an_unbuilt_prior_date_has_no_prior_count(
    db_session: Session, cat: Catalogue, mart: Bank, path: str
) -> None:
    """A prior period nobody built is not a prior count of 0 — and so not a
    delta of +4 loans, which is what a 0 would have said."""
    compiled, rows = _run(
        db_session,
        cat,
        mart,
        _query(measures=["loans.count"], time={"as_of": SEP_18, "compare_to": date(2026, 7, 31)}),
    )
    (row,) = _as_dict(compiled, rows)
    assert row["loans.count"] == 4, path
    assert row["loans.count|prior"] is None, path
    assert row["loans.count|delta"] is None, path
    assert row["loans.count|delta_pct"] is None, path


def test_a_filter_that_matches_no_row_reads_as_no_data_exactly_as_a_sum_always_did(
    db_session: Session, cat: Catalogue, mart: Bank, path: str
) -> None:
    """A COUNT under a filter nothing satisfies is 0 — a MEASUREMENT, not absence.

    This test previously pinned the opposite, as a deliberate decision: a user
    filter is a WHERE clause, so a filter no row satisfies leaves the select with
    no row to see, and the count was made NULL beside the sum that is always NULL
    there. **Verification (auditor V1) showed that decision was wrong**, because
    the gate cannot tell an empty SELECTION from an unbuilt DATE, and it is the
    same expression for both. What a reader got was the worse of the two readings:
    "how many USD loans" on a bank that holds none rendered the needs-data panel —
    "the figure may not have been computed… or the data behind it may not have
    arrived" — for a bank whose book is complete and whose honest answer is none.
    A branch-scoped reader whose branch was simply quiet that day was told the
    platform had nothing for them.

    So the gate now applies only to an UNFILTERED, unpivoted select (and a
    rollup's grand total), where the filtered row count and the period's row count
    are the same fact. A filter matching nothing answers 0 for the count and NULL
    for the sum — which is not symmetry, but it is what each one means: nobody
    matched, versus nothing was added up.

    The unbuilt-date case the original finding was about is still covered, by
    `test_a_count_on_a_date_that_was_never_built_is_null_not_zero`.
    """
    _, rows = _run(
        db_session,
        cat,
        mart,
        _query(
            measures=["loans.count", "loans.balance_rc"],
            filters=[{"member": "position.currency", "op": "in", "values": ["XXX"]}],
        ),
    )
    assert rows == [(0, None)], path


# --- audit A360 (low): a ratio is an unrounded float, and that is a decision ------------------


def _npl_bank(db: Session, *, non_performing: str, performing: str) -> Bank:
    bank = _bank(db, ORG_1, f"NPL {uuid4().hex[:6]}")
    facts = [
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="B1",
            sector="agri",
            balance=Decimal(non_performing),
            non_performing=True,
            counterparty=None,
            rate=None,
            dpd_band="90_179",
            grade="substandard",
            stage=3,
        ),
        _loan(
            bank,
            SEP_18,
            position_id=uuid4(),
            branch="B1",
            sector="agri",
            balance=Decimal(performing),
            non_performing=False,
            counterparty=None,
            rate=None,
        ),
    ]
    db.add_all(facts)
    db.add_all(_aggregate_rows(facts))
    db.add(_calendar(bank, SEP_18, has_data=True, last_in_month=True))
    db.flush()
    return bank


def test_a_ratio_is_the_unrounded_float_quotient_not_the_engines_quantized_decimal(
    db_session: Session, cat: Catalogue, path: str
) -> None:
    """Audit A360-3 (low), left as it is on purpose and pinned so the choice is
    visible: ``compiler._ratio`` casts to ``Float`` and divides, so
    ``loans.npl_ratio_pct`` over 3,000,000 of 84,850,000 is ``3.5356511490866236``
    where the credit engine's ``npl_ratio_pct`` is the fraction quantized to
    ``ENGINE_RATIO_QUANTUM`` and scaled: ``Decimal('3.535700')``. Identical at
    two decimals, different in a full-precision export cell. The compiler's
    figure is a portfolio measure it computes; the engine's is the CERTIFIED
    figure, copied into ``engine.*`` never recomputed, and R1 reconciles the two
    at the engine's own quantum — so the export that needs the filed number
    reads the engine copy. Changing ``_ratio`` to Decimal arithmetic would move
    every ratio, share and HHI in the catalogue and every golden that reads
    them; it is not done here.
    """
    bank = _npl_bank(db_session, non_performing="3000000", performing="81850000")
    value = _value(db_session, cat, bank, "loans.npl_ratio_pct")
    assert isinstance(value, float), path
    assert value == pytest.approx(3_000_000 / 84_850_000 * 100, rel=1e-15), path
    engine = (Decimal(3_000_000) / Decimal(84_850_000)).quantize(
        reconciliation.ENGINE_RATIO_QUANTUM
    ) * 100
    assert engine == Decimal("3.535700")
    assert Decimal(str(value)) != engine, "the two figures are not the same number"
    assert Decimal(str(value)).quantize(Decimal("0.01")) == engine.quantize(Decimal("0.01"))
