"""Canonical connected-group and single-name identity."""

import hashlib
from collections.abc import Mapping


def counterparty_group_key(
    name: str, group_reference: str | None, attributes: Mapping[str, object]
) -> str:
    if group_reference:
        return f"group:{group_reference}"
    for key in ("group_reference", "group", "parent"):
        value = attributes.get(key)
        if value:
            return f"group:{value}"
    return f"cp:{name}"


def opaque_group_key(index: str) -> str:
    return "group:" + hashlib.sha256(index.encode()).hexdigest()
