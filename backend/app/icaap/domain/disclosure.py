"""¶82 publication: what a bank may put on its website, and what it may never (pure).

¶82 asks an institution to publish the results of its ICAAP. It does not say
what "the results" are, so the bank selects — and every section starts NOT
selected, because a default that published is a default that leaks.

One class of content is not selectable at all. A supervisory add-on is the
regulator's own instruction to this bank; publishing it would disclose a
supervisory measure the supervisor has not published. So a selected section
that embeds or quotes a never-public block has those nodes REDACTED rather than
the whole section dropped: the narrative the bank chose to publish still reads,
and the withheld items are listed so an auditor can see exactly what was
removed and why.

Redaction happens here, in pure code over a canonical ProseMirror document, so
there is exactly one path a published document can come out of — and a test can
prove no path emits a never-public figure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: What the reader sees where a supervisory figure stood. Deliberately a
#: sentence, not a blank: a silent hole reads as an omission nobody decided.
WITHHELD_TEXT = "Withheld (supervisory information)"


@dataclass(frozen=True)
class WithheldItem:
    section_key: str
    block_key: str
    block_type: str
    node_type: str
    fact_key: str | None


@dataclass(frozen=True)
class RedactedSection:
    section_key: str
    doc: dict[str, Any]
    withheld: tuple[WithheldItem, ...]


def _withheld_paragraph() -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": WITHHELD_TEXT}]}


def _withheld_run() -> dict[str, Any]:
    return {"type": "text", "text": WITHHELD_TEXT}


def _redact(
    node: Mapping[str, Any],
    *,
    section_key: str,
    never_public: Mapping[str, tuple[str, str]],
    found: list[WithheldItem],
) -> dict[str, Any]:
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    block_id = str(attrs.get("blockId")) if attrs.get("blockId") is not None else None
    if kind in {"dataBlock", "factRef"} and block_id is not None and block_id in never_public:
        block_key, block_type = never_public[block_id]
        found.append(
            WithheldItem(
                section_key=section_key,
                block_key=block_key,
                block_type=block_type,
                node_type=str(kind),
                fact_key=(str(attrs["factKey"]) if kind == "factRef" else None),
            )
        )
        return _withheld_paragraph() if kind == "dataBlock" else _withheld_run()
    children = node.get("content")
    if not children:
        return dict(node)
    return {
        **node,
        "content": [
            _redact(child, section_key=section_key, never_public=never_public, found=found)
            for child in children
        ],
    }


def redact_section(
    section_key: str,
    doc: Mapping[str, Any],
    *,
    never_public: Mapping[str, tuple[str, str]],
) -> RedactedSection:
    """Replace every never-public embed or quotation with the withheld sentence.

    ``never_public`` maps a block id to ``(block_key, block_type)``. The caller
    decides which blocks those are — the framework's
    ``disclosure.never_public_block_types`` plus anything sourced from the
    supervisory add-on register — and this function decides nothing except how
    to remove them.
    """
    found: list[WithheldItem] = []
    redacted = _redact(doc, section_key=section_key, never_public=never_public, found=found)
    return RedactedSection(section_key=section_key, doc=redacted, withheld=tuple(found))


def contains_never_public(doc: Mapping[str, Any], never_public: Sequence[str]) -> bool:
    """True when the document still embeds or quotes one of these block ids."""
    forbidden = set(never_public)

    def walk(node: Mapping[str, Any]) -> bool:
        attrs = node.get("attrs") or {}
        if node.get("type") in {"dataBlock", "factRef"} and str(attrs.get("blockId")) in forbidden:
            return True
        return any(walk(child) for child in (node.get("content") or ()))

    return walk(doc)


__all__ = [
    "WITHHELD_TEXT",
    "RedactedSection",
    "WithheldItem",
    "contains_never_public",
    "redact_section",
]
