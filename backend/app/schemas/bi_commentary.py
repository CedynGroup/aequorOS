"""The AI commentary contract: ask for a draft, and read whatever is on offer.

One response model for both routes, because the surface has exactly one job — say
what commentary is available for this reporting date, and say whose words it is.
That is why there is no error-shaped outcome in here for a refusal: a gate that is
shut, a spent daily cap and a sheet with nothing quotable all produce COMMENTARY
(the platform's own, composed from the same insight statements the strip shows),
and a contract that modelled those as errors would invite a client to render an
empty panel where a refusal belongs.

Three fields carry that, and a client must read all three:

``author``
    ``model`` or ``platform``. A bank reviewing its own reporting is entitled to
    know which sentences a model wrote, so this is never absent and never
    inferred. Every surface that shows the paragraphs must show this.
``state``
    ``pending`` while a draft is being written — the paragraphs below are the
    platform's in the meantime, so the panel is never empty — and ``ready``
    once there is nothing more to wait for. ``poll_after_seconds`` is set only
    while pending, so a client stops polling by reading the contract rather
    than by guessing.
``notice``
    Production copy, ready to render. It names whose words the paragraphs are
    and, when nothing was sent to a model, why. ``reason`` beside it is a
    machine code for telemetry and tests and must never be shown to a reader.

Class names here are unique across ``app/schemas`` (AGENTS.md: two Pydantic
classes sharing a name make FastAPI emit ``app__schemas__x__Name`` component keys
the generated client cannot map back).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bi import BiTrustBadge

#: Whose words the served paragraphs are. Mirrors
#: ``commentary.view.MODEL_SOURCE`` / ``commentary.fallback.FALLBACK_SOURCE``.
BiCommentaryAuthor = Literal["model", "platform"]

#: ``pending`` means a model call is outstanding for THIS reader; ``ready`` means
#: the answer below is final until they ask again.
BiCommentaryState = Literal["pending", "ready"]

#: One run of a paragraph. ``figure`` and ``name`` runs are the PLATFORM's own
#: values, resolved server-side from the frozen bindings and the current
#: registers; only ``text`` is ever the model's.
BiCommentarySegmentKind = Literal["text", "figure", "name"]


class BiCommentaryClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BiCommentarySegmentRead(BiCommentaryClosedModel):
    """One run of a paragraph, so a surface can mark the platform's own values."""

    kind: BiCommentarySegmentKind
    text: str
    #: The catalogue measure behind a ``figure`` run, so the surface can link the
    #: sentence back to the chart it came from. ``None`` for text and names.
    measure_id: str | None = None


class BiCommentaryParagraphRead(BiCommentaryClosedModel):
    """One paragraph of commentary, as segments plus the flattened text.

    ``text`` is the concatenation of the segments and is carried so a client that
    only needs prose (a tooltip, an accessibility label) does not reassemble it
    and risk dropping a figure.
    """

    segments: list[BiCommentarySegmentRead]
    text: str
    #: Every measure this paragraph quotes, in first-mention order.
    measure_ids: list[str] = Field(default_factory=list)


class BiCommentaryRequest(BiCommentaryClosedModel):
    """Ask for commentary on one reporting date.

    The same two dates the insights surface takes, and nothing else: the facts a
    draft may quote are decided by the reader's own access, never by the request.
    """

    as_of: date
    #: The earlier period movements are measured against. Defaults to the
    #: insights surface's own convention when omitted.
    compare_to: date | None = None


class BiCommentaryRead(BiCommentaryClosedModel):
    """What commentary is available for this reader, this institution, this date."""

    as_of: date
    compare_to: date
    state: BiCommentaryState
    author: BiCommentaryAuthor
    #: Production copy. Names whose words the paragraphs are, why a model was not
    #: asked when it was not, and that the book has moved when it has.
    notice: str
    paragraphs: list[BiCommentaryParagraphRead]
    #: Questions the model wants a human to answer. Only ever present on served
    #: model prose; the platform's commentary asks nothing.
    open_questions: list[str] = Field(default_factory=list)
    #: Whether this reader has asked for a draft of this date at all. False means
    #: the paragraphs are the platform's because nobody asked, not because
    #: anything was refused.
    requested: bool = False
    #: The reader's own most recent request, when there is one. A draft belongs to
    #: the reader who asked for it and is never served to anyone else.
    draft_id: UUID | None = None
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    #: The figures behind the draft have changed since it was written. Reported,
    #: never silently corrected: a model paragraph describes the book it was given.
    stale: bool = False
    #: The value-based digest of the facts this reader's sheet holds right now.
    fact_sheet_hash: str
    #: Seconds to wait before polling again. ``None`` means STOP — the state is
    #: final and no further call will change it.
    poll_after_seconds: int | None = None
    #: Whether asking now would reach a model at all. Advisory: the request route
    #: re-decides, and it is the only thing that may act on the answer.
    can_request_draft: bool = False
    #: A machine code — a gate code, a quota code, ``no_commentable_facts``, or the
    #: draft's own failure code. For telemetry and tests. NEVER shown to a reader:
    #: ``notice`` is the copy.
    reason: str | None = None
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str


__all__ = [
    "BiCommentaryAuthor",
    "BiCommentaryClosedModel",
    "BiCommentaryParagraphRead",
    "BiCommentaryRead",
    "BiCommentaryRequest",
    "BiCommentarySegmentKind",
    "BiCommentarySegmentRead",
    "BiCommentaryState",
]
