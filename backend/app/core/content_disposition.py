"""``Content-Disposition`` for downloads whose name we did not choose.

A stored upload keeps the filename the client sent, and that name is
attacker-controlled. Interpolating it straight into a header
(``f'attachment; filename="{row.original_filename}"'``) lets it close the
quoted string and append a parameter of its choosing: a file stored as

    x.pdf"; filename="evil.html

downloads as ``evil.html`` for every colleague who opens it. Newlines are
refused by the HTTP layer, so this is parameter injection inside one header
rather than header splitting — the file itself is still the evidence file, but
the name it is saved under belongs to whoever uploaded it.

Every download therefore emits two parameters:

* ``filename`` — ASCII only, reduced to ``[A-Za-z0-9._-]``. A client that
  ignores RFC 5987 still gets a usable, inert name.
* ``filename*`` — the original name, percent-encoded as UTF-8 (RFC 5987), so a
  bank reviewing its own evidence keeps the spelling it uploaded under.

Neither parameter can carry ``"``, ``;``, ``\\``, a control character or a path
separator, because the ASCII form is restricted and the UTF-8 form is
percent-encoded in full.
"""

from __future__ import annotations

import re
from urllib.parse import quote

#: Kept when reducing a name to its ASCII form. Everything else becomes ``_``.
_ASCII_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Control characters cannot appear in a header at all; strip them before the
#: name is used for either parameter.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: The name a header falls back to when the stored one is unusable.
DEFAULT_FILENAME = "document"

#: Long enough for a descriptive name, short enough that the whole header stays
#: well inside the usual 8 KiB request-line budget alongside the UTF-8 form.
_MAX_ASCII_STEM = 120
_MAX_SUFFIX = 16


def _basename(filename: str) -> str:
    """``filename`` with any directory part and control characters removed."""
    cleaned = _CONTROL.sub("", filename).replace("\\", "/")
    return cleaned.rsplit("/", 1)[-1].strip() or DEFAULT_FILENAME


def _ascii_form(name: str) -> str:
    """``name`` reduced to characters no header and no filesystem reinterprets."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    ascii_stem = _ASCII_UNSAFE.sub("_", stem).strip("._-")[:_MAX_ASCII_STEM]
    ascii_suffix = _ASCII_UNSAFE.sub("", suffix)[:_MAX_SUFFIX].lower()
    if not ascii_stem:
        return f"{DEFAULT_FILENAME}.{ascii_suffix}" if ascii_suffix else DEFAULT_FILENAME
    return f"{ascii_stem}.{ascii_suffix}" if ascii_suffix else ascii_stem


def attachment_name_parts(filename: str) -> tuple[str, str]:
    """The ``(ascii, utf8)`` forms of ``filename``, path already stripped."""
    name = _basename(filename)
    return _ascii_form(name), name


def attachment_headers(filename: str) -> dict[str, str]:
    """Headers that save ``filename`` under an inert, recoverable name."""
    ascii_name, utf8_name = attachment_name_parts(filename)
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(utf8_name, safe='')}"
        )
    }


__all__ = [
    "DEFAULT_FILENAME",
    "attachment_headers",
    "attachment_name_parts",
]
