"""Opt-in CI duration-balanced module sharding; normal developer pytest runs are unchanged."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from scripts.ci_postgres import SHARDS, selected_tests, strings


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
    directory = Path(os.environ["CI_POSTGRES_REPORT_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    worker = os.getenv("PYTEST_XDIST_WORKER", "main")
    (directory / f"collection-{worker}.json").write_text(
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
