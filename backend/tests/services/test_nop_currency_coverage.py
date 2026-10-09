"""Notice BG/FMD/2026/07 ¶1(a)–(b): every currency enters the NOP basis."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.errors import ModuleDataUnavailable
from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.fx.engine import compute_nop
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    RegulatoryRun,
)
from app.services import regulatory_fx
from app.services.fact_derivation import (
    GroupResult,
    _derive_fx_positions,  # pyright: ignore[reportPrivateUsage]
)
from app.services.regulatory_reporting import dbk_generation, generation
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext
from app.services.regulatory_reporting.bog_forms.sources_ext import bsd13
from app.services.regulatory_reporting.registry import get_definition
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.services.test_derivation_fail_closed_defaults import (
    _canonical,  # pyright: ignore[reportPrivateUsage]
    _row,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.factories.canonical import seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1

pytestmark = pytest.mark.requirement("Notice BG/FMD/2026/07 ¶1(a)–(b)")


@pytest.mark.parametrize("currency", ["USD", "CHF", "JPY"])
@pytest.mark.parametrize("quoted", [False, True])
def test_currency_without_history_is_written_and_measured_or_refused(
    currency: str, quoted: bool
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b), "NET OPEN POSITION COMPUTATION": no omission."""
    canonical = _canonical(
        _row("FX/LOAN", "LOAN", currency=currency, balance="1000", balance_ghs="12850")
    )
    if quoted:
        canonical.refs["fx_rates_current"] = [{"currency": currency, "spot_rate": "12.85"}]
    groups: list[GroupResult] = []
    specs, included = _derive_fx_positions(canonical, groups)
    assert included == {currency}
    position_specs = [spec for spec in specs if spec.fact_group == "fx_position"]
    assert len(position_specs) == 1
    assert position_specs[0].amount == Decimal("12850")
    facts = [
        BankFinancialFact(
            fact_group=spec.fact_group,
            category=spec.category,
            amount=spec.amount,
            attributes=spec.attributes,
        )
        for spec in position_specs
    ]
    if not quoted:
        with pytest.raises(NotComputable) as exc:
            regulatory_fx._read_positions(facts)  # pyright: ignore[reportPrivateUsage]
        assert exc.value.blocks_filing
        assert currency in str(exc.value)
        return
    read = regulatory_fx._read_positions(facts)  # pyright: ignore[reportPrivateUsage]
    nop = compute_nop(list(read.positions), Decimal("50000"), Decimal("10"), Decimal("20"))
    assert nop.overall_nop == Decimal("12850")
    assert nop.single_ccy_max_pct == Decimal("25.700000")
    assert not nop.within_single_limit
    assert not nop.within_aggregate_limit
    assert any("return history" in warning for group in groups for warning in group.warnings)


@pytest.mark.parametrize("source", ["reference", "market"])
def test_one_historical_spot_is_not_a_return_history_or_a_current_quote(source: str) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): one historical spot cannot license NOP omission."""
    canonical = _canonical(
        _row("USD/ONE-SPOT", "LOAN", currency="USD", balance="1000", balance_ghs="12850")
    )
    if source == "reference":
        canonical.refs["fx_rates_historical"] = [
            {"currency": "USD", "spot_rate": "12.85", "date": canonical.as_of.isoformat()}
        ]
    else:
        canonical.market_fx_history["USD"] = [(canonical.as_of, Decimal("12.85"))]
    specs, included = _derive_fx_positions(canonical, [])
    assert included == {"USD"}
    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    fact = BankFinancialFact(
        fact_group=position.fact_group,
        category=position.category,
        amount=position.amount,
        attributes=position.attributes,
    )
    with pytest.raises(NotComputable) as exc:
        regulatory_fx._read_positions([fact])  # pyright: ignore[reportPrivateUsage]
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert "USD" in str(exc.value)


@pytest.mark.parametrize(
    ("hedge_sale", "capital_long", "capital_short", "deposit_ghs"),
    [("500", "6425", "0", None), ("2000", "0", "12850", None), ("500", "0", "0", "12000")],
)
def test_stricter_nop_quotes_preserve_the_capital_fx_basis(
    hedge_sale: str, capital_long: str, capital_short: str, deposit_ghs: str | None
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): NOP quote rules do not rewrite the CAR basis."""
    canonical = _canonical(
        _row("USD/LOAN", "LOAN", currency="USD", balance="1000", balance_ghs="12850"),
        _row(
            "USD/HEDGE",
            "FX_HEDGE",
            currency="USD",
            balance=hedge_sale,
            attributes={"sell_currency": "USD", "buy_currency": "GHS", "contract_rate": "12.85"},
        ),
    )
    if deposit_ghs is not None:
        canonical.positions.append(
            _row("USD/DEPOSIT", "DEPOSIT", currency="USD", balance="1000", balance_ghs=deposit_ghs)
        )
    specs, included = _derive_fx_positions(canonical, [])
    assert included == {"USD"}
    market = {spec.category: spec.amount for spec in specs if spec.fact_group == "market_risk"}
    assert market == {
        "net_long_fx": Decimal(capital_long),
        "net_short_fx": Decimal(capital_short),
    }
    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    fact = BankFinancialFact(
        fact_group=position.fact_group,
        category=position.category,
        amount=position.amount,
        attributes=position.attributes,
    )
    with pytest.raises(NotComputable) as exc:
        regulatory_fx._read_positions([fact])  # pyright: ignore[reportPrivateUsage]
    assert exc.value.blocks_filing
    assert "USD" in str(exc.value)


def _book(session: Session) -> tuple[Bank, BankReportingPeriod, TenantContext]:
    materialize_canonical_test_book(session)
    bank = session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    period = session.scalar(
        select(BankReportingPeriod)
        .where(BankReportingPeriod.bank_id == bank.id)
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert period is not None
    seed_canonical_fixture(session, organization_id=ORG_1, bank_id=bank.id, as_of=period.period_end)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
    run = regulatory_fx._create_and_execute(session, ctx, bank, period, "baseline")  # pyright: ignore[reportPrivateUsage]
    assert run.status == "succeeded"
    return bank, period, ctx


def _add_unrepresented_currency(session: Session, bank: Bank, period: BankReportingPeriod) -> None:
    batch = session.scalar(select(IngestionBatch).where(IngestionBatch.bank_id == bank.id))
    assert batch is not None
    lineage = session.scalar(
        select(LineageRecord).where(LineageRecord.ingestion_batch_id == batch.id)
    )
    assert lineage is not None
    position = CanonicalPosition(
        organization_id=ORG_1,
        bank_id=bank.id,
        as_of_date=period.period_end,
        source_system="API_PUSH",
        source_reference="CHF/NO-HISTORY",
        position_type="LOAN",
        currency="CHF",
        validation_status="accepted",
        ingestion_batch_id=batch.id,
        lineage_id=lineage.id,
    )
    session.add(position)
    session.flush()
    session.add(
        CanonicalPositionSnapshot(
            organization_id=ORG_1,
            bank_id=bank.id,
            as_of_date=period.period_end,
            source_system="API_PUSH",
            source_reference="CHF/NO-HISTORY",
            position_id=position.id,
            balance=Decimal("1000"),
            attributes={"balance_ghs": "12850"},
            validation_status="accepted",
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
        )
    )
    session.flush()


def test_fresh_fx_analysis_refuses_pre_fix_facts_that_omit_a_currency(db_session: Session) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): re-derive an incomplete official currency basis."""
    bank, period, ctx = _book(db_session)
    _add_unrepresented_currency(db_session, bank, period)
    facts = regulatory_fx._load_facts(db_session, ctx, bank, period)  # pyright: ignore[reportPrivateUsage]
    active = regulatory_fx._load_fx_params_or_none(db_session, ctx, bank, period.period_end)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(NotComputable) as exc:
        regulatory_fx._run_analysis(db_session, ctx, bank, period, facts, active)  # pyright: ignore[reportPrivateUsage]
    assert exc.value.state is OutcomeState.DATA_QUALITY_BLOCK
    assert "CHF" in str(exc.value)
    assert exc.value.blocks_filing


def test_stored_dashboard_refuses_a_partial_currency_book(db_session: Session) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): a stored headline must cover every currency."""
    bank, period, ctx = _book(db_session)
    _add_unrepresented_currency(db_session, bank, period)
    with pytest.raises(ModuleDataUnavailable) as exc:
        regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, period.id)
    assert "CHF" in str(exc.value)


@pytest.mark.parametrize("return_code", ["DBK-DAILY", "FX-NOP"])
def test_filed_return_refuses_an_older_partial_fx_run(
    db_session: Session, return_code: str
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b), ¶3: a succeeded partial run is not filing evidence."""
    bank, period, ctx = _book(db_session)
    _add_unrepresented_currency(db_session, bank, period)
    definition = get_definition(return_code)
    assert definition is not None
    with pytest.raises(HTTPException) as exc:
        if return_code == "DBK-DAILY":
            dbk_generation.generate_dbk(db_session, ctx, bank, period, definition)
        else:
            generation._generate_fx(db_session, ctx, bank, period, definition)  # pyright: ignore[reportPrivateUsage]
    assert exc.value.status_code == 409
    assert "CHF" in str(exc.value.detail)


@pytest.mark.parametrize("measure", ["afop", "net", "net_ghs"])
def test_bsd13_refuses_a_partial_run_including_other_currencies(
    db_session: Session, measure: str
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): BSD13 may not report partial Other Currencies."""
    bank, period, ctx = _book(db_session)
    _add_unrepresented_currency(db_session, bank, period)
    rc = ResolveContext(db_session, ctx, bank, period, "other")
    with pytest.raises(NotComputable) as exc:
        bsd13._nop(rc, {"currency": "other", "measure": measure})  # pyright: ignore[reportPrivateUsage]
    assert "CHF" in str(exc.value)
    assert exc.value.blocks_filing


def test_no_history_currency_recovers_only_with_complete_fx_evidence(db_session: Session) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b), ¶3: recovery preserves every currency in filings."""
    bank, period, ctx = _book(db_session)
    before_other = bsd13._nop(  # pyright: ignore[reportPrivateUsage]
        ResolveContext(db_session, ctx, bank, period, "other"),
        {"currency": "other", "measure": "net_ghs"},
    )
    assert isinstance(before_other, Decimal)
    _add_unrepresented_currency(db_session, bank, period)
    canonical = _canonical(
        _row("CHF/NO-HISTORY", "LOAN", currency="CHF", balance="1000", balance_ghs="12850")
    )
    canonical.refs["fx_rates_current"] = [{"currency": "CHF", "spot_rate": "12.85"}]
    specs, _ = _derive_fx_positions(canonical, [])
    for spec in specs:
        if spec.fact_group == "fx_position":
            db_session.add(
                BankFinancialFact(
                    organization_id=ORG_1,
                    bank_id=bank.id,
                    reporting_period_id=period.id,
                    fact_group=spec.fact_group,
                    category=spec.category,
                    amount=spec.amount,
                    currency="CHF",
                    attributes=spec.attributes,
                )
            )
    db_session.flush()
    failed = regulatory_fx._create_and_execute(  # pyright: ignore[reportPrivateUsage]
        db_session, ctx, bank, period, "baseline"
    )
    assert failed.status == "failed"
    stored = db_session.get(RegulatoryRun, failed.id)
    assert stored is not None
    assert "fx_return_history:CHF" in str(stored.error_details)
    workbench = regulatory_fx.compute_scenario_analysis(db_session, ctx, bank, period, {})
    assert "CHF" in {row.currency for row in workbench.nop.currencies}
    history = list(
        db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group == "fx_return_history",
                BankFinancialFact.category == "USD",
            )
        )
    )
    assert history
    for fact in history:
        db_session.add(
            BankFinancialFact(
                organization_id=ORG_1,
                bank_id=bank.id,
                reporting_period_id=period.id,
                fact_group=fact.fact_group,
                category="CHF",
                amount=fact.amount,
                currency="CHF",
                attributes={**fact.attributes, "currency": "CHF"},
            )
        )
    db_session.flush()
    complete = regulatory_fx._create_and_execute(  # pyright: ignore[reportPrivateUsage]
        db_session, ctx, bank, period, "baseline"
    )
    assert complete.status == "succeeded"
    for return_code in ("DBK-DAILY", "FX-NOP"):
        definition = get_definition(return_code)
        assert definition is not None
        generated = (
            dbk_generation.generate_dbk(db_session, ctx, bank, period, definition)
            if return_code == "DBK-DAILY"
            else generation._generate_fx(  # pyright: ignore[reportPrivateUsage]
                db_session, ctx, bank, period, definition
            )
        )
        assert "CHF" in str(generated.snapshot)
    after_other = bsd13._nop(  # pyright: ignore[reportPrivateUsage]
        ResolveContext(db_session, ctx, bank, period, "other"),
        {"currency": "other", "measure": "net_ghs"},
    )
    assert after_other == before_other + Decimal("12850")
