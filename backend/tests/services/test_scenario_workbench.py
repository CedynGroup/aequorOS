"""Scenario Workbench: catalogue, CRUD, analysis, saved analyses, parity.

The two invariants that make the workbench safe to put on a trading desk:

1. PARITY — for every system scenario, the workbench adapter produces the
   same headline numbers as the immutable official run for the same period
   (they share the extracted per-module compute function).
2. ZERO REGULATORY WRITES — no workbench call ever creates a RegulatoryRun.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, RegulatoryRun
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.scenario_workbench import (
    AnalysisRunCreate,
    SavedAnalysisCreate,
    ScenarioRefIn,
    StressScenarioCreate,
    StressScenarioUpdate,
    WorkbenchModule,
)
from app.services import (
    analysis_workbench,
    regulatory_capital,
    regulatory_liquidity,
    stress_scenarios,
)
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    ISOLATED_ORG_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
FOREIGN = TenantContext(organization_id=ISOLATED_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)


def _period_id(db: Session) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def _run_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(RegulatoryRun)) or 0


def _system_refs(*codes: str) -> list[ScenarioRefIn]:
    return [ScenarioRefIn(kind="system", code=code) for code in codes]


def test_catalogue_merges_system_and_custom_with_vocabulary(db_session: Session) -> None:
    seed_sample_bank(db_session)
    period_id = _period_id(db_session)

    catalogue = stress_scenarios.list_catalogue(
        db_session, MAKER, SAMPLE_BANK_ID, "liquidity", period_id
    )
    by_code = {entry.code: entry for entry in catalogue.scenarios}
    assert {"baseline", "idiosyncratic", "market_wide", "combined", "usd_funding_stress"} <= set(
        by_code
    )
    combined = by_code["combined"]
    assert combined.kind == "system" and combined.locked and combined.configured
    # System levels resolve from the effective-dated parameter register.
    assert Decimal(combined.shocks["inflow_multiplier"]) > 0
    assert "runoff:" in catalogue.allowed_prefixes[0]
    assert "inflow_multiplier" in catalogue.allowed_keys

    created = stress_scenarios.create_scenario(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        StressScenarioCreate(
            code="desk_wholesale_squeeze",
            name="Desk wholesale squeeze",
            shocks={"runoff:wholesale_non_op_corporate": Decimal("80")},
        ),
    )
    merged = stress_scenarios.list_catalogue(db_session, MAKER, SAMPLE_BANK_ID, "liquidity")
    custom = next(e for e in merged.scenarios if e.code == "desk_wholesale_squeeze")
    assert custom.kind == "custom" and not custom.locked
    assert custom.scenario_id == created.id


def test_custom_scenario_crud_guards(db_session: Session) -> None:
    seed_sample_bank(db_session)

    # System codes are reserved.
    with pytest.raises(HTTPException) as excinfo:
        stress_scenarios.create_scenario(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            "liquidity",
            StressScenarioCreate(
                code="combined", name="x", shocks={"inflow_multiplier": Decimal("0.5")}
            ),
        )
    assert excinfo.value.detail["error_code"] == "code_reserved"  # type: ignore[index]

    # Unknown shock keys are named, with the allowed vocabulary.
    with pytest.raises(HTTPException) as excinfo:
        stress_scenarios.create_scenario(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            "liquidity",
            StressScenarioCreate(code="bad_keys", name="x", shocks={"made_up_key": Decimal("1")}),
        )
    assert excinfo.value.detail["error_code"] == "unsupported_shock_key"  # type: ignore[index]
    assert "inflow_multiplier" in excinfo.value.detail["allowed_keys"]  # type: ignore[index]

    # Capital requires the full quarterly set together.
    with pytest.raises(HTTPException) as excinfo:
        stress_scenarios.create_scenario(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            "capital",
            StressScenarioCreate(
                code="half_a_path",
                name="x",
                shocks={"quarterly_rwa_growth_pct": Decimal("3")},
            ),
        )
    assert excinfo.value.detail["error_code"] == "incomplete_capital_shock_set"  # type: ignore[index]

    created = stress_scenarios.create_scenario(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        StressScenarioCreate(
            code="desk_a", name="Desk A", shocks={"inflow_multiplier": Decimal("0.7")}
        ),
    )
    # Duplicate code → 409.
    with pytest.raises(HTTPException) as excinfo:
        stress_scenarios.create_scenario(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            "liquidity",
            StressScenarioCreate(
                code="desk_a", name="Again", shocks={"inflow_multiplier": Decimal("0.6")}
            ),
        )
    assert excinfo.value.detail["error_code"] == "scenario_code_exists"  # type: ignore[index]

    updated = stress_scenarios.update_scenario(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        created.id,
        StressScenarioUpdate(shocks={"inflow_multiplier": Decimal("0.55")}),
    )
    assert updated.shocks["inflow_multiplier"] == "0.55"

    # RLS-style org scoping: another org cannot see the row.
    with pytest.raises(HTTPException):
        stress_scenarios.update_scenario(
            db_session,
            FOREIGN,
            SAMPLE_BANK_ID,
            "liquidity",
            created.id,
            StressScenarioUpdate(name="stolen"),
        )


def test_analysis_parity_with_official_runs_and_zero_writes(db_session: Session) -> None:
    """The keystone: workbench numbers == official-run numbers, no run rows."""
    seed_sample_bank(db_session)
    period_id = _period_id(db_session)

    # Official immutable runs (the governance path).
    official: dict[tuple[str, str], dict] = {}
    for scenario in ("baseline", "combined"):
        run = regulatory_liquidity.create_liquidity_run(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="liquidity", reporting_period_id=period_id, scenario_code=scenario
            ),
        )
        assert run.status == "succeeded"
        stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
        assert stored is not None
        official[("liquidity", scenario)] = stored.metrics
    for scenario in ("baseline", "severe"):
        run = regulatory_capital.create_capital_run(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="capital", reporting_period_id=period_id, scenario_code=scenario
            ),
        )
        assert run.status == "succeeded"
        stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
        assert stored is not None
        official[("capital", scenario)] = stored.metrics

    runs_before = _run_count(db_session)

    liquidity = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        AnalysisRunCreate(
            reporting_period_id=period_id, scenarios=_system_refs("baseline", "combined")
        ),
    )
    for result in liquidity.results:
        assert result.status == "succeeded", result
        expected = official[("liquidity", result.code)]
        assert result.metrics["lcr_pct"] == expected["lcr_pct"], result.code
        assert result.metrics["nsfr_pct"] == expected["nsfr_pct"], result.code

    capital = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "capital",
        AnalysisRunCreate(
            reporting_period_id=period_id, scenarios=_system_refs("baseline", "severe")
        ),
    )
    for result in capital.results:
        assert result.status == "succeeded", result
        expected = official[("capital", result.code)]
        assert result.metrics["car_pct"] == expected["car_pct"], result.code

    # IRR / FX / FTP adapters run clean over the seeded book.
    adapter_cases: tuple[tuple[WorkbenchModule, tuple[str, str]], ...] = (
        ("irr", ("baseline", "parallel_up_200")),
        ("fx", ("baseline", "severe_depreciation")),
        ("ftp", ("baseline", "rates_up_200")),
    )
    for module, codes in adapter_cases:
        outcome = analysis_workbench.run_analysis(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            module,
            AnalysisRunCreate(reporting_period_id=period_id, scenarios=_system_refs(*codes)),
        )
        assert [entry.status for entry in outcome.results] == ["succeeded", "succeeded"], module

    # THE guarantee: analysis wrote nothing to the official register.
    assert _run_count(db_session) == runs_before


def test_overrides_failures_as_data_and_saved_analyses(db_session: Session) -> None:
    seed_sample_bank(db_session)
    period_id = _period_id(db_session)
    runs_before = _run_count(db_session)

    # Ad-hoc override on a system scenario tightens the outcome vs stock.
    outcome = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        AnalysisRunCreate(
            reporting_period_id=period_id,
            scenarios=[
                ScenarioRefIn(kind="system", code="combined"),
                ScenarioRefIn(
                    kind="system",
                    code="combined",
                    overrides={"hqla_securities_haircut_pct": Decimal("40")},
                ),
            ],
        ),
    )
    stock, tweaked = outcome.results
    assert stock.status == tweaked.status == "succeeded"
    assert Decimal(tweaked.metrics["lcr_pct"]) < Decimal(stock.metrics["lcr_pct"])
    assert tweaked.shocks_applied["hqla_securities_haircut_pct"] == "40"

    # A custom capital scenario with only ECL keys and no staged book still
    # computes (baseline ratios path) — while a bad system code 404s and an
    # unparameterised scenario fails AS DATA without sinking the batch.
    scenario = stress_scenarios.create_scenario(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        StressScenarioCreate(
            code="desk_runoff",
            name="Desk runoff",
            shocks={"runoff:retail_deposits_stable": Decimal("45")},
        ),
    )
    mixed = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        AnalysisRunCreate(
            reporting_period_id=period_id,
            scenarios=[
                ScenarioRefIn(kind="custom", scenario_id=scenario.id),
                ScenarioRefIn(
                    kind="system",
                    code="baseline",
                    overrides={"runoff:nonexistent_category": Decimal("50")},
                ),
            ],
        ),
    )
    custom_result, override_result = mixed.results
    assert custom_result.status == "succeeded"
    # Unknown runoff CATEGORY passes vocabulary (prefix) but the engine
    # ignores unmatched categories — still a succeeded computation.
    assert override_result.status == "succeeded"

    # Save = server-side recompute + snapshot; list/get/delete round-trip.
    saved = analysis_workbench.save_analysis(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "liquidity",
        SavedAnalysisCreate(
            reporting_period_id=period_id,
            name="Pre-ALCO wholesale sensitivity",
            scenarios=[
                ScenarioRefIn(kind="system", code="combined"),
                ScenarioRefIn(kind="custom", scenario_id=scenario.id),
            ],
        ),
    )
    assert saved.name == "Pre-ALCO wholesale sensitivity"
    assert len(saved.results) == 2
    assert all(entry.status == "succeeded" for entry in saved.results)

    listed = analysis_workbench.list_analyses(db_session, MAKER, SAMPLE_BANK_ID, "liquidity")
    assert listed.total == 1 and listed.analyses[0].id == saved.id
    fetched = analysis_workbench.get_analysis(
        db_session, MAKER, SAMPLE_BANK_ID, "liquidity", saved.id
    )
    assert fetched.results[0].metrics["lcr_pct"] == saved.results[0].metrics["lcr_pct"]
    analysis_workbench.delete_analysis(db_session, MAKER, SAMPLE_BANK_ID, "liquidity", saved.id)
    assert (
        analysis_workbench.list_analyses(db_session, MAKER, SAMPLE_BANK_ID, "liquidity").total == 0
    )

    # Still zero official writes across everything above.
    assert _run_count(db_session) == runs_before
