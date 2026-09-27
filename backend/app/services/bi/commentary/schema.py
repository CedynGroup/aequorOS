"""The structured output: what the model is allowed to return, as a type.

One Pydantic model, passed to ``client.complete_structured`` as the request's
``output_type``, so a reply that is not this shape never becomes a draft — the
client reports ``schema_invalid`` and the row takes a terminal status without
anything being shown.

Deliberately NOT in ``app/schemas/bi.py``: this is the model's contract, not the
API's. The wire shape a browser reads is built by the read path from the RESOLVED
draft, because what the model returns still contains unresolved placeholders and
must never be serialised to a client as it stands.

``open_questions`` exists for the same reason it does in ICAAP: when the facts do
not support something the reader would expect, the honest output is a question
for a person, not a sentence that fills the gap.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CommentaryParagraph(BaseModel):
    """One paragraph of commentary, in the bank's own voice."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description=(
            "One paragraph of plain prose. Every figure is a {{F:id}} placeholder "
            "and every name is an {{E:key}} placeholder, both copied exactly from "
            "the fact sheet. No digits, no percent signs, no currencies, no dates."
        )
    )


class CommentaryDraft(BaseModel):
    """The whole reply: a few paragraphs, and anything a person should answer."""

    model_config = ConfigDict(extra="forbid")

    paragraphs: list[CommentaryParagraph] = Field(
        default_factory=list,
        description="The commentary, in reading order. Fewer, better-grounded paragraphs.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Short questions for the bank's own staff where the figures do not "
            "support a statement the reader would expect. Same placeholder rules."
        ),
    )


__all__ = ["CommentaryDraft", "CommentaryParagraph"]
