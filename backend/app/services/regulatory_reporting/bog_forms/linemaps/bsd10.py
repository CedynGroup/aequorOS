"""BSD10 — Capital Expenditure (half-yearly, ¢'Million).

Official layout: sheet ``BSD10`` — one grid of asset classes (columns C–G: Land
and Buildings / Furniture and Equipment / Computers / Other Office Equipment /
Motor Vehicles; H = ``SUM`` total, BoG's formula) × ten leaf rows: A. Purchased,
B. On finance-lease, C. On Hire-Purchase, D. Capital WIP, E. Contracted but not
provided, F. Authorised but not contracted, G. Forecast to be acquired in next
six months, H. Disposal proceeds, and the forecast split 0–3 / 3–6 months (row 18
totals are template formulas). All 50 leaf cells are template INPUT cells and
every one is bound below.

Sources (data-gap closure 2026-08-16): the ``capital_expenditure`` reference
dataset — the bank's fixed-asset / capex register, one row per (period_end,
asset_class), pushed through the Data Engine (schema
``app/domain/ingestion/reference_schemas/capital_expenditure.py``, spec
docs/data_engine/datasets/capital_expenditure.md). Each cell is ``refs.sum`` of
the row's field over the register classes the column rolls up
(``BSD10_COLUMN_CLASSES``: "Land and Buildings" = ``land_buildings`` +
``staff_land_premises``, the other four columns one class each;
``other_property_legal_rights`` is a BSD2-only class — the Guide excludes
intangibles from BSD10). Because one :class:`RowSource` binds a whole row and
the value differs per COLUMN here, the grid is bound one column at a time
(five ``leaf_lines`` calls, ``code_prefix`` ``BSD10.<column>``). Nothing is
derived from a stock balance: every field is the Guide's own half-year
figure, stated by the bank. Blank (``input_required``) until the register has
been ingested; ``0`` for a class the register carries no row / a nil field for.
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.ingestion.reference_schemas.capital_expenditure import BSD10_COLUMN_CLASSES

from ..layout import load_layout
from ..spec import LineSpec
from ._common import RowSource, leaf_lines

KIND = "capital_expenditure"

#: official column key → sheet column letter (Guide items 1–5)
COLUMNS: dict[str, str] = {
    "land_buildings": "C",
    "furniture_equipment": "D",
    "computers": "E",
    "other_office_equipment": "F",
    "motor_vehicles": "G",
}

#: official leaf row → register field (Guide items A–H + the forecast split)
ROW_FIELDS: dict[int, str] = {
    7: "additions_purchased_ghs",  # A. Purchased
    8: "additions_finance_lease_ghs",  # B. On finance-lease
    9: "additions_hire_purchase_ghs",  # C. On Hire-Purchase
    10: "capital_wip_ghs",  # D. Capital WIP (half-year expenditure carried as WIP)
    11: "contracted_not_provided_ghs",  # E. Contracted but not provided
    12: "authorised_not_contracted_ghs",  # F. Authorised but not contracted
    13: "forecast_next_6m_ghs",  # G. Forecast to be acquired in next six months
    14: "disposal_proceeds_ghs",  # H. Disposal proceeds
    16: "forecast_0_3m_ghs",  # forecast split 0–3 months
    17: "forecast_3_6m_ghs",  # forecast split 3–6 months
}


def _register(column: str, field: str) -> RowSource:
    classes = list(BSD10_COLUMN_CLASSES[column])
    return RowSource(
        "refs.sum",
        {"kind": KIND, "value_field": field, "filters": {"asset_class": classes}},
        notes=(
            f"Σ {field} over the capital_expenditure register rows of asset class "
            f"{' + '.join(classes)} for the half-year (latest register on/before the reporting "
            "date; the bank's own figure per the Guide's BSD10 definitions — blank until the "
            "register is ingested)"
        ),
    )


def _with_item_labels(lines: tuple[LineSpec, ...]) -> tuple[LineSpec, ...]:
    """The official row label is "A." + "Purchased" across two cells (A/B);
    join them so the completion notes read like the form."""
    sheet = load_layout("BSD10").sheet("BSD10")
    out: list[LineSpec] = []
    for line in lines:
        row = int(line.code.rsplit("R", 1)[1])
        item = sheet.by_ref.get(f"A{row}")
        text = sheet.by_ref.get(f"B{row}")
        parts = [str(c.value).strip() for c in (item, text) if c is not None and c.value]
        out.append(replace(line, label=" ".join(parts) or line.label))
    return tuple(out)


def _column_lines(column: str, letter: str) -> tuple[LineSpec, ...]:
    return leaf_lines(
        "BSD10",
        "BSD10",
        value_columns={column: letter},
        row_sources={row: _register(column, field) for row, field in ROW_FIELDS.items()},
        code_prefix=f"BSD10.{column}",
    )


LINES = {
    "BSD10": _with_item_labels(
        tuple(line for column, letter in COLUMNS.items() for line in _column_lines(column, letter))
    ),
}
