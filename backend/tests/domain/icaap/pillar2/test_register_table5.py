"""The Pillar 2 register totals and the Table 5 grid it composes (B3).

The two properties under test are the ones a supervisor would notice:

* the register's own total equals the grid's Pillar 2 total, so the summary
  and the grid cannot state different requirements;
* a row whose current and stress sides rest on different items is refused when
  the grid is composed for a reader, because a smaller stress figure that is
  only smaller because something is missing reads as a result.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap.pillar2 import register, table5
from app.domain.stress.appendix_ii import thousands

ROWS = ("credit_concentration", "irrbb", "sovereign", "country_and_fx", "reputational", "others")
LABELS = {key: key.replace("_", " ").title() for key in ROWS}


def _item(
    component: str,
    row: str | None,
    baseline: str | None,
    stressed: str | None,
    *,
    source: str = "icaap_method",
) -> register.RegisterItem:
    return register.RegisterItem(
        item_key=component,
        component_key=component,
        table5_row=row,
        baseline=None if baseline is None else Decimal(baseline),
        stressed=None if stressed is None else Decimal(stressed),
        source=source,
        method="benchmark_mapped",
        status="computed",
        approved=True,
    )


def _columns() -> list[table5.AppendixColumn]:
    def pillar1(credit: str, requirement: str) -> dict[str, Decimal | None]:
        return {
            "credit_rwa": Decimal(credit),
            "operational_rwa": Decimal("150"),
            "market_rwa": Decimal("50"),
            "total_pillar1_rwa": Decimal(credit) + Decimal("200"),
            "pillar1_requirement": Decimal(requirement),
        }

    return [
        table5.AppendixColumn("current", "Current", "baseline", pillar1("800", "130")),
        table5.AppendixColumn("base_y1", "Base year 1", "baseline", pillar1("820", "132.6")),
        table5.AppendixColumn("stress_y1", "Stress year 1", "stressed", pillar1("900", "143")),
    ]


# --- register totals -------------------------------------------------------


def test_every_item_lands_on_exactly_one_row_and_the_rows_total_it() -> None:
    totals = register.row_totals(
        [
            _item("credit_concentration", "credit_concentration", "18.72", "21.06"),
            _item("irrbb", "irrbb", "116.7599", "116.7599"),
            _item("operational", "others", "55", "60"),
            _item("other_market", "others", "5", "5"),
        ],
        row_keys=ROWS,
    )
    by_row = {row.row: row for row in totals.rows}
    assert by_row["others"].baseline == Decimal("60.0000")
    assert by_row["others"].item_keys == ("operational", "other_market")
    assert by_row["reputational"].baseline is None, "not modelled is never zero"
    assert totals.total_baseline == Decimal("195.4799")
    assert totals.total_stressed == Decimal("202.8199")
    assert totals.partial_rows == ()


def test_a_risk_assessed_without_a_capital_line_joins_no_row() -> None:
    totals = register.row_totals([_item("liquidity", None, None, None)], row_keys=ROWS)
    assert totals.uncapitalised_item_keys == ("liquidity",)
    assert totals.total_baseline is None


def test_two_items_cannot_claim_the_same_component() -> None:
    with pytest.raises(register.RegisterError) as caught:
        register.row_totals(
            [_item("irrbb", "irrbb", "1", "1"), _item("irrbb", "irrbb", "2", "2")],
            row_keys=ROWS,
        )
    assert caught.value.code == register.FINDING_DUPLICATE_COMPONENT


def test_an_item_naming_a_row_the_framework_does_not_publish_is_refused() -> None:
    with pytest.raises(register.RegisterError) as caught:
        register.row_totals([_item("x", "invented_row", "1", "1")], row_keys=ROWS)
    assert caught.value.code == register.FINDING_UNKNOWN_ROW


def test_a_figure_on_one_basis_only_is_reported_per_item() -> None:
    items = [
        _item("credit_concentration", "credit_concentration", "18.72", None),
        _item("irrbb", "irrbb", None, "9"),
    ]
    findings = register.like_for_like_findings(items)
    assert {finding.params["missing_basis"] for finding in findings} == {"stressed", "baseline"}
    assert all(finding.code == register.FINDING_LIKE_FOR_LIKE for finding in findings)
    totals = register.row_totals(items, row_keys=ROWS)
    assert set(totals.partial_rows) == {"credit_concentration", "irrbb"}


def test_a_component_cannot_map_to_two_rows() -> None:
    with pytest.raises(register.RegisterError):
        register.component_row_map([("fx", "country_and_fx"), ("fx", "others")])


# --- Table 5 ---------------------------------------------------------------


def test_current_and_base_take_the_baseline_and_stress_takes_the_stressed() -> None:
    totals = register.row_totals(
        [
            _item("credit_concentration", "credit_concentration", "18.72", "21.06"),
            _item("irrbb", "irrbb", "116.7599", "130"),
        ],
        row_keys=ROWS,
    )
    grid = table5.compose(_columns(), totals, LABELS)
    concentration = next(row for row in grid.rows if row.key == "credit_concentration")
    assert concentration.cells["current"] == thousands(Decimal("18.72"))
    assert concentration.cells["base_y1"] == thousands(Decimal("18.72"))
    assert concentration.cells["stress_y1"] == thousands(Decimal("21.06"))


def test_the_registers_total_equals_the_grids_pillar_two_total() -> None:
    totals = register.row_totals(
        [
            _item("credit_concentration", "credit_concentration", "18.72", "21.06"),
            _item("irrbb", "irrbb", "116.7599", "130"),
            _item("operational", "others", "55", "60"),
        ],
        row_keys=ROWS,
    )
    grid = table5.compose(_columns(), totals, LABELS)
    assert table5.pillar2_total_for(grid, "current") == grid.register_total_baseline
    assert table5.pillar2_total_for(grid, "stress_y1") == grid.register_total_stressed


def test_the_pillar_one_rows_are_copied_from_the_run_unchanged() -> None:
    totals = register.row_totals([], row_keys=ROWS)
    grid = table5.compose(_columns(), totals, LABELS)
    credit = next(row for row in grid.rows if row.key == "credit_rwa")
    assert credit.cells["current"] == Decimal("800")
    assert credit.cells["stress_y1"] == Decimal("900")
    requirement = next(row for row in grid.rows if row.key == table5.ROW_TOTAL_REQUIREMENT)
    # Nothing is modelled, so the requirement is the run's own Pillar 1 figure.
    assert requirement.cells["current"] == Decimal("130")


def test_the_total_requirement_adds_pillar_one_and_pillar_two() -> None:
    totals = register.row_totals([_item("irrbb", "irrbb", "1000", "2000")], row_keys=ROWS)
    grid = table5.compose(_columns(), totals, LABELS)
    requirement = next(row for row in grid.rows if row.key == table5.ROW_TOTAL_REQUIREMENT)
    assert requirement.cells["current"] == Decimal("130") + thousands(Decimal("1000"))
    assert requirement.cells["stress_y1"] == Decimal("143") + thousands(Decimal("2000"))


def test_a_row_mixing_sources_refuses_when_the_grid_is_composed_strictly() -> None:
    """Two items on one row, one of them stressed only — the row is incomplete."""
    totals = register.row_totals(
        [
            _item("operational", "others", "55", "60"),
            _item("other_market", "others", "5", None),
        ],
        row_keys=ROWS,
    )
    with pytest.raises(table5.Table5Error) as caught:
        table5.compose(_columns(), totals, LABELS, strict=True)
    assert caught.value.code == table5.ERROR_ROW_MIXES_SOURCES
    assert caught.value.context["table5_row"] == "others"

    # Loosely composed, the same grid renders with the row flagged, so a
    # preparer can see what is missing rather than being shown nothing.
    grid = table5.compose(_columns(), totals, LABELS, strict=False)
    assert grid.partial_rows == ("others",)
    assert next(row for row in grid.rows if row.key == "others").partial is True


def test_a_cell_nobody_modelled_is_none_not_zero() -> None:
    totals = register.row_totals([], row_keys=ROWS)
    grid = table5.compose(_columns(), totals, LABELS)
    for row in grid.rows:
        if row.group != "pillar2":
            continue
        assert all(value is None for value in row.cells.values())


def test_the_framework_row_others_maps_to_the_appendix_field_other() -> None:
    """The one structural difference between the two vocabularies (B3)."""
    assert table5.APPENDIX_FIELD_BY_ROW["others"] == "other"
    for key, field in table5.APPENDIX_FIELD_BY_ROW.items():
        if key != "others":
            assert key == field
    assert set(table5.APPENDIX_FIELD_BY_ROW) == set(ROWS)
