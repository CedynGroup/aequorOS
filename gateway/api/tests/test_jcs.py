# pyright: standard
"""RFC 8785 canonicalisation, including the RFC's own example."""

from __future__ import annotations

import json
import math

import pytest
from pydantic import JsonValue

from app.core.encoding import jcs_sha256
from app.core.jcs import CanonicalizationError, canonical_text, canonicalize

EURO = chr(0x20AC)
BACKSLASH = chr(0x5C)
QUOTE = chr(0x22)


def test_rfc8785_section_3_2_3_example() -> None:
    string = (
        EURO
        + "$"
        + chr(0x0F)
        + chr(0x0A)
        + "A'"
        + chr(0x42)
        + QUOTE
        + BACKSLASH
        + BACKSLASH
        + QUOTE
        + "/"
    )
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": string,
        "literals": [None, True, False],
    }
    expected_string = (
        QUOTE
        + EURO
        + "$"
        + BACKSLASH
        + "u000f"
        + BACKSLASH
        + "nA'B"
        + BACKSLASH
        + QUOTE
        + BACKSLASH * 4
        + BACKSLASH
        + QUOTE
        + "/"
        + QUOTE
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":' + expected_string + "}"
    )
    assert canonical_text(value) == expected


def test_object_keys_sorted_by_utf16_code_units() -> None:
    # RFC 8785 3.2.3: by UTF-16 code units, U+1F600 (surrogates D83D DE00) sorts before U+FB33.
    keys_in = [chr(0x20AC), "\r", chr(0xFB33), "1", chr(0x1F600), chr(0x80), chr(0xF6)]
    value: dict[str, JsonValue] = {key: "x" for key in keys_in}
    keys = list(json.loads(canonical_text(value)).keys())
    assert keys == ["\r", "1", chr(0x80), chr(0xF6), chr(0x20AC), chr(0x1F600), chr(0xFB33)]


def test_control_characters_are_escaped_in_lowercase_hex() -> None:
    assert canonical_text(chr(0x1F)) == QUOTE + BACKSLASH + "u001f" + QUOTE
    assert (
        canonical_text("\t\b\f")
        == QUOTE + BACKSLASH + "t" + BACKSLASH + "b" + BACKSLASH + "f" + QUOTE
    )
    assert canonical_text(chr(0x7F)) == QUOTE + chr(0x7F) + QUOTE


@pytest.mark.parametrize(
    ("number", "text"),
    [
        (0, "0"),
        (-0.0, "0"),
        (1, "1"),
        (-1, "-1"),
        (100.0, "100"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (123.456, "123.456"),
        (0.001, "0.001"),
        (1e-7, "1e-7"),
        (1.5e-7, "1.5e-7"),
        (-1.5e21, "-1.5e+21"),
        (9007199254740991, "9007199254740991"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
    ],
)
def test_numbers_follow_ecmascript(number: float, text: str) -> None:
    assert canonical_text(number) == text


def test_whitespace_and_nesting() -> None:
    assert (
        canonicalize({"b": [1, {"d": True, "c": None}], "a": "x"})
        == b'{"a":"x","b":[1,{"c":null,"d":true}]}'
    )


def test_refuses_non_json_numbers_and_unsafe_integers() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(math.nan)
    with pytest.raises(CanonicalizationError):
        canonicalize(math.inf)
    with pytest.raises(CanonicalizationError):
        canonicalize(2**53)


def test_digest_is_independent_of_key_order() -> None:
    assert jcs_sha256({"a": 1, "b": 2}) == jcs_sha256({"b": 2, "a": 1})
    assert jcs_sha256({"a": 1}) != jcs_sha256({"a": 2})
