"""Converting a drafted paragraph into editor nodes."""

from __future__ import annotations

import pytest

from app.domain.icaap import ai_convert
from app.domain.icaap.ai_convert import FactBinding
from app.domain.icaap.prosemirror import ProseMirrorValidationError, validate_doc

_BLOCK = "0199a3c0-1111-7000-8000-000000000001"
_SUGGESTION = "0199a3c0-2222-7000-8000-000000000002"
_BINDINGS = {
    "capital_position.car_pct": FactBinding(block_id=_BLOCK, fact_key="car_pct", binding_seq=3)
}
_ENTITIES = {"bank": "Examplar Savings Bank", "as_of": "31 December 2025"}


def test_fact_placeholder_becomes_a_bound_reference_not_text() -> None:
    """The whole promise: the model's words, the platform's numbers."""
    nodes = ai_convert.to_paragraph_nodes(
        ["{{E:bank}} held {{F:capital_position.car_pct}} at {{E:as_of}}."],
        _BINDINGS,
        _ENTITIES,
        _SUGGESTION,
    )
    (paragraph,) = nodes
    kinds = [child["type"] for child in paragraph["content"]]
    assert kinds == ["text", "factRef", "text"]
    ref = paragraph["content"][1]
    assert ref["attrs"] == {
        "blockId": _BLOCK,
        "factKey": "car_pct",
        "suggestionId": _SUGGESTION,
    }


def test_entity_placeholder_is_merged_into_the_text_run() -> None:
    """A name is a word, not a live reference."""
    nodes = ai_convert.to_paragraph_nodes(
        ["{{E:bank}} is well capitalised."], {}, _ENTITIES, _SUGGESTION
    )
    assert nodes[0]["content"][0]["text"] == "Examplar Savings Bank is well capitalised."


def test_every_paragraph_carries_the_ai_marker() -> None:
    nodes = ai_convert.to_paragraph_nodes(["One.", "Two."], {}, {}, _SUGGESTION)
    assert all(node["attrs"]["aiSuggestionId"] == _SUGGESTION for node in nodes)


def test_converted_paragraphs_pass_the_editor_schema() -> None:
    doc = ai_convert.append_to_doc(
        {"type": "doc", "content": []},
        ai_convert.to_paragraph_nodes(
            ["{{E:bank}} held {{F:capital_position.car_pct}}."],
            _BINDINGS,
            _ENTITIES,
            _SUGGESTION,
        ),
    )
    validate_doc(doc)


def test_markup_in_model_output_stays_literal_text() -> None:
    """The validator rejects markup, but if one ever got through it is inert."""
    nodes = ai_convert.to_paragraph_nodes(["<b>x</b>"], {}, {}, _SUGGESTION)
    assert nodes[0]["content"][0]["text"] == "<b>x</b>"
    assert nodes[0]["content"][0]["type"] == "text"


def test_append_never_replaces_existing_content() -> None:
    """v1 never rewrites a paragraph a person already wrote."""
    existing = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Mine."}]}],
    }
    merged = ai_convert.append_to_doc(
        existing, ai_convert.to_paragraph_nodes(["Theirs."], {}, {}, _SUGGESTION)
    )
    assert merged["content"][0] == existing["content"][0]
    assert len(merged["content"]) == 2


def test_unknown_reference_is_a_programming_error_not_a_silent_drop() -> None:
    """By conversion time the draft has passed grounding, so this cannot happen."""
    with pytest.raises(KeyError):
        ai_convert.to_paragraph_nodes(["{{F:unknown.fact}}"], {}, {}, _SUGGESTION)
    with pytest.raises(KeyError):
        ai_convert.to_paragraph_nodes(["{{E:nobody}}"], {}, {}, _SUGGESTION)


def test_provenance_counts_only_marked_paragraphs() -> None:
    doc = ai_convert.append_to_doc(
        {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Mine."}]}],
        },
        ai_convert.to_paragraph_nodes(["Theirs.", "Also theirs."], {}, {}, _SUGGESTION),
    )
    assert ai_convert.count_paragraphs(doc) == 3
    assert ai_convert.count_ai_paragraphs(doc) == 2
    assert ai_convert.ai_paragraph_ids(doc) == (_SUGGESTION,)


def test_preview_shows_the_real_figure_and_its_freshness() -> None:
    segments = ai_convert.to_preview_segments(
        "{{E:bank}} held {{F:capital_position.car_pct}}.",
        _BINDINGS,
        _ENTITIES,
        {"capital_position.car_pct": ("15.42%", "fresh")},
    )
    assert segments[0].text == "Examplar Savings Bank held "
    assert segments[1].kind == "fact"
    assert segments[1].display == "15.42%"
    assert segments[1].status == "fresh"


def test_an_invalid_doc_still_fails_validation() -> None:
    with pytest.raises(ProseMirrorValidationError):
        validate_doc({"type": "doc", "content": [{"type": "nonsense"}]})
