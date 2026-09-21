"""The validator never crashes, and never lets an un-allowlisted node through.

A section editor is an attacker-reachable parser: whatever the browser sends
lands here first. Example-based tests cover the shapes we thought of; these
properties cover the ones we did not. The contract is narrow on purpose —
``validate_doc`` either returns a canonical document made only of allowlisted
parts, or raises ``ProseMirrorValidationError``. A ``KeyError`` escaping would
be a 500 where a 422 belongs.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.domain.icaap import prosemirror as pm

_SCHEMA = pm.load_editor_schema()
_NODE_NAMES = sorted(_SCHEMA.nodes)
_MARK_NAMES = sorted(_SCHEMA.marks)
_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: Node and mark names drawn from a SUPERSET of the allowlist: the interesting
#: inputs are the ones that look almost right.
_ANY_NODE = st.sampled_from([*_NODE_NAMES, "image", "table", "codeBlock", "script", "", "doc "])
_ANY_MARK = st.sampled_from([*_MARK_NAMES, "link", "code", "strike", ""])

_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-5, max_value=5),
    st.floats(allow_nan=False, allow_infinity=False, width=16),
    st.text(max_size=8),
)


def _arbitrary_nodes(depth: int = 3) -> st.SearchStrategy[Any]:
    leaves = st.one_of(
        _SCALARS,
        st.fixed_dictionaries({"type": _ANY_NODE}),
        st.fixed_dictionaries({"type": st.just("text"), "text": st.text(max_size=8)}),
    )
    return st.recursive(
        leaves,
        lambda children: st.one_of(
            st.lists(children, max_size=3),
            st.fixed_dictionaries(
                {"type": _ANY_NODE},
                optional={
                    "content": st.lists(children, max_size=3),
                    "attrs": st.dictionaries(
                        st.sampled_from(["level", "start", "type", "blockId", "factKey", "x"]),
                        _SCALARS,
                        max_size=3,
                    ),
                    "marks": st.lists(st.fixed_dictionaries({"type": _ANY_MARK}), max_size=2),
                    "text": st.text(max_size=8),
                },
            ),
        ),
        max_leaves=12,
    ).filter(lambda _value: depth > 0)


def _allowlisted(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("type") not in _SCHEMA.nodes:
        return False
    spec = _SCHEMA.nodes[node["type"]]
    if set(node.get("attrs") or {}) - set(spec.attrs):
        return False
    for mark in node.get("marks") or []:
        if mark.get("type") not in _SCHEMA.marks:
            return False
    return all(_allowlisted(child) for child in node.get("content") or [])


@given(payload=st.one_of(_arbitrary_nodes(), st.text(), st.integers(), st.lists(st.integers())))
@_SETTINGS
def test_validation_either_canonicalises_or_refuses_cleanly(payload: Any) -> None:
    try:
        canonical = pm.validate_doc(payload)
    except pm.ProseMirrorValidationError as exc:
        assert exc.code
        assert isinstance(exc.path, str)
        return
    assert _allowlisted(canonical)
    # Anything it returns must be renderable and digestible without raising.
    pm.plain_text(canonical)
    pm.collect_refs(canonical)
    pm.to_render_blocks(canonical)
    pm.canonical_digest(canonical)


@given(payload=st.one_of(st.text(), st.integers(), st.floats(allow_nan=False), st.none()))
@_SETTINGS
def test_a_non_document_is_always_refused(payload: Any) -> None:
    try:
        pm.validate_doc(payload)
    except pm.ProseMirrorValidationError as exc:
        assert exc.code in {"not_an_object", "not_a_doc"}
    else:  # pragma: no cover - a scalar is never a document
        raise AssertionError("a scalar was accepted as a document")


_BLOCK_ID = "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5b"


def _text_node() -> st.SearchStrategy[dict[str, Any]]:
    return st.builds(
        lambda body, marks: (
            {"type": "text", "text": body, "marks": [{"type": m} for m in marks]}
            if marks
            else {"type": "text", "text": body}
        ),
        st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126),
            min_size=1,
            max_size=20,
        ),
        st.lists(st.sampled_from(_MARK_NAMES), max_size=3, unique=True),
    )


def _inline() -> st.SearchStrategy[dict[str, Any]]:
    return st.one_of(
        _text_node(),
        st.just({"type": "hardBreak"}),
        st.just({"type": "factRef", "attrs": {"blockId": _BLOCK_ID, "factKey": "car_pct"}}),
    )


def _paragraph() -> st.SearchStrategy[dict[str, Any]]:
    return st.builds(
        lambda content: {"type": "paragraph", "content": content},
        st.lists(_inline(), max_size=4),
    )


def _block() -> st.SearchStrategy[dict[str, Any]]:
    return st.one_of(
        _paragraph(),
        st.builds(
            lambda level, content: {
                "type": "heading",
                "attrs": {"level": level},
                "content": content,
            },
            st.sampled_from([2, 3, 4]),
            st.lists(_inline(), max_size=3),
        ),
        st.builds(
            lambda items: {
                "type": "bulletList",
                "content": [{"type": "listItem", "content": [p]} for p in items],
            },
            st.lists(_paragraph(), min_size=1, max_size=3),
        ),
        st.builds(
            lambda content: {"type": "blockquote", "content": content},
            st.lists(_paragraph(), min_size=1, max_size=2),
        ),
        st.just({"type": "dataBlock", "attrs": {"blockId": _BLOCK_ID}}),
    )


@given(content=st.lists(_block(), max_size=5))
@_SETTINGS
def test_a_grammatical_document_is_always_accepted_and_stable(
    content: list[dict[str, Any]],
) -> None:
    document = {"type": "doc", "content": content}
    canonical = pm.validate_doc(document)
    assert pm.validate_doc(canonical) == canonical
    assert pm.canonical_digest(pm.validate_doc(canonical)) == pm.canonical_digest(canonical)
    pm.plain_text(canonical)
