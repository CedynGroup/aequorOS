"""Repricing buckets: the day boundaries BI and fact derivation share (P0-8).

The nine ``(name, upper days, midpoint)`` triples are written out by hand from
the literal ``fact_derivation._IRR_BUCKETS`` carried before the lift — never
echoed from the module — and the midpoints are asserted to be the STRING the
``irr_position`` fact hashes, not the engine's Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.domain.irr.buckets import (
    BUCKET_UPPER_DAYS,
    REPRICING_BUCKETS,
    bucket_for_days,
    repricing_bucket,
)
from app.domain.irr.engine import IRR_BUCKETS
from app.services import fact_derivation

EXPECTED_BUCKETS: tuple[tuple[str, int | None, str], ...] = (
    ("overnight", 1, "0.003"),
    ("1-7d", 7, "0.014"),
    ("8-30d", 30, "0.06"),
    ("1-3m", 91, "0.17"),
    ("3-6m", 182, "0.38"),
    ("6-12m", 365, "0.75"),
    ("1-3y", 1095, "1.9"),
    ("3-5y", 1825, "4.0"),
    ("5y+", None, "7.0"),
)

AS_OF = date(2026, 3, 31)


@dataclass(frozen=True)
class _Row:
    rate_type: str | None
    contractual_maturity: date | None
    next_repricing_date: date | None


def test_the_buckets_are_the_nine_irrbb_buckets_with_string_midpoints() -> None:
    assert REPRICING_BUCKETS == EXPECTED_BUCKETS
    assert all(type(midpoint) is str for _, _, midpoint in REPRICING_BUCKETS)


def test_names_and_midpoints_come_from_the_engine_and_only_the_days_are_added() -> None:
    assert tuple(name for name, _, _ in REPRICING_BUCKETS) == tuple(name for name, _ in IRR_BUCKETS)
    assert tuple(midpoint for _, _, midpoint in REPRICING_BUCKETS) == tuple(
        str(midpoint) for _, midpoint in IRR_BUCKETS
    )
    assert tuple(BUCKET_UPPER_DAYS) == tuple(name for name, _ in IRR_BUCKETS)


def test_the_day_boundaries_ascend_and_only_the_long_end_is_open() -> None:
    bounded = [upper for _, upper, _ in REPRICING_BUCKETS if upper is not None]
    assert bounded == sorted(bounded)
    assert len(set(bounded)) == len(bounded)
    assert REPRICING_BUCKETS[-1][1] is None
    assert all(upper is not None for _, upper, _ in REPRICING_BUCKETS[:-1])


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "overnight"),
        (1, "overnight"),
        (2, "1-7d"),
        (7, "1-7d"),
        (8, "8-30d"),
        (30, "8-30d"),
        (31, "1-3m"),
        (91, "1-3m"),
        (92, "3-6m"),
        (182, "3-6m"),
        (183, "6-12m"),
        (365, "6-12m"),
        (366, "1-3y"),
        (1095, "1-3y"),
        (1096, "3-5y"),
        (1825, "3-5y"),
        (1826, "5y+"),
        (100_000, "5y+"),
    ],
)
def test_bucket_for_days_edges(days: int, expected: str) -> None:
    assert bucket_for_days(days) == expected


def test_a_floating_position_reprices_at_its_next_repricing_date() -> None:
    row = _Row("FLOATING", date(2031, 3, 31), date(2026, 4, 30))
    assert repricing_bucket(row, AS_OF) == "8-30d"


def test_a_floating_position_without_a_repricing_date_uses_its_maturity() -> None:
    # 31 Mar → 29 Sep is 182 days, the last day of 3-6m; one more day is 6-12m.
    assert repricing_bucket(_Row("FLOATING", date(2026, 9, 29), None), AS_OF) == "3-6m"
    assert repricing_bucket(_Row("FLOATING", date(2026, 9, 30), None), AS_OF) == "6-12m"


def test_a_fixed_position_reprices_at_maturity_even_with_a_repricing_date() -> None:
    row = _Row("FIXED", date(2028, 3, 31), date(2026, 4, 30))
    assert repricing_bucket(row, AS_OF) == "1-3y"


def test_a_fixed_position_without_a_maturity_falls_back_to_its_repricing_date() -> None:
    row = _Row("FIXED", None, date(2026, 4, 1))
    assert repricing_bucket(row, AS_OF) == "overnight"


def test_a_position_with_no_horizon_is_none() -> None:
    assert repricing_bucket(_Row(None, None, None), AS_OF) is None
    assert repricing_bucket(_Row("FLOATING", None, None), AS_OF) is None


def test_a_horizon_in_the_past_is_day_zero() -> None:
    assert repricing_bucket(_Row("FIXED", date(2020, 1, 1), None), AS_OF) == "overnight"


def test_fact_derivation_reads_the_domain_definition() -> None:
    assert fact_derivation._IRR_BUCKETS is REPRICING_BUCKETS
    assert fact_derivation._bucket_for_days is bucket_for_days
    assert fact_derivation._repricing_bucket is repricing_bucket
