"""The analytical days-past-due bands (pure).

These are portfolio-at-risk / roll-rate bands for management views, NOT
regulatory classification boundaries — those are always resolved from the
control plane (``app.domain.capital.loan_classification``). Until P0-8 the same
seven bands were spelled out three times: the classification service's
``_DPD_BANDS`` (with labels), ``credit.migration.ROLL_BUCKETS`` (codes only) and
``regulatory_credit._ROLL_BAND_EDGES`` (edges only). This module is the one
definition; each of those names now reads from it.
"""

from __future__ import annotations

from typing import NamedTuple


class DpdBand(NamedTuple):
    """One band: a wire code, a display label and an inclusive day range."""

    code: str
    label: str
    minimum: int
    #: ``None`` for the open-ended last band.
    maximum: int | None


DPD_BANDS: tuple[DpdBand, ...] = (
    DpdBand("current", "Current", 0, 0),
    DpdBand("1_29", "1–29 days", 1, 29),
    DpdBand("30_59", "30–59 days", 30, 59),
    DpdBand("60_89", "60–89 days", 60, 89),
    DpdBand("90_179", "90–179 days", 90, 179),
    DpdBand("180_359", "180–359 days", 180, 359),
    DpdBand("360_plus", "360+ days", 360, None),
)

#: The band codes in band order — the roll-rate matrix axis.
DPD_BAND_CODES: tuple[str, ...] = tuple(band.code for band in DPD_BANDS)


def dpd_band(days_past_due: int | None) -> str | None:
    """The band code for a days-past-due count, or ``None`` when it states none.

    A negative count matches no band and is also ``None``.
    """
    if days_past_due is None:
        return None
    for code, _label, low, high in DPD_BANDS:
        if days_past_due >= low and (high is None or days_past_due <= high):
            return code
    return None
