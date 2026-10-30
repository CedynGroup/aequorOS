"""Live's public contracts must compose without import-order cycles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "entrypoint",
    [
        "app.live.public",
        "app.main",
        "app.worker",
        "app.operator.main",
        "app.services.regulatory_capital",
        "app.liquidity.service.regulatory_liquidity",
        "app.bi.service.enqueue",
    ],
)
def test_live_composes_from_a_fresh_interpreter(entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({entrypoint!r})"],
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""},
        capture_output=True,
        text=True,
        # Cold application imports build the full schema graph on shared CI hosts.
        # This guard checks import composition, not startup latency.
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
