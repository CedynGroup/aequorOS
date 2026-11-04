"""Synthetic currency books with coherent valuation and limit boundaries."""

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import strategies as st

from app.fx.domain.engine import FxPosition, money
from app.policy.service.parameter_register import BANK_FX_THRESHOLDS


@dataclass(frozen=True)
class FxBook:
    positions: tuple[FxPosition, ...]
    tier1: Decimal
    single_limit_pct: Decimal
    aggregate_limit_pct: Decimal
    target_pct: Decimal


@st.composite
def fx_books(draw: st.DrawFn) -> FxBook:
    """Limits: parameter_register.BANK_FX_THRESHOLDS; precision: fx/engine.py.

    Synthetic GHS rates use bands around the hand-constructed USD/EUR/GBP rates
    in tests/fx/domain/test_fx_engine.py (not current quotes). Two same-direction
    currencies hit aggregate-limit neighbors; each also hits single-limit
    neighbors. A third opposite-direction leg tests netting. Currency amounts
    and GHS equivalents have four-decimal HALF_UP precision.
    """
    tier1 = Decimal(draw(st.integers(10_000_000, 500_000_000)))
    single = Decimal(BANK_FX_THRESHOLDS["fx_nop_single_limit_pct"])
    aggregate = Decimal(BANK_FX_THRESHOLDS["fx_nop_aggregate_limit_pct"])
    offset = draw(
        st.one_of(
            st.sampled_from(
                tuple(Decimal(v) for v in ("-0.000001", "0", "0.000001", "-0.5", "0.5"))
            ),
            st.decimals(Decimal(-2), Decimal(2), places=2),
        )
    )
    target = aggregate + offset
    direction = draw(st.sampled_from((-1, 1)))
    opposite_share = draw(st.sampled_from((-single / 2, Decimal(0))))
    positions: list[FxPosition] = []
    for ccy, low, high, share in (
        ("USD", 10, 15, target / 2),
        ("EUR", 12, 16, target / 2),
        ("GBP", 13, 18, opposite_share),
    ):
        spot = draw(st.decimals(Decimal(low), Decimal(high), places=4))
        desired = money(tier1 * share / 100 * direction)
        net = money(desired / spot)
        liability = money(abs(net) * 2 + Decimal(100_000))
        derivatives = draw(st.sampled_from((Decimal(0), Decimal(1000), Decimal(-1000))))
        assets = liability + net - derivatives
        positions.append(
            FxPosition(ccy, money(net * spot), spot, net, assets, liability, derivatives)
        )
    return FxBook(tuple(positions), tier1, single, aggregate, target)
