"""Shape one compiled BI result for the AG Grid Community row models (D-030).

The grid library does no analytics here. Grouping, pivoting, subtotals, Top-N
and period comparison are all compiled server-side by
``app/services/bi/compiler.py`` — they always were — so what the Community
edition has to render is an ordinary rectangle. This module is the only place
that knows the difference between the compiler's wire shape and the grid's.

Three translations, each of which the grid cannot do for itself:

* **Positional rows become keyed rows.** The compiler returns tuples in column
  order because that is the cheapest honest wire shape for five thousand rows;
  AG Grid addresses cells by column id. The keys are the compiler's own column
  ids, pivot cells (``measure|value``) and comparison cells
  (``measure|prior``, ``measure|delta``, ``measure|delta_pct``) included, so a
  column definition and a row agree by construction.
* **The ``__level`` marker becomes row metadata.** With subtotals the compiler
  emits a ``ROLLUP`` and marks each row with how many group keys it actually
  grouped. That is not a column a user should see — it is what tells the
  renderer how far to indent the row and whether it is a subtotal. It is
  identified by ``kind == "marker"``, never by its name, and it is stripped
  from the column list.
* **A page reports whether it is the last one.** The Infinite Row Model needs a
  total row count or nothing at all. The compiler detects truncation by asking
  for one row more than the cap, so a page that was not truncated IS the last
  one and its end is the total; a truncated page reports ``None`` and the grid
  keeps asking.

Page bounds are the surface's, never the client's: :func:`page_bounds` clamps
whatever ``startRow`` / ``endRow`` arrive to ``BI_GRID_PAGE_CAP`` (S13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.bi.compiler import ColumnSpec
from app.services.bi.execution import QueryResult

#: A grid page is at least one row: an ``endRow`` at or below ``startRow`` is a
#: client bug, and answering it with an empty page would look like an empty book.
MIN_PAGE_ROWS = 1


@dataclass(frozen=True, slots=True)
class GridRow:
    """One rendered row: its cells by column id, plus how to render it."""

    values: dict[str, Any]
    #: How many group keys this row grouped; ``None`` when the query has no
    #: subtotals. ``0`` is the grand total.
    level: int | None
    #: True when the row aggregates over at least one grouped dimension.
    subtotal: bool


@dataclass(frozen=True, slots=True)
class GridPage:
    """One page of rows for the grid, with the columns it is keyed by."""

    columns: tuple[ColumnSpec, ...]
    rows: tuple[GridRow, ...]
    #: Row-group columns, in order — the indent depth a subtotal row can have.
    dimension_count: int
    start_row: int
    #: The total row count when this page proved to be the last one, else ``None``.
    last_row: int | None
    truncated: bool
    elapsed_ms: int
    used_aggregate: bool


def page_bounds(start_row: int, end_row: int | None, *, cap: int) -> tuple[int, int]:
    """``(offset, limit)`` for a requested page, clamped to the surface cap.

    ``end_row`` is exclusive, as AG Grid sends it. Omitted, the caller gets one
    capped page from ``start_row``; larger than the cap, it is cut to the cap —
    the client can page, it cannot widen the page.
    """

    if cap < MIN_PAGE_ROWS:
        raise ValueError("cap must be at least one row")
    offset = max(0, start_row)
    requested = cap if end_row is None else end_row - offset
    return offset, max(MIN_PAGE_ROWS, min(requested, cap))


def to_grid(result: QueryResult, *, start_row: int) -> GridPage:
    """Key ``result``'s positional rows by column id and lift the level marker."""

    marker_index = next(
        (index for index, column in enumerate(result.columns) if column.kind == "marker"),
        None,
    )
    columns = tuple(column for column in result.columns if column.kind != "marker")
    dimension_count = sum(1 for column in columns if column.kind == "dimension")
    rows: list[GridRow] = []
    for row in result.rows:
        level = None if marker_index is None else _as_level(row[marker_index])
        cells = (
            row
            if marker_index is None
            else tuple(value for index, value in enumerate(row) if index != marker_index)
        )
        rows.append(
            GridRow(
                values={column.id: value for column, value in zip(columns, cells, strict=True)},
                level=level,
                subtotal=level is not None and level < dimension_count,
            )
        )
    return GridPage(
        columns=columns,
        rows=tuple(rows),
        dimension_count=dimension_count,
        start_row=start_row,
        last_row=None if result.truncated else start_row + len(rows),
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
        used_aggregate=result.used_aggregate,
    )


def _as_level(value: Any) -> int | None:
    """The marker as an int; ``None`` when the database returned nothing for it."""

    return None if value is None else int(value)


__all__ = ["MIN_PAGE_ROWS", "GridPage", "GridRow", "page_bounds", "to_grid"]
