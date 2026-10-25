"""Canonical connected-group and single-name identity."""

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
