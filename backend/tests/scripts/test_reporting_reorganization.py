"""Reporting moves preserve executable imports and generated coverage output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bog_coverage_matrix

BACKEND = Path(__file__).parents[2]


def _execute(program: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", program, *args],
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "module",
    [
        "app.capital.service.sdi_views",
        "app.reporting.service.generation",
        "app.reporting.service.hub",
        "app.reporting.service.dbk_generation",
        "app.reporting.service.le_generation",
        "app.reporting.service.lrt_generation",
        "app.reporting.service.npl_generation",
        "app.reporting.service.bog_forms.generation",
    ],
)
def test_reporting_operations_after_fresh_import(module: str) -> None:
    _execute(
        """
import importlib
import sys

importlib.import_module(sys.argv[1])
from app.reporting.service.generation import snapshot_row, snapshot_section
from app.reporting.service.hub import generate_package

assert callable(generate_package)
row = snapshot_row("assets", "Assets", 12, unit="ghs")
section = snapshot_section("balance_sheet", "Balance sheet", [row])
assert section["rows"][0]["value"] == "12"
assert section["unit"] == "ghs"
""",
        module,
    )


def test_coverage_matrix_reports_relocated_form_tests(capsys: pytest.CaptureFixture[str]) -> None:
    bog_coverage_matrix.main()
    rows = {
        columns[1].strip(): columns[11].strip()
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("| BSD")
        for columns in [line.split("|")]
    }
    assert len(rows) == 23
    assert all(coverage.startswith("✓ test_") for coverage in rows.values())
    assert rows["BSD1"] == "✓ test_bsd1.py"
    assert rows["BSD10"] == rows["BSD17"] == "✓ test_bsd10_11_16_17.py"
    assert rows["BSD5A"] == rows["BSD5B"] == "✓ test_bsd5.py"
