"""What an insight IS, and the copy rules that keep it honest.

An :class:`Insight` is a sentence plus the evidence for it. Four properties are
structural rather than editorial, which is why they live in the type and not in
a style guide:

**It states no number the facts do not carry.** Every figure in the copy is
rendered by :func:`render_value` from a value held on a fact, in its own
recorded precision. Nothing is rounded for readability — rounding a capital
ratio to make a sentence shorter changes what the sentence says — and nothing is
ever softened to "about" or "roughly".

**It carries the trust of the data under it.** ``trust`` is the worst state of
the facts it cites. A ``grey`` state means the reconciliation check could not be
assessed and is reported as exactly that, never as a pass; ``amber`` and ``red``
are stated too. ``certified`` can only be true when every cited fact is a filed
engine figure AND the trust badge is green, so an insight over an unreconciled
book cannot present itself as certified.

**It never dresses advisory analysis as a filed figure** (D-022 / D-055). An
advisory or unregistered designation puts a qualifier on the insight in the
bank's own words and forces ``certified`` to false.

**Projections are marked as projections.** ``statement_class`` distinguishes
what happened from what would happen if a trend continued, and the copy for a
projection is written in the conditional.

The copy here is production copy. It names no currency, no regulator and no
country: an amount is stated in the reporting currency, which the institution's
jurisdiction resolves, and a percentage carries its own sign.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.domain.bi.authority import AdvisoryDesignation
from app.domain.bi.catalogue.members import ValueType
from app.services.bi import reconciliation
from app.services.bi.insights.digest import canonical_decimal
from app.services.bi.insights.drivers import Favourability
from app.services.bi.insights.facts import Fact, MissingReason, TrustState, TrustStatus

__all__ = [
    "Emphasis",
    "Insight",
    "StatementClass",
    "advisory_qualifier",
    "insight_from",
    "missing_reason_copy",
    "render_value",
    "stated_as",
    "trust_qualifier",
    "worst_trust",
]

StatementClass = Literal["movement", "attribution", "projection", "data_gap", "trust_notice"]
Emphasis = Literal["high", "normal", "low"]

_EMPHASIS_ORDER: dict[Emphasis, int] = {"high": 0, "normal": 1, "low": 2}

_STATEMENT_ORDER: dict[StatementClass, int] = {
    "trust_notice": 0,
    "data_gap": 1,
    "movement": 2,
    "attribution": 3,
    "projection": 4,
}

#: How a figure is stated, by what kind of figure it is. An amount names the
#: reporting currency rather than any currency: the jurisdiction resolves it.
#: How a figure is stated, per value type: the factor it is shown at, and the
#: unit that names it. Both halves matter and neither is optional.
#:
#: A ``fraction`` is a proportion of one, so it is shown multiplied by a hundred
#: — a suffix alone would render 0.061 as "0.061 %", and omitting the entry
#: entirely renders it as a bare "0.061" beside a ``pct`` twin reading "6.1 %",
#: which is the same quantity stated two ways in one strip (audit A8-03). An
#: ``index`` and a ``count`` are deliberately bare: neither has a unit, and
#: inventing one would be a claim about the number. Completeness over the
#: catalogue's numeric vocabulary is asserted in
#: ``tests/services/bi/test_insights_value_types.py``, because a value type this
#: map does not know is not rejected — it silently falls through unscaled.
_STATED_AS: dict[str, tuple[Decimal, str]] = {
    "pct": (Decimal(1), " %"),
    "fraction": (Decimal(100), " %"),
    "index": (Decimal(1), ""),
    "duration_years": (Decimal(1), " years"),
    "amount": (Decimal(1), " in the reporting currency"),
    "count": (Decimal(1), ""),
}

#: What a reader is told when a figure has no value. Never "0", never "flat".
_MISSING_COPY: dict[MissingReason, str] = {
    "not_supplied": ("the underlying data has not been supplied, so there is no figure to report"),
    "not_answerable": (
        "the question cannot be answered for this part of the book, so there is no figure to report"
    ),
    "not_computed": "this figure has not been computed for this date",
}

#: What an advisory designation means to the person reading the sentence.
_ADVISORY_COPY: dict[AdvisoryDesignation, str] = {
    "supervisory_monitoring": (
        "This figure is monitored by the supervisor but is not a filed return line."
    ),
    "advisory_only": "This is an analytical figure, not a filed one.",
    "unregistered": (
        "No filing authority is recorded for this figure, so it stands as analysis only."
    ),
}

#: What a reconciliation state means to the person reading the sentence. Grey is
#: stated as "not checked" and is never allowed to read as agreement.
_TRUST_COPY: dict[str, str] = {
    "grey": (
        "These figures have not been checked against the returns the platform files, "
        "so nothing here is confirmed."
    ),
    "amber": (
        "These figures were checked against the returns the platform files and a gap was found."
    ),
    "red": "These figures do not agree with the returns the platform files.",
}


def stated_as(value_type: str) -> tuple[Decimal, str]:
    """The factor and unit a figure of this type is stated at.

    Falls back to "as held, unnamed" for a non-numeric type, which is right for
    text, a date or a flag and is the only case where silence is correct.
    """
    return _STATED_AS.get(value_type, (Decimal(1), ""))


def render_value(value: Decimal, value_type: ValueType) -> str:
    """A figure as a reader is shown it, at the factor and unit of its type."""
    scale, suffix = stated_as(value_type)
    return f"{canonical_decimal(value * scale if scale != 1 else value)}{suffix}"


def missing_reason_copy(reason: MissingReason) -> str:
    return _MISSING_COPY[reason]


def advisory_qualifier(advisory: AdvisoryDesignation | None) -> str | None:
    """The sentence an advisory figure must carry, or ``None`` when filed."""
    if advisory is None or advisory == "filed":
        return None
    return _ADVISORY_COPY[advisory]


def trust_qualifier(trust: TrustState) -> str | None:
    """The sentence a non-green reconciliation state must carry."""
    return _TRUST_COPY.get(trust.overall)


def worst_trust(facts: Sequence[Fact]) -> TrustState:
    """The trust an insight over ``facts`` carries: the worst of them.

    The checks of every cited fact are kept, so the reader can see which
    reconciliation failed rather than only that something did.
    """
    if not facts:
        return TrustState(overall="grey")
    overall = reconciliation.overall_trust([fact.trust.overall for fact in facts])
    checks: dict[str, str] = {}
    for fact in facts:
        for check_id, status in fact.trust.checks:
            seen = checks.get(check_id)
            checks[check_id] = (
                status if seen is None else reconciliation.overall_trust([seen, status])
            )
    folded: tuple[tuple[str, TrustStatus], ...] = tuple(
        (check_id, _as_status(status)) for check_id, status in sorted(checks.items())
    )
    return TrustState(overall=_as_status(overall), checks=folded)


def _as_status(value: str) -> Literal["green", "amber", "red", "grey"]:
    if value == reconciliation.GREEN:
        return "green"
    if value == reconciliation.AMBER:
        return "amber"
    if value == reconciliation.RED:
        return "red"
    return "grey"


@dataclass(frozen=True, slots=True, kw_only=True)
class Insight:
    """One statement a bank may be shown, and everything that qualifies it."""

    id: str
    rule_id: str
    statement_class: StatementClass
    headline: str
    detail: str
    as_of: date
    measure_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    """Stable fact keys, never the volatile fact ids (``facts.py``)."""
    trust: TrustState
    advisory: AdvisoryDesignation | None = None
    certified: bool = False
    favourability: Favourability = "neutral"
    emphasis: Emphasis = "normal"
    qualifiers: tuple[str, ...] = ()

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Deterministic order: emphasis, then kind of statement, then id."""
        return (
            _EMPHASIS_ORDER[self.emphasis],
            _STATEMENT_ORDER[self.statement_class],
            self.id,
        )


def insight_from(  # noqa: PLR0913 - one statement plus everything that qualifies it
    *,
    rule_id: str,
    statement_class: StatementClass,
    facts: Sequence[Fact],
    headline: str,
    detail: str,
    as_of: date,
    favourability: Favourability = "neutral",
    emphasis: Emphasis = "normal",
) -> Insight:
    """Build an insight whose trust, designation and certification follow the facts.

    Nothing here is a caller's choice: the trust is the worst of the cited
    facts, the advisory designation is the strongest reservation any of them
    carries, and ``certified`` requires every cited fact to be a filed engine
    figure under a green badge.
    """
    trust = worst_trust(facts)
    advisory = _strongest_reservation(facts)
    qualifiers = tuple(
        sentence for sentence in (advisory_qualifier(advisory), trust_qualifier(trust)) if sentence
    )
    return Insight(
        id=f"{rule_id}|{'+'.join(fact.key for fact in facts)}",
        rule_id=rule_id,
        statement_class=statement_class,
        headline=headline,
        detail=detail,
        as_of=as_of,
        measure_ids=tuple(dict.fromkeys(fact.measure_id for fact in facts)),
        evidence=tuple(fact.key for fact in facts),
        trust=trust,
        advisory=advisory,
        certified=bool(facts) and all(fact.certified for fact in facts) and trust.reconciles,
        favourability=favourability,
        emphasis=emphasis,
        qualifiers=qualifiers,
    )


#: Weakest assurance wins: an insight citing one advisory figure is advisory.
_RESERVATION_RANK: dict[AdvisoryDesignation, int] = {
    "unregistered": 0,
    "advisory_only": 1,
    "supervisory_monitoring": 2,
    "filed": 3,
}


def _strongest_reservation(facts: Sequence[Fact]) -> AdvisoryDesignation | None:
    designations: list[AdvisoryDesignation] = [
        fact.advisory for fact in facts if fact.advisory is not None
    ]
    if not designations:
        return None
    return min(designations, key=lambda value: _RESERVATION_RANK[value])
