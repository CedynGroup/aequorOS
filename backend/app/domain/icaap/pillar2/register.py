"""The Pillar 2 register: what each Table 5 row totals, and where it disagrees.

Every live Pillar 2 item names exactly one Table 5 row, through the framework
component it quantifies. This module turns a set of items into the per-row
totals the BoG grid needs, and into the machine findings that say a total
cannot be presented honestly yet.

Two rules are the whole point of the module.

**Like for like.** The Current and Base columns come from the ICAAP BASELINE
figures and the Stress columns from the ICAAP STRESSED figures. A row whose
baseline exists but whose stressed figure does not would present a stress
column that covers fewer risks than the current one — a smaller number that
looks like a result. That is ``table5_like_for_like``, and it blocks.

**Simple sum.** Rows are added without a diversification credit unless the
institution holds a governed allowance and has recorded the benefit as its own
component (audit M19). Nothing here decides that; the caller passes the items
it has, and a diversification component is simply one more (negative) item.

No regulatory number appears here: the module adds and compares figures other
code resolved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.icaap.units import amount

ZERO = Decimal(0)

#: A stressed figure is absent while the baseline is present, or the reverse.
FINDING_LIKE_FOR_LIKE = "table5_like_for_like"
#: Two live items claim the same component key.
FINDING_DUPLICATE_COMPONENT = "pillar2_duplicate_component"
#: An item names a Table 5 row the framework does not publish.
FINDING_UNKNOWN_ROW = "pillar2_unknown_table5_row"


class RegisterError(ValueError):
    """The register cannot be presented as a Table 5 grid."""

    def __init__(self, code: str, **context: str) -> None:
        self.code = code
        self.context = context
        super().__init__(code if not context else f"{code}:{sorted(context.items())}")


@dataclass(frozen=True)
class RegisterItem:
    """One live Pillar 2 figure, as the register sees it."""

    item_key: str
    component_key: str
    #: ``None`` for a risk assessed without a capital line (liquidity) — such an
    #: item contributes to no Table 5 row at all.
    table5_row: str | None
    baseline: Decimal | None
    stressed: Decimal | None
    #: ``icaap_method`` | ``capital_plan`` | ``stress_overlay`` | ``supervisory``
    #: | ``judgemental`` — carried so a reader can see what a row rests on.
    source: str
    method: str
    status: str
    approved: bool


@dataclass(frozen=True)
class RowTotal:
    """One Table 5 row's Pillar 2 total, on both bases."""

    row: str
    baseline: Decimal | None
    stressed: Decimal | None
    item_keys: tuple[str, ...]
    sources: tuple[str, ...]
    #: True when the row has items but at least one of them is not quantified
    #: on one of the two bases — the printed figure understates the row.
    partial: bool


@dataclass(frozen=True)
class Finding:
    """A machine code plus the identifiers the service renders a sentence from."""

    code: str
    ref: str
    params: Mapping[str, str]


@dataclass(frozen=True)
class RegisterTotals:
    """Every Table 5 row, plus the register's own totals."""

    rows: tuple[RowTotal, ...]
    total_baseline: Decimal | None
    total_stressed: Decimal | None
    partial_rows: tuple[str, ...]
    #: Items that carry no Table 5 row at all (``not_capitalised``).
    uncapitalised_item_keys: tuple[str, ...]


def _sum(values: Sequence[Decimal | None]) -> Decimal | None:
    """The sum of the stated figures, or ``None`` when none is stated.

    ``None`` means "not modelled" and must never print as a measured zero, so
    a row nobody quantified stays ``None`` rather than collapsing to 0.
    """
    stated = [value for value in values if value is not None]
    if not stated:
        return None
    total = ZERO
    for value in stated:
        total += value
    return amount(total)


def row_totals(items: Sequence[RegisterItem], *, row_keys: Sequence[str]) -> RegisterTotals:
    """Total the live items into the framework's Table 5 rows, in its order."""
    duplicates = _duplicate_components(items)
    if duplicates:
        raise RegisterError(FINDING_DUPLICATE_COMPONENT, component_key=duplicates[0])
    unknown = sorted(
        {
            item.table5_row
            for item in items
            if item.table5_row is not None and item.table5_row not in set(row_keys)
        }
    )
    if unknown:
        raise RegisterError(FINDING_UNKNOWN_ROW, table5_row=unknown[0])

    rows: list[RowTotal] = []
    partial: list[str] = []
    for row in row_keys:
        members = [item for item in items if item.table5_row == row]
        if not members:
            rows.append(
                RowTotal(
                    row=row,
                    baseline=None,
                    stressed=None,
                    item_keys=(),
                    sources=(),
                    partial=False,
                )
            )
            continue
        baseline = _sum([item.baseline for item in members])
        stressed = _sum([item.stressed for item in members])
        is_partial = any(item.baseline is None for item in members) or any(
            item.stressed is None for item in members
        )
        if is_partial:
            partial.append(row)
        rows.append(
            RowTotal(
                row=row,
                baseline=baseline,
                stressed=stressed,
                item_keys=tuple(item.item_key for item in members),
                sources=tuple(sorted({item.source for item in members})),
                partial=is_partial,
            )
        )
    return RegisterTotals(
        rows=tuple(rows),
        total_baseline=_sum([row.baseline for row in rows]),
        total_stressed=_sum([row.stressed for row in rows]),
        partial_rows=tuple(partial),
        uncapitalised_item_keys=tuple(item.item_key for item in items if item.table5_row is None),
    )


def _duplicate_components(items: Sequence[RegisterItem]) -> tuple[str, ...]:
    seen: set[str] = set()
    repeated: list[str] = []
    for item in items:
        if item.component_key in seen and item.component_key not in repeated:
            repeated.append(item.component_key)
        seen.add(item.component_key)
    return tuple(sorted(repeated))


def like_for_like_findings(items: Sequence[RegisterItem]) -> tuple[Finding, ...]:
    """Items whose two bases do not cover the same risk.

    A Table 5 stress column that omits a risk the current column carries is
    not a smaller requirement; it is an incomplete one. It is reported per
    item, because the item is what somebody has to go and finish.
    """
    findings: list[Finding] = []
    for item in items:
        if item.table5_row is None:
            continue
        if item.baseline is not None and item.stressed is None:
            findings.append(
                Finding(
                    code=FINDING_LIKE_FOR_LIKE,
                    ref=item.item_key,
                    params={
                        "table5_row": item.table5_row,
                        "component_key": item.component_key,
                        "missing_basis": "stressed",
                    },
                )
            )
        elif item.stressed is not None and item.baseline is None:
            findings.append(
                Finding(
                    code=FINDING_LIKE_FOR_LIKE,
                    ref=item.item_key,
                    params={
                        "table5_row": item.table5_row,
                        "component_key": item.component_key,
                        "missing_basis": "baseline",
                    },
                )
            )
    return tuple(findings)


def component_row_map(
    components: Sequence[tuple[str, str | None]],
) -> Mapping[str, str | None]:
    """``component_key -> table5_row``, refusing a component claimed twice.

    The framework is the authority for this mapping; the register only proves
    that one component cannot end up on two rows, which would double-count.
    """
    mapping: dict[str, str | None] = {}
    for key, row in components:
        if key in mapping and mapping[key] != row:
            raise RegisterError(FINDING_DUPLICATE_COMPONENT, component_key=key)
        mapping[key] = row
    return mapping


__all__ = [
    "FINDING_DUPLICATE_COMPONENT",
    "FINDING_LIKE_FOR_LIKE",
    "FINDING_UNKNOWN_ROW",
    "ZERO",
    "Finding",
    "RegisterError",
    "RegisterItem",
    "RegisterTotals",
    "RowTotal",
    "component_row_map",
    "like_for_like_findings",
    "row_totals",
]
