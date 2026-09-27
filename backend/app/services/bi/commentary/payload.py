"""The minimiser: the smallest payload that can support the sentences we want.

Everything here answers one question — *what is the least that has to leave the
country for a bank to get a paragraph worth reading?* The answer is: the
platform's own **descriptors** of its own typed facts, the catalogue's **labels**
for what they measure, and a **placeholder id** per figure the model may point
at. Not the figures themselves, unless the tenant has explicitly opted out of
descriptor-only mode.

What is asserted ABSENT from the payload, in BOTH modes:

* the institution's name, its organisation's name, its regulator, central bank,
  country or currency — those are ``{{E:key}}`` keys whose values the platform
  resolves after the model has written the sentence (``pseudonymise``);
* any person, and any identifier at all: no bank id, no fact id, no mart build
  fingerprint, no URL, no email;
* any DATE, including the reporting date and the period compared against, and
  any MONETARY AMOUNT — the two classes ICAAP withholds in both modes, for the
  same reason: an amount is an institution's size, which is its identity;
* any dimension VALUE label — a branch, product or counterparty name is tenant
  data with no entity key, so a fact scoped to one is dropped from the payload
  entirely rather than described;
* the platform's own insight SENTENCES. ``Insight.headline`` and
  ``Insight.detail`` carry rendered figures (``statements.render_value``), so
  sending them would put values into a descriptor-only request through the back
  door. What travels instead is the ``priorities`` list: which facts the platform
  considers noteworthy, in what class, with what verdict — codes, never prose.

And in descriptor-only mode — the DEFAULT for a tenant that has made no choice
(``gates.descriptor_only``) — no figure at all: every quotable figure is offered
as ``value_withheld``, the model places it, and the platform fills it in from
``fact_bindings``. Descriptor-only governs EGRESS, never display: the reader sees
their own numbers either way.

The descriptors are read off the typed facts and nowhere else. A movement's
direction, its size against its own base and whether it was good for the bank
all come from :mod:`app.services.bi.insights.facts` and
:mod:`app.services.bi.insights.drivers`, and materiality is the SAME threshold
the insight strip uses — so commentary cannot characterise a figure in a way the
strip beside it would contradict, and a missing figure cannot be described as a
move at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.bi.catalogue.members import VALUE_TYPES, ValueType
from app.models import Bank
from app.services.ai import pseudonymise
from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.bi.insights.drivers import RatioBridge
from app.services.bi.insights.facts import (
    BridgeFact,
    Fact,
    FactSheet,
    MovementFact,
    ObservedFact,
    ProjectionFact,
)
from app.services.bi.insights.projections import Projection
from app.services.bi.insights.rules import DEFAULT_POLICY, InsightPolicy, InsightSet
from app.services.bi.insights.statements import render_value

__all__ = [
    "PAYLOAD_SCHEMA",
    "VALUE_BEARING_TYPES",
    "WITHHELD_TYPES",
    "CommentaryBuild",
    "FigureBinding",
    "NoCommentableFactsError",
    "bindings_as_dict",
    "bindings_from_dict",
    "build_payload",
    "payload_digest",
]

#: Versioned like every other digest input on the platform: a change in what the
#: payload carries is a change of schema, never a silent re-meaning of a hash.
PAYLOAD_SCHEMA = "aequoros-bi-commentary-payload-v1"

#: Value types whose figure may cross the boundary in STANDARD mode. Mirrors
#: ``app.domain.icaap.ai_facts.VALUE_BEARING_KINDS`` over BI's own vocabulary.
VALUE_BEARING_TYPES: frozenset[ValueType] = frozenset(
    {"pct", "fraction", "index", "duration_years", "count"}
)

#: Value types withheld in BOTH modes: an amount is an institution's size, a
#: date is its calendar, and ``text``/``flag`` are dimension values no measure
#: carries. Named as the complement of the sendable set so the two together can
#: be asserted to cover ``VALUE_TYPES`` exactly — a new value type is withheld
#: until someone decides otherwise, which is the correct default.
WITHHELD_TYPES: frozenset[ValueType] = frozenset(VALUE_TYPES) - VALUE_BEARING_TYPES

#: The alias each fact kind takes in a placeholder id. Short, because it is only
#: a handle: ``{{F:mv1.current}}`` is "the current figure of the first movement",
#: and the fact's own ``label`` says what it measures.
_KIND_ALIAS: Mapping[str, str] = {
    "movement": "mv",
    "observed": "ob",
    "bridge": "br",
    "projection": "pj",
}

#: What each quotable figure IS, in the model's own reading. Production copy: it
#: is the only explanation the model gets of what a placeholder stands for.
_FIGURE_ROLES: Mapping[str, str] = {
    "current": "the figure at the reporting date",
    "prior": "the figure for the period compared against",
    "change": "the change between the two periods",
    "value": "the figure at the reporting date",
    "largest_part": "the part of the change contributed by the largest driver",
    "projected": "the figure the observed trend reaches if it continues",
    "horizon": "the date the projection reaches",
}

#: Figure roles that are DATES whatever the measure's own value type says, and
#: are therefore withheld in both modes.
_DATE_ROLES: frozenset[str] = frozenset({"horizon"})

#: How a move is worded. ``drivers.MoveDirection`` is ``up``/``down``/``flat``;
#: a reader is told higher, lower or unchanged.
_MOVED: Mapping[str, str] = {"up": "higher", "down": "lower", "flat": "unchanged"}

_NOT_ASSESSED = "not_assessed"
_ZERO = Decimal(0)


class NoCommentableFactsError(RuntimeError):
    """The sheet holds nothing a grounded paragraph could rest on."""


@dataclass(frozen=True, slots=True)
class FigureBinding:
    """What a ``{{F:...}}`` id resolves to. NEVER sent."""

    measure_id: str
    role: str
    #: The platform's own rendered figure, in the precision the fact holds it.
    display: str
    value_type: ValueType

    def as_dict(self) -> dict[str, Any]:
        return {
            "measure_id": self.measure_id,
            "role": self.role,
            "display": self.display,
            "value_type": self.value_type,
        }


@dataclass(frozen=True, slots=True)
class CommentaryBuild:
    """One frozen request: what leaves, what it resolves to, and its digest."""

    payload: dict[str, Any]
    sha256: str
    mode: str
    bindings: dict[str, FigureBinding]
    entity_map: pseudonymise.EntityMap
    #: fid -> whether the PLATFORM holds the figure, for ``grounding.validate``.
    #: Always true for an offered figure: withholding a value from the model does
    #: not stop the placeholder resolving.
    availability: dict[str, bool] = field(default_factory=dict)
    fact_count: int = 0
    #: Facts dropped because they describe a named slice of the book rather than
    #: the institution. Counted, never named.
    scoped_facts_withheld: int = 0
    facts_dropped_over_cap: int = 0


def payload_digest(payload: Mapping[str, Any]) -> str:
    """The canonical digest the worker re-checks before anything is sent."""
    return sha256_hex(canonical_json(dict(payload)))


def build_payload(  # noqa: PLR0913 - one sheet, the reader's verdicts, one mode
    db: Session,
    *,
    bank: Bank,
    sheet: FactSheet,
    insight_set: InsightSet,
    descriptor_only: bool,
    policy: InsightPolicy = DEFAULT_POLICY,
) -> CommentaryBuild:
    """Freeze the minimised, pseudonymised payload for one commentary request."""
    cap = get_settings().ai.max_facts_per_sheet

    entries: list[dict[str, Any]] = []
    bindings: dict[str, FigureBinding] = {}
    availability: dict[str, bool] = {}
    aliases: dict[str, str] = {}
    counters: dict[str, int] = {}
    scoped_withheld = 0
    dropped = 0

    for fact in sheet.facts:
        if not fact.scope.whole_institution:
            # A branch, product or counterparty name is tenant data with no
            # entity key. There is no safe way to name the slice, so the fact
            # does not travel: the reader loses one sentence, not their anonymity.
            scoped_withheld += 1
            continue
        alias_prefix = _KIND_ALIAS.get(fact.kind)
        if alias_prefix is None:  # pragma: no cover - the fact union is closed
            dropped += 1
            continue
        if len(entries) >= cap:
            dropped += 1
            continue
        counters[alias_prefix] = counters.get(alias_prefix, 0) + 1
        alias = f"{alias_prefix}{counters[alias_prefix]}"
        aliases[fact.key] = alias
        figures = _figures_of(fact, alias)
        entries.append(
            _fact_entry(fact, alias, figures, descriptor_only=descriptor_only, policy=policy)
        )
        for fid, binding in figures.items():
            bindings[fid] = binding
            availability[fid] = True

    if not availability:
        message = "No figure on this sheet can support a grounded paragraph."
        raise NoCommentableFactsError(message)

    entity_map = pseudonymise.build_entity_map(
        db,
        bank,
        # ``build_entity_map`` takes four labels. BI has no regulatory framework
        # and no reporting basis, and an empty label is OMITTED from the map — so
        # those two keys are never offered, and a model that writes
        # ``{{E:framework}}`` fails validation as an unknown entity.
        as_of_label=sheet.as_of.isoformat(),
        fiscal_year_label=str(sheet.as_of.year),
        framework_label="",
        basis_label="",
    )

    payload: dict[str, Any] = {
        "schema": PAYLOAD_SCHEMA,
        "mode": "descriptor_only" if descriptor_only else "standard",
        "entities": [dict(offer) for offer in entity_map.offered()],
        "facts": entries,
        "priorities": _priorities(insight_set, aliases),
    }
    return CommentaryBuild(
        payload=payload,
        sha256=payload_digest(payload),
        mode=str(payload["mode"]),
        bindings=bindings,
        entity_map=entity_map,
        availability=availability,
        fact_count=len(entries),
        scoped_facts_withheld=scoped_withheld,
        facts_dropped_over_cap=dropped,
    )


# ---------------------------------------------------------------------------
# one fact: its descriptors, and the figures it makes quotable
# ---------------------------------------------------------------------------


def _figures_of(fact: Fact, alias: str) -> dict[str, FigureBinding]:
    """Every figure this fact makes quotable, keyed by its placeholder id.

    A figure exists here only when the PLATFORM holds it. A movement with one
    unknown side offers no ``change``, so no sentence can point at one.
    """
    figures: dict[str, FigureBinding] = {}
    if isinstance(fact, MovementFact):
        for role, value in (
            ("current", fact.current),
            ("prior", fact.prior),
            ("change", fact.delta),
        ):
            if value is not None:
                figures[f"{alias}.{role}"] = _binding(fact, role, value)
        return figures
    if isinstance(fact, ObservedFact):
        if fact.value is not None:
            figures[f"{alias}.value"] = _binding(fact, "value", fact.value)
        return figures
    if isinstance(fact, BridgeFact):
        bridge = fact.bridge
        if isinstance(bridge, RatioBridge):
            figures[f"{alias}.change"] = _binding(fact, "change", bridge.change)
            figures[f"{alias}.largest_part"] = _binding(
                fact, "largest_part", bridge.largest_leg.contribution
            )
        return figures
    projection = fact.projection
    if isinstance(projection, Projection):
        figures[f"{alias}.projected"] = _binding(fact, "projected", projection.value)
        figures[f"{alias}.horizon"] = FigureBinding(
            measure_id=fact.measure_id,
            role="horizon",
            display=projection.horizon.isoformat(),
            value_type="date",
        )
    return figures


def _fact_entry(  # noqa: PLR0913 - the fact, its handle, its figures, mode, policy
    fact: Fact,
    alias: str,
    figures: Mapping[str, FigureBinding],
    *,
    descriptor_only: bool,
    policy: InsightPolicy,
) -> dict[str, Any]:
    """One fact as the payload carries it."""
    entry: dict[str, Any] = {
        "id": alias,
        "label": fact.label,
        "kind": fact.kind,
        "available": _available(fact),
        "descriptors": _descriptors(fact, policy=policy),
    }
    reason = _unavailable_reason(fact)
    if reason is not None:
        entry["unavailable_reason"] = reason
    parts = _parts(fact)
    if parts is not None:
        entry["parts"] = parts
    if figures:
        entry["figures"] = [
            _figure_offer(fid, figures[fid], descriptor_only=descriptor_only)
            for fid in sorted(figures)
        ]
    return entry


def _available(fact: Fact) -> bool:
    if isinstance(fact, MovementFact | ObservedFact):
        return not fact.is_missing
    return fact.available


def _unavailable_reason(fact: Fact) -> str | None:
    """Why there is nothing to say — a CODE the prompt teaches the model to read."""
    if isinstance(fact, MovementFact | ObservedFact):
        return fact.missing_reason
    if isinstance(fact, BridgeFact):
        return None if isinstance(fact.bridge, RatioBridge) else fact.bridge.reason
    return None if isinstance(fact.projection, Projection) else fact.projection.reason


def _descriptors(fact: Fact, *, policy: InsightPolicy) -> dict[str, Any]:
    """Everything the model may say about this figure, and nothing more.

    Trust and designation ride on the fact, so they ride into the payload: a
    model told a figure is unreconciled or advisory can write the sentence the
    reader needs, while one told nothing writes the sentence it would have
    written about a filed, checked number.
    """
    descriptors: dict[str, Any] = {
        "data_trust": fact.trust.overall,
        "certified": fact.certified,
    }
    if fact.advisory is not None:
        descriptors["designation"] = fact.advisory
    if isinstance(fact, MovementFact):
        if fact.is_missing:
            descriptors["moved"] = _NOT_ASSESSED
            descriptors["size"] = _NOT_ASSESSED
            return descriptors
        descriptors["moved"] = _MOVED[fact.moved or "flat"]
        descriptors["size"] = _size(fact.relative_change, policy=policy)
        descriptors["assessment"] = fact.favourability
        return descriptors
    if isinstance(fact, BridgeFact) and isinstance(fact.bridge, RatioBridge):
        descriptors["moved"] = _MOVED[_direction(fact.bridge.change)]
        descriptors["assessment"] = fact.bridge.favourability
        descriptors["largest_part"] = fact.bridge.largest_leg.label
        # The identity the bridge exists to keep, stated so the model may state
        # it: the parts add up to the whole move exactly.
        descriptors["parts_sum_exactly"] = fact.bridge.sums_exactly()
        return descriptors
    if isinstance(fact, ProjectionFact) and isinstance(fact.projection, Projection):
        descriptors["moved"] = _MOVED[_direction(fact.projection.change_from_last)]
        descriptors["method"] = fact.projection.method
        descriptors["assumption"] = fact.projection.assumption
    return descriptors


def _parts(fact: Fact) -> list[dict[str, Any]] | None:
    """A bridge's legs: what each part is, and which way it pushed the ratio."""
    if not isinstance(fact, BridgeFact) or not isinstance(fact.bridge, RatioBridge):
        return None
    return [
        {"part": leg.label, "pushed": _MOVED[_direction(leg.contribution)]}
        for leg in fact.bridge.legs
    ]


def _direction(value: Decimal) -> str:
    if value > _ZERO:
        return "up"
    if value < _ZERO:
        return "down"
    return "flat"


def _size(relative_change: Decimal | None, *, policy: InsightPolicy) -> str:
    """Material or not, against the SAME threshold the insight strip uses.

    ``None`` — an unknown figure, or a zero base there is no proportion of — is
    ``not_assessed`` rather than "modest": nobody measured it.
    """
    if relative_change is None:
        return _NOT_ASSESSED
    return "material" if relative_change >= policy.material_relative_change else "modest"


def _binding(fact: Fact, role: str, value: Decimal) -> FigureBinding:
    return FigureBinding(
        measure_id=fact.measure_id,
        role=role,
        display=render_value(value, fact.value_type),
        value_type=fact.value_type,
    )


def _figure_offer(fid: str, binding: FigureBinding, *, descriptor_only: bool) -> dict[str, Any]:
    """One quotable figure as the model sees it: an id, a role, and maybe a value.

    ``available`` is always true — the PLATFORM holds the figure, so the
    placeholder will resolve. Whether the model is ALSO told the number is the
    mode's decision and the value type's, never a caller's.
    """
    offer: dict[str, Any] = {
        "id": fid,
        "of": _FIGURE_ROLES.get(binding.role, binding.role),
        "available": True,
    }
    if _sendable(binding, descriptor_only=descriptor_only):
        offer["value"] = binding.display
    else:
        offer["value_withheld"] = True
    return offer


def _sendable(binding: FigureBinding, *, descriptor_only: bool) -> bool:
    if descriptor_only:
        return False
    if binding.role in _DATE_ROLES:
        return False
    return binding.value_type in VALUE_BEARING_TYPES


def _priorities(insight_set: InsightSet, aliases: Mapping[str, str]) -> list[dict[str, Any]]:
    """What the platform already judged noteworthy — as CODES, never sentences.

    An insight's headline and detail carry rendered figures, which is exactly
    what must not travel. What does travel is the shape of the judgement: which
    facts, which class of statement, which verdict, how much emphasis.
    """
    priorities: list[dict[str, Any]] = []
    for insight in insight_set.insights:
        covered = [aliases[key] for key in insight.evidence if key in aliases]
        if not covered:
            continue
        priorities.append(
            {
                "class": insight.statement_class,
                "facts": covered,
                "assessment": insight.favourability,
                "emphasis": insight.emphasis,
            }
        )
    return priorities


def bindings_as_dict(bindings: Mapping[str, FigureBinding]) -> dict[str, dict[str, Any]]:
    """The ``fact_bindings`` column: the map that never leaves the process."""
    return {fid: binding.as_dict() for fid, binding in bindings.items()}


def bindings_from_dict(stored: Mapping[str, Any]) -> dict[str, FigureBinding]:
    """Read ``fact_bindings`` back. An unreadable entry is dropped, never guessed:
    a placeholder with no binding fails to resolve and the draft is not served."""
    out: dict[str, FigureBinding] = {}
    for fid, entry in stored.items():
        if not isinstance(entry, dict):
            continue
        display = entry.get("display")
        measure_id = entry.get("measure_id")
        value_type = entry.get("value_type")
        if not isinstance(display, str) or not isinstance(measure_id, str):
            continue
        out[str(fid)] = FigureBinding(
            measure_id=measure_id,
            role=str(entry.get("role", "")),
            display=display,
            value_type=cast("ValueType", value_type if value_type in VALUE_TYPES else "text"),
        )
    return out
