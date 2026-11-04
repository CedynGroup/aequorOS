"""Nigeria and Kenya, end to end: the frameworks now resolve.

Both shipped as data in P5-E and were published-but-non-functional until
``202609200063``: resolution matches ``jurisdiction_code`` exactly and never
falls back to Ghana, so every governed read — the filing deadline, the review
intervals, the materiality matrix, Kenya's two horizons — answered
``missing_parameter`` and a cycle could not be opened at all. That is correct
behaviour for an unseeded code (D-024 §4) and useless to a bank.

This file proves the other half: with the rows seeded, a Nigerian and a Kenyan
institution open a cycle, get the regulator's own deadline, and read a checklist
whose sentences carry figures rather than ``{param:…}`` placeholders. Every one
of those figures is labelled pending confirmation, because the primary texts
have still not been read here (D-076) — a functional framework and a verified
one are different claims, and only the first is made.

The fixture bank is the canonical one with its jurisdiction changed, which is
the smallest change that exercises the real path: nothing in the ICAAP services
branches on country, so the jurisdiction column and the registry row are the
whole of the difference.

This module's other-suite cases live in ``test_ng_ke_frameworks_integration.py``.
"""

from __future__ import annotations

from app.icaap.service import serializers


def test_the_pending_note_does_not_claim_a_stakeholder_was_asked() -> None:
    """Production copy, and it has to be true in every jurisdiction.

    It read "(figure pending stakeholder confirmation)". For Ghana that is a
    fair description of an exposure draft; for Nigeria and Kenya it is not —
    the outstanding step is reading the regulator's text, which nobody here has
    done (D-076), not hearing back from a stakeholder. A preparer who read the
    old note beside a CBN interval would conclude the opposite of the truth.
    The note now says only what holds for every pending row.
    """
    assert serializers.PENDING_FIGURE_NOTE == " (figure pending confirmation)"
    assert "stakeholder" not in serializers.PENDING_FIGURE_NOTE
