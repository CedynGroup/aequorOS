"""The shared editor samples behave the same on the server as in the browser.

``tests/fixtures/icaap/editor_schema_samples.json`` is read by this file and by
the dashboard's Tiptap parity test. If the two schemas ever drift, one of the
two suites fails on a document the other accepts — which is the whole point of
having one fixture rather than two lists of examples.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.icaap import prosemirror as pm

SAMPLES_PATH = Path(__file__).parents[2] / "fixtures" / "icaap" / "editor_schema_samples.json"
SAMPLES: dict[str, Any] = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))


def test_the_fixture_is_for_this_version_of_the_grammar() -> None:
    assert SAMPLES["schema_version"] == pm.EDITOR_SCHEMA_VERSION


def test_the_fixture_covers_every_node_and_mark_the_grammar_allows() -> None:
    """A node nobody exercises is a node neither stack is checked on."""
    schema = pm.load_editor_schema()
    seen: set[str] = set()
    marks: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("type"), str):
            seen.add(node["type"])
        for mark in node.get("marks") or []:
            if isinstance(mark, dict) and isinstance(mark.get("type"), str):
                marks.add(mark["type"])
        for child in node.get("content") or []:
            walk(child)

    for sample in SAMPLES["valid"]:
        walk(sample["doc"])
    assert seen == set(schema.nodes) - {"doc"} | {"doc"}
    assert marks == set(schema.marks)


@pytest.mark.parametrize(
    "sample", SAMPLES["valid"], ids=[sample["name"] for sample in SAMPLES["valid"]]
)
def test_every_valid_sample_is_accepted(sample: dict[str, Any]) -> None:
    canonical = pm.validate_doc(sample["doc"])
    assert pm.validate_doc(canonical) == canonical


@pytest.mark.parametrize(
    "sample", SAMPLES["invalid"], ids=[sample["name"] for sample in SAMPLES["invalid"]]
)
def test_every_invalid_sample_is_refused_with_its_recorded_code(sample: dict[str, Any]) -> None:
    with pytest.raises(pm.ProseMirrorValidationError) as caught:
        pm.validate_doc(sample["doc"])
    assert caught.value.code == sample["code"]


@pytest.mark.parametrize(
    "sample",
    SAMPLES["service_invalid"],
    ids=[sample["name"] for sample in SAMPLES["service_invalid"]],
)
def test_the_service_only_samples_are_grammatical(sample: dict[str, Any]) -> None:
    """These are well-formed documents that only a service can judge.

    A figure reference to a block this ICAAP does not hold is perfectly legal
    grammar; it takes the cycle's own block list to know it is wrong. Keeping
    them here as a separate list stops anyone "fixing" the grammar to catch
    something the grammar cannot see.
    """
    pm.validate_doc(sample["doc"])
    assert sample["code"] in {"unknown_block", "unknown_fact"}
