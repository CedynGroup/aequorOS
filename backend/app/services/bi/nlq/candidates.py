"""Which catalogue members the model is allowed to name, for ONE question.

The catalogue holds 1,416 measures — 984 of them ``engine.*`` regime, tier and
target variants — and 66 dimensions: 363 KB of labels and descriptions, which is
not a prompt. So a question is answered over a SUBSET, and how that subset is
chosen is a safety property rather than a performance trick.

**It is a subset of what the caller can already see.** The input is the visible
member list the ``GET /bi/catalogue`` pair probe produced for THIS caller, so every
narrowing below can only remove. Nothing here consults a binding, a grant or a
scope; it cannot widen what it is given.

**It is deterministic and lexical.** Token overlap between the question and each
member's id parts, label and description, label weighted highest. No embedding, no
second model call, no vendor. That matters for two reasons: the candidate set is
reproducible from the stored payload when a reviewer asks how a question was
answered, and the process that chooses it is the API process, which holds no model
credential.

**Dimensions are offered whole; measures are retrieved.** Every visible dimension
fits in about 9 KB, and a question that says "by branch" must be able to find the
branch dimension whatever words surround it — retrieval failure on a dimension is
a wrong grouping, which is exactly the plausible-looking wrong answer this surface
must not produce. Measures cannot be offered whole, so they are retrieved, and the
floor under retrieval is the institution's own headline set
(``insights.headline_measures``) rather than an arbitrary slice: if a question
matches no label at all, the model is still looking at the figures this platform
considers worth a headline for this institution.

**An engine measure whose authority is not this institution's is not offered at
all.** ``insights.engine_measure_applies`` is the same filter the certified packs
use: a measure keyed to a regime that did not resolve for this tenant can only ever
read as no value, and offering it invites a question that can only be answered with
a blank.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.bi.catalogue import Catalogue, DimensionDef, MeasureDef, MemberDef
from app.services.bi.insights import engine_measure_applies, headline_measures

#: How many measures one question is allowed to see. Forty entries of id, label,
#: description and allowed dimensions is about 21 KB — roughly 5k tokens — which
#: leaves the static prompt and the answer comfortable room, and is more figures
#: than any single question needs.
CANDIDATE_MEASURE_CAP = 40

#: Words that carry no catalogue signal. Short on purpose: a stop list long enough
#: to be clever is a stop list that swallows "total", "type" or "rate".
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "give",
        "has",
        "have",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "list",
        "me",
        "much",
        "my",
        "of",
        "on",
        "or",
        "our",
        "out",
        "please",
        "show",
        "so",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "they",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "whats",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "would",
        "you",
        "your",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Weights per field. A label is what a person would actually say, an id is the
#: platform's own vocabulary, a description is prose that mentions many things.
_WEIGHT_LABEL = 4
_WEIGHT_ID = 3
_WEIGHT_DESCRIPTION = 1
#: A member whose whole label appears in the question is what was asked for.
_BONUS_LABEL_PHRASE = 12
#: What the headline floor contributes, so a retrieved match always outranks it
#: while the floor still survives a question that matched nothing.
_SCORE_HEADLINE_FLOOR = 1


def tokenise(text: str) -> tuple[str, ...]:
    """Lower-cased alphanumeric tokens, de-duplicated, stop-words dropped.

    A crude singular is added for a token ending in ``s`` so "loans" reaches "loan".
    Deliberately not a stemmer: a stemmer is a dependency and a source of surprises,
    and the two forms cost one extra token each. Words ending ``ss``/``us``/``is`` are
    left alone — "gross" is not a plural, and "gros" is a token that matches nothing
    and could match something.
    """

    found: dict[str, None] = {}
    for raw in _TOKEN_RE.findall(text.casefold()):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        found.setdefault(raw, None)
        if len(raw) > 3 and raw.endswith("s") and not raw.endswith(("ss", "us", "is")):
            singular = raw[:-1]
            if singular not in _STOPWORDS:
                found.setdefault(singular, None)
    return tuple(found)


def _member_tokens(member: MemberDef) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The member's label, id and description token sets."""

    return (
        frozenset(tokenise(member.label)),
        frozenset(tokenise(member.id.replace(".", " ").replace("_", " "))),
        frozenset(tokenise(member.description)),
    )


def score(member: MemberDef, question_tokens: Sequence[str], question: str) -> int:
    """How well ``member`` matches the question. Zero means no overlap at all."""

    label_tokens, id_tokens, description_tokens = _member_tokens(member)
    total = 0
    for token in question_tokens:
        if token in label_tokens:
            total += _WEIGHT_LABEL
        if token in id_tokens:
            total += _WEIGHT_ID
        if token in description_tokens:
            total += _WEIGHT_DESCRIPTION
    label = member.label.casefold()
    if len(label) > 3 and label in question.casefold():
        total += _BONUS_LABEL_PHRASE
    return total


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """The exact members one question may name, and nothing else.

    ``measure_ids`` and ``dimension_ids`` together are the id set the validator
    checks the model's output against: an id outside this set is a refusal, not a
    narrowed query. The set is frozen into the job payload, so the worker that
    reads the model's answer re-derives no authority and cannot widen.
    """

    measures: tuple[MeasureDef, ...]
    dimensions: tuple[DimensionDef, ...]
    #: Drill paths whose every level is a candidate dimension.
    hierarchies: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def measure_ids(self) -> frozenset[str]:
        return frozenset(measure.id for measure in self.measures)

    @property
    def dimension_ids(self) -> frozenset[str]:
        return frozenset(dimension.id for dimension in self.dimensions)

    @property
    def member_ids(self) -> tuple[str, ...]:
        """Every offered id, measures first, in offer order."""

        return (*(m.id for m in self.measures), *(d.id for d in self.dimensions))

    @property
    def empty(self) -> bool:
        """Whether there is nothing to ask a question about."""

        return not self.measures


def select(  # noqa: PLR0913 - the whole selection key, all of it explicit
    cat: Catalogue,
    visible: Iterable[MemberDef],
    *,
    question: str,
    institution_class: str,
    capital_regime: str,
    measure_cap: int = CANDIDATE_MEASURE_CAP,
) -> CandidateSet:
    """The members this question may name, drawn only from ``visible``."""

    visible_by_id: Mapping[str, MemberDef] = MappingProxyType(
        {member.id: member for member in visible}
    )
    tokens = tokenise(question)

    dimensions = tuple(
        member for member in visible_by_id.values() if isinstance(member, DimensionDef)
    )
    dimension_ids = frozenset(dimension.id for dimension in dimensions)

    eligible = [
        member
        for member in visible_by_id.values()
        if isinstance(member, MeasureDef)
        and engine_measure_applies(
            member, institution_class=institution_class, capital_regime=capital_regime
        )
    ]
    order = {measure.id: position for position, measure in enumerate(eligible)}

    floor = {
        measure.id
        for measure in headline_measures(
            cat, institution_class=institution_class, capital_regime=capital_regime
        )
        if measure.id in order
    }
    scored: list[tuple[int, int, MeasureDef]] = []
    for measure in eligible:
        value = score(measure, tokens, question)
        if value == 0 and measure.id not in floor:
            continue
        scored.append((max(value, _SCORE_HEADLINE_FLOOR), order[measure.id], measure))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    measures = tuple(measure for _, _, measure in scored[:measure_cap])

    hierarchies = tuple(
        (hierarchy.id, tuple(hierarchy.levels))
        for hierarchy in cat.hierarchies()
        if hierarchy.levels and all(level in dimension_ids for level in hierarchy.levels)
    )
    return CandidateSet(measures=measures, dimensions=dimensions, hierarchies=hierarchies)


__all__ = [
    "CANDIDATE_MEASURE_CAP",
    "CandidateSet",
    "score",
    "select",
    "tokenise",
]
