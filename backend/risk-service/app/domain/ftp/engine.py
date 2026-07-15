"""Pure funds-transfer-pricing (FTP) engine.

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the FTP transfer curve, the product book, the
branch network, the non-maturity-deposit (NMD) segments and the resolved
parameter set, and receive fully materialized curve, product-profitability,
branch-ranking and NMD-split results with per-line items. Monetary amounts
quantize to ``MONEY`` (4 dp) and rate/percentage figures to ``RATIO_PCT``
(6 dp), both with ``ROUND_HALF_UP``; status classification always happens AFTER
quantization so stored and displayed values agree.

Methodology (Product Documentation Module 5 — matched-maturity FTP):

- **Transfer curve.** Every tenor point carries a base curve yield (BoG policy +
  GHS-IBOR + GoG yields), a liquidity premium (bps, for tenors beyond one year)
  and a bank funding spread (bps, the bank's credit spread over sovereign). The
  transfer rate is ``ftp = base + (liquidity_premium_bps + funding_spread_bps) /
  100``. A tenor whose point is not on the curve is priced by nearest-endpoint
  clamping outside the curve and linear interpolation between the two bracketing
  points inside it.
- **Product profitability.** Each product's FTP rate is re-derived from the
  curve by tenor (the seeded rate is verified, never trusted blindly). Asset
  products earn ``net_margin = customer_rate - ftp - operating_cost -
  expected_credit_loss - capital_charge``; liability products (deposits) earn
  the FTP credit ``net_margin = ftp - customer_rate - operating_cost``. A product
  is flagged when its net margin falls below ``min_product_margin_pct``. The
  balance-weighted portfolio net interest margin, asset yield and funding credit
  summarize the book.
- **Branch profitability.** Each branch earns the portfolio funding credit on
  its deposits and the portfolio asset yield on its loans; branches are ranked
  by net FTP contribution.
- **NMD split.** Non-maturity deposits are decomposed into a stable *core*
  (which receives a long-tenor FTP rate at the segment's effective duration) and
  a *volatile* remainder (which receives the overnight FTP rate). The blended
  assigned rate is the core/volatile weighted average; the overall core share is
  validated against the ``[nmd_core_min_pct, nmd_core_max_pct]`` policy band.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

type FtpCategory = Literal["asset", "liability"]
type FtpStatus = Literal["green", "amber", "red"]
type FtpLineSection = Literal["ftp_curve", "ftp_product", "ftp_branch"]

# A seeded FTP rate is accepted when it agrees with the curve-derived rate within
# this tolerance (in percentage points); a larger gap means the seed and curve
# have drifted apart and the run is degenerate.
FTP_ALIGNMENT_TOLERANCE = Decimal("0.01")
# The NMD core share is green while it sits inside the policy band, amber within
# this many percentage points of either edge, red beyond that.
CORE_AMBER_BAND_PP = Decimal("2")


class MissingParameterError(Exception):
    """A product, branch or segment needs a parameter the input set lacks."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No active FTP parameter covers '{name}'.")
        self.name = name


class FtpComputationError(Exception):
    """The supplied inputs produce a degenerate result (empty book, curve drift)."""


@dataclass(frozen=True)
class CurvePoint:
    """One point on the FTP transfer curve, in percent and basis points."""

    tenor_label: str
    tenor_years: Decimal
    base_yield_pct: Decimal
    liquidity_premium_bps: Decimal
    funding_spread_bps: Decimal
    ftp_rate_pct: Decimal


@dataclass(frozen=True)
class FtpProduct:
    """One product priced against the transfer curve.

    ``ftp_rate_pct`` is the seeded rate carried for verification only; the engine
    re-derives the authoritative rate from the curve by ``tenor_years``.
    """

    product: str
    category: FtpCategory
    balance_ghs: Decimal
    tenor_years: Decimal
    customer_rate_pct: Decimal
    ftp_rate_pct: Decimal
    operating_cost_pct: Decimal
    expected_credit_loss_pct: Decimal
    capital_charge_pct: Decimal


@dataclass(frozen=True)
class FtpBranch:
    branch: str
    deposits_ghs: Decimal
    loans_ghs: Decimal


@dataclass(frozen=True)
class FtpNmd:
    segment: str
    balance_ghs: Decimal
    core_pct: Decimal
    volatile_pct: Decimal
    effective_duration_years: Decimal


@dataclass(frozen=True)
class FtpLineItem:
    section: FtpLineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class CurveResult:
    points: tuple[CurvePoint, ...]
    arithmetic_consistent: bool
    inconsistent_labels: tuple[str, ...]
    line_items: tuple[FtpLineItem, ...]

    def rate_at(self, tenor_years: Decimal) -> Decimal:
        """FTP rate at ``tenor_years`` (endpoint clamp outside, linear inside)."""
        points = self.points
        if not points:
            raise FtpComputationError("The FTP curve has no points to price against.")
        if tenor_years <= points[0].tenor_years:
            return points[0].ftp_rate_pct
        if tenor_years >= points[-1].tenor_years:
            return points[-1].ftp_rate_pct
        for lower, upper in zip(points, points[1:], strict=False):
            if lower.tenor_years <= tenor_years <= upper.tenor_years:
                if tenor_years == lower.tenor_years:
                    return lower.ftp_rate_pct
                if tenor_years == upper.tenor_years:
                    return upper.ftp_rate_pct
                span = upper.tenor_years - lower.tenor_years
                fraction = (tenor_years - lower.tenor_years) / span
                return ratio_pct(
                    lower.ftp_rate_pct + fraction * (upper.ftp_rate_pct - lower.ftp_rate_pct)
                )
        return points[-1].ftp_rate_pct  # pragma: no cover - bracketed above

    @property
    def overnight_rate_pct(self) -> Decimal:
        return self.points[0].ftp_rate_pct


@dataclass(frozen=True)
class ProductProfit:
    product: str
    category: FtpCategory
    balance_ghs: Decimal
    tenor_years: Decimal
    customer_rate_pct: Decimal
    ftp_rate_pct: Decimal
    operating_cost_pct: Decimal
    expected_credit_loss_pct: Decimal
    capital_charge_pct: Decimal
    net_margin_pct: Decimal
    contribution_ghs: Decimal
    below_min_margin: bool


@dataclass(frozen=True)
class ProductResult:
    products: tuple[ProductProfit, ...]
    portfolio_nim_pct: Decimal
    weighted_asset_yield_pct: Decimal
    weighted_funding_credit_pct: Decimal
    total_balance_ghs: Decimal
    total_contribution_ghs: Decimal
    products_below_min_margin: int
    below_min_products: tuple[str, ...]
    min_product_margin_pct: Decimal
    line_items: tuple[FtpLineItem, ...]


@dataclass(frozen=True)
class BranchProfit:
    branch: str
    deposits_ghs: Decimal
    loans_ghs: Decimal
    book_ghs: Decimal
    ftp_adjusted_nim_pct: Decimal
    net_contribution_ghs: Decimal
    rank: int


@dataclass(frozen=True)
class BranchResult:
    branches: tuple[BranchProfit, ...]
    total_contribution_ghs: Decimal
    total_deposits_ghs: Decimal
    total_loans_ghs: Decimal
    line_items: tuple[FtpLineItem, ...]


@dataclass(frozen=True)
class NmdSegment:
    segment: str
    balance_ghs: Decimal
    core_pct: Decimal
    volatile_pct: Decimal
    core_amount_ghs: Decimal
    volatile_amount_ghs: Decimal
    effective_duration_years: Decimal
    core_ftp_pct: Decimal
    volatile_ftp_pct: Decimal
    assigned_ftp_pct: Decimal
    within_policy: bool


@dataclass(frozen=True)
class NmdResult:
    segments: tuple[NmdSegment, ...]
    total_balance_ghs: Decimal
    total_core_ghs: Decimal
    total_volatile_ghs: Decimal
    core_pct: Decimal
    volatile_pct: Decimal
    blended_assigned_ftp_pct: Decimal
    core_min_pct: Decimal
    core_max_pct: Decimal
    within_policy: bool


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def classify_core_band(core_pct: Decimal, min_pct: Decimal, max_pct: Decimal) -> FtpStatus:
    """Classify the already-quantized NMD core share against the policy band."""
    if min_pct <= core_pct <= max_pct:
        return "green"
    if (min_pct - CORE_AMBER_BAND_PP) <= core_pct <= (max_pct + CORE_AMBER_BAND_PP):
        return "amber"
    return "red"


def build_curve(points: Sequence[CurvePoint]) -> CurveResult:
    """Order the transfer curve by tenor and verify each point's arithmetic.

    A point is consistent when ``ftp == base + (liquidity_bps + funding_bps) /
    100`` within ``MONEY`` tolerance; inconsistent labels are reported so the
    caller can raise a validation rather than silently mispricing the book.
    """
    if not points:
        raise FtpComputationError("At least one curve point is required to build the FTP curve.")
    ordered = sorted(points, key=lambda point: point.tenor_years)
    inconsistent: list[str] = []
    line_items: list[FtpLineItem] = []
    for point in ordered:
        expected = (
            point.base_yield_pct
            + (point.liquidity_premium_bps + point.funding_spread_bps) / _HUNDRED
        )
        if money(expected) != money(point.ftp_rate_pct):
            inconsistent.append(point.tenor_label)
        line_items.append(
            FtpLineItem(
                section="ftp_curve",
                line_code=point.tenor_label,
                description=(
                    f"{point.tenor_label} FTP Transfer Rate "
                    f"(Base {_pct_text(point.base_yield_pct)}% + Liquidity "
                    f"{_bps_text(point.liquidity_premium_bps)}bp + Funding "
                    f"{_bps_text(point.funding_spread_bps)}bp)"
                ),
                exposure_amount=None,
                rate_pct=point.base_yield_pct,
                weighted_amount=money(point.ftp_rate_pct),
            )
        )
    return CurveResult(
        points=tuple(ordered),
        arithmetic_consistent=not inconsistent,
        inconsistent_labels=tuple(inconsistent),
        line_items=tuple(line_items),
    )


def shift_curve(curve: CurveResult, shift_pct: Decimal) -> CurveResult:
    """Return the curve with every FTP rate lifted by ``shift_pct`` (a stress overlay).

    The base/liquidity/funding components are carried unchanged; the shift is an
    overlay on the transfer rate that products reprice against under a scenario.
    """
    if shift_pct == _ZERO:
        return curve
    shifted = [
        replace(point, ftp_rate_pct=point.ftp_rate_pct + shift_pct) for point in curve.points
    ]
    return build_curve(shifted)


def validate_product_alignment(products: Sequence[FtpProduct], base_curve: CurveResult) -> None:
    """Raise when a product's seeded FTP rate has drifted from the base curve."""
    for product in products:
        derived = base_curve.rate_at(product.tenor_years)
        if abs(derived - product.ftp_rate_pct) > FTP_ALIGNMENT_TOLERANCE:
            raise FtpComputationError(
                f"Product '{product.product}' seeded FTP rate {product.ftp_rate_pct}% differs from "
                f"the curve rate {derived}% at tenor {product.tenor_years}y."
            )


def product_profitability(
    products: Sequence[FtpProduct], curve: CurveResult, min_product_margin_pct: Decimal
) -> ProductResult:
    """Reprice every product against ``curve`` and summarize the book's margins."""
    if not products:
        raise FtpComputationError("At least one product is required to compute profitability.")
    profits: list[ProductProfit] = []
    line_items: list[FtpLineItem] = []
    below_products: list[str] = []
    asset_bal = _ZERO
    asset_weighted = _ZERO
    liability_bal = _ZERO
    liability_weighted = _ZERO
    for product in sorted(products, key=lambda item: item.product):
        balance = money(product.balance_ghs)
        ftp = curve.rate_at(product.tenor_years)
        if product.category == "asset":
            margin = ratio_pct(
                product.customer_rate_pct
                - ftp
                - product.operating_cost_pct
                - product.expected_credit_loss_pct
                - product.capital_charge_pct
            )
            asset_bal += balance
            asset_weighted += balance * margin
        else:
            margin = ratio_pct(ftp - product.customer_rate_pct - product.operating_cost_pct)
            liability_bal += balance
            liability_weighted += balance * margin
        contribution = money(balance * margin / _HUNDRED)
        below = margin < min_product_margin_pct
        if below:
            below_products.append(product.product)
        profits.append(
            ProductProfit(
                product=product.product,
                category=product.category,
                balance_ghs=balance,
                tenor_years=product.tenor_years,
                customer_rate_pct=product.customer_rate_pct,
                ftp_rate_pct=ftp,
                operating_cost_pct=product.operating_cost_pct,
                expected_credit_loss_pct=product.expected_credit_loss_pct,
                capital_charge_pct=product.capital_charge_pct,
                net_margin_pct=margin,
                contribution_ghs=contribution,
                below_min_margin=below,
            )
        )
        flag = " [below min margin]" if below else ""
        line_items.append(
            FtpLineItem(
                section="ftp_product",
                line_code=product.product,
                description=(
                    f"{_describe(product.product)} ({product.category.title()}, "
                    f"Customer {_pct_text(product.customer_rate_pct)}% vs FTP "
                    f"{_pct_text(ftp)}%, Net Margin {_pct_text(margin)}%){flag}"
                ),
                exposure_amount=balance,
                rate_pct=margin,
                weighted_amount=contribution,
            )
        )
    total_bal = money(asset_bal + liability_bal)
    total_weighted = asset_weighted + liability_weighted
    return ProductResult(
        products=tuple(profits),
        portfolio_nim_pct=_weighted_pct(total_weighted, asset_bal + liability_bal),
        weighted_asset_yield_pct=_weighted_pct(asset_weighted, asset_bal),
        weighted_funding_credit_pct=_weighted_pct(liability_weighted, liability_bal),
        total_balance_ghs=total_bal,
        total_contribution_ghs=money(total_weighted / _HUNDRED),
        products_below_min_margin=len(below_products),
        below_min_products=tuple(below_products),
        min_product_margin_pct=min_product_margin_pct,
        line_items=tuple(line_items),
    )


def branch_profitability(
    branches: Sequence[FtpBranch],
    asset_yield_pct: Decimal,
    funding_credit_pct: Decimal,
) -> BranchResult:
    """Rank branches by net FTP contribution (deposit credit plus asset yield)."""
    if not branches:
        raise FtpComputationError("At least one branch is required to rank the network.")
    scored: list[tuple[FtpBranch, Decimal, Decimal, Decimal]] = []
    for branch in branches:
        deposits = money(branch.deposits_ghs)
        loans = money(branch.loans_ghs)
        contribution = money(
            deposits * funding_credit_pct / _HUNDRED + loans * asset_yield_pct / _HUNDRED
        )
        scored.append((branch, deposits, loans, contribution))
    scored.sort(key=lambda row: (-row[3], row[0].branch))

    profits: list[BranchProfit] = []
    line_items: list[FtpLineItem] = []
    total_contribution = _ZERO
    total_deposits = _ZERO
    total_loans = _ZERO
    for rank, (branch, deposits, loans, contribution) in enumerate(scored, start=1):
        book = money(deposits + loans)
        nim = _weighted_pct(contribution * _HUNDRED, book)
        total_contribution += contribution
        total_deposits += deposits
        total_loans += loans
        profits.append(
            BranchProfit(
                branch=branch.branch,
                deposits_ghs=deposits,
                loans_ghs=loans,
                book_ghs=book,
                ftp_adjusted_nim_pct=nim,
                net_contribution_ghs=contribution,
                rank=rank,
            )
        )
        line_items.append(
            FtpLineItem(
                section="ftp_branch",
                line_code=branch.branch,
                description=(
                    f"{_describe(branch.branch)} Branch "
                    f"(Rank {rank}, FTP-Adjusted NIM {_pct_text(nim)}%)"
                ),
                exposure_amount=book,
                rate_pct=nim,
                weighted_amount=contribution,
            )
        )
    return BranchResult(
        branches=tuple(profits),
        total_contribution_ghs=money(total_contribution),
        total_deposits_ghs=money(total_deposits),
        total_loans_ghs=money(total_loans),
        line_items=tuple(line_items),
    )


def nmd_split(
    nmds: Sequence[FtpNmd],
    curve: CurveResult,
    core_min_pct: Decimal,
    core_max_pct: Decimal,
) -> NmdResult:
    """Split each NMD segment into core/volatile and assign matched FTP rates."""
    if not nmds:
        raise FtpComputationError("At least one NMD segment is required to compute the split.")
    overnight = curve.overnight_rate_pct
    segments: list[NmdSegment] = []
    total_balance = _ZERO
    total_core = _ZERO
    total_volatile = _ZERO
    blended_weighted = _ZERO
    for nmd in sorted(nmds, key=lambda item: item.segment):
        balance = money(nmd.balance_ghs)
        core_amount = money(balance * nmd.core_pct / _HUNDRED)
        volatile_amount = money(balance - core_amount)
        core_ftp = curve.rate_at(nmd.effective_duration_years)
        assigned = ratio_pct((nmd.core_pct * core_ftp + nmd.volatile_pct * overnight) / _HUNDRED)
        within = core_min_pct <= nmd.core_pct <= core_max_pct
        total_balance += balance
        total_core += core_amount
        total_volatile += volatile_amount
        blended_weighted += balance * assigned
        segments.append(
            NmdSegment(
                segment=nmd.segment,
                balance_ghs=balance,
                core_pct=ratio_pct(nmd.core_pct),
                volatile_pct=ratio_pct(nmd.volatile_pct),
                core_amount_ghs=core_amount,
                volatile_amount_ghs=volatile_amount,
                effective_duration_years=nmd.effective_duration_years,
                core_ftp_pct=core_ftp,
                volatile_ftp_pct=overnight,
                assigned_ftp_pct=assigned,
                within_policy=within,
            )
        )
    core_pct = _weighted_pct(total_core * _HUNDRED, total_balance)
    volatile_pct = _weighted_pct(total_volatile * _HUNDRED, total_balance)
    return NmdResult(
        segments=tuple(segments),
        total_balance_ghs=money(total_balance),
        total_core_ghs=money(total_core),
        total_volatile_ghs=money(total_volatile),
        core_pct=core_pct,
        volatile_pct=volatile_pct,
        blended_assigned_ftp_pct=_weighted_pct(blended_weighted, total_balance),
        core_min_pct=core_min_pct,
        core_max_pct=core_max_pct,
        within_policy=core_min_pct <= core_pct <= core_max_pct,
    )


def curve_within_premium_limits(
    curve: CurveResult, liquidity_premium_max_bps: Decimal, funding_spread_max_bps: Decimal
) -> bool:
    """True when every curve point respects the liquidity/funding premium caps."""
    return all(
        point.liquidity_premium_bps <= liquidity_premium_max_bps
        and point.funding_spread_bps <= funding_spread_max_bps
        for point in curve.points
    )


def _weighted_pct(weighted: Decimal, base: Decimal) -> Decimal:
    if base <= _ZERO:
        return _ZERO
    return ratio_pct(weighted / base)


def _pct_text(value: Decimal) -> str:
    if value == _ZERO:
        return "0"
    return format(value.normalize(), "f")


def _bps_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != _ZERO else "0"


def _describe(value: str) -> str:
    return value.replace("_", " ").title()
