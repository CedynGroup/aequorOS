"""SDI-scoped enterprise stress (docs/sdi.md §4.6, Phase H).

The bank stress engine is shared; the SDI regime rides on ``CapitalParams
.basel_applicable=False``: the projection's minima check omits the Basel sub-tier
+ leverage floors (only CAR + paid-up bind), and Appendix II omits the Table-2
Basel 3-tier capital build. Banks (``basel_applicable=True``) are unchanged.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.stress.appendix_ii import build_appendix_ii
from app.domain.stress.orchestrator import (
    EnterpriseStressInputs,
    run_enterprise_stress,
)
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    MinimaCheck,
    project_enterprise,
)
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    bog_capital_params,
    bog_forecast_params,
    bog_liquidity_params,
    capital_facts,
    liquidity_facts,
    sample_bank_latest_facts,
    severe_paths,
)


def _minima(*, basel_applicable: bool) -> MinimaCheck:
    # CAR + paid-up pass; CET1/Tier1/leverage all FAIL. Under s.29 the Basel legs
    # must not count as breaches.
    return MinimaCheck(
        car_pct=Decimal("15"),
        car_min_pct=Decimal("10"),
        car_ok=True,
        cet1_pct=Decimal("1"),
        cet1_min_pct=Decimal("8"),
        cet1_ok=False,
        tier1_pct=Decimal("1"),
        tier1_min_pct=Decimal("10"),
        tier1_ok=False,
        leverage_pct=Decimal("1"),
        leverage_min_pct=Decimal("5"),
        leverage_ok=False,
        paid_up=Decimal("20000000"),
        paid_up_min=Decimal("15000000"),
        paid_up_ok=True,
        basel_applicable=basel_applicable,
    )


def test_sdi_minima_ignores_the_basel_sub_tier_and_leverage_floors() -> None:
    sdi = _minima(basel_applicable=False)
    # Only CAR + paid-up bind under s.29 — both pass here → all_ok, no breaches.
    assert sdi.all_ok is True
    assert sdi.binding == ()


def test_bank_minima_still_binds_on_the_basel_floors() -> None:
    bank = _minima(basel_applicable=True)
    # Byte-identical bank behaviour: CET1/Tier1/leverage breaches still count.
    assert bank.all_ok is False
    assert set(bank.binding) == {"cet1", "tier1", "leverage"}


def _projection():
    return project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="SEVERE-2027",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )  # type: ignore[arg-type]
    )


def test_sdi_appendix_ii_omits_the_basel_table2_capital_build() -> None:
    projection = _projection()
    sdi_tables = build_appendix_ii(projection, severe_paths(), basel_applicable=False)
    # Table 2 (Basel CET1/AT1/Tier2 build) is excluded for an SDI ...
    assert sdi_tables.table2_capital.rows == ()
    # ... while the class-agnostic tables remain.
    assert sdi_tables.table1_summary is not None
    assert sdi_tables.table5_rwa is not None

    bank_tables = build_appendix_ii(projection, severe_paths())  # default: bank
    assert len(bank_tables.table2_capital.rows) > 0


def _orchestrator_inputs(*, basel_liquidity: bool) -> EnterpriseStressInputs:
    return EnterpriseStressInputs(
        scenario_code="SEVERE-2027",
        scenario_paths=severe_paths(),
        capital_facts=capital_facts(),
        capital_params=bog_capital_params(),
        liquidity_facts=liquidity_facts() if basel_liquidity else [],
        liquidity_params=bog_liquidity_params() if basel_liquidity else None,
        baseline_annual_preprovision_income=Decimal("180000000"),
        baseline_annual_credit_loss=Decimal("14000000"),
        baseline_credit_allowance=Decimal("15000000"),
        basel_liquidity=basel_liquidity,
    )


def test_sdi_enterprise_outcome_omits_the_basel_liquidity_leg() -> None:
    # QA audit 2026-08-20 P0-1: an SDI run must NOT present a Basel LCR/NSFR verdict.
    sdi = run_enterprise_stress(_orchestrator_inputs(basel_liquidity=False))
    assert sdi.liquidity is None
    assert sdi.coupling is None
    serialized = sdi.serialize()
    # The absence is an explicit "not assessed" marker naming the reason, never a
    # passing LCR — no consumer can mistake it for a Basel result.
    assert serialized["liquidity"] == {
        "assessed": False,
        "regime": "sdi_lmtd",
        "reason": (
            "Basel LCR/NSFR are excluded for an SDI (docs/sdi.md §4.6); the LMTD "
            "Table-1 stress replacement is pending a BoG SDI stress methodology."
        ),
    }
    assert "coupling" not in serialized
    # The solvency leg still runs — capital is assessed for an SDI.
    assert sdi.capital is not None


def test_bank_enterprise_outcome_keeps_the_basel_liquidity_leg() -> None:
    bank = run_enterprise_stress(_orchestrator_inputs(basel_liquidity=True))
    assert bank.liquidity is not None
    assert bank.coupling is not None
    serialized = bank.serialize()
    assert "stressed_lcr_pct" in serialized["liquidity"]  # type: ignore[operator]
    assert "coupling" in serialized


def test_sdi_projection_excludes_basel_lcr_nsfr() -> None:
    inputs_kw = dict(
        scenario_code="SEVERE-2027",
        scenario_paths=severe_paths(),
        facts=sample_bank_latest_facts(),
        params=bog_forecast_params(),
        plan=BASE_ASSUMPTIONS,
        horizon_years=3,
    )
    sdi = project_enterprise(EnterpriseProjectionInputs(**inputs_kw, basel_liquidity=False))  # type: ignore[arg-type]
    assert all(y.lcr_pct is None and y.nsfr_pct is None for y in sdi.stress)
    assert sdi.current.lcr_pct is None

    bank = project_enterprise(EnterpriseProjectionInputs(**inputs_kw))  # type: ignore[arg-type]
    assert all(y.lcr_pct is not None for y in bank.stress)  # bank byte-identical
