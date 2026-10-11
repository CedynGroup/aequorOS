"""Shared scaffolding for line maps.

:func:`leaf_lines` walks an official sheet's INPUT cells row by row and emits
one :class:`LineSpec` per leaf row, binding the sheet's value columns (e.g.
Domestic ``B`` / Foreign ``C``) to the row's source from ``row_sources`` — or
to ``input_required`` when the platform cannot yet feed the row. Because it
enumerates the layout, a line map can never silently omit an official cell.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..layout import load_layout
from ..spec import LineSpec


#: A row's source binding: resolver name, params, and a note for the completion
#: sheet. ``None`` resolver ⇒ input_required.
@dataclass(frozen=True)
class RowSource:
    source: str | None
    params: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    unscaled: bool = False


INPUT_REQUIRED = RowSource(None, notes="bank must supply (no canonical source yet)")
BANK_COA_MAPPING = RowSource(
    None,
    notes="bank-specific chart-of-accounts mapping table required to split this line",
)


def leaf_lines(  # noqa: PLR0913 — one keyword per binding dimension
    form: str,
    sheet: str,
    *,
    value_columns: Mapping[str, str],
    row_sources: Mapping[int, RowSource],
    code_prefix: str | None = None,
    default: RowSource = INPUT_REQUIRED,
    only_rows: Sequence[int] | None = None,
) -> tuple[LineSpec, ...]:
    """Bind every leaf row of ``sheet`` whose cells in ``value_columns`` are
    template INPUT cells.

    ``value_columns`` maps column key → column letter, e.g.
    ``{"domestic": "B", "foreign": "C"}``. A row is a leaf when at least one of
    those cells is an input; only its input cells are bound (a template formula
    in one of the columns is left to the evaluator).
    """
    layout = load_layout(form).sheet(sheet)
    prefix = code_prefix or f"{form}.{sheet}"
    rows: dict[int, dict[str, str]] = {}
    for key, letter in value_columns.items():
        for cell in layout.input_cells:
            if cell.ref.startswith(letter) and cell.ref[len(letter) :].isdigit():
                rows.setdefault(cell.row, {})[key] = cell.ref
    lines: list[LineSpec] = []
    for row in sorted(rows):
        if only_rows is not None and row not in only_rows:
            continue
        binding = row_sources.get(row, default)
        lines.append(
            LineSpec(
                code=f"{prefix}.R{row}",
                label=layout.label_for_row(row) or f"row {row}",
                cells=dict(rows[row]),
                source=binding.source,
                params=dict(binding.params),
                notes=binding.notes,
                unscaled=binding.unscaled,
            )
        )
    return tuple(lines)


def positions(**params: Any) -> RowSource:
    return RowSource("positions.sum", params)


def facts(group: str, *categories: str, **params: Any) -> RowSource:
    return RowSource("facts.sum", {"group": group, "categories": list(categories), **params})


def run_metric(module: str, metric: str, *, scenario: str = "baseline", **params: Any) -> RowSource:
    return RowSource(
        "run.metric", {"module": module, "metric": metric, "scenario": scenario, **params}
    )


def form_cell(form: str, sheet: str, ref: str) -> RowSource:
    return RowSource("form.cell", {"form": form, "sheet": sheet, "ref": ref})


def grid_lines(  # noqa: PLR0913 — one keyword per binding dimension
    form: str,
    sheet: str,
    *,
    rows: Sequence[int],
    value_columns: Mapping[str, str],
    row_sources: Mapping[int, RowSource],
    code_prefix: str | None = None,
    default: RowSource = INPUT_REQUIRED,
    label_column: str | None = None,
) -> tuple[LineSpec, ...]:
    """Bind a BLANK data grid: templates whose data cells are empty (no ``0``
    placeholder) — e.g. BSD2A, BSD3A detail rows, BSD11 registers, BSD1A — carry
    no ``input`` cells in the layout, so :func:`leaf_lines` cannot see them.
    ``rows`` × ``value_columns`` names the official data cells explicitly; any
    such cell that is a template FORMULA is skipped (the engine evaluates it).
    Labels come from the row's left-most label cell (or ``label_column``).
    """
    layout = load_layout(form).sheet(sheet)
    prefix = code_prefix or f"{form}.{sheet}"
    lines: list[LineSpec] = []
    for row in rows:
        cells: dict[str, str] = {}
        for key, letter in value_columns.items():
            ref = f"{letter}{row}"
            cell = layout.by_ref.get(ref)
            if cell is not None and cell.kind == "formula":
                continue
            cells[key] = ref
        if not cells:
            continue
        if label_column is not None:
            label_cell = layout.by_ref.get(f"{label_column}{row}")
            label = str(label_cell.value).strip() if label_cell is not None else ""
        else:
            label = layout.label_for_row(row)
        binding = row_sources.get(row, default)
        lines.append(
            LineSpec(
                code=f"{prefix}.R{row}",
                label=label or f"row {row}",
                cells=cells,
                source=binding.source,
                params=dict(binding.params),
                notes=binding.notes,
                unscaled=binding.unscaled,
            )
        )
    return tuple(lines)
