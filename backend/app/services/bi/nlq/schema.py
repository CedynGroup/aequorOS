"""What the model is allowed to return: a draft query, never SQL and never a date.

Two rules shape every field here, and both exist because a model can be wrong in
a way that looks right.

**Structurally a ``BiQuery``, in plain scalars.** The draft mirrors
``app/schemas/bi.py::BiQuery`` field for field, so translating a question can only
ever produce a typed query over NAMED catalogue members — there is nowhere in this
schema for a fragment of SQL to live. The types are deliberately looser than
``BiQuery``'s (plain ``str`` for every id and value, no ``Literal`` on the filter
operator) because the VALIDATION is the refusal boundary: ``nlq.validate`` turns a
draft into a real ``BiQuery`` and anything that does not fit is refused. A schema
that could not fail would move the failure into the query path.

**No date, anywhere.** ``NlqTimeDraft`` carries an intent — a single reporting date
or a trailing window, with or without a prior-period comparison — and no date
field at all. Every actual date is computed by the platform from the ``as_of`` the
USER chose. A real figure for a date the reader did not ask for is the worst
outcome this surface can produce, and the only way to make it unreachable is to
give the model no way to state a date.

**No prose.** There is no free-text field. A question the model cannot express
comes back as a closed ``unanswerable_reason`` and, at most, catalogue member ids —
which the platform re-looks-up in its own catalogue before showing anything. So no
byte the model writes can reach a reader as words.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Bounds on the draft itself. They mirror the BI query caps rather than restating
#: new numbers, so a draft that validates cannot be larger than a hand-built query.
NLQ_DRAFT_MAX_MEASURES = 6
NLQ_DRAFT_MAX_DIMENSIONS = 3
NLQ_DRAFT_MAX_FILTERS = 6
NLQ_DRAFT_MAX_FILTER_VALUES = 20
NLQ_DRAFT_MAX_SORTS = 2
NLQ_DRAFT_MAX_SUGGESTIONS = 5
#: The longest trailing window a question may ask for, in months.
NLQ_MAX_TRAILING_MONTHS = 36

#: Why a question could not be turned into a query. Closed, because the reader is
#: shown the PLATFORM's sentence for each of these and never the model's.
NlqUnanswerableReason = Literal[
    "no_matching_figure",
    "needs_a_figure_the_platform_does_not_hold",
    "ambiguous",
    "not_a_question_about_figures",
]

#: Filter operators a translated question may use. A strict subset of
#: ``BiFilterOp``: ``contains`` is excluded because a substring predicate over a
#: name is a probe rather than a question, and the record-level surface is the
#: place a question about one obligor belongs.
NlqFilterOp = Literal[
    "eq",
    "ne",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "not_null",
]


class NlqModel(BaseModel):
    """Closed, like every BI schema: an unexpected key is a refusal."""

    model_config = ConfigDict(extra="forbid")


class NlqFilterDraft(NlqModel):
    """One predicate. ``values`` are strings; the compiler coerces them."""

    member: str
    op: NlqFilterOp
    values: list[str] = Field(default_factory=list, max_length=NLQ_DRAFT_MAX_FILTER_VALUES)


class NlqTimeDraft(NlqModel):
    """The reporting window as an INTENT. Carries no date, by construction."""

    window: Literal["as_of", "trailing_range"] = "as_of"
    #: Required for ``trailing_range`` and refused for ``as_of``.
    trailing_months: int | None = Field(default=None, ge=1, le=NLQ_MAX_TRAILING_MONTHS)
    compare_with_prior: bool = False


class NlqSortDraft(NlqModel):
    member: str
    direction: Literal["asc", "desc"] = "desc"


class NlqTopNDraft(NlqModel):
    dimension: str
    n: int = Field(ge=1)


class NlqQueryDraft(NlqModel):
    """The query half of a draft: a ``BiQuery`` in plain scalars."""

    measures: list[str] = Field(min_length=1, max_length=NLQ_DRAFT_MAX_MEASURES)
    dimensions: list[str] = Field(default_factory=list, max_length=NLQ_DRAFT_MAX_DIMENSIONS)
    filters: list[NlqFilterDraft] = Field(default_factory=list, max_length=NLQ_DRAFT_MAX_FILTERS)
    time: NlqTimeDraft = Field(default_factory=NlqTimeDraft)
    top_n: NlqTopNDraft | None = None
    sort: list[NlqSortDraft] = Field(default_factory=list, max_length=NLQ_DRAFT_MAX_SORTS)


class NlqDraft(NlqModel):
    """The model's whole output for one question.

    ``answerable`` is stated rather than inferred from ``query is None`` so a model
    that fills neither, or both, is a contradiction the validator can name instead
    of a shape it has to guess at.
    """

    answerable: bool
    query: NlqQueryDraft | None = None
    unanswerable_reason: NlqUnanswerableReason | None = None
    #: Catalogue member ids the model believes are near the question. Ids only: the
    #: platform looks each one up in ITS catalogue and drops what it cannot resolve
    #: or the caller may not see, so this cannot become a channel for model prose.
    suggested_members: list[str] = Field(default_factory=list, max_length=NLQ_DRAFT_MAX_SUGGESTIONS)


__all__ = [
    "NLQ_DRAFT_MAX_DIMENSIONS",
    "NLQ_DRAFT_MAX_FILTERS",
    "NLQ_DRAFT_MAX_FILTER_VALUES",
    "NLQ_DRAFT_MAX_MEASURES",
    "NLQ_DRAFT_MAX_SORTS",
    "NLQ_DRAFT_MAX_SUGGESTIONS",
    "NLQ_MAX_TRAILING_MONTHS",
    "NlqDraft",
    "NlqFilterDraft",
    "NlqFilterOp",
    "NlqModel",
    "NlqQueryDraft",
    "NlqSortDraft",
    "NlqTimeDraft",
    "NlqTopNDraft",
    "NlqUnanswerableReason",
]
