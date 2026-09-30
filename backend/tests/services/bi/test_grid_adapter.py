"""The AG Grid Community adapter (D-030): paging bounds and row shaping.

Unit tests over hand-built ``QueryResult`` values rather than over a database,
because what this module owns is a translation: positional rows to rows keyed by
column id, the ``__level`` marker to row metadata, and a requested page to a
page the surface allows. The compiler's own suites own the SQL; the route suites
own the two together.

The column shapes below are the compiler's as documented in its wave-2 handoff —
``measure|value`` for a pivot cell, ``measure|prior`` / ``|delta`` / ``|delta_pct``
for a comparison, one ``kind="marker"`` column when subtotals are on — so a
change on either side of the seam fails here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.services.bi.compiler import ColumnSpec
from app.services.bi.execution import QueryResult
from app.services.bi.grid_adapter import MIN_PAGE_ROWS, page_bounds, to_grid


def dimension(member_id: str) -> ColumnSpec:
    return ColumnSpec(
        id=member_id, label=member_id, kind="dimension", format="text", member_id=member_id
    )


def measure(column_id: str, *, member_id: str, **extra: Any) -> ColumnSpec:
    return ColumnSpec(
        id=column_id, label=column_id, kind="measure", format="amount", member_id=member_id, **extra
    )


MARKER = ColumnSpec(id="__level", label="Level", kind="marker", format="int")


def result(
    columns: tuple[ColumnSpec, ...],
    rows: list[tuple[Any, ...]],
    *,
    truncated: bool = False,
) -> QueryResult:
    return QueryResult(
        columns=columns,
        rows=rows,
        truncated=truncated,
        elapsed_ms=7,
        used_aggregate=False,
    )


# --- page bounds ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start_row", "end_row", "cap", "expected"),
    [
        (0, 100, 500, (0, 100)),
        (0, None, 500, (0, 500)),
        (500, 1_000, 500, (500, 500)),
        # S13: a client asking for more than the cap gets the cap, not its ask.
        (0, 10_000, 500, (0, 500)),
        (-5, 10, 500, (0, 10)),
        # A page is at least one row: an empty page would read as an empty book.
        (10, 10, 500, (10, MIN_PAGE_ROWS)),
    ],
)
def test_a_requested_page_is_clamped_to_the_surface_cap(
    start_row: int, end_row: int | None, cap: int, expected: tuple[int, int]
) -> None:
    assert page_bounds(start_row, end_row, cap=cap) == expected


def test_a_cap_below_one_row_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        page_bounds(0, 10, cap=0)


# --- row shaping ---------------------------------------------------------------------------


def test_positional_rows_become_rows_keyed_by_column_id() -> None:
    page = to_grid(
        result(
            (dimension("branch.code"), measure("loans.balance_rc", member_id="loans.balance_rc")),
            [("B1", Decimal("400")), ("B2", Decimal("200"))],
        ),
        start_row=0,
    )
    assert [row.values for row in page.rows] == [
        {"branch.code": "B1", "loans.balance_rc": Decimal("400")},
        {"branch.code": "B2", "loans.balance_rc": Decimal("200")},
    ]
    assert page.dimension_count == 1
    assert all(row.level is None and not row.subtotal for row in page.rows)


def test_a_page_that_was_not_truncated_reports_the_total_row_count() -> None:
    """What the Infinite Row Model needs: the total, or nothing at all."""
    complete = to_grid(result((dimension("branch.code"),), [("B1",), ("B2",)]), start_row=100)
    assert complete.last_row == 102
    assert complete.truncated is False
    more = to_grid(result((dimension("branch.code"),), [("B1",)], truncated=True), start_row=100)
    assert more.last_row is None
    assert more.truncated is True


def test_the_level_marker_becomes_row_metadata_and_leaves_the_columns() -> None:
    """A rollup row is identified by how many keys it grouped, not by a column a
    user can see: level 0 is the grand total, level 1 a branch subtotal, level 2
    (the full depth) an ordinary row."""
    page = to_grid(
        result(
            (
                dimension("branch.code"),
                dimension("loan.grade"),
                MARKER,
                measure("loans.balance_rc", member_id="loans.balance_rc"),
            ),
            [
                (None, None, 0, Decimal("600")),
                ("B1", None, 1, Decimal("400")),
                ("B1", "standard", 2, Decimal("400")),
            ],
        ),
        start_row=0,
    )
    assert [column.id for column in page.columns] == [
        "branch.code",
        "loan.grade",
        "loans.balance_rc",
    ]
    assert page.dimension_count == 2
    assert [(row.level, row.subtotal) for row in page.rows] == [
        (0, True),
        (1, True),
        (2, False),
    ]
    assert page.rows[0].values == {
        "branch.code": None,
        "loan.grade": None,
        "loans.balance_rc": Decimal("600"),
    }


def test_pivot_and_comparison_column_ids_survive_verbatim() -> None:
    """The grid addresses cells by the compiler's own ids; renaming one here
    would silently blank a column."""
    columns = (
        dimension("branch.code"),
        measure(
            "loans.balance_rc|retail_loans",
            member_id="loans.balance_rc",
            pivot_value="retail_loans",
        ),
        measure("loans.balance_rc|prior", member_id="loans.balance_rc", role="prior"),
        measure("loans.balance_rc|delta", member_id="loans.balance_rc", role="delta"),
        measure("loans.balance_rc|delta_pct", member_id="loans.balance_rc", role="delta_pct"),
    )
    page = to_grid(
        result(columns, [("B1", Decimal("400"), Decimal("350"), Decimal("50"), 14.3)]),
        start_row=0,
    )
    assert set(page.rows[0].values) == {
        "branch.code",
        "loans.balance_rc|retail_loans",
        "loans.balance_rc|prior",
        "loans.balance_rc|delta",
        "loans.balance_rc|delta_pct",
    }
    assert [column.pivot_value for column in page.columns if column.pivot_value] == ["retail_loans"]
    assert [column.role for column in page.columns if column.role] == [
        "prior",
        "delta",
        "delta_pct",
    ]


def test_a_null_marker_is_carried_as_no_level_rather_than_a_zero() -> None:
    """Level 0 means "the grand total"; a missing marker must not read as one."""
    page = to_grid(
        result((dimension("branch.code"), MARKER), [("B1", None)]),
        start_row=0,
    )
    assert page.rows[0].level is None
    assert page.rows[0].subtotal is False


def test_an_empty_result_is_an_empty_page_that_is_still_the_last_one() -> None:
    page = to_grid(result((dimension("branch.code"),), []), start_row=0)
    assert page.rows == ()
    assert page.last_row == 0
    assert page.dimension_count == 1
