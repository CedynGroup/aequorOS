"""AI commentary for BI: opt-in, grounded in computed facts, always answered.

``docs/bi.md`` §Phase 2 "AI commentary (opt-in)" (P2-AI1, P2-AI2). The surface is
a BI read — a reader on a pack asks the platform to say, in words, what the
figures they are already looking at show. Almost nothing here is new machinery:
the egress gates, the consent row, the pseudonymiser, the grounding validator,
the placeholder grammar, the quota, the vendor tier and the recorded-model test
path were all built for ICAAP drafting and are REUSED unchanged. What this package
adds is the last mile, in five small pieces:

``payload``
    The minimiser. A :class:`~app.services.bi.insights.facts.FactSheet` in, the
    smallest thing that could support a paragraph out: descriptors, catalogue
    labels and a placeholder id per quotable figure. In descriptor-only mode — the
    DEFAULT, because a tenant that has not chosen has not chosen to send figures —
    no figure travels at all.

``prompt``
    A byte-stable static system block, one of two fixed mode blocks, then the
    payload as the user turn. Prompt caching is requested with a flag; whether it
    can be honoured is the vendor's question, answered in ``app/services/ai``.

``schema``
    ``CommentaryDraft`` — the structured output the model must return.

``validate``
    The BI context for ``app/domain/ai/grounding.py``. Every number and every name
    in the model's prose is a placeholder or the draft is refused.

``fallback``
    The deterministic commentary, composed from the platform's own insight
    statements. It is written to the row BEFORE the model is called and served
    whenever the model path produces nothing usable — which is why every failure
    path in this feature is short: there is only one answer to fall back to, and
    it is a good one.

``view``
    Binding a validated draft back to the platform's own figures and names, as
    segments so a surface can mark which words are the model's.

Two structural points that are easy to miss:

**The row lives in the AI plane, and so do its writes.** The draft row is
``ai_commentary_drafts`` (``app/models/bi_commentary.py``) — AI egress evidence,
beside the consent row that gates it. ``tests/architecture/test_bi_plane_boundary.py``
therefore forbids anything under ``app/services/bi`` from writing it, so the row
lifecycle — the request, the job handler, the audit event — lives in
``app/jobs/bi_commentary.py``, exactly as ``app/jobs/bi_export.py`` owns the
``audit_events`` write for the same reason. Everything in THIS package reads.

**Authorization is not re-invented here.** Commentary is built from an
``AssembledInsights`` the BI query path already produced for this reader, whose
facts exist only for members ``authorize_query`` allowed. A reader who may not
query a measure gets no sentence mentioning it, because no fact about it was ever
assembled.
"""

from __future__ import annotations

from app.services.bi.commentary.fallback import FALLBACK_SOURCE, deterministic_commentary
from app.services.bi.commentary.payload import (
    PAYLOAD_SCHEMA,
    VALUE_BEARING_TYPES,
    WITHHELD_TYPES,
    CommentaryBuild,
    FigureBinding,
    NoCommentableFactsError,
    bindings_as_dict,
    bindings_from_dict,
    build_payload,
    payload_digest,
)
from app.services.bi.commentary.prompt import (
    MODE_PROMPTS,
    PROMPT_VERSION,
    STATIC_SYSTEM_PROMPT,
    build_request,
    model_metadata,
    prompt_digest,
)
from app.services.bi.commentary.schema import CommentaryDraft, CommentaryParagraph
from app.services.bi.commentary.validate import error_entries, validate_draft
from app.services.bi.commentary.view import (
    MODEL_SOURCE,
    CommentaryParagraphView,
    CommentarySegment,
    CommentaryView,
    build_view,
    entity_values,
    resolve_paragraph,
)

__all__ = [
    "FALLBACK_SOURCE",
    "MODEL_SOURCE",
    "MODE_PROMPTS",
    "PAYLOAD_SCHEMA",
    "PROMPT_VERSION",
    "STATIC_SYSTEM_PROMPT",
    "VALUE_BEARING_TYPES",
    "WITHHELD_TYPES",
    "CommentaryBuild",
    "CommentaryDraft",
    "CommentaryParagraph",
    "CommentaryParagraphView",
    "CommentarySegment",
    "CommentaryView",
    "FigureBinding",
    "NoCommentableFactsError",
    "bindings_as_dict",
    "bindings_from_dict",
    "build_payload",
    "build_request",
    "build_view",
    "deterministic_commentary",
    "entity_values",
    "error_entries",
    "model_metadata",
    "payload_digest",
    "prompt_digest",
    "resolve_paragraph",
    "validate_draft",
]
