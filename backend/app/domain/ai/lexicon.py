"""The grounding lexicon: the word lists the validator matches against.

The lists are DATA (``lexicon.json``) rather than code, for two reasons. The
governed-literals scan reads ``.py`` files, and a regulatory term that happens
to contain a digit ("Pillar 2", "CET1") is not a tunable — it is vocabulary,
and vocabulary belongs beside the framework text it comes from.

The file is jurisdiction-neutral on purpose: currency codes, central-bank names
and country names for the tenant's own registry are merged in at runtime by
``app/services/ai/grounding.py`` from the global ``jurisdictions`` table, so
adding a country never means editing this file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_LEXICON_PATH = Path(__file__).parent / "lexicon.json"
_SCHEMA = "ai-grounding-lexicon-v1"


class LexiconError(ValueError):
    """The shipped lexicon is not usable — a packaging fault, not a user error."""


@dataclass(frozen=True)
class Lexicon:
    """Word lists and compiled patterns, loaded once per process."""

    #: Regulatory terms that may contain a digit ("Pillar 2", "CET1").
    allowed_terms: tuple[str, ...]
    #: Phrases whose number word is idiomatic ("three lines of defence").
    allowed_number_word_phrases: tuple[str, ...]
    number_words: frozenset[str]
    percent_words: tuple[str, ...]
    currency_symbols: frozenset[str]
    currency_symbol_tokens: tuple[str, ...]
    currency_codes: frozenset[str]
    honorifics: tuple[str, ...]
    advice_patterns: tuple[re.Pattern[str], ...]
    url_patterns: tuple[re.Pattern[str], ...]
    email_pattern: re.Pattern[str]
    markup_patterns: tuple[re.Pattern[str], ...]
    person_pattern: re.Pattern[str]
    #: Longest-first so "percentage points" masks before "percentage point".
    maskable_terms: tuple[str, ...]


def _strings(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"lexicon.{key} must be a list of strings"
        raise LexiconError(message)
    items: list[str] = [item for item in value if isinstance(item, str)]
    if len(set(items)) != len(items):
        message = f"lexicon.{key} contains duplicates"
        raise LexiconError(message)
    return tuple(items)


def _compile(patterns: tuple[str, ...], key: str) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:  # pragma: no cover - a packaging fault
            message = f"lexicon.{key} has an invalid pattern: {pattern!r}"
            raise LexiconError(message) from exc
    return tuple(compiled)


def _parse(raw: dict[str, Any]) -> Lexicon:
    if raw.get("schema") != _SCHEMA:
        message = f"lexicon schema must be {_SCHEMA!r}"
        raise LexiconError(message)
    allowed_terms = _strings(raw, "allowed_terms")
    allowed_phrases = _strings(raw, "allowed_number_word_phrases")
    percent_words = _strings(raw, "percent_words")
    symbol_tokens = _strings(raw, "currency_symbol_tokens")
    honorifics = _strings(raw, "honorifics")
    email = raw.get("email_pattern")
    if not isinstance(email, str):
        message = "lexicon.email_pattern must be a string"
        raise LexiconError(message)
    # Longest first: masking "percentage points" before "percentage point"
    # stops the shorter phrase leaving a stray "s" that reads as prose.
    maskable = tuple(
        sorted(
            (*allowed_terms, *allowed_phrases),
            key=len,
            reverse=True,
        )
    )
    honorific_alternation = "|".join(re.escape(title) for title in honorifics)
    return Lexicon(
        allowed_terms=allowed_terms,
        allowed_number_word_phrases=allowed_phrases,
        number_words=frozenset(word.casefold() for word in _strings(raw, "number_words")),
        percent_words=percent_words,
        currency_symbols=frozenset(_strings(raw, "currency_symbols")),
        currency_symbol_tokens=symbol_tokens,
        currency_codes=frozenset(_strings(raw, "currency_codes")),
        honorifics=honorifics,
        advice_patterns=_compile(_strings(raw, "advice_patterns"), "advice_patterns"),
        url_patterns=_compile(_strings(raw, "url_patterns"), "url_patterns"),
        email_pattern=re.compile(email, re.IGNORECASE),
        markup_patterns=_compile(_strings(raw, "markup_patterns"), "markup_patterns"),
        # An honorific, an optional full stop, then a capitalised word: the
        # shape of a named individual. Names are never sent to the model, so
        # one appearing in the output is an invention by definition.
        person_pattern=re.compile(rf"\b(?:{honorific_alternation})\.?\s+[A-Z][a-zA-Z'-]+"),
        maskable_terms=maskable,
    )


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    """The shipped lexicon, parsed and compiled once."""
    raw = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):  # pragma: no cover - a packaging fault
        message = "lexicon.json must contain an object"
        raise LexiconError(message)
    return _parse(raw)


def lexicon_source() -> str:
    """The raw file, for the parity/neutrality tests."""
    return _LEXICON_PATH.read_text(encoding="utf-8")


__all__ = ["Lexicon", "LexiconError", "load_lexicon", "lexicon_source"]
