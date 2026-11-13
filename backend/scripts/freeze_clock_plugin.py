"""Freeze the wall clock in tests with time-machine (CODEBASE_CONVENTIONS.md §1).

Two entry points, both backed by ``time_machine.travel(..., tick=False)``:

* ``@pytest.mark.freeze_time("<ISO-8601 UTC>")`` freezes the wall clock for the
  whole test (setup, call and teardown) at the marked instant.
* the ``frozen_clock`` fixture yields the ``time_machine.Traveller`` traveller
  so a test can move the clock with ``traveller.move_to(...)``; without a
  ``freeze_time`` marker it freezes at ``DEFAULT_FROZEN_TIME``.

Wall-clock reads (``datetime.now``, ``time.time``, ``app.db.base.utc_now``) then
return a constant, while ``time.monotonic`` and ``time.perf_counter`` keep
running: they measure elapsed time, not the calendar, and freezing them would
hide real hangs. ``scripts/test_determinism_guard.py`` (AQPY001) accepts clock
reads in tests that carry the marker, sit inside ``time_machine.travel(...,
tick=False)`` or take the ``frozen_clock`` fixture. The marker instant must
carry an explicit UTC offset: a naive ISO string would freeze at the host's
local timezone.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
import time_machine

#: Where ``frozen_clock`` freezes when the test carries no ``freeze_time`` marker.
DEFAULT_FROZEN_TIME = "2026-01-01T00:00:00+00:00"

_MARKER = "freeze_time"
#: The traveller of the marker freeze that is currently wrapping a test, if
#: any. pytest runs items strictly sequentially (each xdist worker is its own
#: process), so a single-entry map is a safe hand-off between the protocol
#: hook and the fixture; ``pytest.Stash`` is only partially typed and fails
#: basedpyright strict.
_active: dict[int, time_machine.Traveller] = {}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{_MARKER}(iso): freeze the wall clock with time-machine at an "
        "ISO-8601 UTC instant, e.g. @pytest.mark.freeze_time('2026-01-01T00:00:00+00:00')",
    )


def _marked_instant(item: pytest.Item) -> str | None:
    """The marker's instant, or None when absent or not a UTC-pinned instant.

    A naive ISO string would make time-machine freeze at the host's local
    timezone, so the same marker could freeze different instants on different
    hosts; an explicit UTC offset is required.
    """
    marker = item.get_closest_marker(_MARKER)
    if marker is None:
        return None
    instant = marker.args[0] if marker.args else marker.kwargs.get("iso")
    if isinstance(instant, str):
        try:
            parsed = datetime.fromisoformat(instant)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return instant
    return None


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker(_MARKER) is not None and _marked_instant(item) is None:
        pytest.fail(
            "freeze_time requires an ISO-8601 string with an explicit UTC offset, "
            "e.g. @pytest.mark.freeze_time('2026-01-01T00:00:00+00:00')",
            pytrace=False,
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    instant = _marked_instant(item)
    if instant is None:
        yield
        return
    with time_machine.travel(instant, tick=False) as traveller:
        _active.clear()
        _active[id(item)] = traveller
        try:
            yield
        finally:
            _active.clear()


@pytest.fixture
def frozen_clock(request: pytest.FixtureRequest) -> Iterator[time_machine.Traveller]:
    """Freeze the wall clock and yield the traveller for ``move_to`` adjustments."""
    del request
    if _active:
        yield next(iter(_active.values()))
        return
    with time_machine.travel(DEFAULT_FROZEN_TIME, tick=False) as traveller:
        yield traveller
