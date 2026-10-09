"""BoG CRD (June 2018) ¶98, ¶107, ¶139: fresh runs require corrected facts."""

from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from app.domain.capital.engine import CapitalComputationError
from app.models import Bank, BankFinancialFact, BankReportingPeriod, CurrentFinancialFact
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital, regulatory_forecasting
from app.services.fact_derivation import derive_current_facts, derive_facts
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.support.authority import grant_organization_analyst

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶107, ¶139")


def test_stale_facts_refuse_fresh_official_and_live_calculations_preserving_history(
    db_session: Session,
) -> None:
    """BoG CRD (June 2018) ¶98, ¶107, ¶139: stale bases refuse; sealed outputs survive."""
    seed_book(db_session)
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    derive_current_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    bank = db_session.get(Bank, derived.bank_id)
    period = db_session.get(BankReportingPeriod, derived.reporting_period_id)
    assert bank is not None and period is not None
    for module in (ModuleScope.CAPITAL, ModuleScope.FORECASTING):
        grant_organization_analyst(
            db_session,
            module,
            SensitivityScope.CONFIDENTIAL,
            grantor="credit-basis-test",
            reason="Exercise corrected and stale calculation bases.",
        )
    capital_payload = RegulatoryRunCreate(
        module="capital", reporting_period_id=period.id, scenario_code="baseline"
    )
    forecast_payload = ForecastRunCreate(reporting_period_id=period.id, scenario_code="base")
    capital = regulatory_capital.create_capital_run(db_session, MAKER, bank.id, capital_payload)
    forecast = regulatory_forecasting.create_forecast_run(
        db_session, MAKER, bank.id, forecast_payload
    )
    assert capital.status == "succeeded", capital.error
    assert forecast.status == "succeeded", forecast.error
    old_car = Decimal(cast(str, capital.metrics["car_pct"]))
    old_path = forecast.path
    db_session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == bank.id, BankFinancialFact.fact_group == "credit_exposure"
        )
    )
    db_session.execute(
        delete(CurrentFinancialFact).where(
            CurrentFinancialFact.bank_id == bank.id,
            CurrentFinancialFact.fact_group == "credit_exposure",
        )
    )
    db_session.flush()
    new_capital = regulatory_capital.create_capital_run(db_session, MAKER, bank.id, capital_payload)
    new_forecast = regulatory_forecasting.create_forecast_run(
        db_session, MAKER, bank.id, forecast_payload
    )
    for run in (new_capital, new_forecast):
        assert run.status == "failed"
        assert run.error is not None
        assert run.error.code == "credit_exposure_basis_missing"
    for compute in (regulatory_capital.compute_live, regulatory_forecasting.compute_live):
        with pytest.raises(CapitalComputationError, match="net CRD credit exposure basis"):
            compute(db_session, MAKER, bank, period)
    historic_capital = regulatory_capital.get_capital_dashboard(
        db_session, MAKER, bank.id, reporting_period_id=period.id
    )
    historic_forecast = regulatory_forecasting.get_forecast_run(
        db_session, MAKER, bank.id, forecast.id
    )
    assert historic_capital.metrics.car_pct == old_car
    assert historic_forecast.path == old_path
