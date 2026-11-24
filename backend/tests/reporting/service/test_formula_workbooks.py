"""The CAR-RWA and FX-NOP formula workbooks recompute their returns with live formulas.

What is being protected: the regulator receives an Excel whose figures are
FORMULAS over the return's own report sheets, and every figure the formulas
recompute agrees with the figure the return reports. The workbook is rendered
exactly as the export renders it, then every formula in it is evaluated with the
same evaluator the official BoG forms use (``bog_forms.formulas``); each check
cell must read MATCH. A wrong reference, a wrong unit or a wrong rule shows up
as CHECK, or as a formula the evaluator cannot read.

CAR-RWA is rendered from a real generated package (the frozen fixture under
``tests/fixtures/pre_p0_packages``). FX-NOP has no such fixture, so its snapshot is
built here in the generator's own shape (``generation._generate_fx``) with
figures that reconcile the way the FX engine computes them.
"""

from __future__ import annotations

import copy
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from app.reporting.service.bog_forms.formulas import WorkbookEvaluator
from app.reporting.service.exports.xlsx import render_xlsx
from app.reporting.service.registry import get_definition
from app.reporting.service.templates import build_rendered_return, get_template

FIXTURE = Path(__file__).parents[2] / "fixtures" / "pre_p0_packages" / "CAR-RWA.json"
GENERATED_AT = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _car_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record = json.loads(FIXTURE.read_text())
    snapshot: dict[str, Any] = record["snapshot"]
    snapshot.setdefault("metadata", {})["formula_workbook"] = "filed"
    return snapshot, record.get("source_runs", [])


def _row(code: str, description: str, value: object, **extra: object) -> dict[str, Any]:
    return {"code": code, "description": description, "value": str(value), **extra}


def _fx_snapshot() -> dict[str, Any]:
    base, _ = _car_snapshot()
    snapshot = copy.deepcopy(base)
    snapshot.update(
        {"return_code": "FX-NOP", "return_family": "fx", "template_id": "bog-fx-nop-v1"}
    )
    currencies = (
        # currency, side, net_ccy, spot, net_ghs, |net| / Tier 1 %, within single limit
        ("EUR", "long", "241000.00", "14.5200", "3500000.00", "1.250000", True),
        ("GBP", "short", "-69800.00", "17.2300", "-1200000.00", "0.428571", True),
        ("USD", "short", "-1855000.00", "12.9400", "-24000000.00", "8.571429", True),
    )
    position_rows = [
        _row(
            currency,
            f"Net open position in {currency}",
            net_ghs,
            side=side,
            net_ccy=net_ccy,
            spot_ghs=spot,
            abs_pct_tier1=pct,
            within_single_limit=within,
        )
        for currency, side, net_ccy, spot, net_ghs, pct, within in currencies
    ]
    summary = (
        ("nop_ghs", "Aggregate net open position", "25200000.00"),
        ("nop_pct_tier1", "Aggregate NOP as % of Tier 1", "9.000000"),
        ("sum_long_ghs", "Sum of long positions", "3500000.00"),
        ("sum_short_ghs", "Sum of short positions", "25200000.00"),
        ("var_99_1d_ghs", "Portfolio VaR (99%, 1-day)", "410000.00"),
        ("stressed_var_ghs", "Stressed VaR (cedi crisis)", "980000.00"),
        ("tier1_ghs", "Tier 1 capital", "280000000.00"),
    )
    snapshot["sections"] = [
        {"code": "currency_positions", "rows": position_rows, "optional": False},
        {
            "code": "standalone_var",
            "rows": [_row("USD", "Standalone VaR for USD", "390000.00", net_ghs="-24000000.00")],
            "optional": False,
        },
        {"code": "hedges", "rows": [], "optional": True},
        {
            "code": "scenario_nop",
            "rows": [
                _row(
                    "mild_depreciation",
                    "NOP under mild_depreciation",
                    "26460000.00",
                    shock_pct="5.000000",
                    nop_pct_tier1="9.450000",
                    within_aggregate_limit=True,
                )
            ],
            "optional": False,
        },
        {"code": "nop_summary", "rows": [_row(c, d, v) for c, d, v in summary], "optional": False},
    ]
    snapshot["totals"] = [_row(c, d, v) for c, d, v in summary if c in ("nop_ghs", "tier1_ghs")]
    snapshot["metadata"] = {
        **snapshot.get("metadata", {}),
        "nop_single_limit_pct": "10.000000",
        "nop_aggregate_limit_pct": "20.000000",
        "formula_workbook": "filed",
    }
    return snapshot


def _render(code: str, snapshot: dict[str, Any], runs: list[dict[str, Any]]) -> bytes:
    definition = get_definition(code)
    assert definition is not None
    assert definition.supports_working_copy, f"{code} must declare its formula workbook"
    template = get_template(definition.template_id)
    assert template is not None
    rendered = build_rendered_return(
        template,
        snapshot,
        runs,
        package_id="00000000-0000-0000-0000-000000000001",
        package_version=1,
    )
    return render_xlsx(rendered, generated_at=GENERATED_AT, working_copy=True, snapshot=snapshot)


def _evaluate(payload: bytes) -> tuple[Any, WorkbookEvaluator]:
    workbook = load_workbook(io.BytesIO(payload), data_only=False)
    formulas: dict[tuple[str, str], str] = {}
    inputs: dict[tuple[str, str], Any] = {}
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas[(sheet.title, cell.coordinate)] = cell.value
                elif cell.value is not None:
                    inputs[(sheet.title, cell.coordinate)] = cell.value
    return workbook, WorkbookEvaluator(formulas, inputs)


def _checks(workbook: Any, evaluator: WorkbookEvaluator) -> dict[str, Any]:
    sheet = workbook["Working Calculations"]
    found: dict[str, Any] = {}
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and '"MATCH"' in cell.value:
                found[cell.coordinate] = evaluator.value(sheet.title, cell.coordinate)
    return found


def test_the_car_workbook_recomputes_capital_rwa_and_every_ratio() -> None:
    snapshot, runs = _car_snapshot()
    workbook, evaluator = _evaluate(_render("CAR-RWA", snapshot, runs))

    checks = _checks(workbook, evaluator)
    # CET1, Tier 1, total capital, 12 credit lines, credit RWA, open position, FX
    # charge, market RWA, gross income, BIA charge, operational RWA, total RWA and
    # three ratios.
    assert len(checks) >= 25
    assert set(checks.values()) == {"MATCH"}, checks

    sheet = workbook["Working Calculations"]
    labels = {
        str(row[0].value): row[2].coordinate
        for row in sheet.iter_rows(min_row=5)
        if row[0].value and isinstance(row[0].value, str) and not row[0].value.startswith("=")
    }
    car = evaluator.value(sheet.title, labels["Capital adequacy ratio (CAR)"])
    assert car == pytest.approx(15.832363, abs=1e-6)
    rwa = evaluator.value(sheet.title, labels["Total RWA"])
    assert rwa == pytest.approx(2_147_500.0)
    assert "FORMULA WORKBOOK" in str(sheet["A1"].value)


def test_the_fx_workbook_recomputes_every_position_and_the_aggregate() -> None:
    workbook, evaluator = _evaluate(_render("FX-NOP", _fx_snapshot(), []))

    checks = _checks(workbook, evaluator)
    # three currencies, then long, short, aggregate NOP and NOP % of Tier 1
    assert len(checks) == 7
    assert set(checks.values()) == {"MATCH"}, checks

    sheet = workbook["Working Calculations"]
    nop_row = next(
        row for row in sheet.iter_rows() if row[0].value == "Aggregate NOP as % of Tier 1"
    )
    assert evaluator.value(sheet.title, nop_row[2].coordinate) == pytest.approx(9.0)
    assert evaluator.value(sheet.title, nop_row[6].coordinate) == "Yes"
    usd = next(
        row
        for row in sheet.iter_rows(min_row=10)
        if row[1].value and "USD" in str(evaluator.value(sheet.title, row[0].coordinate))
    )
    # Converted at the end-of-day rate, beside the booked figure the return reports.
    assert evaluator.value(sheet.title, usd[3].coordinate) == pytest.approx(
        -1_855_000 * 12.94 / 1000
    )
    assert evaluator.value(sheet.title, usd[9].coordinate) == "Yes"


def test_a_package_generated_before_the_decision_keeps_its_working_copy_wording() -> None:
    snapshot, runs = _car_snapshot()
    del snapshot["metadata"]["formula_workbook"]
    workbook, _ = _evaluate(_render("CAR-RWA", snapshot, runs))
    assert "not a filing artifact" in str(workbook["Working Calculations"]["A1"].value)
