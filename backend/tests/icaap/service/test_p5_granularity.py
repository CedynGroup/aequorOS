"""The full granularity adjustment, from a bank's canonical book to a figure.

The pure derivation and its P2 adapter were built first and are covered by
``tests/credit/domain`` and ``tests/icaap/domain/pillar2``. What is pinned here
is the half that makes them reachable: the exposure book this service assembles
from the canonical position snapshots, the order it resolves a PD and an ELGD
in, the rows it excludes and counts, and the refusals it returns instead of a
number it cannot honestly produce.

The end-to-end cases run through ``pillar2.compute_item`` — the same path the
dashboard uses — against the GOVERNED console rows as they are seeded, so the
gate they exercise is the real one. ``ga_min_effective_names = 50`` is
REPRESENTATIVE (D-059 F9) and decides whether the method engages at all, so both
sides of it are asserted: a book below it is declined with that number printed,
and the same book computes once the console lowers it.

This module's other-suite cases live in ``test_p5_granularity_integration.py``.
"""

from __future__ import annotations

from app.icaap.domain.pillar2 import DEFERRED_METHODS
from tests.icaap.service.test_p5_granularity_integration import METHOD


def test_the_method_is_no_longer_answered_as_not_built() -> None:
    assert METHOD not in DEFERRED_METHODS
