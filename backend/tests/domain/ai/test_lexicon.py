"""The shipped lexicon: loadable, honest, and jurisdiction-neutral."""

from __future__ import annotations

import json
import re

from app.domain.ai.lexicon import lexicon_source, load_lexicon


def test_lexicon_loads_and_compiles() -> None:
    lexicon = load_lexicon()
    assert lexicon.allowed_terms
    assert lexicon.number_words
    assert lexicon.person_pattern.search("Dr Mensahx") is not None


def test_every_allowed_term_earns_its_place() -> None:
    """An allow-listed term exists to let a FIGURE through, nothing else.

    A term with no digit and no numeral would be allow-listing ordinary prose,
    which widens the masking pass for no reason. Roman numerals count: "Basel
    II" reads as a number to a person even though no rule fires on it, and the
    pair "Basel II"/"Basel III" belongs together as vocabulary.
    """
    lexicon = load_lexicon()
    for term in lexicon.allowed_terms:
        numeric = any(char.isdigit() for char in term)
        roman = bool(re.search(r"\b[IVX]+\b", term))
        assert numeric or roman, f"{term!r} carries no figure"


def test_allowed_number_word_phrases_contain_a_number_word() -> None:
    lexicon = load_lexicon()
    for phrase in lexicon.allowed_number_word_phrases:
        tokens = {token.casefold() for token in re.findall(r"[A-Za-z]+", phrase)}
        assert tokens & lexicon.number_words, f"{phrase!r} masks nothing"


def test_lexicon_is_jurisdiction_neutral() -> None:
    """No country, regulator or central-bank name may be hard-coded here.

    Registry terms are merged in at runtime from the global ``jurisdictions``
    table, so adding a country is a data change, never an edit to this file.
    """
    raw = json.loads(lexicon_source())
    haystack = json.dumps(raw).casefold()
    for banned in (
        "ghana",
        "bank of ghana",
        "nigeria",
        "kenya",
        "south africa",
        "cedi",
        "naira",
    ):
        assert banned not in haystack, f"lexicon.json names {banned!r}"


def test_currency_codes_do_not_collide_with_risk_acronyms() -> None:
    lexicon = load_lexicon()
    for acronym in ("CAR", "LCR", "NPL", "RWA", "EVE", "NII", "NSF", "PIT"):
        assert acronym not in lexicon.currency_codes


def test_number_words_exclude_pronouns_and_ordinals() -> None:
    """ "One", "first", "second" and "quarter" are prose, not quantities."""
    lexicon = load_lexicon()
    for word in ("one", "first", "second", "third", "quarter"):
        assert word not in lexicon.number_words
