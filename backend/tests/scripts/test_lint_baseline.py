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
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from scripts.lint_baseline import (
    SCOPES,
    Counts,
    Drift,
    Scope,
    compare,
    count,
    load_baseline,
    parse_report,
    render,
    update,
    violation,
    write_baseline,
)

VIOLATIONS: Counts = {
    "tests/db/test_a.py": {"PT011": 3, "PT012": 2},
    "tests/services/test_b.py": {"RUF043": 1},
}
BASELINE: Counts = {
    "tests/db/test_a.py": {"PT011": 3, "PT012": 2},
    "tests/services/test_b.py": {"RUF043": 1},
}

# Bounded properties over valid nested count mappings: real test paths, the
# ratcheted rule codes, positive counts. The ci profile bounds the examples.
_paths = st.text(alphabet=st.sampled_from("abcdefmrstx_/.0123456789"), min_size=1, max_size=24).map(
    lambda name: f"tests/{name}.py"
)
_codes = st.sampled_from(("PT011", "PT012", "PT017", "RUF043", "DTZ"))
_counts = st.dictionaries(
    _paths,
    st.dictionaries(_codes, st.integers(min_value=1, max_value=50), max_size=4),
    max_size=6,
)
_violation_groups = st.dictionaries(
    st.tuples(_paths, _codes),
    st.lists(st.integers(min_value=1, max_value=1000), min_size=1, max_size=6),
    max_size=8,
)


def _oracle(current: Counts, baseline: Counts) -> tuple[frozenset[Drift], frozenset[Drift]]:
    """An independent restatement of the ratchet comparison.

    Written as two straightforward loops over each mapping's own entries, so it
    shares no code path with ``compare``'s key-union walk.
    """
    grown: set[Drift] = set()
    stale: set[Drift] = set()
    for path, codes in current.items():
        for code, found in codes.items():
            allowed = baseline.get(path, {}).get(code, 0)
            if found > allowed:
                grown.add(Drift(path, code, found, allowed))
    for path, codes in baseline.items():
        for code, allowed in codes.items():
            found = current.get(path, {}).get(code, 0)
            if found < allowed:
                stale.add(Drift(path, code, found, allowed))
    return frozenset(grown), frozenset(stale)


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


class TestCompareProperties:
    @given(current=_counts, baseline=_counts)
    def test_compare_matches_an_independent_oracle(self, current: Counts, baseline: Counts) -> None:
        comparison = compare(current, baseline)
        grown, stale = _oracle(current, baseline)
        assert frozenset(comparison.grown) == grown
        assert frozenset(comparison.stale) == stale
        assert comparison.passes == (not grown and not stale)


class TestCountProperties:
    @given(groups=_violation_groups)
    def test_count_preserves_every_violation(
        self, groups: dict[tuple[str, str], list[int]]
    ) -> None:
        violations = tuple(
            violation(path, code, row, "message")
            for (path, code), rows in groups.items()
            for row in rows
        )
        counts = count(violations)
        assert sum(total for codes in counts.values() for total in codes.values()) == len(
            violations
        )
        for (path, code), rows in groups.items():
            assert counts[path][code] == len(rows)


class TestSerializationProperties:
    @given(counts=_counts)
    def test_the_baseline_survives_a_write_read_round_trip(self, counts: Counts) -> None:
        with tempfile.TemporaryDirectory() as directory:
            baseline_file = Path(directory) / "lint_baseline.json"
            write_baseline(counts, baseline_file)
            assert load_baseline(baseline_file) == counts

    @given(counts=_counts)
    def test_render_is_canonical_regardless_of_key_order(self, counts: Counts) -> None:
        scrambled = {
            path: dict(reversed(list(codes.items())))
            for path, codes in reversed(list(counts.items()))
        }
        assert render(scrambled) == render(counts)


class TestUpdateProperties:
    @given(baseline=_counts, data=st.data())
    def test_update_never_persists_a_grown_count(
        self, baseline: Counts, data: st.DataObject
    ) -> None:
        entries = [(path, code) for path, codes in baseline.items() for code in codes]
        grown = {path: dict(codes) for path, codes in baseline.items()}
        if entries:
            path, code = data.draw(st.sampled_from(entries))
            grown[path][code] += 1
        else:
            grown = {"tests/new/test_x.py": {"PT011": 1}}
        with tempfile.TemporaryDirectory() as directory:
            baseline_file = Path(directory) / "lint_baseline.json"
            write_baseline(baseline, baseline_file)
            before = baseline_file.read_text(encoding="utf-8")
            assert update(grown, baseline_file) == 1
            assert baseline_file.read_text(encoding="utf-8") == before

    @given(counts=_counts)
    def test_update_lowers_a_stale_baseline_to_the_recorded_counts(self, counts: Counts) -> None:
        current = {path: codes for path, codes in counts.items() if path != "tests/seed/test_y.py"}
        with tempfile.TemporaryDirectory() as directory:
            baseline_file = Path(directory) / "lint_baseline.json"
            write_baseline({**counts, "tests/seed/test_y.py": {"DTZ": 2}}, baseline_file)
            assert update(current, baseline_file) == 0
            assert load_baseline(baseline_file) == current


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
