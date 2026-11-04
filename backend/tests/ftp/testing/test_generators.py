"""Public FTP engines establish validity and metamorphic relations."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.ftp.domain.engine import (
    build_curve,
    nmd_split,
    product_profitability,
    validate_product_alignment,
)
from tests.ftp.testing.assertions import assert_ftp_invariants, assert_ftp_ratios_unchanged
from tests.ftp.testing.generators import FtpBook, ftp_books, policy


@given(book=ftp_books(), scale=st.integers(1, 10))
def test_generated_books_align_price_and_reconcile(book: FtpBook, scale: int) -> None:
    curve = build_curve(book.points)
    assert curve.arithmetic_consistent
    assert all(
        a.tenor_years < b.tenor_years and a.ftp_rate_pct <= b.ftp_rate_pct
        for a, b in zip(curve.points, curve.points[1:], strict=False)
    )
    validate_product_alignment(book.products, curve)
    floor = policy("ftp_min_product_margin_pct")
    result = product_profitability(book.products, curve, floor)
    nmd = nmd_split(book.nmds, curve, policy("nmd_core_min_pct"), policy("nmd_core_max_pct"))
    assert_ftp_invariants(result, nmd)
    with pytest.raises(AssertionError, match="FTP: product balances"):
        assert_ftp_invariants(replace(result, total_balance_ghs=result.total_balance_ghs + 1), nmd)
    with pytest.raises(AssertionError, match="FTP: NMD core"):
        assert_ftp_invariants(result, replace(nmd, total_core_ghs=nmd.total_core_ghs + 1))
    assert_ftp_ratios_unchanged(
        product_profitability(
            tuple(replace(p, balance_ghs=p.balance_ghs * scale) for p in book.products),
            curve,
            floor,
        ),
        result,
    )
    assert (
        product_profitability(
            tuple(reversed(book.products)), build_curve(tuple(reversed(book.points))), floor
        )
        == result
    ), "FTP: reordered inputs change priced book"
    assert (
        nmd_split(tuple(reversed(book.nmds)), curve, nmd.core_min_pct, nmd.core_max_pct) == nmd
    ), "FTP: reordered deposits change split"
