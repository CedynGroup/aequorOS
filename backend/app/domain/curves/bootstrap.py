"""Sovereign zero-curve bootstrap from Ghana's instrument reality (spec §5 step 4).

No swap market: the curve is built from BoG T-bills (91/182/364-day, quoted as
discount rates or true yields on ACT/364) and GoG/GFIM bonds (clean or dirty
prices, annual or semi-annual coupons). Bills pin the short end **exactly**
(a bill's price *is* its discount factor); bonds are appended in maturity
order, each solving one new node zero rate so the bond reprices to its quoted
price exactly on the curve interpolated through all previously-set nodes —
Newton on the node zero with a guaranteed bisection fallback.

Ghana quirks handled here (spec §6.3 "Building from a bond/T-bill market"):
sparse and clustered maturities are simply nodes wherever instruments exist;
duplicate-maturity quotes collapse by ``volume_weight`` (turnover-weighted
average of the quoted yield/price — GFIM publishes volumes) or ``last_wins``
(input order, for pre-sorted feeds).

Because monotone convex and PCHIP couple neighbouring intervals (each node's
forward estimate reads the adjacent discrete forwards), appending a later node
perturbs the curve around earlier ones. The sequential pass therefore only
seeds the solution; **iterative refinement sweeps** then re-solve each bond
node against the full node set until every instrument reprices simultaneously
— the same device as QuantLib's IterativeBootstrap for non-local
interpolators. Strictly local methods (linear-zero, log-linear DF) converge in
the first sweep.

The finished :class:`ZeroCurve` stores **continuously compounded** zeros on
the curve day count (default ACT/364, the GFIM normalization target) and is
immutable — rebuilding is the only mutation, mirroring the platform's
immutable-curve-build rule (spec §7).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import cached_property
from typing import Literal

from app.domain.curves.conventions import (
    DayCount,
    accrued_interest,
    bond_cashflows,
    discount_to_yield,
    year_fraction,
)
from app.domain.curves.interpolation import (
    Extrapolation,
    ZeroInterpolator,
    make_interpolator,
)

__all__ = [
    "BillQuote",
    "BondQuote",
    "BootstrapError",
    "DuplicatePolicy",
    "ZeroCurve",
    "bootstrap_zero_curve",
]

type DuplicatePolicy = Literal["volume_weight", "last_wins"]

_REPRICE_TOLERANCE = 1e-10  # solver target; the acid test asserts 1e-8


class BootstrapError(ValueError):
    """The instrument set cannot produce a well-formed curve."""


@dataclass(frozen=True)
class BillQuote:
    """One T-bill: quoted either as a discount rate or a true (interest) yield.

    Exactly one of ``discount_rate`` / ``yield_rate`` must be set (decimals).
    ``day_count`` defaults to the GFIM ACT/364 standard. ``volume`` (face or
    turnover, any consistent unit) feeds volume-weighted duplicate collapsing.
    """

    settlement: date
    maturity: date
    discount_rate: float | None = None
    yield_rate: float | None = None
    day_count: DayCount = DayCount.ACT_364
    volume: float | None = None

    def __post_init__(self) -> None:
        if (self.discount_rate is None) == (self.yield_rate is None):
            raise BootstrapError(
                "A BillQuote needs exactly one of discount_rate or yield_rate."
            )
        if self.maturity <= self.settlement:
            raise BootstrapError("Bill maturity must fall strictly after settlement.")
        if self.volume is not None and self.volume <= 0.0:
            raise BootstrapError("Bill volume must be strictly positive when given.")

    @property
    def days(self) -> int:
        return (self.maturity - self.settlement).days

    def true_yield(self) -> float:
        """The bill's true (interest) yield on its own day count."""
        if self.yield_rate is not None:
            return self.yield_rate
        assert self.discount_rate is not None
        return discount_to_yield(self.discount_rate, self.days, self.day_count)

    def discount_factor(self) -> float:
        """Exact settlement-to-maturity discount factor: DF = 1 / (1 + i * t)."""
        t = year_fraction(self.settlement, self.maturity, self.day_count)
        return 1.0 / (1.0 + self.true_yield() * t)


@dataclass(frozen=True)
class BondQuote:
    """One fixed-coupon bond quote per 100 face.

    ``price_type`` says whether ``price`` is clean (GFIM convention) or dirty;
    ``day_count`` defaults to ACT/365F (GoG bond accrual). ``volume`` feeds
    volume-weighted duplicate collapsing (prices are averaged, turnover-
    weighted, before the maturity node is solved).
    """

    settlement: date
    maturity: date
    coupon_rate: float
    frequency: int
    price: float
    price_type: Literal["clean", "dirty"] = "clean"
    day_count: DayCount = DayCount.ACT_365F
    volume: float | None = None

    def __post_init__(self) -> None:
        if self.maturity <= self.settlement:
            raise BootstrapError("Bond maturity must fall strictly after settlement.")
        if self.price <= 0.0:
            raise BootstrapError("Bond price must be strictly positive.")
        if self.frequency not in (1, 2):
            raise BootstrapError("Bond coupon frequency must be 1 or 2.")
        if self.volume is not None and self.volume <= 0.0:
            raise BootstrapError("Bond volume must be strictly positive when given.")

    def dirty_price(self) -> float:
        if self.price_type == "dirty":
            return self.price
        return self.price + accrued_interest(
            self.settlement, self.maturity, self.coupon_rate, self.frequency, self.day_count
        )


@dataclass(frozen=True)
class ZeroCurve:
    """Immutable zero curve: continuously compounded node zeros on one day count.

    ``times`` are year fractions from ``valuation_date`` on ``day_count``;
    ``zeros`` are continuously compounded decimals. The interpolator is built
    lazily and cached; the dataclass is frozen, so a curve can never drift
    from the nodes it was published with.
    """

    valuation_date: date
    day_count: DayCount
    times: tuple[float, ...]
    zeros: tuple[float, ...]
    interpolation: str
    extrapolation: Extrapolation = "flat_forward"

    @cached_property
    def _interpolator(self) -> ZeroInterpolator:
        return make_interpolator(self.interpolation, self.times, self.zeros, self.extrapolation)

    def time_of(self, day: date) -> float:
        """Curve time (years, curve day count) of a calendar date."""
        return year_fraction(self.valuation_date, day, self.day_count)

    def zero(self, t: float) -> float:
        """Continuously compounded zero rate at time ``t``."""
        return self._interpolator.zero(t)

    def df(self, t: float) -> float:
        """Discount factor ``exp(-z(t) * t)``."""
        if t == 0.0:
            return 1.0
        return math.exp(-self.zero(t) * t)

    def instantaneous_forward(self, t: float) -> float:
        return self._interpolator.instantaneous_forward(t)

    def forward(self, t1: float, t2: float) -> float:
        """Continuously compounded forward rate between ``t1`` and ``t2``."""
        if t2 <= t1:
            raise BootstrapError("forward(t1, t2) requires t1 < t2.")
        return (self.zero(t2) * t2 - self.zero(t1) * t1) / (t2 - t1)


@dataclass
class _Node:
    time: float
    zero: float
    label: str


def bootstrap_zero_curve(  # noqa: PLR0913 - the methodology's versioned params, spelled out
    bills: Sequence[BillQuote],
    bonds: Sequence[BondQuote] = (),
    interpolation: str = "monotone_convex",
    extrapolation: Extrapolation = "flat_forward",
    curve_day_count: DayCount = DayCount.ACT_364,
    duplicate_policy: DuplicatePolicy = "volume_weight",
) -> ZeroCurve:
    """Bootstrap the sovereign zero curve from bills and bonds.

    Bills give exact discount factors at their maturities; bonds are added in
    maturity order, each solving its own node zero so the bond reprices
    exactly. Every input instrument reprices on the finished curve to within
    1e-8 (the acid test).
    """
    if not bills and not bonds:
        raise BootstrapError("At least one instrument is required to bootstrap a curve.")
    valuation_date = _common_settlement(bills, bonds)
    nodes: list[_Node] = []

    for bill in _collapse_bills(bills, duplicate_policy):
        t = year_fraction(valuation_date, bill.maturity, curve_day_count)
        df = bill.discount_factor()
        nodes.append(_Node(time=t, zero=-math.log(df) / t, label=f"bill:{bill.maturity}"))

    nodes.sort(key=lambda node: node.time)
    _reject_clashes(nodes)

    last_bill_time = nodes[-1].time if nodes else 0.0
    bond_flows: list[tuple[int, tuple[tuple[float, float], ...], float]] = []
    for bond in _collapse_bonds(bonds, duplicate_policy):
        t = year_fraction(valuation_date, bond.maturity, curve_day_count)
        if t <= last_bill_time or any(math.isclose(t, node.time, abs_tol=1e-12) for node in nodes):
            raise BootstrapError(
                f"Bond maturing {bond.maturity} does not extend the curve "
                "(node clash or maturity inside the bill span)."
            )
        flows = tuple(
            (year_fraction(valuation_date, flow.payment_date, curve_day_count), flow.amount)
            for flow in bond_cashflows(
                bond.settlement, bond.maturity, bond.coupon_rate, bond.frequency, bond.day_count
            )
        )
        target = bond.dirty_price()
        zero = _solve_node(nodes, len(nodes), t, flows, target, interpolation, extrapolation)
        nodes.append(_Node(time=t, zero=zero, label=f"bond:{bond.maturity}"))
        bond_flows.append((len(nodes) - 1, flows, target))
        # Bonds arrive in maturity order past the bill span, so no re-sort here;
        # the node index recorded above must stay stable for the refinement pass.

    _refine_bond_nodes(nodes, bond_flows, interpolation, extrapolation)

    return ZeroCurve(
        valuation_date=valuation_date,
        day_count=curve_day_count,
        times=tuple(node.time for node in nodes),
        zeros=tuple(node.zero for node in nodes),
        interpolation=interpolation,
        extrapolation=extrapolation,
    )


def _common_settlement(bills: Sequence[BillQuote], bonds: Sequence[BondQuote]) -> date:
    settlements = {quote.settlement for quote in (*bills, *bonds)}
    if len(settlements) != 1:
        raise BootstrapError(
            "All instruments must share one settlement (valuation) date, "
            f"got {sorted(settlements)}."
        )
    return next(iter(settlements))


def _collapse_bills(
    bills: Sequence[BillQuote], policy: DuplicatePolicy
) -> tuple[BillQuote, ...]:
    """Collapse duplicate-maturity bills to one representative quote.

    ``volume_weight`` averages the true yields, weighted by volume (equal
    weights when any volume is missing); ``last_wins`` keeps the final quote
    in input order — for feeds already ordered by publication time.
    """
    grouped: dict[date, list[BillQuote]] = {}
    for bill in bills:
        grouped.setdefault(bill.maturity, []).append(bill)
    collapsed: list[BillQuote] = []
    for maturity in sorted(grouped):
        group = grouped[maturity]
        if len(group) == 1 or policy == "last_wins":
            collapsed.append(group[-1])
            continue
        weights = _weights(tuple(quote.volume for quote in group))
        blended_yield = sum(w * quote.true_yield() for w, quote in zip(weights, group, strict=True))
        collapsed.append(
            replace(group[-1], discount_rate=None, yield_rate=blended_yield, volume=None)
        )
    return tuple(collapsed)


def _collapse_bonds(
    bonds: Sequence[BondQuote], policy: DuplicatePolicy
) -> tuple[BondQuote, ...]:
    """Collapse duplicate-maturity bonds; volume-weighting blends dirty prices.

    Duplicate quotes must agree on the bond's terms (coupon, frequency, day
    count) — a maturity shared by two *different* bonds is a data error the
    caller must resolve upstream, not something to average through.
    """
    grouped: dict[date, list[BondQuote]] = {}
    for bond in bonds:
        grouped.setdefault(bond.maturity, []).append(bond)
    collapsed: list[BondQuote] = []
    for maturity in sorted(grouped):
        group = grouped[maturity]
        if len(group) == 1 or policy == "last_wins":
            collapsed.append(group[-1])
            continue
        terms = {(bond.coupon_rate, bond.frequency, bond.day_count) for bond in group}
        if len(terms) != 1:
            raise BootstrapError(
                f"Duplicate-maturity bonds at {maturity} disagree on coupon terms; "
                "volume-weighting only blends prices of the same bond."
            )
        weights = _weights(tuple(bond.volume for bond in group))
        blended_dirty = sum(w * bond.dirty_price() for w, bond in zip(weights, group, strict=True))
        collapsed.append(
            replace(group[-1], price=blended_dirty, price_type="dirty", volume=None)
        )
    return tuple(collapsed)


def _weights(volumes: tuple[float | None, ...]) -> tuple[float, ...]:
    if any(volume is None for volume in volumes):
        equal = 1.0 / len(volumes)
        return tuple(equal for _ in volumes)
    total = sum(v for v in volumes if v is not None)
    return tuple((v if v is not None else 0.0) / total for v in volumes)


def _reject_clashes(nodes: Sequence[_Node]) -> None:
    for left, right in zip(nodes, nodes[1:], strict=False):
        if math.isclose(left.time, right.time, abs_tol=1e-12):
            raise BootstrapError(
                f"Two curve nodes collide at t={left.time} ({left.label} vs {right.label})."
            )


def _solve_node(  # noqa: PLR0913
    nodes: Sequence[_Node],
    node_index: int,
    node_time: float,
    flows: tuple[tuple[float, float], ...],
    target_dirty: float,
    interpolation: str,
    extrapolation: Extrapolation,
) -> float:
    """Solve one node's zero rate so its bond reprices exactly on the curve.

    ``node_index == len(nodes)`` appends a trial node (the sequential pass);
    a smaller index re-solves an existing node in place with every other node
    fixed (the refinement pass). Cash-flow times are already on the curve day
    count (the bond's own day count governs only accrued interest). The
    interpolator is rebuilt each iteration, so coupon dates beyond the last
    fixed node correctly co-move with the unknown.
    """
    times = [node.time for node in nodes]
    zeros = [node.zero for node in nodes]
    if node_index == len(nodes):
        times.append(node_time)
        zeros.append(zeros[-1] if zeros else 0.10)

    def model_price(candidate_zero: float) -> float:
        zeros[node_index] = candidate_zero
        interpolator = make_interpolator(interpolation, times, zeros, extrapolation)
        return sum(amount * math.exp(-interpolator.zero(t) * t) for t, amount in flows)

    # Sequential pass: warm-start from the last known zero (seeded on append);
    # refinement pass: warm-start from the node's current value.
    initial = zeros[node_index]
    root = _newton_with_bisection(
        lambda z: model_price(z) - target_dirty,
        initial=initial,
        lower=-0.5,
        upper=2.0,
        tolerance=_REPRICE_TOLERANCE,
    )
    if root is None:
        raise BootstrapError(
            f"No zero rate in [-50%, 200%] reprices the bond at node t={node_time:.4f} "
            f"to {target_dirty} — check the quote."
        )
    return root


_MAX_REFINEMENT_SWEEPS = 50


def _refine_bond_nodes(
    nodes: list[_Node],
    bond_flows: Sequence[tuple[int, tuple[tuple[float, float], ...], float]],
    interpolation: str,
    extrapolation: Extrapolation,
) -> None:
    """Sweep the bond nodes until every bond reprices simultaneously.

    Monotone convex and PCHIP couple neighbouring intervals, so the sequential
    pass alone leaves earlier bonds slightly off once later nodes exist. Each
    sweep re-solves every bond node in maturity order against the full node
    set; the fixed point is the curve on which all bonds reprice within
    ``_REPRICE_TOLERANCE`` at once (bills are node-exact by construction and
    never drift). Coupling is weak, so a handful of sweeps converge; a failure
    to converge inside ``_MAX_REFINEMENT_SWEEPS`` is a data problem and raises.
    """
    if not bond_flows:
        return
    for _ in range(_MAX_REFINEMENT_SWEEPS):
        interpolator = make_interpolator(
            interpolation,
            [node.time for node in nodes],
            [node.zero for node in nodes],
            extrapolation,
        )
        worst = max(
            abs(
                sum(amount * math.exp(-interpolator.zero(t) * t) for t, amount in flows)
                - target
            )
            for _, flows, target in bond_flows
        )
        if worst < _REPRICE_TOLERANCE:
            return
        for node_index, flows, target in bond_flows:
            nodes[node_index].zero = _solve_node(
                nodes, node_index, nodes[node_index].time, flows, target,
                interpolation, extrapolation,
            )
    raise BootstrapError(
        "Bootstrap refinement failed to converge — the instrument set is "
        "internally inconsistent (crossed or arbitrageable quotes)."
    )


def _newton_with_bisection(
    objective: Callable[[float], float],
    initial: float,
    lower: float,
    upper: float,
    tolerance: float,
) -> float | None:
    """Newton (numeric derivative) with a bisection fallback on [lower, upper]."""
    x = min(max(initial, lower), upper)
    step = 1e-7
    for _ in range(50):
        error = objective(x)
        if abs(error) < tolerance:
            return x
        slope = (objective(x + step) - objective(x - step)) / (2.0 * step)
        if slope == 0.0 or not math.isfinite(slope):
            break
        candidate = x - error / slope
        if not math.isfinite(candidate) or candidate <= lower or candidate >= upper:
            break
        x = candidate
    low, high = lower, upper
    low_error = objective(low)
    high_error = objective(high)
    if low_error == 0.0:
        return low
    if high_error == 0.0:
        return high
    if math.copysign(1.0, low_error) == math.copysign(1.0, high_error):
        return None
    for _ in range(200):
        midpoint = (low + high) / 2.0
        error = objective(midpoint)
        if abs(error) < tolerance or (high - low) / 2.0 < 1e-15:
            return midpoint
        if math.copysign(1.0, error) == math.copysign(1.0, low_error):
            low, low_error = midpoint, error
        else:
            high = midpoint
    return (low + high) / 2.0
