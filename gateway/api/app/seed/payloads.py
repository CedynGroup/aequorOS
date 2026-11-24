"""Deterministic randomness and institution size for the seed's synthetic figures.

The figures themselves are built from the aligned definitions in
:mod:`app.seed.catalogue_payloads`; sizes grow smoothly month to month.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date


def rng_for(*parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def month_index(day: date) -> int:
    return day.year * 12 + day.month


def scale_at(base: int, code: str, day: date) -> float:
    """Institution size at a reporting date: ~0.6% monthly growth with small noise."""
    months = month_index(day) - month_index(date(2025, 11, 30))
    noise = rng_for("scale", code, day.isoformat()).uniform(-0.012, 0.012)
    return base * (1.006**months) * (1 + noise)
