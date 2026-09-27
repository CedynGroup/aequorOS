"""``performance_targets`` — the bank's own budget / reforecast figures: the
right-hand side of every BI variance (docs/bi.md §Phase 2 "Targets").

One row per (period, grain, measure id, scope, version): the number the board
signed off for ONE catalogue measure over ONE period, optionally narrowed to one
value of one dimension (a branch, a sector). A bank-wide target names no scope,
so ``scope_dimension`` / ``scope_value`` are absent — not blank strings, and
never a "TOTAL" sentinel. The BI catalogue turns each row into the ``.target`` /
``.variance`` / ``.variance_pct`` / ``.attainment_pct`` variants of the measure
the row names.

**``time_behaviour`` is required and never defaulted.** A STOCK target is a
level to be standing at ``period`` (gross loans at 31 March); a FLOW target is
an amount to be accumulated OVER the window ``grain`` names (disbursements
during Q1). Comparing one against the other is arithmetically silent — the
subtraction still produces a number, and it is wrong by a whole quarter — so the
basis is DECLARED by the bank rather than inferred from the measure, and a row
that omits it is refused. Defaulting it to ``stock`` would be the misread with
extra steps.

``measure_id`` is validated for SHAPE only — a dotted lower-snake catalogue id
(``loans.balance_rc``, ``engine.car_pct.crd.official``) — and ``scope_dimension``
likewise (``branch.region``). Whether the id names a measure that exists, and
whether that measure is targetable at all, is the catalogue's decision at read
time: ``app/domain/ingestion`` is UPSTREAM of ``app/domain/bi`` and must not
import it, or the dataset that feeds the catalogue would depend on the catalogue
that consumes it. The two ``time_behaviour`` words deliberately mirror the
catalogue's own vocabulary; parity between them is asserted from the catalogue
side, which is the side that may see both.

``period`` is the LAST DAY of the window the row covers and must agree with
``grain``: a quarterly target dated 15 March is a mistake about which quarter is
meant, not a quarter. Nothing is coerced — every rejection names its field.

``value`` is in the measure's OWN unit (reporting currency for an amount,
percentage points for a ``_pct``); the row carries no unit because a measure's
unit is not the bank's to redefine. A target of zero is legitimate (zero
write-offs) and leaves ``attainment_pct`` undefined — the division is the
catalogue's to make safe.

Docs: docs/API_INTEGRATION.md §3.5.
"""

from __future__ import annotations

import calendar
import dataclasses
import re
from datetime import date

from . import ReferenceSchema, register

#: The window one row covers. A period grain, not a filing cadence: the
#: regulatory ``ReturnFrequency`` vocabulary (``monthly`` …) answers "how often
#: is this filed", which is a different question about a different object.
GRAINS: tuple[str, ...] = ("month", "quarter", "half_year", "year")
#: Mirrors the BI catalogue's ``TimeBehaviour`` without importing it (see the
#: module docstring).
TIME_BEHAVIOURS: tuple[str, ...] = ("stock", "flow")
#: ``budget`` is the approved plan; ``reforecast`` is the in-year revision. Both
#: can coexist for one period — they are different rows, not a correction.
VERSIONS: tuple[str, ...] = ("budget", "reforecast")

#: Catalogue measure and dimension ids: dot-separated lower-snake segments
#: (``loans.npl_ratio_pct``, ``branch.region``, ``engine.car_pct.crd.official``).
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9][a-z0-9_]*)+")
#: The months a grain's window can end in.
_GRAIN_END_MONTHS: dict[str, tuple[int, ...]] = {
    "month": tuple(range(1, 13)),
    "quarter": (3, 6, 9, 12),
    "half_year": (6, 12),
    "year": (12,),
}

SCHEMA = register(
    ReferenceSchema(
        kind="performance_targets",
        description=(
            "Budget / reforecast targets for BI measures: one row per period, grain, measure "
            "and scope, with the stock-or-flow basis the comparison must use"
        ),
        grain=(
            "one row per (period, grain, measure_id, scope_dimension, scope_value, version); "
            "period = the last day of the window"
        ),
        required=("period", "grain", "measure_id", "time_behaviour", "value", "version"),
        optional=("scope_dimension", "scope_value", "notes"),
        numeric=("value",),
        dates=("period",),
        enums={"grain": GRAINS, "time_behaviour": TIME_BEHAVIOURS, "version": VERSIONS},
    )
)


def _parse_period(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def period_end_for(period: date, grain: str) -> date | None:
    """The last day of the ``grain`` window ``period`` falls in (None: no such grain)."""
    end_months = _GRAIN_END_MONTHS.get(grain)
    if end_months is None:
        return None
    month = next(candidate for candidate in end_months if candidate >= period.month)
    return date(period.year, month, calendar.monthrange(period.year, month)[1])


def target_key(row: dict) -> tuple[str, str, str, str, str, str]:
    """The identity of one target — what a second row with the same key replaces.

    Scope is part of the key, so a bank-wide target and a per-branch target for
    the same measure, period and version are different rows rather than a
    conflict; two rows that agree on all six are the same target stated twice.
    """
    return (
        str(row.get("period") or "").strip(),
        str(row.get("grain") or "").strip(),
        str(row.get("measure_id") or "").strip(),
        str(row.get("scope_dimension") or "").strip(),
        str(row.get("scope_value") or "").strip(),
        str(row.get("version") or "").strip(),
    )


def validate_target_row(row: dict) -> list[str]:
    """Schema problems plus the rules a target must satisfy to be comparable at
    all: the period parses and is the last day of the grain it names; the
    measure id and any scope dimension are well-formed catalogue ids; scope
    dimension and value are given together or not at all."""
    problems = SCHEMA.validate_row(row)
    period = _parse_period(row.get("period"))
    if row.get("period") not in (None, "") and period is None:
        problems.append(
            f"field 'period' must be an ISO date, YYYY-MM-DD (got {row.get('period')!r})"
        )
    elif period is not None:
        grain = str(row.get("grain") or "").strip()
        expected = period_end_for(period, grain)
        if expected is not None and period != expected:
            problems.append(
                f"field 'period' must be the last day of the {grain} it names "
                f"(got {period.isoformat()}, that {grain} ends {expected.isoformat()})"
            )
    measure_id = str(row.get("measure_id") or "").strip()
    if measure_id and not _IDENTIFIER.fullmatch(measure_id):
        problems.append(
            "field 'measure_id' must be a catalogue measure id like 'loans.balance_rc' "
            f"(got {measure_id!r})"
        )
    dimension = str(row.get("scope_dimension") or "").strip()
    scope_value = str(row.get("scope_value") or "").strip()
    if bool(dimension) != bool(scope_value):
        problems.append(
            "'scope_dimension' and 'scope_value' are given together or not at all "
            "(a bank-wide target gives neither)"
        )
    elif dimension and not _IDENTIFIER.fullmatch(dimension):
        problems.append(
            "field 'scope_dimension' must be a catalogue dimension id like 'branch.code' "
            f"(got {dimension!r})"
        )
    return problems


# Bound after the function exists, because the rules need the schema they belong
# to. Re-registered so ``schema_for('performance_targets')`` returns the schema WITH its
# rules — the ingestion path asks ``problems_for``, and without this binding it
# would silently get the declarative half only (audit A7-07 / H-027).
SCHEMA = register(dataclasses.replace(SCHEMA, row_validator=validate_target_row))
