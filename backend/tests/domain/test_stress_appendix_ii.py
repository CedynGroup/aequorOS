"""Hand-verified tests for the Appendix II Tables 1–6 builders (Phase 2 item 3)."""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.stress.appendix_ii import (
    CRD_EXPOSURE_CLASSES,
    Pillar2Requirement,
    build_appendix_ii,
    build_table6,
    thousands,
)
from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionPlan,
    apply_management_actions,
)
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    project_enterprise,
)
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_capital_params,
    bog_forecast_params,
    sample_bank_latest_facts,
    severe_paths,
)


def _projection(paths, **overrides):
    defaults = {
        "scenario_code": "SEVERE-2027",
        "scenario_paths": paths,
        "facts": sample_bank_latest_facts(),
        "params": bog_forecast_params(),
        "plan": BASE_ASSUMPTIONS,
        "horizon_years": 3,
    }
    defaults.update(overrides)
    return project_enterprise(EnterpriseProjectionInputs(**defaults))  # type: ignore[arg-type]


def test_tables_have_the_prescribed_shape() -> None:
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths(), paid_up_min=Decimal("400000000"))
    # current + base Y1-3 + stress Y1-3.
    assert [row.label for row in tables.table2_capital.rows] == [
        "current",
        "base_y1",
        "base_y2",
        "base_y3",
        "stress_y1",
        "stress_y2",
        "stress_y3",
    ]
    assert len(tables.table4_financial_position.rows) == 7
    assert len(tables.table5_rwa.rows) == 7
    # P&L is projected years only (no as-of P&L).
    assert len(tables.table3_profit_and_loss.rows) == 6
    assert len(tables.table1_summary.pre_adverse) == 3
    assert len(tables.table1_summary.post_adverse) == 3


def test_table2_cet1_build_ties_to_the_engine_components() -> None:
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths())
    current = tables.table2_capital.rows[0]  # as-of
    cet1 = current.cet1
    # Sample bank CET1 components (GHS'000): 150,000 paid-up, 95,000 retained,
    # 45,000 statutory, 10,000 other reserves; 25,000 + 15,000 deductions.
    assert cet1.paid_up == Decimal("150000.000")
    assert cet1.retained_earnings == Decimal("95000.000")
    assert cet1.statutory_reserves == Decimal("45000.000")
    assert cet1.other_reserves == Decimal("10000.000")
    assert cet1.gross_cet1 == Decimal("300000.000")
    assert cet1.deduction_intangibles == Decimal("25000.000")
    assert cet1.deduction_dta == Decimal("15000.000")
    assert cet1.total_deductions == Decimal("40000.000")
    assert cet1.cet1_after_deductions == Decimal("260000.000")


def test_table5_pillar1_rwa_ties_to_table1_stressed_rwa() -> None:
    """The one hard directive invariant: T5 stressed Pillar-1 RWA == T1 stressed RWA."""
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths())
    t5 = {row.label: row.total_pillar1_rwa for row in tables.table5_rwa.rows}
    for snapshot in tables.table1_summary.post_adverse:
        assert t5[snapshot.label] == snapshot.total_rwa
    # Pillar-1 requirement is 13% of total Pillar-1 RWA (computed in full GHS,
    # then reported in GHS'000 — the builder's basis).
    stress_row = next(row for row in tables.table5_rwa.rows if row.label == "stress_y1")
    stress_year = next(year for year in projection.stress if year.year == 1)
    assert stress_row.pillar1_requirement == thousands(
        stress_year.rwa.total_rwa * Decimal("13") / Decimal("100")
    )


def test_table1_impact_allocates_the_full_adverse_loss() -> None:
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths())
    for (year, losses), base_year, stress_year in zip(
        tables.table1_summary.impact_of_adverse,
        projection.base,
        projection.stress,
        strict=True,
    ):
        assert year == stress_year.year
        assert {loss.exposure_class for loss in losses} == set(CRD_EXPOSURE_CLASSES)
        total_allocated = sum((loss.loss for loss in losses), Decimal("0"))
        adverse = (stress_year.pnl.credit_losses - base_year.pnl.credit_losses) / Decimal("1000")
        assert abs(total_allocated - adverse) < Decimal("0.01")
        # The corporate/retail book carries the bulk of the adverse impairment.
        by_class = {loss.exposure_class: loss.loss for loss in losses}
        assert by_class["corporates"] > Decimal("0")


def test_table1_capital_gap_reflects_the_paid_up_shortfall() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    tables = build_appendix_ii(projection, severe_paths(), paid_up_min=Decimal("400000000"))
    # 400,000 floor − 150,000 paid-up = 250,000 (GHS'000) shortfall, and the CAR
    # stays above 13% so the paid-up gap dominates.
    assert tables.table1_summary.capital_gap == Decimal("250000.000")
    for _year, amount in tables.table1_summary.capital_required_paid_up:
        assert amount == Decimal("250000.000")


def test_management_action_blocks_are_explicit_placeholders() -> None:
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths())
    # A run that models NO plan leaves the Phase 3 blocks explicitly None (not a
    # fabricated zero) — the pre-management-action projection stands alone.
    assert tables.table1_summary.management_actions is None
    assert tables.table1_summary.post_capitalisation is None
    assert tables.table1_summary.residual_capital_required_after_actions is None


def _management_result(
    projection, plan, *, severity: str | None = "severe", paid_up_min=Decimal("0")
):
    return apply_management_actions(
        projection,
        plan,
        severity=severity,
        capital_params=bog_capital_params(),
        paid_up_min=paid_up_min,
        car_target_pct=Decimal("13"),
    )


def test_table1_management_actions_block_reports_a_capital_raise() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    plan = ManagementActionPlan(
        plan_id="raise_only",
        name="Equity issuance",
        actions=(
            ManagementAction(
                action_id="equity",
                kind="raise_capital",
                label="Rights issue",
                trigger=ActionTrigger(kind="always"),
                effective_year=1,
                capital_raise_ghs=Decimal("100000000"),
                counts_as_paid_up=True,
            ),
        ),
    )
    result = _management_result(
        projection, plan, severity=None, paid_up_min=Decimal("400000000")
    )
    tables = build_appendix_ii(
        projection,
        severe_paths(),
        paid_up_min=Decimal("400000000"),
        management_actions=result,
    )
    block = tables.table1_summary.management_actions
    assert block is not None
    assert block.plan_id == "raise_only"
    row1 = block.rows[0]
    # 100M raise reported in GHS'000, all CET1, no RWA relief / dividend.
    assert row1.capital_raised_cet1 == Decimal("100000.000")
    assert row1.capital_raised_total == Decimal("100000.000")
    assert row1.revision_of_dividend_policy == Decimal("0.000")
    assert row1.rwa_relief_total == Decimal("0.000")
    assert row1.total_management_actions == Decimal("100000.000")
    # Post-capitalisation snapshot ties to the WITH-actions position (CAR 21.0053%).
    post_cap = tables.table1_summary.post_capitalisation
    assert post_cap is not None
    assert post_cap[0].label == "post_cap_y1"
    assert post_cap[0].car_pct == Decimal("21.005310")
    # 150M paid-up + a 100M equity raise ⇒ 250,000.000 (GHS'000).
    assert post_cap[0].paid_up == Decimal("250000.000")
    # The T5 == T1 stressed-RWA tie is untouched by the management overlay.
    t5 = {row.label: row.total_pillar1_rwa for row in tables.table5_rwa.rows}
    for snapshot in tables.table1_summary.post_adverse:
        assert t5[snapshot.label] == snapshot.total_rwa


def test_table1_rwa_relief_is_reported_as_a_capital_equivalent() -> None:
    projection = _projection(severe_paths())
    plan = ManagementActionPlan(
        plan_id="derisk",
        name="Risk reduction",
        actions=(
            ManagementAction(
                action_id="cut_limits",
                kind="reduce_risk",
                label="Reduce RWA",
                trigger=ActionTrigger(kind="always"),
                effective_year=1,
                rwa_reduction_ghs=Decimal("200000000"),
            ),
        ),
    )
    result = _management_result(projection, plan, severity=None)
    tables = build_appendix_ii(projection, severe_paths(), management_actions=result)
    row1 = tables.table1_summary.management_actions.rows[0]  # type: ignore[union-attr]
    # 200M relief × 13% CAR target = 26M freed capital ⇒ 26,000.000 (GHS'000).
    assert row1.risk_reduction == Decimal("26000.000")
    assert row1.rwa_relief_total == Decimal("200000.000")
    assert row1.total_management_actions == Decimal("26000.000")
    assert row1.capital_raised_total == Decimal("0.000")


def test_table1_residual_after_actions_is_zero_when_restored() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    plan = ManagementActionPlan(
        plan_id="restore",
        name="Fill residual",
        actions=(
            ManagementAction(
                action_id="fill",
                kind="raise_capital",
                label="Capital raise to restore adequacy",
                trigger=ActionTrigger(kind="on_breach"),
                effective_year=1,
                sizing="fill_residual",
                capital_raise_ghs=Decimal("1"),
                counts_as_paid_up=True,
            ),
        ),
    )
    result = _management_result(
        projection, plan, paid_up_min=Decimal("400000000")
    )
    tables = build_appendix_ii(
        projection,
        severe_paths(),
        paid_up_min=Decimal("400000000"),
        management_actions=result,
    )
    residual = tables.table1_summary.residual_capital_required_after_actions
    assert residual is not None
    assert residual.worst == Decimal("0.000")
    assert all(row.residual_capital_required == Decimal("0.000") for row in residual.rows)
    assert tables.table1_summary.management_actions.stays_above_all_minima is True  # type: ignore[union-attr]


def test_serialized_management_blocks_are_json_safe() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    result = _management_result(
        projection,
        ManagementActionPlan(
            plan_id="p",
            name="p",
            actions=(
                ManagementAction(
                    action_id="fill",
                    kind="raise_capital",
                    label="raise",
                    trigger=ActionTrigger(kind="on_breach"),
                    sizing="fill_residual",
                    capital_raise_ghs=Decimal("1"),
                    counts_as_paid_up=True,
                ),
            ),
        ),
        paid_up_min=Decimal("400000000"),
    )
    tables = build_appendix_ii(
        projection,
        severe_paths(),
        paid_up_min=Decimal("400000000"),
        management_actions=result,
    )
    serialized = tables.serialize()
    json.dumps(serialized)
    t1 = serialized["table1_summary"]
    assert t1["management_actions"] is not None  # type: ignore[index,call-overload]
    assert t1["post_capitalisation"] is not None  # type: ignore[index,call-overload]
    assert t1["residual_capital_required_after_actions"] is not None  # type: ignore[index,call-overload]


def test_table6_carries_base_and_stress_driver_paths() -> None:
    table6 = build_table6(severe_paths(), source="BoG/IMF")
    # 7 variables × 4 years (0..3).
    assert len(table6.rows) == 28
    assert table6.source == "BoG/IMF"
    fx = [row for row in table6.rows if row.variable == "fx_usd_ghs"]
    year3 = next(row for row in fx if row.year_index == 3)
    assert year3.base_value == Decimal("12.5")
    assert year3.stress_value == Decimal("15.0")


def test_table3_opening_retained_starts_from_as_of() -> None:
    projection = _projection(severe_paths())
    tables = build_appendix_ii(projection, severe_paths())
    base_y1 = next(
        row for row in tables.table3_profit_and_loss.rows if row.label == "base_y1"
    )
    # As-of retained earnings 95,000 (GHS'000).
    assert base_y1.opening_retained_earnings == Decimal("95000.000")


def test_bottom_up_decomposition_overlay_drives_table1_impact() -> None:
    """Phase 4: the real exposure-class decomposition replaces the RWA-share proxy."""
    projection = _projection(severe_paths())
    losses = {
        1: {"corporates": Decimal("5000000"), "retail_sme": Decimal("3000000")},
        2: {"corporates": Decimal("6000000"), "banks": Decimal("1000000")},
        3: {"corporates": Decimal("7000000")},
    }
    tables = build_appendix_ii(projection, severe_paths(), exposure_class_losses=losses)
    year1 = next(
        item for item in tables.table1_summary.impact_of_adverse if item[0] == 1
    )
    by_class = {loss.exposure_class: loss.loss for loss in year1[1]}
    # Reported in GHS'000 (5,000,000 → 5,000.000); every CRD class present.
    assert by_class["corporates"] == Decimal("5000.000")
    assert by_class["retail_sme"] == Decimal("3000.000")
    assert by_class["gog"] == Decimal("0.000")
    assert {loss.exposure_class for loss in year1[1]} == set(CRD_EXPOSURE_CLASSES)


def test_pillar2_overlay_populates_table5_stress_rows_and_keeps_the_tie() -> None:
    projection = _projection(severe_paths())
    overlay = {
        year: Pillar2Requirement(
            credit_concentration=Decimal("1000.000"), irrbb=Decimal("2000.000")
        )
        for year in (1, 2, 3)
    }
    tables = build_appendix_ii(projection, severe_paths(), pillar2_by_stress_year=overlay)
    stress_row = next(row for row in tables.table5_rwa.rows if row.label == "stress_y1")
    assert stress_row.pillar2.credit_concentration == Decimal("1000.000")
    assert stress_row.pillar2.irrbb == Decimal("2000.000")
    assert stress_row.pillar2.total == Decimal("3000.000")
    # Total requirement = Pillar-1 ('000) + Pillar-2 ('000), both in GHS'000.
    assert stress_row.total_capital_requirement == (
        stress_row.pillar1_requirement + Decimal("3000.000")
    )
    # Base rows carry no Pillar-2; the T5 == T1 Pillar-1 tie still holds.
    base_row = next(row for row in tables.table5_rwa.rows if row.label == "base_y1")
    assert base_row.pillar2.total == Decimal("0")
    t5 = {row.label: row.total_pillar1_rwa for row in tables.table5_rwa.rows}
    for snapshot in tables.table1_summary.post_adverse:
        assert t5[snapshot.label] == snapshot.total_rwa


def test_serialized_tables_are_json_safe_and_unit_tagged() -> None:
    projection = _projection(base_paths())
    tables = build_appendix_ii(projection, base_paths())
    serialized = tables.serialize()
    assert serialized["unit"] == "GHS'000"
    # Round-trips through JSON without error.
    json.dumps(serialized)
    assert set(serialized).issuperset(
        {
            "table1_summary",
            "table2_capital",
            "table3_profit_and_loss",
            "table4_financial_position",
            "table5_rwa",
            "table6_risk_drivers",
        }
    )
