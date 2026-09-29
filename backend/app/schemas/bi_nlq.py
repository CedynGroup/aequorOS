"""Wire contract for natural-language questions (docs/bi.md §Phase 5).

Three shapes, and each one carries a rule the surface must not be able to lose.

``BiAskRequest`` carries the reader's words AND the reporting date the reader
selected. The date is required, and it is here rather than in the model's answer for
the reason the whole feature turns on: a real figure for a date nobody asked for is
the most convincing wrong answer available, so the platform computes every date and
the model states only what SHAPE of period was wanted.

``BiAskRead`` is the proposal, and it is deliberately not a result. It carries the
``BiQuery`` the platform is willing to run and the READING of it — the same query as
a sentence, clause by clause, in the catalogue's own labels — because a reader who
cannot read the query cannot meaningfully confirm it. No field here ever carries a
row, a figure or a word the model wrote: a refusal carries the platform's own message
for a closed reason code, and a suggestion carries a member id with the label the
platform looked up for it.

``BiAskRunRequest`` is the confirmation. It echoes the query back, and the server
refuses unless it is byte-for-byte the query it proposed — which is what makes "shown
to the user for confirmation" a server-side property rather than a client's promise.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.bi import BiClosedModel, BiQuery

#: The longest question that may be asked, and therefore the longest untrusted span
#: that can reach a prompt. Long enough for a real sentence, short enough that it
#: cannot become a document. It lives HERE, with the wire contract, rather than in
#: the service: ``app/schemas`` is deliberately outside the BI plane's exemption
#: (``tests/architecture/test_bi_plane_boundary.py`` rule (a)), so the contract must
#: not import the service that enforces it — the service imports the bound.
QUESTION_MAX_CHARS = 300

#: Where a question has got to. ``translating`` is the only non-terminal one.
#:
#: ``stopped`` covers every way there is no proposal and will not be one — a shut
#: gate, a spent daily cap, a vendor that could not be reached, a request that waited
#: out its queue expiry. They are one state on purpose: the reader's next action is
#: identical (read the message, ask again later) and splitting them would invite a
#: surface to treat an infrastructure failure as a governance refusal.
BiAskState = Literal["translating", "proposed", "refused", "stopped"]

#: Which part of the sentence a clause is, so a surface can render the confirmation
#: as the checklist a person actually reads rather than as one long line.
BiAskClauseKind = Literal[
    "figure", "grouping", "period", "comparison", "filter", "ranking", "order"
]


class BiAskRequest(BiClosedModel):
    """One question, and the reporting date the reader has selected."""

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    as_of: date


class BiAskClauseRead(BiClosedModel):
    """One thing the reader is agreeing to before the query runs."""

    kind: BiAskClauseKind
    text: str


class BiAskReadingRead(BiClosedModel):
    """The proposed query as a person reads it, plus its window as machine values.

    The dates are repeated as fields so a client can format them in the
    institution's own locale rather than parse them out of ``sentence`` — the
    sentence renders them ISO-8601, which is the one form that names no country.
    """

    sentence: str
    clauses: list[BiAskClauseRead] = Field(default_factory=list)
    as_of: date | None = None
    range_start: date | None = None
    range_end: date | None = None
    compare_to: date | None = None
    member_ids: list[str] = Field(default_factory=list)


class BiAskSuggestionRead(BiClosedModel):
    """A figure the platform offers instead. Id plus the CATALOGUE's own label."""

    member_id: str
    label: str


class BiAskRead(BiClosedModel):
    """A question, and whatever the platform is prepared to propose for it."""

    question_id: UUID
    state: BiAskState
    #: Production copy for this state. Always set — a state with nothing to say
    #: renders as an empty panel, which is the failure the message exists to prevent.
    message: str
    question: str
    as_of: date
    catalogue_version: str
    #: How many figures the model was shown. The audit answer to "what could it have
    #: named?", and the honest way to explain a refusal without naming any of them.
    figures_offered: int
    #: Set only in the ``proposed`` state, and never executed by returning it.
    query: BiQuery | None = None
    reading: BiAskReadingRead | None = None
    suggestions: list[BiAskSuggestionRead] = Field(default_factory=list)


class BiAskRunRequest(BiClosedModel):
    """The reader's confirmation: the proposed query, echoed back unchanged."""

    query: BiQuery


__all__ = [
    "QUESTION_MAX_CHARS",
    "BiAskClauseKind",
    "BiAskClauseRead",
    "BiAskRead",
    "BiAskReadingRead",
    "BiAskRequest",
    "BiAskRunRequest",
    "BiAskState",
    "BiAskSuggestionRead",
]
