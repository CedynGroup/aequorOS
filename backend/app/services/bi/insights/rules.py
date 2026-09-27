"""The deterministic conditions that turn facts into insights.

A rule is a pure function from a :class:`~app.services.bi.insights.facts.
FactSheet` to a tuple of :class:`~app.services.bi.insights.statements.Insight`.
It reads nothing else — no database, no clock, no catalogue lookup — so the same
sheet always produces the same insights, in the same order, with the same
wording. That is what makes an insight citable: the reader can be shown the
fact sheet and the hash of it, and re-derive exactly what they were told.

The five rules, and the order they are applied in:

``trust_notice``
    One statement per non-green reconciliation state present on the sheet,
    naming the measures it covers. ``grey`` is stated as "not checked" — it is
    never reported as a pass, because a check that could not run has proved
    nothing (``reconciliation.py``).

``data_gap``
    One statement per figure that has no value, saying which dataset is missing
    and, explicitly, that the figure is neither zero nor flat. This is the rule
    that keeps D-042 / D-047 visible to the reader rather than only true in the
    compiler.

``movement``
    A figure that moved by more than the policy's materiality. The verdict is
    the fact's own (``drivers.favourability``), so a ``magnitude_lower_better``
    figure whose exposure shrank is favourable even when the signed number rose
    (D-013), and the sentence says so in as many words.

``attribution``
    Where a ratio's move came from, using the exact bridge (``drivers.py``). The
    parts are stated individually AND said to add up, because that is the claim
    the bridge makes and the reader is entitled to check it.

``projection``
    Where a trend reaches if it continues — in the conditional, carrying its
    method and its assumption (``projections.py``).

Materiality is a PRESENTATION threshold
---------------------------------------
:class:`InsightPolicy` decides how big a move has to be before it is worth a
sentence, and how many sentences a reader gets. It governs what is SHOWN and
never what a figure IS. No regulatory limit is expressed here or anywhere else
in this package: a measure's limit resolves from the register named by its
catalogue ``thresholds_source``, never from a number in code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.bi.catalogue.members import ValueType
from app.services.bi.insights.digest import canonical_decimal, fact_sheet_hash
from app.services.bi.insights.drivers import RatioBridge
from app.services.bi.insights.facts import (
    BridgeFact,
    Fact,
    FactSheet,
    MissingReason,
    MovementFact,
    ObservedFact,
    ProjectionFact,
)
from app.services.bi.insights.projections import Projection
from app.services.bi.insights.statements import (
    Emphasis,
    Insight,
    StatementClass,
    insight_from,
    missing_reason_copy,
    render_value,
)

__all__ = [
    "DEFAULT_POLICY",
    "InsightPolicy",
    "InsightSet",
    "RULES",
    "attribution_rule",
    "data_gap_rule",
    "derive_insights",
    "movement_rule",
    "projection_rule",
    "statement_classes",
    "trust_notice_rule",
]


@dataclass(frozen=True, slots=True)
class InsightPolicy:
    """How much has to happen before it is worth a sentence.

    Both values are presentation choices (see the module docstring), stated once
    here so a surface can widen or narrow its strip without any rule changing.
    """

    material_relative_change: Decimal = Decimal("0.05")
    """The smallest move, against its own base, that earns a sentence."""
    max_insights: int = 12
    """The most statements one reader is given at once."""


DEFAULT_POLICY = InsightPolicy()


@dataclass(frozen=True, slots=True)
class InsightSet:
    """What one run produced, bound to the facts it was derived from."""

    institution_id: str
    as_of: date
    catalogue_version: str
    fact_sheet_hash: str
    insights: tuple[Insight, ...]
    truncated: bool = False


# ---------------------------------------------------------------------------
# copy helpers
# ---------------------------------------------------------------------------

#: A change in a percentage is a change in POINTS, not a percentage of itself.
_CHANGE_SUFFIX: dict[str, str] = {
    "pct": " percentage points",
    "amount": " in the reporting currency",
}

_TRUST_HEADLINE: dict[str, str] = {
    "grey": "Some figures have not been checked",
    "amber": "A gap was found when checking some figures",
    "red": "Some figures do not agree with the returns the platform files",
}

_TRUST_DETAIL: dict[str, str] = {
    "grey": (
        "The reconciliation checks behind these figures could not be run, so nothing "
        "shown for them is confirmed. Not checked is not the same as checked and correct."
    ),
    "amber": (
        "These figures were compared with the returns the platform files and a gap was "
        "found. Read them with that gap in mind."
    ),
    "red": (
        "These figures were compared with the returns the platform files and did not "
        "agree. Treat them as unreliable until the difference is resolved."
    ),
}

_TRUST_EMPHASIS: dict[str, Emphasis] = {"grey": "normal", "amber": "high", "red": "high"}


def _render_change(value: Decimal, value_type: ValueType) -> str:
    return f"{canonical_decimal(value)}{_CHANGE_SUFFIX.get(value_type, '')}"


def _names(measures: Sequence[str]) -> str:
    """A readable list of labels: "A", "A and B", "A, B and C"."""
    unique = list(dict.fromkeys(measures))
    if len(unique) == 1:
        return unique[0]
    return f"{', '.join(unique[:-1])} and {unique[-1]}"


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def trust_notice_rule(sheet: FactSheet, policy: InsightPolicy) -> tuple[Insight, ...]:
    """One notice per non-green reconciliation state on the sheet."""
    _ = policy
    grouped: dict[str, list[Fact]] = {}
    for fact in sheet.facts:
        if fact.trust.overall != "green":
            grouped.setdefault(fact.trust.overall, []).append(fact)
    insights: list[Insight] = []
    for state in ("red", "amber", "grey"):
        facts = grouped.get(state)
        if not facts:
            continue
        insights.append(
            insight_from(
                rule_id="trust_notice",
                statement_class="trust_notice",
                facts=tuple(facts),
                headline=_TRUST_HEADLINE[state],
                detail=(
                    f"{_TRUST_DETAIL[state]} This covers {_names([fact.label for fact in facts])}."
                ),
                as_of=sheet.as_of,
                emphasis=_TRUST_EMPHASIS[state],
            )
        )
    return tuple(insights)


def data_gap_rule(sheet: FactSheet, policy: InsightPolicy) -> tuple[Insight, ...]:
    """One statement per figure that has no value, and why."""
    _ = policy
    insights: list[Insight] = []
    for fact in sheet.facts:
        reason = _missing_reason(fact)
        if reason is None:
            continue
        insights.append(
            insight_from(
                rule_id="data_gap",
                statement_class="data_gap",
                facts=(fact,),
                headline=f"No figure for {fact.label}",
                detail=(
                    f"{fact.label} has no value {fact.scope.label} at "
                    f"{fact.as_of.isoformat()} because {missing_reason_copy(reason)}. "
                    "It is not zero, and it has not stayed flat."
                ),
                as_of=fact.as_of,
            )
        )
    return tuple(insights)


def _missing_reason(fact: Fact) -> MissingReason | None:
    if isinstance(fact, ObservedFact | MovementFact):
        return fact.missing_reason
    return None


def movement_rule(sheet: FactSheet, policy: InsightPolicy) -> tuple[Insight, ...]:
    """A figure that moved by more than the policy's materiality."""
    insights: list[Insight] = []
    for fact in sheet.facts:
        if not isinstance(fact, MovementFact):
            continue
        relative = fact.relative_change
        delta, prior, current = fact.delta, fact.prior, fact.current
        if relative is None or delta is None or prior is None or current is None:
            continue
        if relative < policy.material_relative_change or fact.moved == "flat":
            continue
        verb = "rose" if fact.moved == "up" else "fell"
        detail = (
            f"Between {fact.prior_as_of.isoformat()} and {fact.as_of.isoformat()}, "
            f"{fact.label} {verb} by {_render_change(abs(delta), fact.value_type)} "
            f"{fact.scope.label}, from {render_value(prior, fact.value_type)} to "
            f"{render_value(current, fact.value_type)}."
        )
        if fact.direction == "magnitude_lower_better":
            sized = "grew" if abs(current) > abs(prior) else "shrank"
            detail += f" Measured by size, which is how this figure is judged, it {sized}."
        insights.append(
            insight_from(
                rule_id="movement",
                statement_class="movement",
                facts=(fact,),
                headline=(f"{fact.label} {verb} to {render_value(current, fact.value_type)}"),
                detail=detail,
                as_of=fact.as_of,
                favourability=fact.favourability,
                emphasis="high" if fact.favourability == "adverse" else "normal",
            )
        )
    return tuple(insights)


def attribution_rule(sheet: FactSheet, policy: InsightPolicy) -> tuple[Insight, ...]:
    """Where a ratio's move came from, using the exact bridge."""
    _ = policy
    insights: list[Insight] = []
    for fact in sheet.facts:
        if not isinstance(fact, BridgeFact) or not isinstance(fact.bridge, RatioBridge):
            continue
        bridge: RatioBridge = fact.bridge
        parts = "; ".join(
            f"{leg.label} contributed {_render_change(leg.contribution, fact.value_type)}"
            for leg in bridge.legs
        )
        insights.append(
            insight_from(
                rule_id="attribution",
                statement_class="attribution",
                facts=(fact,),
                headline=(f"{fact.label}: most of the move came from {bridge.largest_leg.label}"),
                detail=(
                    f"Between {fact.prior_as_of.isoformat()} and {fact.as_of.isoformat()}, "
                    f"{fact.label} moved by "
                    f"{_render_change(bridge.change, fact.value_type)}, from "
                    f"{render_value(bridge.prior.value, fact.value_type)} to "
                    f"{render_value(bridge.current.value, fact.value_type)}. "
                    f"Of that move, {parts}. Those parts add up to the whole move exactly."
                ),
                as_of=fact.as_of,
                favourability=bridge.favourability,
                emphasis="normal",
            )
        )
    return tuple(insights)


def projection_rule(sheet: FactSheet, policy: InsightPolicy) -> tuple[Insight, ...]:
    """Where a trend reaches if it continues — stated as a projection."""
    _ = policy
    insights: list[Insight] = []
    for fact in sheet.facts:
        if not isinstance(fact, ProjectionFact) or not isinstance(fact.projection, Projection):
            continue
        projection: Projection = fact.projection
        insights.append(
            insight_from(
                rule_id="projection",
                statement_class="projection",
                facts=(fact,),
                headline=(
                    f"{fact.label} would reach "
                    f"{render_value(projection.value, fact.value_type)} by "
                    f"{projection.horizon.isoformat()}"
                ),
                detail=(
                    f"On the trend in the {projection.observation_count} figures from "
                    f"{projection.window_start.isoformat()} to "
                    f"{projection.window_end.isoformat()}, {fact.label} would be "
                    f"{render_value(projection.value, fact.value_type)} at "
                    f"{projection.horizon.isoformat()}, a change of "
                    f"{_render_change(projection.change_from_last, fact.value_type)} from "
                    "the latest figure. This is a projection, not something that has "
                    f"happened: it {projection.assumption}."
                ),
                as_of=fact.as_of,
                emphasis="low",
            )
        )
    return tuple(insights)


Rule = Callable[[FactSheet, InsightPolicy], tuple[Insight, ...]]

#: Applied in this order; the result is then sorted by ``Insight.sort_key``.
RULES: tuple[Rule, ...] = (
    trust_notice_rule,
    data_gap_rule,
    movement_rule,
    attribution_rule,
    projection_rule,
)


def derive_insights(sheet: FactSheet, policy: InsightPolicy = DEFAULT_POLICY) -> InsightSet:
    """Every insight the sheet supports, in a deterministic order.

    The set is bound to ``fact_sheet_hash`` of the sheet it came from, so a
    reader can tell whether two identical-looking strips were drawn from the
    same figures.
    """
    produced: list[Insight] = []
    for rule in RULES:
        produced.extend(rule(sheet, policy))
    produced.sort(key=lambda insight: insight.sort_key)
    kept = tuple(produced[: policy.max_insights])
    return InsightSet(
        institution_id=sheet.institution_id,
        as_of=sheet.as_of,
        catalogue_version=sheet.catalogue_version,
        fact_sheet_hash=fact_sheet_hash(sheet),
        insights=kept,
        truncated=len(produced) > len(kept),
    )


def statement_classes() -> tuple[StatementClass, ...]:
    """Every class of statement the rules can produce, for the UI's legend."""
    return ("trust_notice", "data_gap", "movement", "attribution", "projection")
