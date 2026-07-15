"""Find the actual tables inside the grid a bank's spreadsheet really is.

Bank workbooks are not clean tables: title rows above the data, merged header
cells, blank separator rows, and several unrelated tables stacked in one
sheet. The analyzer works on the raw cell grid and recovers structured tables
with their true worksheet row numbers, so every extracted record stays
addressable back to its source cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.excel_csv.type_coercion import is_null_like

MergedRange = tuple[int, int, int, int]  # min_row, min_col, max_row, max_col (1-based)


@dataclass(frozen=True)
class AnalyzedTable:
    """One recovered table: named columns plus rows with worksheet row numbers."""

    name: str
    columns: tuple[str, ...]
    header_row: int
    rows: tuple[tuple[int, dict[str, Any]], ...] = field(default_factory=tuple)


def fill_merged_cells(grid: list[list[Any]], merged_ranges: list[MergedRange]) -> list[list[Any]]:
    """Propagate each merged range's top-left value across the whole range."""
    if not merged_ranges:
        return grid
    filled = [list(row) for row in grid]
    for min_row, min_col, max_row, max_col in merged_ranges:
        try:
            value = filled[min_row - 1][min_col - 1]
        except IndexError:
            continue
        for row_index in range(min_row - 1, min(max_row, len(filled))):
            row = filled[row_index]
            for col_index in range(min_col - 1, min(max_col, len(row))):
                if row[col_index] is None:
                    row[col_index] = value
    return filled


def _is_blank_row(row: list[Any]) -> bool:
    # Only truly empty rows split tables. Rows of placeholders ("-", "N/A")
    # frequently appear inside real bank tables and are dropped as data rows
    # instead, so they must not end the table early.
    return all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row)


def _looks_like_header(row: list[Any]) -> bool:
    values = [cell for cell in row if not is_null_like(cell)]
    if len(values) < 2:
        return False
    return all(isinstance(value, str) for value in values)


def _header_names(row: list[Any]) -> list[str]:
    """Stringify header cells, blanking unusable ones and deduping repeats."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for cell in row:
        if is_null_like(cell) or not isinstance(cell, str):
            names.append("")
            continue
        name = " ".join(cell.split())
        count = seen.get(name, 0) + 1
        seen[name] = count
        names.append(name if count == 1 else f"{name} ({count})")
    return names


def analyze_sheet(
    sheet_name: str,
    grid: list[list[Any]],
    merged_ranges: list[MergedRange] | None = None,
) -> list[AnalyzedTable]:
    """Recover every table in a sheet's grid.

    Tables are separated by fully-blank rows. Within each block the header is
    the first row whose populated cells are all text; rows above it (titles,
    notes) are ignored. A single-table sheet keeps the sheet's name; stacked
    tables get ``#1``, ``#2``, ... suffixes.
    """
    filled = fill_merged_cells(grid, merged_ranges or [])

    blocks: list[list[tuple[int, list[Any]]]] = []
    current: list[tuple[int, list[Any]]] = []
    for row_number, row in enumerate(filled, start=1):
        if _is_blank_row(row):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((row_number, row))
    if current:
        blocks.append(current)

    tables: list[AnalyzedTable] = []
    for block in blocks:
        header_offset = next(
            (index for index, (_, row) in enumerate(block) if _looks_like_header(row)),
            None,
        )
        if header_offset is None:
            continue
        header_row_number, header_cells = block[header_offset]
        columns = _header_names(header_cells)

        rows: list[tuple[int, dict[str, Any]]] = []
        for row_number, row in block[header_offset + 1 :]:
            record = {
                column: row[index] if index < len(row) else None
                for index, column in enumerate(columns)
                if column
            }
            if all(is_null_like(value) for value in record.values()):
                continue
            rows.append((row_number, record))
        if not rows:
            continue
        tables.append(
            AnalyzedTable(
                name=sheet_name,
                columns=tuple(column for column in columns if column),
                header_row=header_row_number,
                rows=tuple(rows),
            )
        )

    if len(tables) > 1:
        tables = [
            AnalyzedTable(
                name=f"{table.name}#{index}",
                columns=table.columns,
                header_row=table.header_row,
                rows=table.rows,
            )
            for index, table in enumerate(tables, start=1)
        ]
    return tables
