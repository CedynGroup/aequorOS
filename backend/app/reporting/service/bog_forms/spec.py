"""Form / sheet / line specifications for the official BoG returns.

A :class:`FormSpec` is the registry-facing description of one return: which
official workbook it is, its sheets, unit convention, frequency, basis and
dependencies (all from the Guide). A :class:`LineSpec` binds ONE official input
cell (or a row of Domestic/Foreign/Total cells) to a named source resolver; a
cell with no line spec is emitted blank with status ``unmapped`` so the official
structure is never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

type Basis = Literal["solo", "consolidated"]
type Frequency = Literal["weekly", "monthly", "quarterly", "semiannual", "annual"]
type LineStatus = Literal["mapped", "input_required", "unmapped", "derived"]

#: The Guide's stated unit for a sheet: cell values are stored in the platform's
#: base unit (cedis) and scaled on export.
type UnitConvention = Literal["millions", "thousands", "units", "percent", "count", "text"]

UNIT_DIVISOR: dict[UnitConvention, int] = {
    "millions": 1_000_000,
    "thousands": 1_000,
    "units": 1,
    "percent": 1,
    "count": 1,
    "text": 1,
}


@dataclass(frozen=True)
class HeaderCells:
    """Where the official sheet expects its header values."""

    bank_name: str | None = None
    period: str | None = None
    reporting_date: str | None = None
    #: extra fixed strings to write, e.g. {"A2": "BALANCE SHEET STATEMENT OF BANKS"}
    extras: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LineSpec:
    """One official input cell (or a Domestic/Foreign/Total row) → a source.

    ``cells`` maps a column key to the official cell reference on the sheet,
    e.g. ``{"domestic": "B7", "foreign": "C7", "total": "D7"}`` — the total is
    usually a template FORMULA (``=B7+C7``) and therefore never listed here; only
    genuine INPUT cells belong in ``cells``.

    ``source`` is a resolver name registered in :mod:`sources` (with optional
    ``params``); ``None`` marks the line ``input_required`` — the bank must supply
    the figure (or a bank-specific mapping table) and the cell exports blank.
    """

    code: str
    label: str
    cells: dict[str, str]
    source: str | None = None
    params: dict[str, object] = field(default_factory=dict)
    notes: str = ""
    #: When True the resolver's value is already in the sheet's unit (e.g. a
    #: percentage or a count) and must not be scaled.
    unscaled: bool = False


@dataclass(frozen=True)
class SheetSpec:
    name: str
    unit: UnitConvention = "millions"
    header: HeaderCells = field(default_factory=HeaderCells)
    lines: tuple[LineSpec, ...] = ()
    #: Free-text notes surfaced in the Completion-notes sheet.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormSpec:
    code: str
    title: str
    workbook: str
    frequency: Frequency
    time_limit_days: int
    basis: Basis
    sheets: tuple[SheetSpec, ...]
    depends_on: tuple[str, ...] = ()
    #: Guide notes that shape scope (branch coverage, consolidation, …).
    scope_notes: tuple[str, ...] = ()
    #: BSD1: Ghana branches only (Guide, General Notes §1).
    ghana_branches_only: bool = False

    def sheet(self, name: str) -> SheetSpec | None:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        return None


@dataclass(frozen=True)
class LineValue:
    """A resolved input line for the snapshot + export."""

    sheet: str
    code: str
    label: str
    column: str
    cell: str
    value: float | None
    status: LineStatus
    source: str | None
    notes: str = ""
    #: The value is already in the sheet's official unit (percent, count,
    #: foreign-currency units) — export must NOT apply the sheet's divisor.
    unscaled: bool = False
