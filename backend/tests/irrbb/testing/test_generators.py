"""Validate real ladder structure, standard shocks and scaling through the engine."""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.irrbb.domain.standardised import CurrencyInputs, SfInputs
from app.irrbb.domain.standardised import run as sf_run
from tests.irrbb.testing.assertions import (
    assert_irrbb_invariants,
    assert_irrbb_ratio_scaled,
    assert_irrbb_scaled,
)
from tests.irrbb.testing.generators import AS_OF, PARAMS, irrbb_books, ladder

CANCELLATION_BOOK = SfInputs(
    AS_OF,
    "GHS",
    (
        replace(
            ladder("GHS", [Decimal(100_001)] + [Decimal(0)] * (PARAMS.bucket_count - 1)),
            portfolio="synthetic_assets",
        ),
        replace(
            ladder("GHS", [Decimal(-100_000)] + [Decimal(0)] * (PARAMS.bucket_count - 1)),
            portfolio="synthetic_funding",
        ),
    ),
    (),
    (
        CurrencyInputs(
            "GHS",
            tuple(Decimal("0.25") for _ in range(PARAMS.bucket_count)),
            Decimal(1),
            Decimal(100_001),
            Decimal(100_000),
        ),
    ),
    Decimal("0.000140") * 100 / PARAMS.outlier_threshold_pct,
)


@settings(max_examples=60, deadline=None)
@given(book=irrbb_books(), scale=st.integers(1, 9))
@example(book=CANCELLATION_BOOK, scale=6)
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
    assert_irrbb_ratio_scaled(after, before, scale, tier1=book.tier1)
    assert (
        abs(before.pct_tier1 - PARAMS.outlier_threshold_pct) <= Decimal("0.00001")
        or before.pct_tier1 == 0
    ), "IRRBB: generated capital misses intended outlier boundary"


def test_ratio_scaling_rejects_a_material_change() -> None:
    before = sf_run(CANCELLATION_BOOK, PARAMS)
    with pytest.raises(AssertionError, match="IRRBB: scaling book and capital"):
        assert_irrbb_ratio_scaled(
            replace(before, pct_tier1=before.pct_tier1 + Decimal(1)),
            before,
            1,
            tier1=CANCELLATION_BOOK.tier1,
        )
