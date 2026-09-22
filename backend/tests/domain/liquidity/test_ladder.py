"""The contractual ladder horizons and bucket rule BI reuses (P0-8).

Written out by hand from the ``regulatory_liquidity`` literals carried before
the lift — the five LMTD horizons and the undated-item convention (demand-
natured → shortest bucket, everything else → longest).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.liquidity.ladder import LADDER_HORIZON_DAYS, ladder_bucket_index
from app.services import regulatory_liquidity

AS_OF = date(2026, 3, 31)


def test_the_horizons_are_the_five_lmtd_buckets() -> None:
    assert LADDER_HORIZON_DAYS == (30, 91, 182, 365, None)


@pytest.mark.parametrize(
    ("days", "expected_index"),
    [
        (-400, 0),
        (-1, 0),
        (0, 0),
        (30, 0),
        (31, 1),
        (91, 1),
        (92, 2),
        (182, 2),
        (183, 3),
        (365, 3),
        (366, 4),
        (10_000, 4),
    ],
)
def test_a_dated_position_takes_the_first_horizon_at_or_beyond_its_days(
    days: int, expected_index: int
) -> None:
    maturity = AS_OF + timedelta(days=days)
    assert ladder_bucket_index(maturity, AS_OF, on_demand=False) == expected_index
    # ``on_demand`` only decides where an UNDATED position goes.
    assert ladder_bucket_index(maturity, AS_OF, on_demand=True) == expected_index


def test_an_undated_demand_item_is_the_shortest_bucket() -> None:
    assert ladder_bucket_index(None, AS_OF, on_demand=True) == 0


def test_an_undated_non_demand_item_is_the_longest_bucket() -> None:
    assert ladder_bucket_index(None, AS_OF, on_demand=False) == len(LADDER_HORIZON_DAYS) - 1 == 4


def test_regulatory_liquidity_reads_the_domain_definition() -> None:
    assert regulatory_liquidity._LADDER_HORIZON_DAYS is LADDER_HORIZON_DAYS
    assert regulatory_liquidity._ladder_bucket_index is ladder_bucket_index
