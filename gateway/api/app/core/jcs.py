"""RFC 8785 JSON Canonicalization Scheme.

Serialises the JSON data model deterministically:

* objects: members sorted by the UTF-16 code units of their names, no whitespace;
* strings: the minimal escaping of RFC 8785 section 3.2.2.2 (``\\b \\t \\n \\f \\r \\" \\\\``
  and ``\\u00XX`` for other control characters), everything else literal UTF-8;
* numbers: the ECMAScript ``Number.prototype.toString`` form (section 3.2.2.3). Integers
  are exact within the IEEE 754 double range the scheme assumes; larger integers are
  refused rather than silently rounded.
"""

from __future__ import annotations

import math

from pydantic import JsonValue

MAX_SAFE_INTEGER = 2**53 - 1

_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class CanonicalizationError(ValueError):
    pass


def _string(value: str) -> str:
    out: list[str] = ['"']
    for char in value:
        code = ord(char)
        if code in _ESCAPES:
            out.append(_ESCAPES[code])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        elif 0xD800 <= code <= 0xDFFF:
            raise CanonicalizationError("lone surrogate in string")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError("NaN and Infinity are not JSON numbers")
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    # ECMAScript Number::toString: shortest round-trip digits, exponent form outside
    # 1e-7 <= |x| < 1e21.
    digits, exponent = _shortest_digits(value)
    sign = "-" if value < 0 else ""
    k = len(digits)
    n = exponent  # value = 0.digits * 10**n
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    exp_text = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return sign + digits + "e" + exp_text
    return sign + digits[0] + "." + digits[1:] + "e" + exp_text


def _shortest_digits(value: float) -> tuple[str, int]:
    """Shortest decimal digits and exponent ``n`` with ``|value| = 0.d1d2… * 10**n``."""
    text = repr(abs(value))  # Python's repr is the shortest round-trip representation
    mantissa, _, exp_part = text.partition("e")
    exp = int(exp_part) if exp_part else 0
    if "." in mantissa:
        int_part, frac_part = mantissa.split(".")
    else:
        int_part, frac_part = mantissa, ""
    if frac_part == "0":
        frac_part = ""
    all_digits = int_part + frac_part
    stripped = all_digits.lstrip("0")
    leading_zeros = len(all_digits) - len(stripped)
    digits = stripped.rstrip("0") or "0"
    n = len(int_part) - leading_zeros + exp
    return digits, n


def _encode(value: JsonValue, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer outside the IEEE 754 exact range")
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        out.append("{")
        for index, (key, member) in enumerate(items):
            if index:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _encode(member, out)
        out.append("}")
    else:
        out.append("[")
        for index, member in enumerate(value):
            if index:
                out.append(",")
            _encode(member, out)
        out.append("]")


def canonicalize(value: JsonValue) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``value``."""
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def canonical_text(value: JsonValue) -> str:
    return canonicalize(value).decode("utf-8")
