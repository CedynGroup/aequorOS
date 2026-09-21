"""Concentration metrics, on one scale, with one definition each (D-017).

The platform already had a concentration monitor whose index runs 0–10,000 for
display. A Pillar 2 band table cannot be read against a display scale, so the
canonical scale here is **0–1** and the conversion is the reader's, not the
metric's.

Three metrics, because no one of them is honest alone:

* **HHI** is share-of-share-squared. It sees both size and count, and its floor
  is ``1/N`` — a perfectly spread book of four names scores 0.25, not zero.
* **Gini** is inequality only. Two equal names score 0, however large they are,
  which is why a single exposure is ``not computable`` rather than 0: reading
  "0" as "diversified" is exactly the mistake. Never quote it alone.
* **CRn** is the top-n share, the measure a supervisor states in words.

Every function is order-independent, scale-invariant, exact in ``Decimal``
(one division at the end, so a uniform book normalises to exactly zero), and
ignores non-positive exposures — N is counted after that exclusion.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.domain.icaap.units import ZERO, ratio

#: The 2 in the Gini rank formula. Not a regulatory value: it is the constant
#: of the definition G = 2·Σ i·x₍ᵢ₎ / (N·X) − (N+1)/N.
TWO = Decimal("2")
#: Gini measures inequality BETWEEN exposures, so it needs a pair. Structural.
PAIR = 2


def positive_exposures(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """The exposures a metric is computed over: strictly positive ones."""
    return tuple(value for value in values if value > ZERO)


def hhi(values: Sequence[Decimal]) -> Decimal | None:
    """Σ sᵢ² on the canonical 0–1 scale. ``None`` for an empty book."""
    exposures = positive_exposures(values)
    if not exposures:
        return None
    total = sum(exposures, ZERO)
    squares = sum((value * value for value in exposures), ZERO)
    return ratio(squares / (total * total))


def hhi_normalised(values: Sequence[Decimal]) -> Decimal | None:
    """(HHI − 1/N) / (1 − 1/N): zero for a uniform book. ``None`` when N < 2."""
    exposures = positive_exposures(values)
    count = len(exposures)
    if count < PAIR:
        return None
    total = sum(exposures, ZERO)
    squares = sum((value * value for value in exposures), ZERO)
    numerator = Decimal(count) * squares - total * total
    return ratio(numerator / (total * total * Decimal(count - 1)))


def gini(values: Sequence[Decimal]) -> Decimal | None:
    """Exposure inequality in [0, (N−1)/N]. ``None`` when N < 2.

    A single exposure has no inequality to measure, and reporting 0 would read
    as a diversified book. The caller states "not computable" instead.
    """
    exposures = positive_exposures(values)
    count = len(exposures)
    if count < PAIR:
        return None
    ascending = sorted(exposures)
    total = sum(ascending, ZERO)
    weighted = sum(
        (Decimal(rank) * value for rank, value in enumerate(ascending, start=1)),
        ZERO,
    )
    numerator = TWO * weighted - Decimal(count + 1) * total
    return ratio(numerator / (Decimal(count) * total))


def gini_normalised(values: Sequence[Decimal]) -> Decimal | None:
    """G · N/(N−1), so a maximally concentrated book reaches 1."""
    exposures = positive_exposures(values)
    count = len(exposures)
    if count < PAIR:
        return None
    ascending = sorted(exposures)
    total = sum(ascending, ZERO)
    weighted = sum(
        (Decimal(rank) * value for rank, value in enumerate(ascending, start=1)),
        ZERO,
    )
    numerator = TWO * weighted - Decimal(count + 1) * total
    return ratio(numerator / (total * Decimal(count - 1)))


def concentration_ratio(values: Sequence[Decimal], n: int) -> Decimal | None:
    """The share of the largest ``n`` exposures. 1 when N ≤ n."""
    if n < 1:
        raise ValueError("concentration_ratio_n_not_positive")
    exposures = positive_exposures(values)
    if not exposures:
        return None
    total = sum(exposures, ZERO)
    top = sum(sorted(exposures, reverse=True)[:n], ZERO)
    return ratio(top / total)


def concentration_ratio_on_capital(
    values: Sequence[Decimal], n: int, capital: Decimal | None
) -> Decimal | None:
    """The largest ``n`` exposures against a declared capital base.

    The denominator is the caller's choice and must be stated with the figure
    (net own funds, or Tier 1): the same book reads differently against each.
    """
    if n < 1:
        raise ValueError("concentration_ratio_n_not_positive")
    exposures = positive_exposures(values)
    if not exposures or capital is None or capital <= ZERO:
        return None
    top = sum(sorted(exposures, reverse=True)[:n], ZERO)
    return ratio(top / capital)


__all__ = [
    "PAIR",
    "TWO",
    "concentration_ratio",
    "concentration_ratio_on_capital",
    "gini",
    "gini_normalised",
    "hhi",
    "hhi_normalised",
    "positive_exposures",
]
