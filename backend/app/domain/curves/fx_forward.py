"""FX-forward (outright) curve by covered interest parity (spec §7 FC-6, §1.2 item 10).

The last piece of the forward-curve breadth story: given two single-currency
discount curves and an FX spot, an **outright forward FX curve** follows with no
new optimisation — it is a pure algebraic composition of discount factors under
**covered interest parity (CIP)**.

CIP relation (the identity this module implements)::

    F(t) = S * DF_base(t) / DF_quote(t)

with the spot ``S`` quoted as *quote-currency units per one base-currency unit*
(e.g. for ``USDGHS`` the base is USD, the quote is GHS, and ``S`` is GHS per
USD), ``DF_base``/``DF_quote`` the two currencies' discount factors to date ``t``.
The derivation is the standard no-arbitrage round trip: one unit of quote
currency invested to ``t`` (growing to ``1/DF_quote``) must equal the same unit
converted to base at spot, invested (``1/DF_base``) and converted back at the
forward — solving for ``F`` gives the relation above. A consequence worth
stating: the higher-yielding currency trades at a forward **discount** (if
``r_quote > r_base`` then ``DF_quote < DF_base`` so ``F > S`` — more quote units
per base in the future, i.e. the quote currency depreciates forward).

The module emits an outright grid (``date -> forward FX rate``) plus the implied
**forward points** ``F(t) - S`` at each date.

**Cross-currency basis — the honest caveat.** A true outright forward also embeds
a cross-currency *basis* — the empirically observed, persistent deviation from
textbook CIP that only a cross-currency **basis swap** market prices. We expose
the basis as an OPTIONAL additive spread (``basis_bps``, a governed methodology
parameter, default ``0.0``) applied continuously to the **base-currency leg's**
discount, ``DF_base_adj(t) = DF_base(t) * exp(-(basis_bps/1e4) * tau(t))`` — the
seam is here and it round-trips, but with ``basis_bps=0`` this is *textbook* CIP.
Calibrating a real basis needs basis-swap quotes (FX forwards / xccy basis swaps)
that are not in this platform's data scope today; do not read a non-zero default
as a calibrated number. (Same discipline the desk applied to the AGD synthetic
discounting level: state exactly what is and is not calibrated.)

Pure like the rest of ``app.domain.curves``: float64, no I/O, no randomness; it
consumes anything satisfying the structural :class:`~app.domain.curves.instruments.DiscountCurve`
protocol, so it composes directly with a solved :class:`~app.domain.curves.multicurve.SolvedCurve`
or any other discount curve. The result is an immutable value object carrying a
value-based canonical ``input_digest`` (same idiom as ``objects.CurveBuildResult``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.domain.curves.conventions import DayCount, year_fraction
from app.domain.curves.instruments import DiscountCurve
from app.domain.curves.objects import canonical_input_digest

__all__ = [
    "FxForwardCurve",
    "FxForwardError",
    "FxForwardPoint",
    "build_fx_forward_curve",
]

# Basis is quoted in basis points; one basis point is 1/10_000 in decimal.
_BPS = 1.0e4


class FxForwardError(ValueError):
    """An FX-forward construction request violates its contract.

    Bad spot, mismatched valuation dates on the two legs, non-monotone or
    pre-spot dates, or a non-positive discount factor from either curve.
    """


@dataclass(frozen=True)
class FxForwardPoint:
    """One outright-forward node: the CIP forward and its forward points at ``date``.

    ``df_base``/``df_quote`` are the raw (pre-basis) discount factors sampled from
    the two legs; ``df_base_adjusted`` carries the ``basis_bps`` overlay (equal to
    ``df_base`` when the basis is zero). ``forward_rate`` is the CIP forward in
    quote-per-base units and ``forward_points`` is ``forward_rate - spot``.
    """

    date: date
    year_fraction: float
    df_base: float
    df_quote: float
    df_base_adjusted: float
    forward_rate: float
    forward_points: float


@dataclass(frozen=True)
class FxForwardCurve:
    """An immutable outright FX-forward curve built by covered interest parity.

    Equality is value equality across every field, so a build is reproducible: the
    same spot, legs (sampled to identical discount factors), dates and basis
    produce an identical curve and an identical ``input_digest``. Nothing volatile
    ever enters the digest.
    """

    as_of: date
    base_ccy: str
    quote_ccy: str
    spot: float
    basis_bps: float
    day_count: DayCount
    points: tuple[FxForwardPoint, ...]
    input_digest: str = field(default="")

    @classmethod
    def create(  # noqa: PLR0913 - the curve's irreducible identity + input surface
        cls,
        *,
        as_of: date,
        base_ccy: str,
        quote_ccy: str,
        spot: float,
        basis_bps: float,
        day_count: DayCount,
        points: Sequence[FxForwardPoint],
    ) -> FxForwardCurve:
        """Assemble the curve and digest its raw inputs (spot, basis, sampled DFs)."""
        payload: dict[str, object] = {
            "as_of": as_of.isoformat(),
            "base_ccy": base_ccy,
            "quote_ccy": quote_ccy,
            "spot": spot,
            "basis_bps": basis_bps,
            "day_count": day_count.value,
            # The value-based inputs to CIP are the RAW leg discount factors at each
            # date — digesting those (not the derived forwards) answers "what went
            # in", exactly like objects.canonical_input_digest over raw quotes.
            "samples": [
                {
                    "date": point.date.isoformat(),
                    "df_base": point.df_base,
                    "df_quote": point.df_quote,
                }
                for point in points
            ],
        }
        return cls(
            as_of=as_of,
            base_ccy=base_ccy,
            quote_ccy=quote_ccy,
            spot=spot,
            basis_bps=basis_bps,
            day_count=day_count,
            points=tuple(points),
            input_digest=canonical_input_digest(payload),
        )


def build_fx_forward_curve(  # noqa: PLR0913 - spot + two legs + grid + governed basis/labels
    spot: float,
    base_curve: DiscountCurve,
    quote_curve: DiscountCurve,
    dates: Sequence[date],
    *,
    basis_bps: float = 0.0,
    day_count: DayCount = DayCount.ACT_365F,
    base_ccy: str = "",
    quote_ccy: str = "",
) -> FxForwardCurve:
    """Construct the outright FX-forward curve by covered interest parity.

    ``spot`` is quote-per-base (e.g. GHS per USD for ``USDGHS``). ``base_curve`` and
    ``quote_curve`` are the two currencies' discount curves and must share a
    valuation date — the FX ``as_of``. For each date ``t`` in ``dates`` the forward
    is ``F(t) = spot * DF_base_adj(t) / DF_quote(t)`` where ``DF_base_adj`` folds in
    the optional cross-currency ``basis_bps`` on the base leg
    (``DF_base * exp(-(basis_bps/1e4) * tau)``, ``tau`` the ``day_count`` year
    fraction from ``as_of`` to ``t``). With ``basis_bps=0`` this is textbook CIP.

    ``dates`` must be strictly increasing and on or after the shared valuation date;
    a date equal to the valuation date is the spot node (``tau=0``, ``DF=1``,
    ``forward=spot``, ``points=0``). Raises :class:`FxForwardError` on any contract
    violation.
    """
    if not math.isfinite(spot) or spot <= 0.0:
        raise FxForwardError("FX spot must be a positive, finite number (quote per base).")
    if not math.isfinite(basis_bps):
        raise FxForwardError("basis_bps must be a finite number of basis points.")
    if base_curve.valuation_date != quote_curve.valuation_date:
        raise FxForwardError(
            "The base and quote discount curves must share a valuation date "
            f"(base {base_curve.valuation_date}, quote {quote_curve.valuation_date})."
        )
    if not dates:
        raise FxForwardError("At least one forward date is required.")

    as_of = base_curve.valuation_date
    ordered = tuple(dates)
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right <= left:
            raise FxForwardError("Forward dates must be strictly increasing.")
    if ordered[0] < as_of:
        raise FxForwardError(
            f"Forward date {ordered[0].isoformat()} precedes the valuation date "
            f"{as_of.isoformat()}."
        )

    basis_decimal = basis_bps / _BPS
    points: list[FxForwardPoint] = []
    for day in ordered:
        tau = year_fraction(as_of, day, day_count)
        df_base = base_curve.discount(day)
        df_quote = quote_curve.discount(day)
        if df_base <= 0.0 or df_quote <= 0.0:
            raise FxForwardError(
                f"Both legs must yield a strictly positive discount factor at "
                f"{day.isoformat()} (base {df_base}, quote {df_quote})."
            )
        df_base_adjusted = df_base * math.exp(-basis_decimal * tau)
        forward_rate = spot * df_base_adjusted / df_quote
        points.append(
            FxForwardPoint(
                date=day,
                year_fraction=tau,
                df_base=df_base,
                df_quote=df_quote,
                df_base_adjusted=df_base_adjusted,
                forward_rate=forward_rate,
                forward_points=forward_rate - spot,
            )
        )

    return FxForwardCurve.create(
        as_of=as_of,
        base_ccy=base_ccy,
        quote_ccy=quote_ccy,
        spot=spot,
        basis_bps=basis_bps,
        day_count=day_count,
        points=points,
    )
