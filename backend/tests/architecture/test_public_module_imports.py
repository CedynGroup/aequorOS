"""Feature interfaces must load in a fresh interpreter without import cycles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.architecture.test_feature_boundaries import COMPOSITION, KERNEL, LAYERS

BACKEND = Path(__file__).parents[2]
PUBLIC_MODULES = sorted(
    f"app.{feature}.public"
    for feature in set(LAYERS) - {KERNEL, COMPOSITION}
    if (BACKEND / "app" / feature / "public.py").is_file()
)


@pytest.mark.parametrize("module", PUBLIC_MODULES)
def test_public_module_imports_in_isolation(module: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, sys; importlib.import_module(sys.argv[1])",
            module,
        ],
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{module} failed to import:\n{result.stderr}"
