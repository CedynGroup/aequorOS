"""Coercion of decoded JSON (typed ``object``) into ``JsonValue``."""

from __future__ import annotations

import json
from typing import cast

from pydantic import JsonValue


class NotJsonError(ValueError):
    pass


def to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, dict):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise NotJsonError("object keys must be strings")
            out[key] = to_json_value(item)  # pyright: ignore[reportUnknownArgumentType]
        return out
    raise NotJsonError(f"not a JSON value: {type(value).__name__}")


def to_json_object(value: object) -> dict[str, JsonValue]:
    converted = to_json_value(value)
    if not isinstance(converted, dict):
        raise NotJsonError("expected a JSON object")
    return converted


def loads_object(data: bytes | str) -> dict[str, JsonValue]:
    try:
        decoded = cast("object", json.loads(data))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotJsonError("not valid JSON") from exc
    return to_json_object(decoded)
