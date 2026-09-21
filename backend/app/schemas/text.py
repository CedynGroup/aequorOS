"""Shared validators for free text that reaches a filed artifact.

A regulatory return is exported to PDF, XLSX and CSV. The XLSX format (XML)
cannot hold C0 control characters other than tab, line feed and carriage
return — openpyxl raises ``IllegalCharacterError`` on them — so text that will
print on a return is refused at input when it carries one (``\\x0b``, the line
break Word puts on the clipboard, is the common case). The exporters also strip
them at render time, so a value stored before this validator existed still
exports.
"""

from __future__ import annotations

import re

#: C0 control characters except tab, line feed and carriage return.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def reject_control_characters(value: str | None) -> str | None:
    """Pydantic field validator: refuse text carrying a C0 control character."""
    if value is not None and _CONTROL_CHARACTERS.search(value):
        raise ValueError(
            "must not contain control characters (other than tab and line breaks); "
            "paste the text as plain text"
        )
    return value


__all__ = ["reject_control_characters"]
