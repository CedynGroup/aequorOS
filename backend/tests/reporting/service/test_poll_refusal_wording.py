"""Why a status check has nothing to ask, in the words a bank officer uses.

What is being protected: a second "Check status" press on a return the regulator
had already accepted answered "Only a submitted package can be polled; this package
is 'acknowledged'" (2026-11-23), a developer's sentence naming a stored value. Each
refusal now says what happened to the return, and never the value itself.
"""

from __future__ import annotations

import pytest

from app.reporting.service import workflow


@pytest.mark.parametrize(
    ("status", "says"),
    [
        ("acknowledged", "it was accepted"),
        ("rejected", "it was sent back for correction"),
        ("declined", "it was declined"),
        ("superseded", "A corrected version replaced this return"),
        ("generated", "has not been filed yet"),
        ("approved", "has not been filed yet"),
    ],
)
def test_each_refusal_says_what_happened_to_the_return(status: str, says: str) -> None:
    sentence = workflow._not_pollable_sentence(status)  # pyright: ignore[reportPrivateUsage]
    assert says in sentence
    assert f"'{status}'" not in sentence
    assert "package" not in sentence
