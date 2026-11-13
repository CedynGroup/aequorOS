"""The freeze_time marker and the frozen_clock fixture freeze the wall clock.

These tests are themselves the demonstration AQPY001 asks for: every clock read
below sits inside a frozen context, ``time.monotonic`` keeps running under a
frozen wall clock, and a marker without an instant fails the test.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine

from app.db.base import utc_now
from scripts.freeze_clock_plugin import DEFAULT_FROZEN_TIME

BACKEND = Path(__file__).resolve().parents[1]
MARKER_INSTANT = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
DEFAULT_INSTANT = datetime.fromisoformat(DEFAULT_FROZEN_TIME)


def _monotonic_later_than(first: float) -> float:
    value = first
    for _ in range(100_000):
        value = time.monotonic()
        if value > first:
            return value
    return value


@pytest.mark.freeze_time("2026-03-01T12:00:00+00:00")
def test_freeze_time_marker_freezes_wall_clock_reads() -> None:
    assert datetime.now(tz=UTC) == MARKER_INSTANT
    assert datetime.now(tz=UTC) == datetime.now(tz=UTC)
    assert time.time() == time.time()
    assert utc_now() == MARKER_INSTANT


@pytest.mark.freeze_time("2026-03-01T12:00:00+00:00")
def test_freeze_time_marker_leaves_monotonic_running() -> None:
    first = time.monotonic()
    assert _monotonic_later_than(first) > first


def test_frozen_clock_fixture_freezes_at_the_default_instant(
    frozen_clock: time_machine.Traveller,
) -> None:
    assert datetime.now(tz=UTC) == DEFAULT_INSTANT
    assert time.time() == DEFAULT_INSTANT.timestamp()
    assert utc_now() == DEFAULT_INSTANT


def test_frozen_clock_fixture_yields_a_traveller_that_moves_the_clock(
    frozen_clock: time_machine.Traveller,
) -> None:
    frozen_clock.move_to("2027-06-01T00:00:00+00:00")
    assert utc_now() == datetime(2027, 6, 1, tzinfo=UTC)


@pytest.mark.freeze_time("2026-03-01T12:00:00+00:00")
def test_frozen_clock_reuses_the_marker_traveller(
    frozen_clock: time_machine.Traveller,
) -> None:
    frozen_clock.move_to("2026-03-01T13:00:00+00:00")
    assert utc_now() == datetime(2026, 3, 1, 13, 0, tzinfo=UTC)


def test_a_freeze_time_marker_without_an_instant_fails_the_test(tmp_path: Path) -> None:
    module = tmp_path / "test_missing_instant.py"
    module.write_text(
        "import pytest\n"
        "@pytest.mark.freeze_time\n"
        "def test_marker_needs_an_instant() -> None:\n"
        "    assert False\n"
    )
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            # The config owns addopts, so the plugin only loads when pytest is
            # pointed at it; a bare tmp_path run would pick /tmp as rootdir.
            "-c",
            str(BACKEND / "pyproject.toml"),
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 1
    assert "freeze_time requires an ISO-8601 UTC string" in result.stdout
