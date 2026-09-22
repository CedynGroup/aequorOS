"""The shared free-text validator for narratives that reach a filed artifact."""

from __future__ import annotations

import pytest

from app.schemas.text import reject_control_characters


@pytest.mark.parametrize("control", ["\x00", "\x08", "\x0b", "\x0c", "\x1f"])
def test_a_c0_control_character_is_refused(control: str) -> None:
    """NUL included: no narrative column may ever hold one (Postgres ``text`` cannot)."""
    with pytest.raises(ValueError, match="control characters"):
        reject_control_characters(f"Line one{control}line two.")


def test_tab_and_line_breaks_pass_and_none_is_the_identity() -> None:
    text = "Tab\there, line\nbreak, carriage\rreturn."
    assert reject_control_characters(text) is text
    assert reject_control_characters(None) is None
