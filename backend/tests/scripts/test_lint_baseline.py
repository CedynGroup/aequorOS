"""Unit tests for the lint count ratchet.

The ratchet (``scripts/lint_baseline.py``) holds per-file, per-rule violation
counts for Ruff codes that the main ``ruff check`` run does not enforce at zero.
Its contract:

* a count above the baseline fails (growth);
* a count below the baseline also fails (stale) until the baseline is lowered
  with ``update``, so a reduction is recorded in the same change that made it;
* an exact match passes.

These tests are hermetic: the comparison and update logic runs on synthetic
counts, and one case drives the real Ruff binary against a small fixture tree
to pin the JSON parsing and path relativisation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from scripts.lint_baseline import (
    SCOPES,
    Counts,
    Drift,
    Scope,
    compare,
    count,
    parse_report,
    update,
    violation,
)

VIOLATIONS: Counts = {
    "tests/db/test_a.py": {"PT011": 3, "PT012": 2},
    "tests/services/test_b.py": {"RUF043": 1},
}
BASELINE: Counts = {
    "tests/db/test_a.py": {"PT011": 3, "PT012": 2},
    "tests/services/test_b.py": {"RUF043": 1},
}


class TestCompare:
    def test_an_exact_match_passes(self) -> None:
        comparison = compare(VIOLATIONS, BASELINE)
        assert comparison.grown == ()
        assert comparison.stale == ()
        assert comparison.passes

    def test_growth_fails(self) -> None:
        current = {"tests/db/test_a.py": {"PT011": 4, "PT012": 2}}
        comparison = compare(current, BASELINE)
        assert comparison.grown == (Drift("tests/db/test_a.py", "PT011", 4, 3),)
        assert not comparison.passes

    def test_a_new_file_fails(self) -> None:
        current = {**BASELINE, "tests/new/test_c.py": {"PT017": 1}}
        comparison = compare(current, BASELINE)
        assert comparison.grown == (Drift("tests/new/test_c.py", "PT017", 1, 0),)
        assert not comparison.passes

    def test_a_new_code_in_a_baselined_file_fails(self) -> None:
        current = {**BASELINE, "tests/db/test_a.py": {"PT011": 3, "PT012": 2, "RUF043": 1}}
        comparison = compare(current, BASELINE)
        assert comparison.grown == (Drift("tests/db/test_a.py", "RUF043", 1, 0),)
        assert not comparison.passes

    def test_a_reduction_is_stale_until_the_baseline_is_lowered(self) -> None:
        current = {
            "tests/db/test_a.py": {"PT011": 2, "PT012": 2},
            "tests/services/test_b.py": {"RUF043": 1},
        }
        comparison = compare(current, BASELINE)
        assert comparison.stale == (Drift("tests/db/test_a.py", "PT011", 2, 3),)
        assert not comparison.passes

    def test_a_removed_file_is_stale_until_the_baseline_is_lowered(self) -> None:
        current: Counts = {"tests/db/test_a.py": {"PT011": 3, "PT012": 2}}
        comparison = compare(current, BASELINE)
        assert comparison.stale == (Drift("tests/services/test_b.py", "RUF043", 0, 1),)
        assert not comparison.passes


class TestCount:
    def test_counts_are_grouped_by_file_and_code(self) -> None:
        violations = (
            violation("tests/db/test_a.py", "PT011", 12, "too broad"),
            violation("tests/db/test_a.py", "PT011", 40, "too broad"),
            violation("tests/db/test_a.py", "PT012", 7, "multiple statements"),
            violation("tests/services/test_b.py", "RUF043", 3, "ambiguous pattern"),
        )
        assert count(violations) == {
            "tests/db/test_a.py": {"PT011": 2, "PT012": 1},
            "tests/services/test_b.py": {"RUF043": 1},
        }


class TestUpdate:
    def test_update_refuses_to_raise_the_baseline(self, tmp_path: Path) -> None:
        baseline_file = tmp_path / "lint_baseline.json"
        baseline_file.write_text(json.dumps(BASELINE, indent=1), encoding="utf-8")
        grown = {**BASELINE, "tests/db/test_a.py": {"PT011": 5, "PT012": 2}}
        assert update(grown, baseline_file) == 1
        assert json.loads(baseline_file.read_text(encoding="utf-8")) == BASELINE

    def test_update_lowers_the_baseline(self, tmp_path: Path) -> None:
        baseline_file = tmp_path / "lint_baseline.json"
        baseline_file.write_text(json.dumps(BASELINE, indent=1), encoding="utf-8")
        lowered: Counts = {"tests/db/test_a.py": {"PT011": 2, "PT012": 2}}
        assert update(lowered, baseline_file) == 0
        assert json.loads(baseline_file.read_text(encoding="utf-8")) == lowered


class TestScopes:
    def test_the_scopes_cover_the_ratcheted_codes(self) -> None:
        codes = {code for scope in SCOPES for code in scope.codes}
        assert codes == {"PT011", "PT012", "PT017", "RUF043", "DTZ"}
        assert all(isinstance(scope, Scope) for scope in SCOPES)


class TestParse:
    def test_the_real_ruff_report_is_relativised_and_parsed(self, tmp_path: Path) -> None:
        ruff = shutil.which("ruff")
        assert ruff is not None, "Ruff must be on the test PATH (uv run pytest)"
        (tmp_path / "fixture_test.py").write_text(
            "import pytest\n\n\ndef test_x() -> None:\n"
            "    with pytest.raises(Exception):\n"
            "        pass\n",
            encoding="utf-8",
        )
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                ruff,
                "check",
                "--isolated",
                "--no-cache",
                "--output-format",
                "json",
                "--select",
                "PT011",
                ".",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        violations = parse_report(result.stdout, tmp_path)
        assert len(violations) == 1
        assert violations[0].path == "fixture_test.py"
        assert violations[0].code == "PT011"
        assert count(violations) == {"fixture_test.py": {"PT011": 1}}
