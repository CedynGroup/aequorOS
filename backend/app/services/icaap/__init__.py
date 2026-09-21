"""ICAAP workspace services.

The module boundary that matters here is the one these services do NOT cross:
nothing under ``app/services/icaap`` reads the live plane. Figures come from
sealed runs, attested sign-offs, approved plans, quarterly snapshots and
register digests for the cycle's exact as-of date, because a report a Board
signs must quote what was computed and reviewed, not what the worker recomputed
this morning. ``tests/architecture/test_icaap_boundaries.py`` enforces it.
"""

from __future__ import annotations
