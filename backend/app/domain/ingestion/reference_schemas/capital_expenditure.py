"""``capital_expenditure`` — the fixed-asset / capital-expenditure register by
asset class (feeds every cell of BSD10 *Capital Expenditure* and the fixed-
asset block of BSD2 *Statement of Assets and Liabilities*, item 12 rows
115–121 at cost + row 123 accumulated depreciation).

BSD10 (Guide: half-yearly, June and December) asks for the half-year CASH
expenditure by asset class — purchased (A), on finance-lease (B), on hire-
purchase (C), capital work-in-progress (D), contracted but not provided (E),
authorised but not contracted (F), forecast for the next six months (G, split
0–3 / 3–6 months) and disposal proceeds (H) — "without deductions for
depreciation, amortisation or obsolescence". BSD2 item 12 asks for the STOCK:
property, plant and equipment at cost by class (bank land and premises, staff
land and premises, computers, furniture / fixtures / equipment, motor
vehicles, other property acquired by legal rights, work-in-progress) less
accumulated depreciation. The platform held fixed assets only as one GL stock
balance with no asset-class attribute, so all of those cells were
``input_required``. This register is what the bank's fixed-asset sub-ledger
already produces: **one row per (period_end, asset_class)** carrying the
half-year movements (BSD10) and the closing stock (BSD2) for that class.

Asset classes are the union of the two forms' classes; the line maps roll them
up where a form is coarser (BSD10 "Land and Buildings" = ``land_buildings`` +
``staff_land_premises``; BSD2 "(d) Furniture, fixtures and equipment" =
``furniture_equipment`` + ``other_office_equipment``; ``other_property_legal_
rights`` is a BSD2-only line — the Guide excludes intangibles from BSD10).
Work-in-progress is a per-class attribute: ``capital_wip_ghs`` (the half-year
expenditure carried as WIP, BSD10 row D) and ``wip_closing_ghs`` (the WIP
balance at period end, BSD2 row 121); ``closing_cost_ghs`` is the cost of
COMPLETED assets of the class (excludes WIP) so BoG's BSD2 sub-total does not
double count.

Amounts are cedis; ``currency`` (optional) is the booking currency of the
class's assets and places the stock in BSD2's Domestic (bank's base currency —
also when omitted) or Foreign column per the Guide. **One period per push**
(batch ``as_of_date`` = ``period_end``): BSD10 / BSD2 read the latest batch
on/before the period end. A bank that wants BSD2's monthly PPE block to move
monthly pushes the stock monthly (any period_end is valid); the BSD10 flows are
the half-year's regardless of the push cadence.
"""

from __future__ import annotations

from . import ReferenceSchema, register

#: Union of the BSD10 columns and the BSD2 item-12 lines.
ASSET_CLASSES: tuple[str, ...] = (
    "land_buildings",
    "staff_land_premises",
    "furniture_equipment",
    "computers",
    "other_office_equipment",
    "motor_vehicles",
    "other_property_legal_rights",
)

#: BSD10 column → register classes rolled into it (Guide items 1–5).
BSD10_COLUMN_CLASSES: dict[str, tuple[str, ...]] = {
    "land_buildings": ("land_buildings", "staff_land_premises"),
    "furniture_equipment": ("furniture_equipment",),
    "computers": ("computers",),
    "other_office_equipment": ("other_office_equipment",),
    "motor_vehicles": ("motor_vehicles",),
}

#: BSD2 item-12 row → register classes (rows 115–120; 121 WIP is a per-class field).
BSD2_ROW_CLASSES: dict[int, tuple[str, ...]] = {
    115: ("land_buildings",),
    116: ("staff_land_premises",),
    117: ("computers",),
    118: ("furniture_equipment", "other_office_equipment"),
    119: ("motor_vehicles",),
    120: ("other_property_legal_rights",),
}

SCHEMA = register(
    ReferenceSchema(
        kind="capital_expenditure",
        description=(
            "Fixed-asset / capital-expenditure register: one row per (period_end, asset_class) "
            "with the half-year movements BSD10 asks for (purchased / finance-lease / hire-"
            "purchase additions, capital WIP, commitments, authorisations, six-month forecast, "
            "disposal proceeds) and the closing stock BSD2 item 12 asks for (cost, accumulated "
            "depreciation, NBV, WIP balance), in cedis"
        ),
        grain=(
            "one row per (period_end, asset_class); one period per push (as_of_date = period_end)"
        ),
        required=(
            "period_end",
            "asset_class",
            "opening_nbv_ghs",
            "additions_purchased_ghs",
            "additions_finance_lease_ghs",
            "additions_hire_purchase_ghs",
            "disposal_proceeds_ghs",
            "disposals_nbv_ghs",
            "depreciation_ghs",
            "closing_cost_ghs",
            "accumulated_depreciation_ghs",
            "closing_nbv_ghs",
        ),
        optional=(
            "currency",
            "capital_wip_ghs",
            "wip_closing_ghs",
            "contracted_not_provided_ghs",
            "authorised_not_contracted_ghs",
            "forecast_next_6m_ghs",
            "forecast_0_3m_ghs",
            "forecast_3_6m_ghs",
            "budget_ghs",
            "notes",
        ),
        numeric=(
            "opening_nbv_ghs",
            "additions_purchased_ghs",
            "additions_finance_lease_ghs",
            "additions_hire_purchase_ghs",
            "capital_wip_ghs",
            "disposal_proceeds_ghs",
            "disposals_nbv_ghs",
            "depreciation_ghs",
            "closing_cost_ghs",
            "accumulated_depreciation_ghs",
            "closing_nbv_ghs",
            "wip_closing_ghs",
            "contracted_not_provided_ghs",
            "authorised_not_contracted_ghs",
            "forecast_next_6m_ghs",
            "forecast_0_3m_ghs",
            "forecast_3_6m_ghs",
            "budget_ghs",
        ),
        dates=("period_end",),
        enums={"asset_class": ASSET_CLASSES},
    )
)


def _num(row: dict, name: str) -> float | None:
    value = row.get(name)
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def validate_capex_row(row: dict) -> list[str]:
    """Schema problems plus the register's own arithmetic, checked only when the
    figures are present and numeric: ``closing_nbv_ghs`` = ``closing_cost_ghs``
    − ``accumulated_depreciation_ghs`` (the sub-ledger invariant BSD2 item 12
    reproduces as sub-total − depreciation) and, when the six-month forecast is
    split, ``forecast_next_6m_ghs`` = ``forecast_0_3m_ghs`` + ``forecast_3_6m_ghs``
    (BSD10 rows 13 and 16 + 17 are separate official inputs)."""
    problems = SCHEMA.validate_row(row)
    cost, dep, nbv = (
        _num(row, "closing_cost_ghs"),
        _num(row, "accumulated_depreciation_ghs"),
        _num(row, "closing_nbv_ghs"),
    )
    if cost is not None and dep is not None and nbv is not None and abs(cost - dep - nbv) > 0.5:  # noqa: PLR2004
        problems.append(
            "closing_nbv_ghs must equal closing_cost_ghs - accumulated_depreciation_ghs "
            f"(got {nbv!r} vs {cost - dep!r})"
        )
    six, near, far = (
        _num(row, "forecast_next_6m_ghs"),
        _num(row, "forecast_0_3m_ghs"),
        _num(row, "forecast_3_6m_ghs"),
    )
    if six is not None and near is not None and far is not None and abs(six - near - far) > 0.5:  # noqa: PLR2004
        problems.append(
            "forecast_next_6m_ghs must equal forecast_0_3m_ghs + forecast_3_6m_ghs "
            f"(got {six!r} vs {near + far!r})"
        )
    return problems
