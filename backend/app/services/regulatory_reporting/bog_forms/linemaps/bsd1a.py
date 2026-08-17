"""BSD1A — Weekly Return on the Twenty Largest Withdrawals Over the Counter.

Official layout: one sheet ``20 LARGEST WITHDRAWALS`` — rows 11–30 are the
twenty ranked withdrawals (``A`` = serial 1…20, shipped in the template and
therefore the sheet's only captured input cells; ``B`` CUSTOMER, ``C`` BRANCH,
``D`` TYPE OF A/C, ``E``…``I`` amounts for THURSDAY / FRIDAY / MONDAY / TUESDAY
/ WEDNESDAY in ¢ Million — blank data cells) and ``J`` = SUM(E:I) per row with
``J31`` the grand total (template formulas). Line/cell map:
docs/bog_returns/bsd1a_line_map.md.

**Data-gap closure (2026-08-16):** every ranked data cell reads the bank's
``teller_withdrawals`` reference dataset (docs/data_engine/datasets/
teller_withdrawals.md — one row per over-the-counter cash withdrawal, pushed
one reporting week per batch through the Data Engine) via ``bsd1a.rank``
(``sources_ext/bsd1a.py``): the ``rank``-th largest weekly withdrawer
(customer × branch × account type, ranked by the week's cedi total, largest
first) with the bound column selecting CUSTOMER / BRANCH / TYPE OF A/C or that
weekday's cedi total (the week = the seven days ending on the reporting date;
Saturday / Sunday withdrawals have no column on the official sheet and are not
ranked — a treatment BoG must confirm). Until the week's file is ingested every
data cell stays ``input_required`` naming the dataset; the serial numbers are
re-emitted from the template so the official numbering survives the
values-only export.
"""

from __future__ import annotations

from dataclasses import replace

from ..spec import LineSpec
from ._common import RowSource, grid_lines, leaf_lines

SHEET = "20 LARGEST WITHDRAWALS"
DATA_ROWS = tuple(range(11, 31))
DATA_COLUMNS = {
    "customer": "B",
    "branch": "C",
    "account_type": "D",
    "thu": "E",
    "fri": "F",
    "mon": "G",
    "tue": "H",
    "wed": "I",
}

def _serial(n: int) -> RowSource:
    return RowSource(
        "constant",
        {"value": n},
        notes="official serial number (template content, re-emitted)",
        unscaled=True,
    )


def _ranked(rank: int) -> RowSource:
    return RowSource(
        "bsd1a.rank",
        {"rank": rank},
        notes=(
            f"rank #{rank} of the week's over-the-counter withdrawers from the "
            "teller_withdrawals dataset (customer × branch × account type by weekly cedi "
            "total; day columns = that weekday's total) — input_required until the week's "
            f"file is ingested, and blank when the week has fewer than {rank} ranked accounts"
        ),
    )


def _labelled(lines: tuple[LineSpec, ...], prefix: str) -> tuple[LineSpec, ...]:
    """The ranked rows carry no label cell (column A is the serial input)."""
    return tuple(
        replace(line, label=f"{prefix} #{int(line.code.rsplit('R', 1)[1]) - 10}") for line in lines
    )


LINES = {
    SHEET: (
        *_labelled(
            leaf_lines(
                "BSD1A",
                SHEET,
                value_columns={"serial": "A"},
                row_sources={row: _serial(row - 10) for row in DATA_ROWS},
                code_prefix="BSD1A.serial",
            ),
            "Serial",
        ),
        *_labelled(
            grid_lines(
                "BSD1A",
                SHEET,
                rows=DATA_ROWS,
                value_columns=DATA_COLUMNS,
                row_sources={row: _ranked(row - 10) for row in DATA_ROWS},
                code_prefix="BSD1A",
            ),
            "Withdrawal",
        ),
    ),
}
