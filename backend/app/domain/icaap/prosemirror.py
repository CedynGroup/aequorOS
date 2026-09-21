"""ProseMirror document grammar for the ICAAP section editor (pure domain).

The editor stores ProseMirror JSON and never HTML, so there is no HTML
sanitiser to get wrong: a document is accepted only if every node, mark,
attribute and content sequence is on the allowlist in ``editor_schema.json``,
and the renderers (PDF, DOCX, and P3's filing artifacts) escape every text run
they draw. A document that reaches storage is therefore already known-safe and
known-renderable.

One file is the source of truth for both stacks. ``editor_schema.json`` is read
here at runtime and read again by the dashboard's Tiptap parity test, so the
browser cannot produce a node the server would reject.

Everything in this module is a pure function of its arguments: no database, no
settings, no clock.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

EDITOR_SCHEMA_VERSION = "icaap-editor-v1"
_SCHEMA_PATH = Path(__file__).parent / "editor_schema.json"

#: Node keys a client may send. Anything else is ``unknown_key`` — a document
#: carrying, say, ``"html"`` alongside ``"text"`` is a smell, not a stray field.
_NODE_KEYS = frozenset({"type", "attrs", "content", "marks"})
_TEXT_KEYS = frozenset({"type", "text", "marks"})
_MARK_KEYS = frozenset({"type"})

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: Characters a filing document may not carry: NUL and the C0 controls, except
#: tab. A newline is a ``hardBreak`` node, never a character inside a text run.
_FORBIDDEN_TEXT = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


class ProseMirrorValidationError(ValueError):
    """A document the editor grammar refuses, with the path that refused it."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{message} (at {path or 'doc'})")
        self.code = code
        self.path = path
        self.message = message


@dataclass(frozen=True)
class AttrSpec:
    name: str
    kind: str
    required: bool
    default: Any
    enum: tuple[Any, ...] | None
    minimum: int | None
    maximum: int | None
    pattern: re.Pattern[str] | None


@dataclass(frozen=True)
class _Term:
    """One term of a content expression: a node name or a group name."""

    name: str
    star: bool


@dataclass(frozen=True)
class ContentSpec:
    expression: str | None
    terms: tuple[_Term, ...]

    @property
    def leaf(self) -> bool:
        return self.expression is None


@dataclass(frozen=True)
class NodeSpec:
    name: str
    content: ContentSpec
    #: Several groups are allowed because ProseMirror writes them as one
    #: space-separated string ("block list"), and a matcher that compared the
    #: whole string would silently stop matching "block".
    groups: frozenset[str]
    leaf: bool
    inline: bool
    atom: bool
    attrs: Mapping[str, AttrSpec]


@dataclass(frozen=True)
class Limits:
    max_doc_bytes: int
    max_depth: int
    max_nodes: int
    max_text_length: int


@dataclass(frozen=True)
class EditorSchema:
    schema_version: str
    top_node: str
    nodes: Mapping[str, NodeSpec]
    marks: frozenset[str]
    limits: Limits


def _parse_attrs(raw: Mapping[str, Any], node: str) -> Mapping[str, AttrSpec]:
    specs: dict[str, AttrSpec] = {}
    for name, body in raw.items():
        pattern = body.get("pattern")
        enum = body.get("enum")
        specs[name] = AttrSpec(
            name=name,
            kind=str(body["type"]),
            required=bool(body.get("required", False)),
            default=body.get("default"),
            enum=tuple(enum) if enum is not None else None,
            minimum=body.get("minimum"),
            maximum=body.get("maximum"),
            pattern=re.compile(pattern) if pattern else None,
        )
        if specs[name].required and "default" in body:
            msg = f"editor_schema.json: {node}.{name} is both required and defaulted"
            raise ValueError(msg)
    return specs


def _parse_content(expression: str | None) -> ContentSpec:
    """Parse the small subset of ProseMirror content expressions we allow.

    A sequence of terms, each a node or group name with an optional ``*`` or
    ``+`` quantifier. ``+`` is expanded to one mandatory term plus a star term,
    so the matcher only ever deals with "exactly one" and "any number".
    """
    if expression is None:
        return ContentSpec(None, ())
    terms: list[_Term] = []
    for token in expression.split():
        if token.endswith("*"):
            terms.append(_Term(token[:-1], star=True))
        elif token.endswith("+"):
            terms.append(_Term(token[:-1], star=False))
            terms.append(_Term(token[:-1], star=True))
        else:
            terms.append(_Term(token, star=False))
    return ContentSpec(expression, tuple(terms))


def _parse_schema(raw: Mapping[str, Any]) -> EditorSchema:
    nodes = {
        name: NodeSpec(
            name=name,
            content=_parse_content(body["content"]),
            groups=frozenset(body["groups"]),
            leaf=bool(body["leaf"]),
            inline=bool(body["inline"]),
            atom=bool(body["atom"]),
            attrs=_parse_attrs(body["attrs"], name),
        )
        for name, body in raw["nodes"].items()
    }
    limits = raw["limits"]
    return EditorSchema(
        schema_version=str(raw["schema_version"]),
        top_node=str(raw["top_node"]),
        nodes=nodes,
        marks=frozenset(raw["marks"]),
        limits=Limits(
            max_doc_bytes=int(limits["max_doc_bytes"]),
            max_depth=int(limits["max_depth"]),
            max_nodes=int(limits["max_nodes"]),
            max_text_length=int(limits["max_text_length"]),
        ),
    )


@functools.cache
def load_editor_schema() -> EditorSchema:
    """The parsed allowlist. Cached: the file ships inside the image."""
    return _parse_schema(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def editor_schema_source() -> str:
    """The raw JSON, for the contract tests that compare both stacks."""
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def canonical_json(payload: Any) -> str:
    """Byte-identical to ``services/attestation/digests.canonical_json``."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _fail(code: str, path: str, message: str) -> ProseMirrorValidationError:
    return ProseMirrorValidationError(code, path, message)


#: ``type(x) is int`` rather than ``isinstance``: ``bool`` is an ``int`` in
#: Python, and ``{"level": true}`` is not a heading level.
_ATTR_SHAPES: Mapping[str, tuple[Callable[[Any], bool], str]] = {
    "integer": (lambda value: type(value) is int, "an integer"),
    "string": (lambda value: isinstance(value, str), "a string"),
    "string_or_null": (
        lambda value: value is None or isinstance(value, str),
        "a string or null",
    ),
    "uuid": (
        lambda value: isinstance(value, str) and _UUID_RE.match(value) is not None,
        "a lowercase UUID",
    ),
    "uuid_or_null": (
        lambda value: (
            value is None or (isinstance(value, str) and _UUID_RE.match(value) is not None)
        ),
        "a lowercase UUID or null",
    ),
}


def _check_attr(spec: AttrSpec, value: Any, path: str) -> Any:
    where = f"{path}.attrs.{spec.name}"
    shape = _ATTR_SHAPES.get(spec.kind)
    if shape is None:  # pragma: no cover - guarded by the schema-file contract test
        raise _fail("bad_attr", where, f"unsupported attribute type {spec.kind!r}")
    accepts, description = shape
    if not accepts(value):
        raise _fail("bad_attr", where, f"{spec.name} must be {description}")
    if spec.minimum is not None and value < spec.minimum:
        raise _fail("bad_attr", where, f"{spec.name} is below {spec.minimum}")
    if spec.maximum is not None and value > spec.maximum:
        raise _fail("bad_attr", where, f"{spec.name} is above {spec.maximum}")
    if spec.enum is not None and value not in spec.enum:
        raise _fail("bad_attr", where, f"{spec.name} is not one of {list(spec.enum)}")
    if spec.pattern is not None and isinstance(value, str) and not spec.pattern.match(value):
        raise _fail("bad_attr", where, f"{spec.name} does not match {spec.pattern.pattern}")
    return value


def _canonical_attrs(spec: NodeSpec, raw: Any, path: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _fail("bad_attr", f"{path}.attrs", "attrs must be an object")
    unknown = set(raw) - set(spec.attrs)
    if unknown:
        raise _fail(
            "unknown_key",
            f"{path}.attrs",
            f"{spec.name} does not accept the attribute(s) {sorted(unknown)}",
        )
    canonical: dict[str, Any] = {}
    for name, attr in spec.attrs.items():
        if name in raw:
            canonical[name] = _check_attr(attr, raw[name], path)
        elif attr.required:
            raise _fail("missing_attr", f"{path}.attrs.{name}", f"{spec.name} requires {name}")
        else:
            canonical[name] = attr.default
    return canonical


def _canonical_marks(schema: EditorSchema, raw: Any, path: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _fail("unknown_mark", f"{path}.marks", "marks must be a list")
    seen: set[str] = set()
    marks: list[dict[str, Any]] = []
    for index, mark in enumerate(raw):
        where = f"{path}.marks[{index}]"
        if not isinstance(mark, dict):
            raise _fail("unknown_mark", where, "a mark must be an object")
        # The TYPE is checked first: a disallowed mark that also carries attrs
        # (a link is the obvious one) must report what it really is.
        name = mark.get("type")
        if not isinstance(name, str) or name not in schema.marks:
            raise _fail("unknown_mark", where, f"mark {name!r} is not allowed")
        extra = set(mark) - _MARK_KEYS
        if extra:
            raise _fail("unknown_key", where, f"a mark carries no {sorted(extra)}")
        if name in seen:
            raise _fail("unknown_mark", where, f"mark {name!r} is repeated")
        seen.add(name)
        marks.append({"type": name})
    # Sorted so two documents that differ only in mark order digest identically.
    return sorted(marks, key=lambda m: m["type"])


def _matches(schema: EditorSchema, term: _Term, node_type: str) -> bool:
    if term.name == node_type:
        return True
    spec = schema.nodes.get(node_type)
    return spec is not None and term.name in spec.groups


def _content_accepted(schema: EditorSchema, spec: ContentSpec, child_types: Sequence[str]) -> bool:
    """NFA match of ``child_types`` against the term sequence.

    States are term indices; a star term may be re-entered or skipped. Linear in
    children x terms, so a 20 000-node document cannot make this backtrack.
    """

    def closure(states: set[int]) -> set[int]:
        out: set[int] = set()
        stack = list(states)
        while stack:
            index = stack.pop()
            if index in out:
                continue
            out.add(index)
            if index < len(spec.terms) and spec.terms[index].star:
                stack.append(index + 1)
        return out

    states = closure({0})
    for node_type in child_types:
        nxt: set[int] = set()
        for index in states:
            if index >= len(spec.terms):
                continue
            term = spec.terms[index]
            if _matches(schema, term, node_type):
                nxt.add(index if term.star else index + 1)
        if not nxt:
            return False
        states = closure(nxt)
    return len(spec.terms) in states


@dataclass
class _Budget:
    nodes: int = 0


def _canonical_node(  # noqa: PLR0912, PLR0915 - one branch per refusal, deliberately flat
    schema: EditorSchema, raw: Any, path: str, depth: int, budget: _Budget
) -> dict[str, Any]:
    limits = schema.limits
    if depth > limits.max_depth:
        raise _fail("too_deep", path, f"nesting is deeper than {limits.max_depth}")
    budget.nodes += 1
    if budget.nodes > limits.max_nodes:
        raise _fail("too_many_nodes", path, f"more than {limits.max_nodes} nodes")
    if not isinstance(raw, dict):
        raise _fail("not_an_object", path, "a node must be an object")
    node_type = raw.get("type")
    if not isinstance(node_type, str) or node_type not in schema.nodes:
        raise _fail("unknown_node", path, f"node {node_type!r} is not allowed")
    spec = schema.nodes[node_type]

    if node_type == "text":
        extra = set(raw) - _TEXT_KEYS
        if extra:
            raise _fail("unknown_key", path, f"a text node carries no {sorted(extra)}")
        text = raw.get("text")
        if not isinstance(text, str) or text == "":
            raise _fail("bad_text", path, "a text node needs non-empty text")
        if len(text) > limits.max_text_length:
            raise _fail("too_large", path, f"text is longer than {limits.max_text_length}")
        if _FORBIDDEN_TEXT.search(text):
            raise _fail("bad_text", path, "text carries a control character")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
            raise _fail("bad_text", path, "text carries an unpaired surrogate")
        canonical: dict[str, Any] = {"type": "text", "text": text}
        marks = _canonical_marks(schema, raw.get("marks"), path)
        if marks:
            canonical["marks"] = marks
        return canonical

    extra = set(raw) - _NODE_KEYS
    if extra:
        raise _fail("unknown_key", path, f"{node_type} carries no {sorted(extra)}")
    if raw.get("marks"):
        raise _fail("unknown_mark", f"{path}.marks", f"{node_type} cannot carry marks")

    canonical = {"type": node_type}
    attrs = _canonical_attrs(spec, raw.get("attrs"), path)
    if attrs:
        canonical["attrs"] = attrs

    children_raw = raw.get("content")
    if spec.content.leaf:
        if children_raw:
            raise _fail("bad_content", path, f"{node_type} holds no content")
        return canonical
    if children_raw is None:
        children_raw = []
    if not isinstance(children_raw, list):
        raise _fail("bad_content", f"{path}.content", "content must be a list")
    children = [
        _canonical_node(schema, child, f"{path}.content[{index}]", depth + 1, budget)
        for index, child in enumerate(children_raw)
    ]
    if not _content_accepted(schema, spec.content, [child["type"] for child in children]):
        raise _fail(
            "bad_content",
            f"{path}.content",
            f"{node_type} requires content matching {spec.content.expression!r}",
        )
    if children:
        canonical["content"] = children
    return canonical


def validate_doc(doc: object, schema: EditorSchema | None = None) -> dict[str, Any]:
    """Return the canonical document, or raise :class:`ProseMirrorValidationError`.

    Canonical means: defaults filled in, empty ``attrs``/``content``/``marks``
    dropped, marks sorted. Two documents that a user would call identical
    therefore produce the same ``canonical_digest``, which is what makes a
    re-commit of unchanged text a ``no_changes`` refusal rather than a new
    immutable version.
    """
    schema = schema or load_editor_schema()
    if not isinstance(doc, dict):
        raise _fail("not_an_object", "", "a document must be a JSON object")
    if doc.get("type") != schema.top_node:
        raise _fail("not_a_doc", "", f"the top node must be {schema.top_node!r}")
    canonical = _canonical_node(schema, doc, "", 0, _Budget())
    encoded = canonical_json(canonical)
    if len(encoded.encode("utf-8")) > schema.limits.max_doc_bytes:
        raise _fail("too_large", "", f"the document exceeds {schema.limits.max_doc_bytes} bytes")
    return canonical


def canonical_digest(doc: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON — the value-based section digest."""
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# References, plain text, render IR
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactRef:
    block_id: str
    fact_key: str
    suggestion_id: str | None = None


@dataclass(frozen=True)
class DocRefs:
    block_ids: frozenset[str]
    fact_refs: tuple[FactRef, ...]


def _walk(node: Mapping[str, Any]):
    yield node
    for child in node.get("content", ()) or ():
        yield from _walk(child)


def collect_refs(doc: Mapping[str, Any]) -> DocRefs:
    """Every block a document embeds or quotes a figure from."""
    blocks: set[str] = set()
    facts: list[FactRef] = []
    for node in _walk(doc):
        attrs = node.get("attrs") or {}
        if node.get("type") == "dataBlock":
            blocks.add(str(attrs["blockId"]))
        elif node.get("type") == "factRef":
            blocks.add(str(attrs["blockId"]))
            facts.append(
                FactRef(
                    block_id=str(attrs["blockId"]),
                    fact_key=str(attrs["factKey"]),
                    suggestion_id=attrs.get("suggestionId"),
                )
            )
    return DocRefs(block_ids=frozenset(blocks), fact_refs=tuple(facts))


def _inline_text(
    nodes: Sequence[Mapping[str, Any]],
    fact_text: Callable[[FactRef], str] | None,
) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node.get("type")
        if kind == "text":
            out.append(str(node.get("text", "")))
        elif kind == "hardBreak":
            out.append("\n")
        elif kind == "factRef":
            attrs = node.get("attrs") or {}
            ref = FactRef(
                block_id=str(attrs["blockId"]),
                fact_key=str(attrs["factKey"]),
                suggestion_id=attrs.get("suggestionId"),
            )
            out.append(fact_text(ref) if fact_text else f"[[fact:{ref.fact_key}]]")
    return "".join(out)


def _block_text(  # noqa: PLR0911 - one return per block kind reads better than a map
    node: Mapping[str, Any],
    fact_text: Callable[[FactRef], str] | None,
    block_text: Callable[[str], str] | None,
) -> str:
    kind = node.get("type")
    children = node.get("content", []) or []
    if kind in {"paragraph", "heading"}:
        return _inline_text(children, fact_text)
    if kind == "blockquote":
        inner = "\n\n".join(_block_text(child, fact_text, block_text) for child in children)
        return "\n".join(f"> {line}" for line in inner.splitlines())
    if kind in {"bulletList", "orderedList"}:
        start = int((node.get("attrs") or {}).get("start") or 1)
        lines: list[str] = []
        for index, item in enumerate(children):
            marker = "- " if kind == "bulletList" else f"{start + index}. "
            body = "\n\n".join(
                _block_text(child, fact_text, block_text) for child in item.get("content", []) or []
            )
            lines.append(marker + body)
        return "\n".join(lines)
    if kind == "dataBlock":
        block_id = str((node.get("attrs") or {})["blockId"])
        return block_text(block_id) if block_text else "[[block]]"
    if kind == "listItem":  # pragma: no cover - only reached through a list
        return "\n\n".join(_block_text(child, fact_text, block_text) for child in children)
    return ""  # pragma: no cover - the grammar admits no other block


def plain_text(
    doc: Mapping[str, Any],
    *,
    fact_text: Callable[[FactRef], str] | None = None,
    block_text: Callable[[str], str] | None = None,
) -> str:
    """A readable, searchable rendering of the document — never markup."""
    return "\n\n".join(
        _block_text(child, fact_text, block_text) for child in doc.get("content", []) or []
    )


def has_content(doc: Mapping[str, Any]) -> bool:
    """True when the section says something: text, a figure, or a table."""
    for node in _walk(doc):
        kind = node.get("type")
        if kind == "text" and str(node.get("text", "")).strip():
            return True
        if kind in {"factRef", "dataBlock"}:
            return True
    return False


ParagraphStyle = Literal["body", "h2", "h3", "h4"]


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    fact: FactRef | None = None
    line_break: bool = False


@dataclass(frozen=True)
class ParagraphBlock:
    runs: tuple[Run, ...]
    style: ParagraphStyle


@dataclass(frozen=True)
class QuoteBlock:
    children: tuple[RenderBlock, ...]


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    start: int
    items: tuple[tuple[RenderBlock, ...], ...]


@dataclass(frozen=True)
class DataBlockRef:
    block_id: str


RenderBlock = ParagraphBlock | QuoteBlock | ListBlock | DataBlockRef

_HEADING_STYLES: Mapping[int, ParagraphStyle] = {2: "h2", 3: "h3", 4: "h4"}


def _runs(nodes: Sequence[Mapping[str, Any]]) -> tuple[Run, ...]:
    runs: list[Run] = []
    for node in nodes:
        kind = node.get("type")
        if kind == "text":
            marks = {mark["type"] for mark in node.get("marks", []) or []}
            runs.append(
                Run(
                    text=str(node.get("text", "")),
                    bold="bold" in marks,
                    italic="italic" in marks,
                    underline="underline" in marks,
                )
            )
        elif kind == "hardBreak":
            runs.append(Run(text="", line_break=True))
        elif kind == "factRef":
            attrs = node.get("attrs") or {}
            runs.append(
                Run(
                    text="",
                    fact=FactRef(
                        block_id=str(attrs["blockId"]),
                        fact_key=str(attrs["factKey"]),
                        suggestion_id=attrs.get("suggestionId"),
                    ),
                )
            )
    return tuple(runs)


def _render_block(node: Mapping[str, Any]) -> RenderBlock | None:
    kind = node.get("type")
    children = node.get("content", []) or []
    if kind == "paragraph":
        return ParagraphBlock(runs=_runs(children), style="body")
    if kind == "heading":
        level = int((node.get("attrs") or {})["level"])
        return ParagraphBlock(runs=_runs(children), style=_HEADING_STYLES[level])
    if kind == "blockquote":
        return QuoteBlock(children=tuple(_render_blocks(children)))
    if kind in {"bulletList", "orderedList"}:
        start = int((node.get("attrs") or {}).get("start") or 1)
        return ListBlock(
            ordered=kind == "orderedList",
            start=start,
            items=tuple(tuple(_render_blocks(item.get("content", []) or [])) for item in children),
        )
    if kind == "dataBlock":
        return DataBlockRef(block_id=str((node.get("attrs") or {})["blockId"]))
    return None  # pragma: no cover - the grammar admits no other block


def _render_blocks(nodes: Sequence[Mapping[str, Any]]) -> list[RenderBlock]:
    return [block for node in nodes if (block := _render_block(node)) is not None]


def to_render_blocks(doc: Mapping[str, Any]) -> tuple[RenderBlock, ...]:
    """The renderer-neutral intermediate form the PDF and DOCX writers consume.

    No reportlab or python-docx type appears here on purpose: P1's draft
    exports and P3's filing artifacts render the same IR, so they cannot drift
    apart in what they show.
    """
    return tuple(_render_blocks(doc.get("content", []) or []))


EMPTY_DOC: dict[str, Any] = {"type": "doc", "content": []}

__all__ = [
    "EDITOR_SCHEMA_VERSION",
    "EMPTY_DOC",
    "DataBlockRef",
    "DocRefs",
    "EditorSchema",
    "FactRef",
    "ListBlock",
    "ParagraphBlock",
    "ProseMirrorValidationError",
    "QuoteBlock",
    "RenderBlock",
    "Run",
    "canonical_digest",
    "canonical_json",
    "collect_refs",
    "editor_schema_source",
    "has_content",
    "load_editor_schema",
    "plain_text",
    "to_render_blocks",
    "validate_doc",
]
