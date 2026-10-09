"""Per-figure results for aequorOS calculation boundaries.

Refusals are expected input or policy conditions, not exceptions. Row references
are one-based positions in the input sequence, for authorised bank readers only.
The optional detail retains the existing bank-facing fail-closed vocabulary;
neither its prose nor its context belongs in operator logs.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.authority.outcomes import OutcomeDetail


@dataclass(frozen=True, slots=True)
class Computed[ValueT]:
    value: ValueT

    def __post_init__(self) -> None:
        if self.value is None:
            raise ValueError("A computed figure requires a value; return Refused instead.")


@dataclass(frozen=True, slots=True)
class Refused:
    reason_code: str
    rule_citation: str
    row_ref: tuple[int, ...] = ()
    detail: OutcomeDetail | None = None

    def __post_init__(self) -> None:
        if not self.reason_code or not self.rule_citation:
            raise ValueError("A refused figure requires a reason code and rule citation.")
        if any(type(position) is not int or position < 1 for position in self.row_ref):
            raise ValueError("Refused row references must be positive one-based positions.")


type FigureResult[ValueT] = Computed[ValueT] | Refused
