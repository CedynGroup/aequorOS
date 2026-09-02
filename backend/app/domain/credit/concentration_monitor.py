"""Standing credit-concentration monitor (pure; credit PR-3).

The BoG Guidelines on Management and Measurement of Credit Concentration Risk
(Sept 2025; banks, savings & loans and finance houses) require concentration
to be MEASURED per dimension — single name at the obligor/connection level,
sector, geography, product/collateral, and any dimension material to the
business model — and compared against a Board limit structure with breach
escalation (§§C–D, F). Payroll/check-off lending makes EMPLOYER a first-class
dimension for a savings & loans: one employer failing to remit turns many
individually-performing loans delinquent at once, which no sector or
single-name view can see (§17(c): counterparties dependent on the same
activity, customer or product).

This engine consumes the SAME ``ConcentrationExposure`` rows the stress
engine reads and computes, per dimension: the full bucket distribution, HHI
(0–10,000 basis), top-N shares of book and of the capital base, coverage
(how much of the book states the dimension at all), and Board-limit
utilisation. It differs from ``app.domain.stress.concentration`` on purpose:
that engine prices tail LOSSES for the stress overlay (Pillar-2 charge);
this one is the measurement-and-limits MIS the Guidelines' §F reporting asks
for. Limits arrive as data (the Board register) — the Guidelines prescribe no
numeric limit, so an absent limit yields ``limit=None`` and status
``not_set``, never an invented threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.stress.concentration import (
    DIM_COLLATERAL,
    DIM_EMPLOYER,
    DIM_GEOGRAPHY,
    DIM_PRODUCT,
    DIM_SECTOR,
    DIM_SINGLE_NAME,
    ConcentrationExposure,
)

#: The dimensions the monitor measures, in display order.
MONITOR_DIMENSIONS: tuple[str, ...] = (
    DIM_SINGLE_NAME,
    DIM_SECTOR,
    DIM_GEOGRAPHY,
    DIM_PRODUCT,
    DIM_COLLATERAL,
    DIM_EMPLOYER,
)

#: HHI is conventionally quoted on the 0–10,000 basis (shares in percent,
#: squared and summed).
_HHI_SCALE = Decimal("10000")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_HHI_Q = Decimal("1")
_PCT_Q = Decimal("0.000001")

LIMIT_SHARE_OF_BOOK = "share_of_book_pct"
LIMIT_SHARE_OF_CAPITAL = "share_of_capital_pct"
LIMIT_HHI = "hhi"

STATUS_WITHIN = "within_limit"
STATUS_ABOVE = "above_limit"
STATUS_NOT_SET = "not_set"
STATUS_NOT_COMPUTABLE = "not_computable"


@dataclass(frozen=True)
class ConcentrationLimit:
    """One Board-approved limit row (from the tenant register)."""

    dimension: str
    limit_kind: str  # share_of_book_pct | share_of_capital_pct | hhi
    value: Decimal
    #: A named bucket (one employer, one sector) or None = the dimension's
    #: largest bucket.
    bucket_key: str | None = None


@dataclass(frozen=True)
class BucketReading:
    """One bucket of one dimension, with its limit assessment."""

    key: str
    exposure_ghs: Decimal
    loan_count: int
    share_of_book_pct: Decimal
    #: None when no capital base was supplied — "not computable", never 0.
    share_of_capital_pct: Decimal | None
    limit_value: Decimal | None
    limit_kind: str | None
    #: within_limit | above_limit | not_set | not_computable
    limit_status: str
    #: utilisation = measured value ÷ limit × 100; None when not assessable.
    utilization_pct: Decimal | None


@dataclass(frozen=True)
class DimensionReading:
    dimension: str
    hhi: Decimal
    bucket_count: int
    #: Share of the TOTAL book that states this dimension at all.
    coverage_pct: Decimal
    stated_exposure_ghs: Decimal
    buckets: tuple[BucketReading, ...]
    #: The dimension-level HHI limit assessment, when such a limit exists.
    hhi_limit: Decimal | None
    hhi_status: str


@dataclass(frozen=True)
class ConcentrationMonitorResult:
    total_book_ghs: Decimal
    capital_base_ghs: Decimal | None
    dimensions: tuple[DimensionReading, ...]
    #: Every bucket currently above a Board limit, across dimensions.
    breaches: tuple[BucketReading, ...]

    def dimension(self, name: str) -> DimensionReading | None:
        return next((d for d in self.dimensions if d.dimension == name), None)


_DIMENSION_ATTR: dict[str, str] = {
    DIM_SINGLE_NAME: "group_key",
    DIM_SECTOR: "sector",
    DIM_GEOGRAPHY: "geography",
    DIM_PRODUCT: "product",
    DIM_COLLATERAL: "collateral_type",
    DIM_EMPLOYER: "employer",
}


def _dimension_key(exposure: ConcentrationExposure, dimension: str) -> str | None:
    attr = _DIMENSION_ATTR.get(dimension)
    return getattr(exposure, attr) if attr is not None else None


def _limit_for(
    limits: tuple[ConcentrationLimit, ...],
    dimension: str,
    kind: str,
    bucket_key: str | None,
) -> Decimal | None:
    named = next(
        (
            limit.value
            for limit in limits
            if limit.dimension == dimension
            and limit.limit_kind == kind
            and limit.bucket_key == bucket_key
        ),
        None,
    )
    if named is not None or bucket_key is None:
        return named
    # A bucket without its own row falls back to the dimension-wide limit.
    return _limit_for(limits, dimension, kind, None)


def _assess_bucket(  # noqa: PLR0913 - one argument per assessment input
    key: str,
    exposure: Decimal,
    count: int,
    total_book: Decimal,
    capital_base: Decimal | None,
    dimension: str,
    limits: tuple[ConcentrationLimit, ...],
) -> BucketReading:
    share_book = (
        (exposure / total_book * _HUNDRED).quantize(_PCT_Q) if total_book > _ZERO else _ZERO
    )
    share_capital = (
        (exposure / capital_base * _HUNDRED).quantize(_PCT_Q)
        if capital_base is not None and capital_base > _ZERO
        else None
    )
    # Capital-basis limits outrank book-basis ones when both exist: the
    # Guidelines' §30 names capital first as the reference for limits.
    capital_limit = _limit_for(limits, dimension, LIMIT_SHARE_OF_CAPITAL, key)
    book_limit = _limit_for(limits, dimension, LIMIT_SHARE_OF_BOOK, key)
    if capital_limit is not None:
        if share_capital is None:
            status, utilization = STATUS_NOT_COMPUTABLE, None
        else:
            status = STATUS_ABOVE if share_capital > capital_limit else STATUS_WITHIN
            utilization = (share_capital / capital_limit * _HUNDRED).quantize(_PCT_Q)
        return BucketReading(
            key=key,
            exposure_ghs=exposure,
            loan_count=count,
            share_of_book_pct=share_book,
            share_of_capital_pct=share_capital,
            limit_value=capital_limit,
            limit_kind=LIMIT_SHARE_OF_CAPITAL,
            limit_status=status,
            utilization_pct=utilization,
        )
    if book_limit is not None:
        status = STATUS_ABOVE if share_book > book_limit else STATUS_WITHIN
        utilization = (
            (share_book / book_limit * _HUNDRED).quantize(_PCT_Q) if book_limit > _ZERO else None
        )
        return BucketReading(
            key=key,
            exposure_ghs=exposure,
            loan_count=count,
            share_of_book_pct=share_book,
            share_of_capital_pct=share_capital,
            limit_value=book_limit,
            limit_kind=LIMIT_SHARE_OF_BOOK,
            limit_status=status,
            utilization_pct=utilization,
        )
    return BucketReading(
        key=key,
        exposure_ghs=exposure,
        loan_count=count,
        share_of_book_pct=share_book,
        share_of_capital_pct=share_capital,
        limit_value=None,
        limit_kind=None,
        limit_status=STATUS_NOT_SET,
        utilization_pct=None,
    )


def monitor_concentration(
    exposures: tuple[ConcentrationExposure, ...] | list[ConcentrationExposure],
    limits: tuple[ConcentrationLimit, ...] = (),
    *,
    capital_base_ghs: Decimal | None = None,
    top_n: int = 20,
) -> ConcentrationMonitorResult:
    """Measure every dimension and assess it against the Board limits.

    Exposures with a non-positive EAD are ignored. A bucket key of ``None``
    (the source did not state the dimension) is EXCLUDED from that dimension —
    coverage discloses how much of the book is excluded; there is never an
    "Unknown" bucket, because grouping unstated rows would fabricate a
    concentration that may not exist.
    """
    active = [e for e in exposures if e.ead > _ZERO]
    total_book = sum((e.ead for e in active), _ZERO)
    dimensions: list[DimensionReading] = []
    breaches: list[BucketReading] = []
    for dimension in MONITOR_DIMENSIONS:
        buckets: dict[str, tuple[Decimal, int]] = {}
        stated = _ZERO
        for exposure in active:
            key = _dimension_key(exposure, dimension)
            if key is None or key == "":
                continue
            stated += exposure.ead
            amount, count = buckets.get(key, (_ZERO, 0))
            buckets[key] = (amount + exposure.ead, count + 1)
        hhi = _ZERO
        if stated > _ZERO:
            for amount, _count in buckets.values():
                share = amount / stated
                hhi += share * share
        hhi_scaled = (hhi * _HHI_SCALE).quantize(_HHI_Q)
        coverage = (
            (stated / total_book * _HUNDRED).quantize(_PCT_Q) if total_book > _ZERO else _ZERO
        )
        ordered = sorted(buckets.items(), key=lambda item: item[1][0], reverse=True)[:top_n]
        readings = tuple(
            _assess_bucket(
                key, amount, count, total_book, capital_base_ghs, dimension, tuple(limits)
            )
            for key, (amount, count) in ordered
        )
        hhi_limit = _limit_for(tuple(limits), dimension, LIMIT_HHI, None)
        if hhi_limit is None:
            hhi_status = STATUS_NOT_SET
        else:
            hhi_status = STATUS_ABOVE if hhi_scaled > hhi_limit else STATUS_WITHIN
        dimensions.append(
            DimensionReading(
                dimension=dimension,
                hhi=hhi_scaled,
                bucket_count=len(buckets),
                coverage_pct=coverage,
                stated_exposure_ghs=stated,
                buckets=readings,
                hhi_limit=hhi_limit,
                hhi_status=hhi_status,
            )
        )
        breaches.extend(r for r in readings if r.limit_status == STATUS_ABOVE)
    return ConcentrationMonitorResult(
        total_book_ghs=total_book,
        capital_base_ghs=capital_base_ghs,
        dimensions=tuple(dimensions),
        breaches=tuple(breaches),
    )
