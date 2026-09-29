"""The query, read back to the reader in their own terms.

A reader who cannot read the query cannot meaningfully confirm it, so rendering a
``BiQuery`` as a sentence is part of the confirmation and not a nicety. Two rules
make the sentence trustworthy:

**Every word comes from the platform.** Labels come from the catalogue, dates from
the reporting date the READER selected, coded filter values from the dimension's own
declared vocabulary. The model contributes the SHAPE of the question and no text at
all, so there is no sentence the model could write that the reader would read as the
platform's own.

**It is clause by clause, not one opaque string.** The clauses are returned beside
the sentence so a surface can render them as a checklist — figure, grouping, period,
filters — which is what a person actually checks before pressing run. The sentence is
the same clauses joined, for the surfaces that want one line.

Dates are rendered ISO-8601 and the structured window is returned beside them, so the
dashboard can localise through its own ``fmtLocale`` rather than parse prose. Nothing
here names a currency, a regulator or a country.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.domain.bi.catalogue import Catalogue, DimensionDef, MeasureDef
from app.schemas.bi import BiFilter, BiQuery

ClauseKind = Literal["figure", "grouping", "period", "comparison", "filter", "ranking", "order"]

#: How each operator reads in a sentence. ``{values}`` is filled with labels.
_OPERATOR_PHRASES: dict[str, str] = {
    "eq": "is {values}",
    "ne": "is not {values}",
    "in": "is one of {values}",
    "not_in": "is none of {values}",
    "gt": "is above {values}",
    "gte": "is {values} or above",
    "lt": "is below {values}",
    "lte": "is {values} or below",
    "between": "is between {values}",
    "is_null": "is not recorded",
    "not_null": "is recorded",
    "contains": "contains {values}",
}


@dataclass(frozen=True, slots=True)
class Clause:
    """One thing a reader has to agree to before the query runs."""

    kind: ClauseKind
    text: str


@dataclass(frozen=True, slots=True)
class Reading:
    """The whole query as a person reads it, plus the window as machine values."""

    sentence: str
    clauses: tuple[Clause, ...] = ()
    as_of: date | None = None
    range_start: date | None = None
    range_end: date | None = None
    compare_to: date | None = None
    #: Ids of every member the sentence names, so a surface can link each one.
    member_ids: tuple[str, ...] = field(default_factory=tuple)


def _label(cat: Catalogue, member_id: str) -> str:
    """A member's own label, falling back to its id if the catalogue lost it."""

    try:
        return cat.member(member_id).label
    except KeyError:  # pragma: no cover - a proposal is validated against the catalogue
        return member_id


def _join(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _value_labels(cat: Catalogue, predicate: BiFilter) -> list[str]:
    """Filter values, shown by their declared names where the dimension has them."""

    try:
        member = cat.member(predicate.member)
    except KeyError:  # pragma: no cover - validated before a reading is built
        member = None
    vocabulary: dict[str, str] = {}
    if isinstance(member, DimensionDef):
        vocabulary = {value.code: value.label for value in member.values}
    return [vocabulary.get(str(value), str(value)) for value in predicate.values]


def _filter_clause(cat: Catalogue, predicate: BiFilter) -> Clause:
    phrase = _OPERATOR_PHRASES.get(predicate.op, "matches {values}")
    values = _join(_value_labels(cat, predicate))
    return Clause(
        kind="filter",
        text=f"where {_label(cat, predicate.member)} {phrase.format(values=values)}",
    )


def build(cat: Catalogue, query: BiQuery) -> Reading:
    """The reading of one validated ``BiQuery``."""

    clauses: list[Clause] = [
        Clause(kind="figure", text=_join([_label(cat, m) for m in query.measures]))
    ]
    if query.dimensions:
        clauses.append(
            Clause(
                kind="grouping",
                text=f"grouped by {_join([_label(cat, d) for d in query.dimensions])}",
            )
        )
    time = query.time
    if time.as_of is not None:
        clauses.append(Clause(kind="period", text=f"as at {time.as_of.isoformat()}"))
    elif time.range is not None:
        clauses.append(
            Clause(
                kind="period",
                text=(
                    f"for each period from {time.range.start.isoformat()} "
                    f"to {time.range.end.isoformat()}"
                ),
            )
        )
    if time.compare_to is not None:
        clauses.append(
            Clause(kind="comparison", text=f"compared with {time.compare_to.isoformat()}")
        )
    clauses.extend(_filter_clause(cat, predicate) for predicate in query.filters)
    if query.top_n is not None:
        headline = _label(cat, query.measures[0])
        remainder = ", with the rest grouped together" if query.top_n.other else ""
        clauses.append(
            Clause(
                kind="ranking",
                text=(
                    f"showing only the {query.top_n.n} largest "
                    f"{_label(cat, query.top_n.dimension)} groups by {headline}{remainder}"
                ),
            )
        )
    for sort in query.sort:
        direction = "largest first" if sort.direction == "desc" else "smallest first"
        clauses.append(
            Clause(kind="order", text=f"ordered by {_label(cat, sort.member)}, {direction}")
        )
    member_ids = tuple(
        dict.fromkeys(
            (
                *query.measures,
                *query.dimensions,
                *(predicate.member for predicate in query.filters),
                *(() if query.top_n is None else (query.top_n.dimension,)),
            )
        )
    )
    return Reading(
        sentence=", ".join(clause.text for clause in clauses),
        clauses=tuple(clauses),
        as_of=time.as_of,
        range_start=None if time.range is None else time.range.start,
        range_end=None if time.range is None else time.range.end,
        compare_to=time.compare_to,
        member_ids=member_ids,
    )


def measure_labels(cat: Catalogue, member_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """``(id, label)`` for each suggested member the catalogue still carries.

    Used for the refusal surface: the platform's own label for an id the model
    suggested, never the model's words, and never an id the caller was not offered.
    """

    pairs: list[tuple[str, str]] = []
    for member_id in member_ids:
        try:
            member = cat.member(member_id)
        except KeyError:
            continue
        if isinstance(member, (MeasureDef, DimensionDef)):
            pairs.append((member.id, member.label))
    return tuple(pairs)


__all__ = ["Clause", "ClauseKind", "Reading", "build", "measure_labels"]
