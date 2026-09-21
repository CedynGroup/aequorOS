"""Band tables: a concentration metric mapped to an add-on (D-024).

The table itself is a governed parameter — a ``value_json`` row staff propose
and approve in the console — so this module holds the GRAMMAR and none of the
numbers. Changing where a band starts is an operator action with a maker and a
checker, not a release.

The grammar is deliberately strict, because a band table that is merely
plausible is worse than one that is refused: a gap silently returns the wrong
add-on, an overlap makes the answer depend on iteration order, and a
non-monotone add-on means a more concentrated book can be charged less. Each is
a typed :class:`BandTableError` at parse time, so the refusal happens when the
row is approved rather than when a figure is filed.

Boundaries: ``lower`` inclusive, ``upper`` exclusive, the last band unbounded.
``step`` charges the band's add-on flat; ``linear`` interpolates to the next
band's add-on across the band, and the last band stays flat.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.domain.icaap.units import ZERO, Basis

BandMode = Literal["step", "linear"]
BAND_MODES: frozenset[str] = frozenset({"step", "linear"})

BandTableCode = Literal[
    "band_table_malformed",
    "bands_empty",
    "bands_not_starting_at_zero",
    "bands_not_contiguous",
    "band_not_ascending",
    "last_band_not_unbounded",
    "unbounded_band_not_last",
    "addon_decreasing",
    "addon_negative",
    "mode_unknown",
    "basis_unknown",
    "metric_out_of_range",
]


class BandTableError(ValueError):
    """A band table that cannot be applied honestly."""

    def __init__(
        self,
        code: BandTableCode,
        *,
        param_code: str | None = None,
        index: int | None = None,
    ):
        self.code: BandTableCode = code
        self.param_code = param_code
        self.index = index
        super().__init__(f"{code}:{param_code}" if param_code else code)


@dataclass(frozen=True)
class Band:
    """``[lower, upper)`` maps to ``addon``, quoted on the table's basis."""

    lower: Decimal
    upper: Decimal | None
    addon: Decimal


@dataclass(frozen=True)
class BandTable:
    """One governed metric → add-on mapping."""

    param_code: str
    basis: Basis
    mode: BandMode
    bands: tuple[Band, ...]
    #: Sector tables name the taxonomy their buckets were drawn from: an HHI
    #: over ten sectors and one over forty are not the same measurement.
    taxonomy: str | None = None

    @property
    def top_addon(self) -> Decimal:
        """The most severe add-on — what an uncoverable book is charged."""
        return self.bands[-1].addon


def _decimal(value: Any, param_code: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise BandTableError("band_table_malformed", param_code=param_code)
    try:
        return Decimal(str(value))
    except InvalidOperation as error:  # pragma: no cover - defensive
        raise BandTableError("band_table_malformed", param_code=param_code) from error


def parse_band_table(payload: Mapping[str, Any] | None, *, param_code: str) -> BandTable:
    """Read a governed ``value_json`` body into a checked :class:`BandTable`."""
    if not isinstance(payload, Mapping):
        raise BandTableError("band_table_malformed", param_code=param_code)

    mode = payload.get("mode")
    if mode not in BAND_MODES:
        raise BandTableError("mode_unknown", param_code=param_code)

    raw_basis = payload.get("basis")
    try:
        basis = Basis(raw_basis)
    except ValueError as error:
        raise BandTableError("basis_unknown", param_code=param_code) from error

    raw_bands = payload.get("bands")
    if not isinstance(raw_bands, Sequence) or isinstance(raw_bands, str) or not raw_bands:
        raise BandTableError("bands_empty", param_code=param_code)

    bands: list[Band] = []
    for index, raw in enumerate(raw_bands):
        if not isinstance(raw, Mapping) or "lower" not in raw or "addon" not in raw:
            raise BandTableError("band_table_malformed", param_code=param_code, index=index)
        upper_raw = raw.get("upper")
        bands.append(
            Band(
                lower=_decimal(raw["lower"], param_code),
                upper=None if upper_raw is None else _decimal(upper_raw, param_code),
                addon=_decimal(raw["addon"], param_code),
            )
        )

    _validate(bands, param_code)
    taxonomy = payload.get("taxonomy")
    return BandTable(
        param_code=param_code,
        basis=basis,
        mode=mode,
        bands=tuple(bands),
        taxonomy=taxonomy if isinstance(taxonomy, str) else None,
    )


def _validate(bands: Sequence[Band], param_code: str) -> None:
    if bands[0].lower != ZERO:
        raise BandTableError("bands_not_starting_at_zero", param_code=param_code, index=0)
    if bands[-1].upper is not None:
        raise BandTableError("last_band_not_unbounded", param_code=param_code)
    previous: Band | None = None
    for index, band in enumerate(bands):
        if band.addon < ZERO:
            raise BandTableError("addon_negative", param_code=param_code, index=index)
        if band.upper is None and index != len(bands) - 1:
            raise BandTableError("unbounded_band_not_last", param_code=param_code, index=index)
        if band.upper is not None and band.upper <= band.lower:
            raise BandTableError("band_not_ascending", param_code=param_code, index=index)
        if previous is not None:
            if previous.upper != band.lower:
                raise BandTableError("bands_not_contiguous", param_code=param_code, index=index)
            if band.addon < previous.addon:
                raise BandTableError("addon_decreasing", param_code=param_code, index=index)
        previous = band


def band_for(table: BandTable, metric_value: Decimal) -> tuple[int, Band]:
    """The band ``metric_value`` falls in: lower inclusive, upper exclusive."""
    if metric_value < table.bands[0].lower:
        raise BandTableError("metric_out_of_range", param_code=table.param_code)
    for index, band in enumerate(table.bands):
        if band.upper is None or metric_value < band.upper:
            return index, band
    raise BandTableError("metric_out_of_range", param_code=table.param_code)  # pragma: no cover


def lookup(table: BandTable, metric_value: Decimal) -> Decimal:
    """The add-on ``metric_value`` earns, on the table's own basis."""
    index, band = band_for(table, metric_value)
    if table.mode == "step" or band.upper is None:
        return band.addon
    following = table.bands[index + 1]
    span = band.upper - band.lower
    return band.addon + (following.addon - band.addon) * (metric_value - band.lower) / span


__all__ = [
    "BAND_MODES",
    "Band",
    "BandMode",
    "BandTable",
    "BandTableCode",
    "BandTableError",
    "band_for",
    "lookup",
    "parse_band_table",
]
