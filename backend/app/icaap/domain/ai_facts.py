"""What a fact sheet may say about a figure, and what it may never say (pure).

The model is not allowed to compare, rank or characterise a number, because it
cannot see one. Everything it is allowed to SAY about a figure is precomputed
here as a **descriptor** — a small closed vocabulary the platform derived from
its own engines — and the model's job is to turn those into a sentence.

Two consequences worth keeping:

* A comparison the platform cannot make is ``not_assessed``, never a guess and
  never a default. "The ratio is above its minimum" said on the strength of a
  missing parameter would be a false statement in a regulatory filing.
* **Amounts and dates never carry a value**, in either mode. A bank's absolute
  balance-sheet size identifies it in a small market as surely as its name, and
  a reporting date narrows it further. Both still reach the finished text: the
  model places ``{{F:...}}``/``{{E:...}}`` and the platform fills them in.

No number lives in this module: every comparator is injected (D-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

VsLimit = Literal["meets", "at_limit", "breaches", "not_assessed"]
LimitKind = Literal["minimum", "maximum"]
LimitStatus = Literal["confirmed", "pending_confirmation"]
VsPriorYear = Literal["higher", "lower", "unchanged", "not_assessed"]
VsAppetite = Literal[
    "within_appetite",
    "outside_appetite_within_tolerance",
    "outside_tolerance",
    "beyond_capacity",
    "not_assessed",
]
Trajectory = Literal["rising", "falling", "mixed", "flat", "not_assessed"]

#: Fact kinds whose VALUE may be sent in standard mode. Everything else is
#: placed by the model and filled in by the platform.
VALUE_BEARING_KINDS: frozenset[str] = frozenset({"ratio_pct", "multiplier", "years", "count"})
#: Amounts and dates are withheld in BOTH modes — see the module docstring.
WITHHELD_KINDS: frozenset[str] = frozenset({"amount", "date"})

#: Text facts whose values are CODES, not prose: safe to send because they
#: carry no free text a person could have typed. Anything not listed is dropped.
SHAREABLE_TEXT_FACTS: frozenset[str] = frozenset(
    {
        "scenario_code",
        "worst_scenario",
        "ewi_escalation_state",
        "capital_basis",
        "method",
        "approach",
        "basis",
        "severity",
    }
)

#: fid -> governed parameter code. Codes only; no value appears here.
LIMIT_PARAMS: Mapping[str, str] = {
    "capital_position.car_pct": "car_min",
    "capital_position.cet1_ratio_pct": "cet1_min",
    "capital_position.tier1_ratio_pct": "tier1_min",
    "capital_position.leverage_ratio_pct": "leverage_min",
    "ilaap.lcr_pct": "lcr_min",
    "ilaap.nsfr_pct": "nsfr_min",
}


@dataclass(frozen=True)
class LimitInput:
    """One resolved limit: its value, which way it binds, and how settled it is."""

    value: Decimal
    kind: LimitKind
    status: LimitStatus


@dataclass(frozen=True)
class Descriptors:
    """Everything the model may say about one figure."""

    vs_limit: VsLimit = "not_assessed"
    limit_kind: LimitKind | None = None
    limit_status: LimitStatus | None = None
    vs_prior_year: VsPriorYear = "not_assessed"
    vs_appetite: VsAppetite = "not_assessed"
    trajectory: Trajectory = "not_assessed"
    flag: Literal["yes", "no"] | None = None

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.flag is not None:
            out["flag"] = self.flag
            return out
        out["vs_limit"] = self.vs_limit
        if self.limit_kind is not None:
            out["limit_kind"] = self.limit_kind
        if self.limit_status is not None:
            out["limit_status"] = self.limit_status
        out["vs_prior_year"] = self.vs_prior_year
        out["vs_appetite"] = self.vs_appetite
        if self.trajectory != "not_assessed":
            out["trajectory"] = self.trajectory
        return out


def to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def compare_to_limit(value: Decimal | None, limit: LimitInput | None) -> Descriptors:
    """``meets`` / ``at_limit`` / ``breaches``, or ``not_assessed``.

    ``at_limit`` is its own answer rather than a rounding of "meets": a ratio
    exactly at its minimum has no headroom, and a narrative that calls that
    comfortable is the one a supervisor would object to.
    """
    if value is None or limit is None:
        return Descriptors()
    if value == limit.value:
        verdict: VsLimit = "at_limit"
    elif limit.kind == "minimum":
        verdict = "meets" if value > limit.value else "breaches"
    else:
        verdict = "meets" if value < limit.value else "breaches"
    return Descriptors(vs_limit=verdict, limit_kind=limit.kind, limit_status=limit.status)


def limit_kind_for(direction: str | None) -> LimitKind | None:
    """FLOOR -> a minimum the bank must stay above; CEILING -> a maximum."""
    if direction == "floor":
        return "minimum"
    if direction == "ceiling":
        return "maximum"
    return None


def compare_to_prior_year(
    current: Decimal | None, prior: Decimal | None, *, quantum: Decimal | None
) -> VsPriorYear:
    """Direction against last year's sealed figure, at DISPLAY precision.

    Comparing at display precision rather than against a tolerance setting is
    deliberate: "unchanged" should mean "the report shows the same number", and
    that is a property of how the figure is printed, not of a tunable somebody
    would have to pick.
    """
    if current is None or prior is None:
        return "not_assessed"
    left, right = current, prior
    if quantum is not None:
        left = current.quantize(quantum)
        right = prior.quantize(quantum)
    if left == right:
        return "unchanged"
    return "higher" if left > right else "lower"


def trajectory_of(values: Sequence[Decimal | None]) -> Trajectory:
    """The shape of a projection series: rising, falling, mixed or flat."""
    known = [value for value in values if value is not None]
    if len(known) < 2:  # noqa: PLR2004 - a direction needs two points, definitionally
        return "not_assessed"
    diffs = [later - earlier for earlier, later in zip(known, known[1:], strict=False)]
    ups = any(diff > 0 for diff in diffs)
    downs = any(diff < 0 for diff in diffs)
    if ups and downs:
        return "mixed"
    if ups:
        return "rising"
    if downs:
        return "falling"
    return "flat"


def boolean_flag(value: str | None) -> Literal["yes", "no"] | None:
    """A boolean fact as a descriptor. ``true``/``1``/``yes`` all read as yes."""
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return "yes"
    if normalized in {"false", "0", "no", "n"}:
        return "no"
    return None


def value_is_sendable(kind: str, *, descriptor_only: bool) -> bool:
    """May the raw VALUE of a fact of this kind cross the boundary?"""
    if descriptor_only:
        return False
    return kind in VALUE_BEARING_KINDS


def text_value_is_sendable(fact_key: str, value: str | None) -> bool:
    """A text fact is sendable only if it is a KNOWN code-shaped key.

    Allow-list, not a shape check: a free-text field whose contents happen to
    look like a code this week is still a field a user types into.
    """
    if value is None:
        return False
    if fact_key not in SHAREABLE_TEXT_FACTS:
        return False
    text = str(value).strip()
    return bool(text) and text == text.casefold() and " " not in text


__all__ = [
    "LIMIT_PARAMS",
    "SHAREABLE_TEXT_FACTS",
    "VALUE_BEARING_KINDS",
    "WITHHELD_KINDS",
    "Descriptors",
    "LimitInput",
    "LimitKind",
    "LimitStatus",
    "Trajectory",
    "VsAppetite",
    "VsLimit",
    "VsPriorYear",
    "boolean_flag",
    "compare_to_limit",
    "compare_to_prior_year",
    "limit_kind_for",
    "text_value_is_sendable",
    "to_decimal",
    "trajectory_of",
    "value_is_sendable",
]
