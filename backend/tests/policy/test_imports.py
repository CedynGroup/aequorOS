"""Policy's public contracts must compose without import-order cycles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# The app-wide entrypoints below are guarded only here, at the strictest
# existing 60 s bound; live's feature entrypoints stay in tests/live/test_imports.py.
@pytest.mark.parametrize(
    "entrypoint", ["app.policy.public", "app.main", "app.worker", "app.operator.main"]
)
def test_policy_composes_from_a_fresh_interpreter(entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({entrypoint!r})"],
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
