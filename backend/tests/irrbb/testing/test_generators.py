"""Validate real ladder structure, standard shocks and scaling through the engine."""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.irr.standardised import SfInputs
from app.domain.irr.standardised import run as sf_run
from tests.irrbb.testing.assertions import TOLERANCE, assert_irrbb_invariants, assert_irrbb_scaled
from tests.irrbb.testing.generators import PARAMS, irrbb_books


@settings(max_examples=60, deadline=None)
@given(book=irrbb_books(), scale=st.integers(1, 9))
def test_books_have_valid_schedules_and_scaling_preserves_outlier_ratio(
    book: SfInputs, scale: int
) -> None:
    for item in book.ladders:
        assert (
            len(item.principal)
            == len(item.interest)
            == len(item.outstanding_end)
            == PARAMS.bucket_count
        )
        for index, outstanding in enumerate(item.outstanding_end):
            assert outstanding == sum(item.principal[index + 1 :], Decimal(0)), (
                "IRRBB: outstanding does not match remaining signed principal"
            )
    before = sf_run(book, PARAMS)
    assert_irrbb_invariants(before)
    corrupt = replace(
        before,
        measures=replace(
            before.measures,
            all_scenarios=replace(before.measures.all_scenarios, measure=Decimal(-1)),
        ),
    )
    with pytest.raises(AssertionError, match="IRRBB: economic-value loss"):
        assert_irrbb_invariants(corrupt)
    scaled = replace(
        book,
        tier1=book.tier1 * scale,
        ladders=tuple(
            replace(
                item,
                principal=tuple(v * scale for v in item.principal),
                interest=tuple(v * scale for v in item.interest),
                outstanding_end=tuple(v * scale for v in item.outstanding_end),
            )
            for item in book.ladders
        ),
        currencies=tuple(
            replace(
                item,
                bb_assets_rep=item.bb_assets_rep * scale,
                bb_liabilities_rep=item.bb_liabilities_rep * scale,
            )
            for item in book.currencies
        ),
    )
    after = sf_run(scaled, PARAMS)
    assert_irrbb_scaled(after, before, scale)
    TOLERANCE.ratios.assert_close(
        after.pct_tier1,
        before.pct_tier1,
        because="IRRBB: scaling book and capital changed outlier percentage",
    )
    assert (
        abs(before.pct_tier1 - PARAMS.outlier_threshold_pct) <= Decimal("0.00001")
        or before.pct_tier1 == 0
    ), "IRRBB: generated capital misses intended outlier boundary"
