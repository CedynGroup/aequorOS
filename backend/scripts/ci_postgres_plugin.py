"""Opt-in CI duration-balanced module sharding; normal developer pytest runs are unchanged."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from scripts.ci_postgres import SHARDS, selected_tests, strings


class _PhaseState:
    """Controller-process state shared between the plugin's collection hooks."""

    sanctioned_empty_phase = False


_phase_state = _PhaseState()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    index = int(os.environ["CI_POSTGRES_SHARD"])
    files = strings(cast(object, json.loads(os.getenv("CI_POSTGRES_FILES", "[]"))))
    if not 1 <= index <= SHARDS:
        raise pytest.UsageError("CI_POSTGRES_SHARD is outside the configured matrix")
    collected = [item.nodeid for item in items]
    selected_ids = set(selected_tests(collected, files, index))
    selected = [item for item in items if item.nodeid in selected_ids]
    deselected = [item for item in items if item.nodeid not in selected_ids]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    # A phase whose command collected real scope but owns nothing in it (a shard
    # with no tests/db modules, or an affected module in the other phase) is a
    # sanctioned no-op: the phase ran, deselected everything, and the aggregate
    # still verifies one execution owner per test. A moved or vanished path
    # collects nothing and must keep pytest's exit 5.
    _phase_state.sanctioned_empty_phase = bool(collected) and not selected
    directory = Path(os.environ["CI_POSTGRES_REPORT_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    worker = os.getenv("PYTEST_XDIST_WORKER", "main")
    phase = os.environ["CI_POSTGRES_PHASE"]
    (directory / f"collection-{phase}-{worker}.json").write_text(
        json.dumps(
            {
                "count": SHARDS,
                "shard": index,
                "files": files,
                "collected": collected,
                "selected": [item.nodeid for item in selected],
            }
        )
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    """Excuse pytest's exit 5 only for the sanctioned empty phase, never a moved path."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and _phase_state.sanctioned_empty_phase:
        session.exitstatus = pytest.ExitCode.OK
