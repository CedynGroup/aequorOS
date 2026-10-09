"""Opt-in CI module sharding; normal developer pytest runs are unchanged."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from scripts.ci_postgres import SHARDS, shard_for, strings


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    index = int(os.environ["CI_POSTGRES_SHARD"])
    files = strings(cast(object, json.loads(os.getenv("CI_POSTGRES_FILES", "[]"))))
    if not 1 <= index <= SHARDS:
        raise pytest.UsageError("CI_POSTGRES_SHARD is outside the configured matrix")
    collected = [item.nodeid for item in items]
    selected = [
        item
        for item in items
        if (not files or item.nodeid.split("::")[0] in files)
        and shard_for(item.nodeid.split("::")[0]) == index
    ]
    selected_ids = {item.nodeid for item in selected}
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
