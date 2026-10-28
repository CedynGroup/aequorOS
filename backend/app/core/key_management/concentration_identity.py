"""Canonical connected-group and single-name identity."""

from collections.abc import Mapping


def counterparty_group_key(
    name: str, group_reference: str | None, attributes: Mapping[str, object]
) -> str:
    group: object = group_reference
    if not group:
        group = next(
            (
                attributes[key]
                for key in ("group_reference", "group", "parent")
                if attributes.get(key)
            ),
            None,
        )
    if group:
        if len(str(group)) > 255:
            raise ValueError("Counterparty group reference must be at most 255 characters.")
        return f"group:{group}"
    return f"cp:{name}"
