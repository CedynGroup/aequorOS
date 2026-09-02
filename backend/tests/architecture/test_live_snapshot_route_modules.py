"""The live-snapshots route must accept every live module.

The Command Center pulse draws one sparkline per live module, so it requests
a ladder for each of them on every dashboard load. The route validates its
``module`` query parameter with a hand-written regex, and a module missing
from that regex 422s on every load while the rest of the page looks fine —
a failure that reaches the browser console, not a test.

That has now happened twice: once for ``rating`` and again for ``credit``
when the credit module shipped. This pins the regex to ``LIVE_MODULES`` so
registering a module and forgetting the route is a build failure.
"""

from __future__ import annotations

import re
import typing

from app.features import manage_live_engine
from app.features.manage_live_engine import list_live_snapshots
from app.models.live import LIVE_MODULES


def _module_pattern() -> re.Pattern[str]:
    """The compiled pattern the route enforces on ``module``."""
    # The module uses `from __future__ import annotations`, so the raw
    # __annotations__ entry is a string; resolve it to reach the Query().
    hints = typing.get_type_hints(
        list_live_snapshots, dict(vars(manage_live_engine)), include_extras=True
    )
    query = hints["module"].__metadata__[0]
    patterns = [
        item.pattern
        for item in getattr(query, "metadata", [])
        if getattr(item, "pattern", None)
    ]
    assert patterns, "the module parameter must stay pattern-validated"
    return re.compile(patterns[0])


def test_every_live_module_is_accepted_by_the_snapshot_route() -> None:
    pattern = _module_pattern()
    rejected = [module for module in LIVE_MODULES if not pattern.fullmatch(module)]
    assert not rejected, (
        f"the live-snapshots route rejects {rejected}; the Command Center pulse "
        "requests a ladder for every live module, so each one must be accepted"
    )


def test_the_route_still_rejects_an_unknown_module() -> None:
    """The pattern is a real gate, not a rubber stamp."""
    pattern = _module_pattern()
    assert not pattern.fullmatch("not_a_module")
    assert not pattern.fullmatch("")
