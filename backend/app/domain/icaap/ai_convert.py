"""Turn a drafted paragraph into editor nodes (pure).

The conversion is where the promise becomes structural. A ``{{F:...}}``
placeholder does NOT become the text of a number — it becomes a ``factRef``
node bound to the block and fact key the sheet was built from, carrying the
suggestion id that produced it. The figure the reader sees is resolved from the
binding at render time, so it refreshes when the block refreshes and freezes
when the cycle freezes, exactly like a figure a person typed the proper way.

``{{E:...}}`` is different: a name is not a live reference, it is a word. It is
substituted into the surrounding text run at accept time from the tenant's own
registers, so the paragraph reads as prose.

Every paragraph node carries ``aiSuggestionId``. That is the badge, and it is
what the provenance summary and the filed document's paragraph count are
computed from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.ai import placeholders


@dataclass(frozen=True)
class FactBinding:
    """Where a fid pointed when the fact sheet was frozen."""

    block_id: str
    fact_key: str
    binding_seq: int


@dataclass(frozen=True)
class PreviewSegment:
    """One run of a previewed paragraph: literal text, or a resolved figure."""

    kind: str
    text: str | None = None
    block_id: str | None = None
    fact_key: str | None = None
    display: str | None = None
    status: str | None = None


def _text_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def to_paragraph_nodes(
    paragraphs: Sequence[str],
    bindings: Mapping[str, FactBinding],
    entity_values: Mapping[str, str],
    suggestion_id: str,
) -> list[dict[str, Any]]:
    """Editor paragraph nodes for the selected drafted paragraphs.

    Raises ``KeyError`` for an unknown fid or entity key — by the time a draft
    reaches here it has passed the grounding validator, so an unknown reference
    is a programming error, not a user-facing state.
    """
    nodes: list[dict[str, Any]] = []
    for text in paragraphs:
        content: list[dict[str, Any]] = []
        pending = ""
        for segment in placeholders.segments(text):
            if segment.kind == "text":
                pending += segment.value
            elif segment.kind == "entity":
                # A name is a word, not a reference: merge it into the run.
                pending += entity_values[segment.value]
            else:
                if pending:
                    content.append(_text_node(pending))
                    pending = ""
                binding = bindings[segment.value]
                content.append(
                    {
                        "type": "factRef",
                        "attrs": {
                            "blockId": binding.block_id,
                            "factKey": binding.fact_key,
                            "suggestionId": suggestion_id,
                        },
                    }
                )
        if pending:
            content.append(_text_node(pending))
        nodes.append(
            {
                "type": "paragraph",
                "attrs": {"aiSuggestionId": suggestion_id},
                "content": content,
            }
        )
    return nodes


def to_preview_segments(
    text: str,
    bindings: Mapping[str, FactBinding],
    entity_values: Mapping[str, str],
    displays: Mapping[str, tuple[str, str]],
) -> list[PreviewSegment]:
    """Render one drafted paragraph for the review drawer.

    ``displays`` maps a fid to ``(display, status)`` so the reviewer sees the
    REAL figure and whether its block is still fresh — the same information they
    would have if they had inserted it, shown before they decide.
    """
    segments: list[PreviewSegment] = []
    pending = ""
    for segment in placeholders.segments(text):
        if segment.kind == "text":
            pending += segment.value
        elif segment.kind == "entity":
            pending += entity_values.get(segment.value, "")
        else:
            if pending:
                segments.append(PreviewSegment(kind="text", text=pending))
                pending = ""
            binding = bindings.get(segment.value)
            display, status = displays.get(segment.value, ("", "unknown"))
            segments.append(
                PreviewSegment(
                    kind="fact",
                    block_id=binding.block_id if binding else None,
                    fact_key=binding.fact_key if binding else None,
                    display=display,
                    status=status,
                )
            )
    if pending:
        segments.append(PreviewSegment(kind="text", text=pending))
    return segments


def append_to_doc(doc: Mapping[str, Any], nodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Append paragraphs to a working document.

    Append only. v1 never replaces or edits text a person already wrote: the
    reviewer decides where it belongs, and an AI that could rewrite an existing
    paragraph could silently rewrite an approved one.
    """
    content = list(doc.get("content") or [])
    content.extend(nodes)
    return {**dict(doc), "type": "doc", "content": content}


def ai_paragraph_ids(doc: Mapping[str, Any]) -> tuple[str, ...]:
    """Distinct suggestion ids marked on this document's paragraphs, sorted."""
    found: set[str] = set()
    for node in _walk(doc):
        if node.get("type") != "paragraph":
            continue
        marker = (node.get("attrs") or {}).get("aiSuggestionId")
        if marker:
            found.add(str(marker))
    return tuple(sorted(found))


def count_ai_paragraphs(doc: Mapping[str, Any]) -> int:
    """How many paragraphs of this document were drafted with AI assistance."""
    return sum(
        1
        for node in _walk(doc)
        if node.get("type") == "paragraph" and (node.get("attrs") or {}).get("aiSuggestionId")
    )


def count_paragraphs(doc: Mapping[str, Any]) -> int:
    return sum(1 for node in _walk(doc) if node.get("type") == "paragraph")


def _walk(node: Mapping[str, Any]):
    yield node
    for child in node.get("content", ()) or ():
        if isinstance(child, Mapping):
            yield from _walk(child)


__all__ = [
    "FactBinding",
    "PreviewSegment",
    "ai_paragraph_ids",
    "append_to_doc",
    "count_ai_paragraphs",
    "count_paragraphs",
    "to_paragraph_nodes",
    "to_preview_segments",
]
