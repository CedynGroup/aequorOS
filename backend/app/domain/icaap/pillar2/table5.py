"""Appendix II Table 5, composed from the ICAAP register (B3).

BoG's Table 5 is one grid with a column per period — Current, Base year 1..n,
Stress year 1..n — and a row per capital line. The Pillar 1 rows are the
attested stress run's own figures, copied unchanged: re-deriving them here
would create a second opinion about what the run said. The Pillar 2 rows are
the ICAAP register's, because the ICAAP is where a bank states its own capital
requirement.

The Current and Base columns take the register's BASELINE figures and the
Stress columns its STRESSED figures. A row whose two sides rest on different
items would print a stress requirement covering fewer risks than the current
one, which reads as a result rather than as a gap — so composing such a grid
strictly is a refusal (``table5_row_mixes_sources``), and composing it loosely
flags the row.

Amounts arrive in the reporting currency and leave in thousands, the unit and
rounding the run itself uses, so the Pillar 1 and Pillar 2 halves of a column
can be added.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from app.domain.icaap.pillar2.register import RegisterTotals, RowTotal
from app.domain.stress.appendix_ii import thousands

Basis = Literal["baseline", "stressed"]

#: The register's framework row key against the field name Appendix II uses
#: for the same risk. ``others`` / ``other`` is the one structural difference;
#: everything else is spelt identically, and a test pins that.
APPENDIX_FIELD_BY_ROW: Mapping[str, str] = MappingProxyType(
    {
        "credit_concentration": "credit_concentration",
        "irrbb": "irrbb",
        "sovereign": "sovereign",
        "country_and_fx": "country_and_fx",
        "reputational": "reputational",
        "others": "other",
    }
)

#: Pillar 1 grid rows, in BoG's order, with the attested run's field name.
PILLAR1_ROWS: tuple[tuple[str, str, str], ...] = (
    ("credit_rwa", "Credit risk-weighted assets", "credit_rwa"),
    ("operational_rwa", "Operational risk-weighted assets", "operational_rwa"),
    ("market_rwa", "Market risk-weighted assets", "market_rwa"),
    ("total_pillar1_rwa", "Total Pillar 1 risk-weighted assets", "total_pillar1_rwa"),
    ("pillar1_requirement", "Pillar 1 capital requirement", "pillar1_requirement"),
)

ROW_PILLAR2_TOTAL = "pillar2_total"
ROW_TOTAL_REQUIREMENT = "total_capital_requirement"

ERROR_ROW_MIXES_SOURCES = "table5_row_mixes_sources"
ERROR_UNKNOWN_BASIS = "table5_unknown_basis"

ZERO = Decimal(0)


class Table5Error(ValueError):
    """The grid cannot be composed without misrepresenting a row."""

    def __init__(self, code: str, **context: str) -> None:
        self.code = code
        self.context = context
        super().__init__(code if not context else f"{code}:{sorted(context.items())}")


@dataclass(frozen=True)
class AppendixColumn:
    """One period column, with the Pillar 1 figures the run reported."""

    key: str
    label: str
    basis: Basis
    #: Already in thousands — these are the run's own published figures.
    pillar1: Mapping[str, Decimal | None]


@dataclass(frozen=True)
class GridRow:
    key: str
    label: str
    group: Literal["pillar1", "pillar2", "total"]
    cells: Mapping[str, Decimal | None]
    #: True when this row's two bases do not rest on the same items.
    partial: bool = False


@dataclass(frozen=True)
class Table5Grid:
    columns: tuple[AppendixColumn, ...]
    rows: tuple[GridRow, ...]
    partial_rows: tuple[str, ...]
    #: The register's total on each basis, in the grid's own unit and rounding.
    #: Equal to the grid's Pillar 2 total by construction — the register
    #: summary and the filed grid cannot state different requirements.
    register_total_baseline: Decimal | None
    register_total_stressed: Decimal | None


def _scaled(value: Decimal | None) -> Decimal | None:
    return None if value is None else thousands(value)


def _row_value(row: RowTotal, basis: Basis) -> Decimal | None:
    if basis == "baseline":
        return row.baseline
    if basis == "stressed":
        return row.stressed
    raise Table5Error(ERROR_UNKNOWN_BASIS, basis=basis)  # pragma: no cover - typed above


def _add(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None and right is None:
        return None
    return (left or ZERO) + (right or ZERO)


def compose(
    columns: Sequence[AppendixColumn],
    totals: RegisterTotals,
    row_labels: Mapping[str, str],
    *,
    strict: bool = False,
) -> Table5Grid:
    """The BoG grid: Pillar 1 from the run, Pillar 2 from the ICAAP register.

    ``strict`` is for anything a regulator will read. It refuses a row whose
    Current and Stress sides do not cover the same risks, rather than printing
    a stress requirement that is smaller only because something is missing.
    """
    if strict and totals.partial_rows:
        raise Table5Error(ERROR_ROW_MIXES_SOURCES, table5_row=totals.partial_rows[0])

    rows: list[GridRow] = []
    for key, label, field in PILLAR1_ROWS:
        rows.append(
            GridRow(
                key=key,
                label=label,
                group="pillar1",
                cells={column.key: column.pillar1.get(field) for column in columns},
            )
        )

    by_row = {row.row: row for row in totals.rows}
    pillar2_cells: dict[str, Decimal | None] = {column.key: None for column in columns}
    for row_key, row in by_row.items():
        cells: dict[str, Decimal | None] = {}
        for column in columns:
            value = _scaled(_row_value(row, column.basis))
            cells[column.key] = value
            if value is not None:
                pillar2_cells[column.key] = _add(pillar2_cells[column.key], value)
        rows.append(
            GridRow(
                key=row_key,
                label=row_labels.get(row_key, row_key),
                group="pillar2",
                cells=cells,
                partial=row.partial,
            )
        )

    rows.append(
        GridRow(
            key=ROW_PILLAR2_TOTAL,
            label="Total Pillar 2 capital requirement",
            group="total",
            cells=dict(pillar2_cells),
        )
    )
    # The total is the sum of the rows AS PRINTED, not the unrounded register
    # total rescaled. Rounding each row to thousands and then adding can differ
    # from adding and then rounding by one quantum, and a filed grid whose
    # column does not add up is the version a supervisor asks about.
    baseline_column = next((column for column in columns if column.basis == "baseline"), None)
    stressed_column = next((column for column in columns if column.basis == "stressed"), None)
    requirement_cells: dict[str, Decimal | None] = {}
    for column in columns:
        pillar1 = column.pillar1.get("pillar1_requirement")
        if pillar1 is None:
            requirement_cells[column.key] = None
            continue
        requirement_cells[column.key] = pillar1 + (pillar2_cells[column.key] or ZERO)
    rows.append(
        GridRow(
            key=ROW_TOTAL_REQUIREMENT,
            label="Total capital requirement",
            group="total",
            cells=requirement_cells,
        )
    )
    return Table5Grid(
        columns=tuple(columns),
        rows=tuple(rows),
        partial_rows=totals.partial_rows,
        register_total_baseline=(
            None if baseline_column is None else pillar2_cells[baseline_column.key]
        ),
        register_total_stressed=(
            None if stressed_column is None else pillar2_cells[stressed_column.key]
        ),
    )


def pillar2_total_for(grid: Table5Grid, column_key: str) -> Decimal | None:
    """The grid's own Pillar 2 total in one column — what a test compares."""
    for row in grid.rows:
        if row.key == ROW_PILLAR2_TOTAL:
            return row.cells.get(column_key)
    return None  # pragma: no cover - the row is always appended


__all__ = [
    "APPENDIX_FIELD_BY_ROW",
    "ERROR_ROW_MIXES_SOURCES",
    "ERROR_UNKNOWN_BASIS",
    "PILLAR1_ROWS",
    "ROW_PILLAR2_TOTAL",
    "ROW_TOTAL_REQUIREMENT",
    "ZERO",
    "AppendixColumn",
    "Basis",
    "GridRow",
    "Table5Error",
    "Table5Grid",
    "compose",
    "pillar2_total_for",
]
