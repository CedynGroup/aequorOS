"""The editor grammar: what it accepts, what it refuses, and what it derives.

The refusals matter more than the acceptances. This validator is the only thing
standing between a browser and a document that a PDF renderer, a DOCX renderer
and a signature digest all have to agree about, and the editor stores JSON
precisely so that "sanitise the HTML" is never a question anyone has to answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.icaap import prosemirror as pm

BLOCK_ID = "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5b"
OTHER_ID = "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5c"
SUGGESTION_ID = "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5d"


def doc(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(content)}


def para(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(content)}


def text(value: str, *marks: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = [{"type": mark} for mark in marks]
    return node


def fact(block_id: str = BLOCK_ID, key: str = "car_pct") -> dict[str, Any]:
    return {"type": "factRef", "attrs": {"blockId": block_id, "factKey": key}}


def block(block_id: str = BLOCK_ID) -> dict[str, Any]:
    return {"type": "dataBlock", "attrs": {"blockId": block_id}}


def item(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "listItem", "content": list(content)}


def _code(payload: object) -> str:
    with pytest.raises(pm.ProseMirrorValidationError) as caught:
        pm.validate_doc(payload)
    return caught.value.code


# --- acceptance ------------------------------------------------------------


def test_an_empty_document_is_a_section_nobody_has_written_yet() -> None:
    assert pm.validate_doc({"type": "doc", "content": []}) == {"type": "doc"}
    assert pm.validate_doc({"type": "doc"}) == {"type": "doc"}


@pytest.mark.parametrize("level", [2, 3, 4])
def test_headings_two_to_four_are_the_section_body(level: int) -> None:
    pm.validate_doc(doc({"type": "heading", "attrs": {"level": level}, "content": [text("H")]}))


@pytest.mark.parametrize("mark", ["bold", "italic", "underline"])
def test_the_three_marks_a_report_needs(mark: str) -> None:
    pm.validate_doc(doc(para(text("emphasis", mark))))


def test_lists_quotes_breaks_blocks_and_figures_all_parse() -> None:
    pm.validate_doc(
        doc(
            {"type": "bulletList", "content": [item(para(text("one")))]},
            {
                "type": "orderedList",
                "attrs": {"start": 4},
                "content": [item(para(text("four")))],
            },
            {"type": "blockquote", "content": [para(text("quoted"))]},
            para(text("line"), {"type": "hardBreak"}, text("break")),
            block(),
            para(fact()),
        )
    )


def test_a_list_item_may_hold_a_nested_list_after_its_paragraph() -> None:
    pm.validate_doc(
        doc(
            {
                "type": "bulletList",
                "content": [
                    item(
                        para(text("parent")),
                        {"type": "bulletList", "content": [item(para(text("child")))]},
                    )
                ],
            }
        )
    )


# --- refusals --------------------------------------------------------------


@pytest.mark.parametrize("node_type", ["image", "table", "codeBlock", "horizontalRule", "iframe"])
def test_a_node_outside_the_allowlist_is_refused(node_type: str) -> None:
    assert _code(doc({"type": node_type})) == "unknown_node"


@pytest.mark.parametrize("mark", ["link", "code", "strike", "highlight"])
def test_a_mark_outside_the_allowlist_is_refused(mark: str) -> None:
    assert _code(doc(para(text("x", mark)))) == "unknown_mark"


def test_a_mark_may_not_carry_attributes() -> None:
    node = para({"type": "text", "text": "x", "marks": [{"type": "bold", "attrs": {"x": 1}}]})
    assert _code(doc(node)) == "unknown_key"


def test_marks_belong_to_text_and_are_never_repeated() -> None:
    assert _code(doc({**para(text("x")), "marks": [{"type": "bold"}]})) == "unknown_mark"
    assert _code(doc(para(text("x", "bold", "bold")))) == "unknown_mark"


def test_a_document_is_an_object_whose_top_node_is_the_document() -> None:
    assert _code("<p>html</p>") == "not_an_object"
    assert _code(["doc"]) == "not_an_object"
    assert _code(42) == "not_an_object"
    assert _code(para(text("orphan"))) == "not_a_doc"


@pytest.mark.parametrize("level", [1, 5, "2", True, None])
def test_a_heading_level_outside_the_allowlist_is_refused(level: object) -> None:
    node = {"type": "heading", "attrs": {"level": level}, "content": [text("H")]}
    assert _code(doc(node)) == "bad_attr"


def test_a_heading_without_a_level_is_refused() -> None:
    assert _code(doc({"type": "heading", "content": [text("H")]})) == "missing_attr"


def test_a_block_reference_must_be_a_real_identifier() -> None:
    assert _code(doc({"type": "dataBlock"})) == "missing_attr"
    assert _code(doc({"type": "dataBlock", "attrs": {"blockId": "nope"}})) == "bad_attr"
    assert _code(doc(para(fact(key="Car Pct")))) == "bad_attr"


def test_unknown_keys_on_nodes_and_attributes_are_refused() -> None:
    assert _code(doc({**para(text("x")), "html": "<b>x</b>"})) == "unknown_key"
    assert _code(doc({**para(text("x")), "attrs": {"style": "color:red"}})) == "unknown_key"
    assert _code(doc(para({"type": "text", "text": "x", "id": 3}))) == "unknown_key"


@pytest.mark.parametrize(
    "value", ["", "with\x00nul", "line\nbreak", "bell\x07", "del\x7f", "\ud800"]
)
def test_text_that_cannot_be_rendered_is_refused(value: str) -> None:
    assert _code(doc(para({"type": "text", "text": value}))) == "bad_text"


def test_a_tab_is_ordinary_text() -> None:
    pm.validate_doc(doc(para(text("a\tb"))))


def test_content_that_breaks_the_grammar_is_refused() -> None:
    assert _code(doc(text("loose"))) == "bad_content"
    assert _code(doc({"type": "bulletList", "content": []})) == "bad_content"
    assert (
        _code(
            doc(
                {
                    "type": "bulletList",
                    "content": [item({"type": "bulletList", "content": [item(para(text("x")))]})],
                }
            )
        )
        == "bad_content"
    )
    assert _code(doc({**block(), "content": [para(text("x"))]})) == "bad_content"


def test_a_document_larger_than_the_limit_is_refused() -> None:
    schema = pm.load_editor_schema()
    long_text = "x" * (schema.limits.max_text_length + 1)
    assert _code(doc(para(text(long_text)))) == "too_large"
    paragraphs = [para(text("x" * 1000)) for _ in range(400)]
    assert _code(doc(*paragraphs)) == "too_large"


def test_a_document_nested_deeper_than_the_limit_is_refused() -> None:
    node: dict[str, Any] = para(text("deep"))
    for _ in range(pm.load_editor_schema().limits.max_depth + 2):
        node = {"type": "blockquote", "content": [node]}
    assert _code(doc(node)) in {"too_deep", "too_large"}


def test_a_document_with_too_many_nodes_is_refused() -> None:
    limit = pm.load_editor_schema().limits.max_nodes
    assert _code(doc(*[para(text("x")) for _ in range(limit)])) in {"too_many_nodes", "too_large"}


# --- canonical form and derivations ---------------------------------------


def test_defaults_are_filled_and_empties_dropped() -> None:
    canonical = pm.validate_doc(
        doc(
            {"type": "orderedList", "attrs": {}, "content": [item(para(text("x")))]},
            para(fact()),
        )
    )
    ordered = canonical["content"][0]
    assert ordered["attrs"] == {"start": 1, "type": None}
    paragraph = canonical["content"][1]
    assert paragraph["attrs"] == {"aiSuggestionId": None}
    assert paragraph["content"][0]["attrs"]["suggestionId"] is None
    assert "marks" not in paragraph["content"][0]


def test_validation_is_idempotent_and_the_digest_ignores_key_order() -> None:
    original = doc(para(text("x", "bold")))
    once = pm.validate_doc(original)
    assert pm.validate_doc(once) == once
    reordered = {"content": list(original["content"]), "type": "doc"}
    assert pm.canonical_digest(pm.validate_doc(reordered)) == pm.canonical_digest(once)


def test_mark_order_does_not_change_the_digest() -> None:
    one = pm.validate_doc(doc(para(text("x", "bold", "italic"))))
    two = pm.validate_doc(doc(para(text("x", "italic", "bold"))))
    assert pm.canonical_digest(one) == pm.canonical_digest(two)


def test_collect_refs_finds_every_block_and_figure() -> None:
    refs = pm.collect_refs(
        pm.validate_doc(
            doc(
                block(),
                para(fact(), fact(OTHER_ID, "cet1_ratio_pct")),
                {"type": "blockquote", "content": [para(fact(OTHER_ID, "tier1_ratio_pct"))]},
            )
        )
    )
    assert refs.block_ids == {BLOCK_ID, OTHER_ID}
    assert [(ref.block_id, ref.fact_key) for ref in refs.fact_refs] == [
        (BLOCK_ID, "car_pct"),
        (OTHER_ID, "cet1_ratio_pct"),
        (OTHER_ID, "tier1_ratio_pct"),
    ]


def test_plain_text_is_readable_and_substitutes_figures() -> None:
    document = pm.validate_doc(
        doc(
            {"type": "heading", "attrs": {"level": 2}, "content": [text("Capital")]},
            para(text("The ratio was "), fact(), text(".")),
            {"type": "bulletList", "content": [item(para(text("one"))), item(para(text("two")))]},
            {
                "type": "orderedList",
                "attrs": {"start": 3},
                "content": [item(para(text("third")))],
            },
            {"type": "blockquote", "content": [para(text("quoted"))]},
            block(),
        )
    )
    assert pm.plain_text(document) == (
        "Capital\n\nThe ratio was [[fact:car_pct]].\n\n- one\n- two\n\n3. third\n\n> quoted"
        "\n\n[[block]]"
    )
    rendered = pm.plain_text(
        document, fact_text=lambda ref: "15.42%", block_text=lambda _id: "[capital table]"
    )
    assert "15.42%" in rendered
    assert "[capital table]" in rendered


def test_has_content_distinguishes_writing_from_whitespace() -> None:
    assert pm.has_content(pm.validate_doc(doc(para(text("words"))))) is True
    assert pm.has_content(pm.validate_doc(doc(para(text("   "))))) is False
    assert pm.has_content(pm.validate_doc(doc(para(fact())))) is True
    assert pm.has_content(pm.validate_doc(doc(block()))) is True
    assert pm.has_content({"type": "doc", "content": []}) is False


def test_the_render_intermediate_form_keeps_marks_lists_and_figures() -> None:
    document = pm.validate_doc(
        doc(
            {"type": "heading", "attrs": {"level": 3}, "content": [text("Heading")]},
            para(text("bold", "bold"), {"type": "hardBreak"}, fact()),
            {
                "type": "orderedList",
                "attrs": {"start": 2},
                "content": [item(para(text("second")))],
            },
            {"type": "blockquote", "content": [para(text("quoted"))]},
            block(),
        )
    )
    heading, paragraph, ordered, quote, embedded = pm.to_render_blocks(document)
    assert isinstance(heading, pm.ParagraphBlock)
    assert heading.style == "h3"
    assert isinstance(paragraph, pm.ParagraphBlock)
    assert paragraph.runs[0].bold is True
    assert paragraph.runs[1].line_break is True
    assert paragraph.runs[2].fact == pm.FactRef(BLOCK_ID, "car_pct", None)
    assert isinstance(ordered, pm.ListBlock)
    assert (ordered.ordered, ordered.start) == (True, 2)
    assert isinstance(quote, pm.QuoteBlock)
    assert isinstance(embedded, pm.DataBlockRef)
    assert embedded.block_id == BLOCK_ID


def test_markup_characters_survive_as_text() -> None:
    """The renderers escape; the grammar does not strip."""
    document = pm.validate_doc(doc(para(text("<b>x</b> & <script>"))))
    assert pm.plain_text(document) == "<b>x</b> & <script>"


def test_the_ai_provenance_attribute_exists_from_the_first_release() -> None:
    """D-028: adopting an AI draft in P4 needs no document migration."""
    document = pm.validate_doc(
        doc({**para(text("drafted")), "attrs": {"aiSuggestionId": SUGGESTION_ID}})
    )
    assert document["content"][0]["attrs"]["aiSuggestionId"] == SUGGESTION_ID
