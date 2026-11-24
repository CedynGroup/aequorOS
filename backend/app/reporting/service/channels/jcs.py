"""RFC 8785 JSON Canonicalization Scheme for filing manifests and payloads.

The AequorOS Reporting Gateway hashes and signs canonical JSON, so the bytes AequorOS
produces must be identical to the bytes the gateway recomputes. Filings carry
only strings, integers, booleans, null, arrays and objects; floats and
non-integral decimals are refused rather than rendered in a way two
implementations could disagree on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

#: RFC 8785 numbers follow IEEE 754 doubles; integers beyond 2**53 are not exact.
_MAX_SAFE_INTEGER = 2**53 - 1
_LITERALS: dict[bool | None, str] = {None: "null", True: "true", False: "false"}


class CanonicalizationError(ValueError):
    """A value cannot be rendered canonically (a float, a fraction, a huge int)."""


def _number(value: int | Decimal | float) -> str:
    if isinstance(value, bool):  # pragma: no cover - guarded by the caller
        raise CanonicalizationError("A boolean is not a number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise CanonicalizationError("Fractional numbers cannot be filed canonically.")
        value = int(value)
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise CanonicalizationError("Fractional numbers cannot be filed canonically.")
        value = int(value)
    if abs(value) > _MAX_SAFE_INTEGER:
        raise CanonicalizationError("Integers beyond 2**53 cannot be filed canonically.")
    return str(value)


def _render(value: object) -> str:
    if value is None or isinstance(value, bool):
        return _LITERALS[value]
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int | Decimal | float):
        return _number(value)
    if isinstance(value, Mapping):
        items = cast(Mapping[object, object], value)
        keys: list[str] = []
        for key in items:
            if not isinstance(key, str):
                raise CanonicalizationError("Object keys must be strings.")
            keys.append(key)
        # RFC 8785 §3.2.3: sort by UTF-16 code units.
        keys.sort(key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(_render(key) + ":" + _render(items[key]) for key in keys) + "}"
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        elements = cast(Sequence[object], value)
        return "[" + ",".join(_render(element) for element in elements) + "]"
    raise CanonicalizationError(f"Cannot canonicalize a {type(value).__name__}.")


def canonical_json(value: object) -> bytes:
    """The RFC 8785 canonical UTF-8 bytes of ``value``."""
    return _render(value).encode("utf-8")
