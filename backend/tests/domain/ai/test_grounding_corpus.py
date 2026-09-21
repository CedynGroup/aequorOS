"""The grounding validator against its corpus.

This is the feature's central safety test. The corpus asserts the EXACT code set
per case, in both directions:

* a rejected case that stops producing its code means a rule has been weakened,
  and an ungrounded draft would reach a reviewer;
* an accepted case that starts producing one means the validator has become
  over-strict, and honest drafts would be silently refused — a failure mode that
  looks like "the AI never works" rather than like a bug.

Both are regressions. Never edit a case to make a change pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.ai import placeholders
from app.domain.ai.grounding import GroundingContext, Limits, validate
from app.domain.ai.lexicon import load_lexicon

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "ai" / "grounding_corpus.json"
_LIMITS = Limits(max_paragraphs=12, max_paragraph_chars=2000, max_open_questions=8)


def _corpus() -> dict[str, Any]:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _context(raw: dict[str, Any]) -> GroundingContext:
    return GroundingContext(
        facts=dict(raw["facts"]),
        entities=frozenset(raw["entities"]),
        requirement_ids=frozenset(raw["requirement_ids"]),
        deny_terms=frozenset(raw["tenant_terms"]),
        jurisdiction_terms=frozenset(raw["jurisdiction_terms"]),
        lexicon=load_lexicon(),
    )


_CORPUS = _corpus()
_CONTEXT = _context(_CORPUS["context"])
_CASES = _CORPUS["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[case["id"] for case in _CASES])
def test_corpus_case(case: dict[str, Any]) -> None:
    result = validate([(case["text"], case["requirement_ids"])], [], _CONTEXT, _LIMITS)
    expected = case["expect"]
    if expected == "accepted":
        assert result.ok, f"{case['id']} should be accepted, got {result.codes}"
        return
    assert not result.ok, f"{case['id']} should be rejected"
    assert set(result.codes) == set(expected), (
        f"{case['id']} expected {sorted(expected)}, got {sorted(result.codes)}"
    )


def test_every_documented_code_is_exercised() -> None:
    """A rule with no case is a rule nobody has checked fires."""
    documented = {
        "placeholder_malformed",
        "unknown_fact",
        "unsupported_fact",
        "unknown_entity",
        "digit",
        "number_word",
        "percent",
        "currency_symbol",
        "currency_code",
        "tenant_name",
        "jurisdiction_name",
        "person_reference",
        "url",
        "email",
        "advice_language",
        "markup",
        "empty_paragraph",
        "unknown_requirement",
    }
    covered: set[str] = set()
    for case in _CASES:
        if case["expect"] != "accepted":
            covered.update(case["expect"])
    assert documented <= covered, f"no corpus case covers {sorted(documented - covered)}"


def test_errors_never_quote_the_offending_text() -> None:
    """An error list is logged and shown; it must not carry ungrounded prose."""
    sentinel = "Examplar reported 15.4% growth"
    result = validate([(sentinel, [])], [], _CONTEXT, _LIMITS)
    assert not result.ok
    serialised = json.dumps(
        [{"code": e.code, "where": e.where, "span": e.span} for e in result.errors]
    )
    for fragment in ("Examplar", "15.4", "growth"):
        assert fragment not in serialised


def test_open_questions_are_validated_too() -> None:
    """An open question is shown to a person, so it is held to the same rules."""
    result = validate(
        [("{{E:bank}} maintains a capital plan.", [])],
        ["Please confirm the 2025 board minute reference."],
        _CONTEXT,
        _LIMITS,
    )
    assert not result.ok
    assert "digit" in result.codes
    assert any(error.where.startswith("open_question") for error in result.errors)


def test_shape_limits_come_from_settings_not_the_module() -> None:
    tight = Limits(max_paragraphs=1, max_paragraph_chars=20, max_open_questions=0)
    result = validate(
        [("{{E:bank}} is adequately capitalised and well governed.", []), ("Another.", [])],
        ["A question?"],
        _CONTEXT,
        tight,
    )
    assert {"too_many_paragraphs", "paragraph_too_long", "too_many_open_questions"} <= set(
        result.codes
    )


def test_no_paragraphs_is_refused() -> None:
    assert "no_paragraphs" in validate([], [], _CONTEXT, _LIMITS).codes


def test_validate_never_raises_on_arbitrary_text() -> None:
    """The validator runs on model output; it must be total, not defensive."""
    awkward = [
        "",
        "{{",
        "}}{{",
        "{{F:" + "a" * 500 + "}}",
        chr(0) + chr(1),
        "{{E:bank}}{{E:bank}}{{F:capital_position.car_pct}}",
        "ü" * 300,
    ]
    for text in awkward:
        validate([(text, [])], [], _CONTEXT, _LIMITS)


def test_placeholder_extraction_is_idempotent() -> None:
    text = "{{E:bank}} reported {{F:capital_position.car_pct}} at {{E:as_of}}."
    assert placeholders.extract(text) == placeholders.extract(text)
    rebuilt = "".join(
        segment.value if segment.kind == "text" else _token(segment)
        for segment in placeholders.segments(text)
    )
    assert rebuilt == text


def _token(segment: placeholders.Segment) -> str:
    prefix = "F" if segment.kind == "fact" else "E"
    return f"{{{{{prefix}:{segment.value}}}}}"
