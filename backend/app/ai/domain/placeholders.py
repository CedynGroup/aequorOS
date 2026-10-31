"""The placeholder grammar a drafted paragraph may contain.

Two kinds, and only two:

``{{F:<block_alias>.<fact_key>}}``
    A figure. It is resolved server-side to a ``factRef`` node bound to the
    block binding the fact sheet was built from, so the number in the finished
    report is the platform's, never the model's.

``{{E:<key>}}``
    A name the model is not allowed to write: the bank, its regulator, the
    central bank, the country, the currency, the as-of date. Resolved
    server-side from the tenant's own registers at preview and accept time.

Anything else containing a brace is malformed. That is deliberately strict:
a near-miss (`{F:x}`, `{{f:x}}`, `{{F:}}`) is a model that did not follow the
grammar, and a validator that silently tolerates near-misses is one that will
eventually tolerate a number.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

#: ``<block_alias>.<fact_key>``; both halves are the repo's usual snake-case id.
FACT_ID_PATTERN = r"[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}"
#: ``bank``, ``regulator``, ``sub_1`` — a short lower-case key, never a value.
ENTITY_KEY_PATTERN = r"[a-z][a-z0-9_]{0,31}"

_PLACEHOLDER_RE = re.compile(
    rf"\{{\{{(?:(?P<fact>F):(?P<fact_id>{FACT_ID_PATTERN})"
    rf"|(?P<entity>E):(?P<entity_key>{ENTITY_KEY_PATTERN}))\}}\}}"
)
#: Any brace run at all. What this finds and ``_PLACEHOLDER_RE`` does not is
#: malformed by definition — no third category exists.
_BRACE_RE = re.compile(r"[{}]+")

PlaceholderKind = Literal["fact", "entity"]


@dataclass(frozen=True)
class Placeholder:
    """One well-formed placeholder and where it sat in the text."""

    kind: PlaceholderKind
    #: The fact id or the entity key, without the braces or the kind prefix.
    value: str
    start: int
    end: int

    @property
    def token(self) -> str:
        prefix = "F" if self.kind == "fact" else "E"
        return f"{{{{{prefix}:{self.value}}}}}"


@dataclass(frozen=True)
class Segment:
    """A run of a paragraph: literal text, or one placeholder."""

    kind: Literal["text", "fact", "entity"]
    #: Literal text for ``text``; the fact id or entity key otherwise.
    value: str


def extract(text: str) -> tuple[Placeholder, ...]:
    """Every well-formed placeholder in ``text``, in order."""
    found: list[Placeholder] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        if match.group("fact") is not None:
            found.append(
                Placeholder(
                    kind="fact",
                    value=match.group("fact_id"),
                    start=match.start(),
                    end=match.end(),
                )
            )
        else:
            found.append(
                Placeholder(
                    kind="entity",
                    value=match.group("entity_key"),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return tuple(found)


def malformed_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Brace runs that are not part of a well-formed placeholder."""
    covered: list[tuple[int, int]] = [(p.start, p.end) for p in extract(text)]
    spans: list[tuple[int, int]] = []
    for match in _BRACE_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(lo <= start and end <= hi for lo, hi in covered):
            continue
        spans.append((start, end))
    return tuple(spans)


def outside_text(text: str) -> Iterator[tuple[int, str]]:
    """The parts of ``text`` that are NOT inside a placeholder, with offsets.

    Every content rule (digits, currency, names) is applied to these runs only:
    the whole point of a placeholder is that what it stands for is the
    platform's to decide, so its own characters are never scanned.
    """
    cursor = 0
    for placeholder in extract(text):
        if placeholder.start > cursor:
            yield cursor, text[cursor : placeholder.start]
        cursor = placeholder.end
    if cursor < len(text):
        yield cursor, text[cursor:]


def segments(text: str) -> tuple[Segment, ...]:
    """Split ``text`` into literal runs and placeholders, in order.

    The conversion to editor nodes and the read-side preview both walk this, so
    a paragraph renders the same way whether it is being previewed or inserted.
    """
    out: list[Segment] = []
    cursor = 0
    for placeholder in extract(text):
        if placeholder.start > cursor:
            out.append(Segment(kind="text", value=text[cursor : placeholder.start]))
        out.append(Segment(kind=placeholder.kind, value=placeholder.value))
        cursor = placeholder.end
    if cursor < len(text):
        out.append(Segment(kind="text", value=text[cursor:]))
    return tuple(out)


def fact_ids(texts: Sequence[str]) -> tuple[str, ...]:
    """Every fact id quoted across ``texts``, de-duplicated, in first-use order."""
    seen: dict[str, None] = {}
    for text in texts:
        for placeholder in extract(text):
            if placeholder.kind == "fact":
                seen.setdefault(placeholder.value, None)
    return tuple(seen)


__all__ = [
    "ENTITY_KEY_PATTERN",
    "FACT_ID_PATTERN",
    "Placeholder",
    "PlaceholderKind",
    "Segment",
    "extract",
    "fact_ids",
    "malformed_spans",
    "outside_text",
    "segments",
]
