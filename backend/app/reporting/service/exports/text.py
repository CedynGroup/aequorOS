"""Render-time text safety shared by the exporters (ICAAP P0 fix round).

Snapshot text is stored exactly as captured — it is sealed and digested — but
not every artifact format can carry every character:

* **XML (XLSX, and reportlab's paragraph markup)** cannot hold the C0 control
  characters other than tab, line feed and carriage return. openpyxl raises
  ``IllegalCharacterError`` on them, which made an attested narrative carrying
  Word's soft line break (``\\x0b``) impossible to export — and the XLSX is
  minted automatically at submission. :func:`xml_safe` maps the two line-break
  controls (vertical tab, form feed) to a line feed and every other one to the
  visible replacement character, so the export never fails and never silently
  drops text.

Only the ARTIFACT changes: the snapshot and its ``content_digest`` are
untouched, and text without such characters renders byte-for-byte as before.
"""

from __future__ import annotations

import re

#: Vertical tab and form feed are line breaks in the documents users paste from.
_LINE_BREAK_CONTROLS = re.compile(r"[\x0b\x0c]")
#: Every other C0 control character XML 1.0 forbids (tab, LF and CR are legal).
_OTHER_CONTROLS = re.compile(r"[\x00-\x08\x0e-\x1f]")
REPLACEMENT_CHARACTER = "�"


def xml_safe(text: str) -> str:
    """``text`` with the characters XML cannot carry made printable."""
    if not _LINE_BREAK_CONTROLS.search(text) and not _OTHER_CONTROLS.search(text):
        return text
    return _OTHER_CONTROLS.sub(REPLACEMENT_CHARACTER, _LINE_BREAK_CONTROLS.sub("\n", text))


__all__ = ["REPLACEMENT_CHARACTER", "xml_safe"]
