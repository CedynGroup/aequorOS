"""The read side: binding a draft back to the platform's own figures and names.

A validated draft is prose full of ``{{F:...}}`` and ``{{E:...}}`` placeholders,
and this is where they become a sentence. Two rules decide the shape:

**The platform resolves, always, and from its own record.** A figure comes from
``fact_bindings`` — frozen when the request was made, never sent, never
re-derived by the model — and a name comes from the tenant's registers as they
stand TODAY (``pseudonymise.build_entity_map``), so a draft written last week
renders the institution's current name. There is no path by which a number the
model wrote could reach a reader.

**A placeholder that does not resolve un-serves the whole draft.** Not the
paragraph: the draft. A missing binding means the row and the prose disagree
about what was cited, and half a commentary is worse than the deterministic one —
so the reader gets the deterministic commentary instead, which is the same answer
every other unusable outcome produces.

The segments exist so the surface can MARK the platform's figures. The reader of
a machine-written paragraph is entitled to see which words are the model's and
which are the platform's, and that is a property of the data, not of a stylesheet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.ai import placeholders
from app.identity.public import Bank
from app.models.bi_commentary import AiCommentaryDraft
from app.services.ai import pseudonymise
from app.services.bi.commentary.fallback import FALLBACK_SOURCE
from app.services.bi.commentary.payload import FigureBinding, bindings_from_dict

__all__ = [
    "MODEL_SOURCE",
    "CommentaryParagraphView",
    "CommentarySegment",
    "CommentaryView",
    "build_view",
    "entity_values",
    "latest_paragraph_texts",
    "resolve_paragraph",
]

#: What the read path reports when the served paragraphs are the model's.
MODEL_SOURCE = "model"

SegmentKind = Literal["text", "figure", "name"]


@dataclass(frozen=True, slots=True)
class CommentarySegment:
    """One run of a paragraph: the model's words, or the platform's own value."""

    kind: SegmentKind
    text: str
    #: The measure behind a ``figure`` segment, so the surface can link it back to
    #: the chart it came from. ``None`` for text and names.
    measure_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommentaryParagraphView:
    """One paragraph, resolved."""

    segments: tuple[CommentarySegment, ...]

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)

    @property
    def measure_ids(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for segment in self.segments:
            if segment.measure_id is not None:
                seen.setdefault(segment.measure_id, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class CommentaryView:
    """What a reader is served for one commentary request."""

    draft_id: Any
    status: str
    #: ``model`` when the served paragraphs are the model's, ``platform`` when
    #: they are the deterministic commentary. Never hidden from the reader.
    source: str
    paragraphs: tuple[CommentaryParagraphView, ...]
    open_questions: tuple[str, ...]
    as_of: Any
    compare_to: Any
    fact_sheet_hash: str
    #: ``None`` when the caller did not offer a current sheet to compare with.
    stale: bool | None
    failure_code: str | None
    refusal_category: str | None
    validation_error_codes: tuple[str, ...]
    model_served: str | None
    fallback_used: bool
    poll_after_seconds: int

    @property
    def pending(self) -> bool:
        return self.status in ("queued", "running")


def entity_values(db: Session, bank: Bank, draft: AiCommentaryDraft) -> dict[str, str]:
    """Resolve the offered entity keys server-side, from the CURRENT registers."""
    entity_map = pseudonymise.build_entity_map(
        db,
        bank,
        as_of_label=draft.as_of.isoformat(),
        fiscal_year_label=str(draft.as_of.year),
        framework_label="",
        basis_label="",
    )
    offered = {str(key) for key in (draft.entity_keys or [])}
    return {key: value for key, value in entity_map.values.items() if key in offered}


def resolve_paragraph(
    text: str,
    bindings: Mapping[str, FigureBinding],
    names: Mapping[str, str],
) -> CommentaryParagraphView | None:
    """One paragraph's segments, or ``None`` when a placeholder does not resolve."""
    segments: list[CommentarySegment] = []
    for segment in placeholders.segments(text):
        if segment.kind == "text":
            segments.append(CommentarySegment(kind="text", text=segment.value))
            continue
        if segment.kind == "fact":
            binding = bindings.get(segment.value)
            if binding is None:
                return None
            segments.append(
                CommentarySegment(
                    kind="figure", text=binding.display, measure_id=binding.measure_id
                )
            )
            continue
        name = names.get(segment.value)
        if name is None:
            return None
        segments.append(CommentarySegment(kind="name", text=name))
    return CommentaryParagraphView(segments=tuple(segments))


def build_view(
    db: Session,
    *,
    bank: Bank,
    draft: AiCommentaryDraft,
    current_fact_sheet_hash: str | None = None,
) -> CommentaryView:
    """What to show for ``draft``: the model's commentary, or the platform's."""
    settings = get_settings()
    names = entity_values(db, bank, draft)
    bindings = bindings_from_dict(draft.fact_bindings or {})
    resolved = _resolved_model_paragraphs(draft, bindings, names)
    if resolved is None:
        paragraphs = _fallback_paragraphs(draft)
        source = FALLBACK_SOURCE
        questions: tuple[str, ...] = ()
    else:
        paragraphs, questions = resolved
        source = MODEL_SOURCE
    return CommentaryView(
        draft_id=draft.id,
        status=draft.status,
        source=source,
        paragraphs=paragraphs,
        open_questions=questions,
        as_of=draft.as_of,
        compare_to=draft.compare_to,
        fact_sheet_hash=draft.fact_sheet_hash,
        stale=(
            None
            if current_fact_sheet_hash is None
            else current_fact_sheet_hash != draft.fact_sheet_hash
        ),
        failure_code=draft.failure_code,
        refusal_category=draft.refusal_category,
        validation_error_codes=tuple(
            sorted(
                {
                    str(entry.get("code"))
                    for entry in (draft.validation_errors or [])
                    if isinstance(entry, dict) and entry.get("code")
                }
            )
        ),
        model_served=draft.model_served,
        fallback_used=bool(draft.fallback_used),
        poll_after_seconds=settings.ai.client_poll_seconds,
    )


def _resolved_model_paragraphs(
    draft: AiCommentaryDraft,
    bindings: Mapping[str, FigureBinding],
    names: Mapping[str, str],
) -> tuple[tuple[CommentaryParagraphView, ...], tuple[str, ...]] | None:
    """The model's paragraphs, or ``None`` if they may not be served.

    Only a ``validated`` row is even considered: a refusal produced nothing and a
    draft that failed grounding is exactly what must not be shown.
    """
    if draft.status != "validated":
        return None
    output = draft.output or {}
    raw = [
        str(entry.get("text", ""))
        for entry in output.get("paragraphs", [])
        if isinstance(entry, dict)
    ]
    if not raw:
        return None
    resolved: list[CommentaryParagraphView] = []
    for text in raw:
        paragraph = resolve_paragraph(text, bindings, names)
        if paragraph is None:
            return None
        resolved.append(paragraph)
    questions: list[str] = []
    for value in output.get("open_questions", []):
        rendered = resolve_paragraph(str(value), bindings, names)
        if rendered is None:
            return None
        questions.append(rendered.text)
    return tuple(resolved), tuple(questions)


def _fallback_paragraphs(draft: AiCommentaryDraft) -> tuple[CommentaryParagraphView, ...]:
    """The deterministic commentary, written at request time and stored whole.

    Its figures are already resolved — it was composed from the platform's own
    insight statements — so it is carried as plain text segments.
    """
    return tuple(
        CommentaryParagraphView(segments=(CommentarySegment(kind="text", text=str(text)),))
        for text in (draft.fallback_paragraphs or [])
        if str(text).strip()
    )


def latest_paragraph_texts(view: CommentaryView) -> Sequence[str]:
    """The served commentary as plain text — for a log line or a test."""
    return [paragraph.text for paragraph in view.paragraphs]
