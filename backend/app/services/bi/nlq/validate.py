"""The refusal boundary: a model draft becomes a ``BiQuery``, or it becomes nothing.

Everything the model returns passes through here, and the only two outcomes are a
typed ``BiQuery`` the platform is willing to PROPOSE, or a refusal. There is no
third outcome — no repaired query, no dropped clause, no widened member set.
That is the rule the brief states and it is the rule this module is: a query that
runs and returns a plausible figure the reader did not ask for is worse than a
refusal, so every ambiguity resolves to a refusal.

Five checks, in this order, because the earlier ones make the later ones meaningful:

1. **the draft is internally consistent** — ``answerable`` agrees with whether a
   query is present. A model that says both, or neither, is refused rather than
   interpreted;
2. **every id was OFFERED.** The frozen candidate set is the authority, not the
   catalogue: an id the caller could see but which this question was not shown is
   refused too. Stricter than necessary on purpose, and it discloses nothing
   because the refusal is byte-identical to the refusal for an id that does not
   exist and for an id the caller's grants hide (``read_bi`` never learns which);
3. **the platform builds the time window**, from the reader's own ``as_of``. The
   draft contributes an intent and no date;
4. **it validates as a ``BiQuery``** (``extra="forbid"``, every cap, every
   operator arity). A ``ValidationError`` here is a refusal, which is why the
   draft's own types are looser: the failure has to be able to happen;
5. **the structural rules the compiler will apply** — a grouping the figure does
   not allow, a Top-N over a dimension the query does not group by, a sort on
   something it does not return — are checked here so a reader is never asked to
   confirm a query that cannot run. It is a pre-check and not a second compiler:
   anything it does not know about still refuses at run time, before any row is
   served.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import ValidationError

from app.domain.bi.catalogue import Catalogue, DimensionDef, MeasureDef
from app.schemas.bi import BiDateRange, BiFilter, BiQuery, BiSort, BiTime, BiTopN, shift_months
from app.services.bi.nlq.schema import NlqDraft, NlqQueryDraft

#: Why a question produced no query. Closed, and mapped to production copy below.
#:
#: ``unrecognised_member`` is ONE code for three different internal facts — an id
#: that is not in the catalogue, an id the caller's grants hide, and an id the
#: caller can see but which was not offered for this question. Merging them is the
#: point: telling the reader which one it was would tell them that a figure they
#: may not see exists.
NlqRefusal = Literal[
    "unrecognised_member",
    "malformed_output",
    "contradictory_output",
    "unusable_query",
    "unanswerable",
]

REFUSAL_MESSAGES: dict[NlqRefusal, str] = {
    "unrecognised_member": (
        "The platform did not recognise every figure in that question, so nothing was "
        "run. Rephrase it using the name of a figure available to you."
    ),
    "malformed_output": (
        "The platform could not turn that question into a question it can answer. "
        "Try naming the figure, the grouping and the period separately."
    ),
    "contradictory_output": (
        "The platform could not turn that question into a question it can answer. "
        "Try naming the figure, the grouping and the period separately."
    ),
    "unusable_query": (
        "That question needs a figure and a grouping that cannot be combined. Ask for "
        "the figure on its own, or group it by something the figure supports."
    ),
    "unanswerable": (
        "The platform has no figure that answers that question. Try naming a figure "
        "from your own list, or ask your Org Owner for access to the one you need."
    ),
}

#: What the reader is told about each reason the MODEL gave, when it gave one. The
#: model's own words are never shown; only the platform's sentence for its code.
UNANSWERABLE_MESSAGES: dict[str, str] = {
    "no_matching_figure": (
        "None of the figures available to you matches that question. Try naming the "
        "figure you want."
    ),
    "needs_a_figure_the_platform_does_not_hold": (
        "That question needs a figure this platform does not hold yet."
    ),
    "ambiguous": (
        "That question could mean more than one figure. Name the figure or the period "
        "you mean and ask again."
    ),
    "not_a_question_about_figures": (
        "This answers questions about your own figures. Ask for a figure, a grouping and a period."
    ),
}


@dataclass(frozen=True, slots=True)
class Translation:
    """The outcome for one question: a query to propose, or a refusal.

    Exactly one of ``query`` and ``refusal`` is set. ``suggested_members`` is only
    ever ids the platform re-resolved in the OFFERED set, so it cannot carry a
    member the reader was not already shown.
    """

    query: BiQuery | None = None
    refusal: NlqRefusal | None = None
    #: The model's own closed reason, when ``refusal`` is ``unanswerable``.
    unanswerable_reason: str | None = None
    suggested_members: tuple[str, ...] = ()
    #: The exact ids the draft named that were not offered. Recorded on the job for
    #: review and NEVER returned to the caller.
    unoffered_members: tuple[str, ...] = ()

    @property
    def proposed(self) -> bool:
        return self.query is not None

    @property
    def message(self) -> str:
        """Production copy for a refusal; empty for a proposal."""

        if self.refusal is None:
            return ""
        if self.refusal == "unanswerable" and self.unanswerable_reason is not None:
            return UNANSWERABLE_MESSAGES.get(
                self.unanswerable_reason, REFUSAL_MESSAGES["unanswerable"]
            )
        return REFUSAL_MESSAGES[self.refusal]


def _time(draft: NlqQueryDraft, as_of: date) -> BiTime | None:
    """The reporting window, computed by the PLATFORM from the reader's own date.

    ``None`` when the draft's intent is incoherent (a trailing window with no
    length, or a length on a single date), which is a refusal like any other. No
    date in this function comes from the model.
    """

    time = draft.time
    if time.window == "as_of":
        if time.trailing_months is not None:
            return None
        return BiTime(
            as_of=as_of, compare_to=shift_months(as_of, -1) if time.compare_with_prior else None
        )
    if time.trailing_months is None:
        return None
    start = shift_months(as_of, -(time.trailing_months - 1))
    return BiTime(
        range=BiDateRange(start=start, end=as_of),
        compare_to=shift_months(start, -1) if time.compare_with_prior else None,
    )


def _named_ids(draft: NlqQueryDraft) -> tuple[str, ...]:
    """Every member id the draft names, in the order it names them."""

    return (
        *draft.measures,
        *draft.dimensions,
        *(predicate.member for predicate in draft.filters),
        *(() if draft.top_n is None else (draft.top_n.dimension,)),
        *(sort.member for sort in draft.sort),
    )


def _structurally_runnable(cat: Catalogue, query: BiQuery) -> bool:
    """The compiler's own structural rules, applied before the reader confirms."""

    measures: list[MeasureDef] = []
    for member_id in query.measures:
        member = cat.member(member_id)
        if not isinstance(member, MeasureDef):
            return False
        measures.append(member)
    sliceable = [
        *query.dimensions,
        *(predicate.member for predicate in query.filters),
        *(() if query.top_n is None else (query.top_n.dimension,)),
    ]
    for dimension_id in sliceable:
        if not isinstance(cat.member(dimension_id), DimensionDef):
            return False
        if any(dimension_id not in measure.allowed_dimensions for measure in measures):
            return False
    if query.top_n is not None and query.top_n.dimension not in query.dimensions:
        return False
    requested = {*query.measures, *query.dimensions}
    return all(sort.member in requested for sort in query.sort)


def translate(  # noqa: PLR0911 - one return per refusal, never a chain that could fall through
    cat: Catalogue,
    draft: NlqDraft,
    *,
    offered_member_ids: frozenset[str],
    as_of: date,
) -> Translation:
    """One model draft as a proposal or a refusal. Never anything in between."""

    suggested = tuple(
        member_id for member_id in draft.suggested_members if member_id in offered_member_ids
    )
    if not draft.answerable:
        if draft.query is not None:
            return Translation(refusal="contradictory_output", suggested_members=suggested)
        return Translation(
            refusal="unanswerable",
            unanswerable_reason=draft.unanswerable_reason,
            suggested_members=suggested,
        )
    if draft.query is None:
        return Translation(refusal="contradictory_output", suggested_members=suggested)

    query_draft = draft.query
    unoffered = tuple(
        dict.fromkeys(
            member_id
            for member_id in _named_ids(query_draft)
            if member_id not in offered_member_ids
        )
    )
    if unoffered:
        return Translation(
            refusal="unrecognised_member",
            suggested_members=suggested,
            unoffered_members=unoffered,
        )

    time = _time(query_draft, as_of)
    if time is None:
        return Translation(refusal="malformed_output", suggested_members=suggested)
    try:
        query = BiQuery(
            measures=list(query_draft.measures),
            dimensions=list(query_draft.dimensions),
            filters=[
                BiFilter(
                    member=predicate.member,
                    op=predicate.op,
                    values=list(predicate.values),
                )
                for predicate in query_draft.filters
            ],
            time=time,
            top_n=(
                None
                if query_draft.top_n is None
                else BiTopN(dimension=query_draft.top_n.dimension, n=query_draft.top_n.n)
            ),
            sort=[BiSort(member=s.member, direction=s.direction) for s in query_draft.sort],
        )
    except ValidationError:
        return Translation(refusal="malformed_output", suggested_members=suggested)
    if not _structurally_runnable(cat, query):
        return Translation(refusal="unusable_query", suggested_members=suggested)
    return Translation(query=query, suggested_members=suggested)


__all__ = [
    "REFUSAL_MESSAGES",
    "UNANSWERABLE_MESSAGES",
    "NlqRefusal",
    "Translation",
    "translate",
]
