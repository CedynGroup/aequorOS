"""Cross-suite test imports: reject new dependencies between suites.

A **test module** is any ``test_*.py`` under ``tests/``. Its **suite** is the
top-level directory it lives in (``tests/db/``, ``tests/identity/``, ...), with
the root-level test files forming one more suite of their own.
Importing a test module from another suite couples
the suites' execution and can expose imported test functions to collection
again; shared helpers belong in ``tests/support`` (where the migration
fixtures landed once the migration test module stopped being one) or in the
owning suite. Importing a non-test module — ``tests/support`` helpers,
``tests/conftest.py``, a suite's ``fixtures.py`` — is not a cross-suite test
import and is out of scope.

**The ratchet** mirrors ``test_feature_boundaries.py``. Every current edge is
recorded once per (importer, imported) module pair in
``cross_suite_import_baseline.json``. An edge not in the baseline fails, and so
does a baseline entry that no longer occurs, so the file only shrinks and a
deleted edge that quietly returns is a new violation again. At the ratchet's
first light the census was 97 edges across 61 importing files (98 import
statement sites), every one an absolute ``import tests.…`` / ``from tests.…
import …`` statement; the audit counts that motivated this guard (329
occurrences across 240 files at c080f8be) were taken at an earlier commit under
a different accounting and are not this census.

After fixing edges, rewrite the baseline with::

    uv run python -c "import tests.architecture.test_cross_suite_imports as t; t.write_baseline()"

and review the diff: it must only delete lines.

Scanning is the shared AST scanner in ``_planes.py``. Relative imports resolve
against the importing module and a literal ``importlib.import_module("tests.…")``
target is caught the same way; a name assembled at runtime is invisible, the
scanner's documented limit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.architecture._planes import imported_modules

BASELINE = Path(__file__).with_name("cross_suite_import_baseline.json")
TESTS = Path(__file__).parents[1]

#: The suite of a root-level test file: every package directory owns a deeper file.
ROOT_SUITE = "<root>"


def _test_files(tests: Path) -> list[Path]:
    return sorted(path for path in tests.rglob("test_*.py") if "__pycache__" not in path.parts)


def _module(path: Path, tests: Path) -> str:
    """Dotted module name of a test file under ``tests``."""
    return ".".join(("tests", *path.relative_to(tests).with_suffix("").parts))


def _suite(module: str) -> str:
    parts = module.split(".")
    return ROOT_SUITE if len(parts) == 2 else parts[1]


def _test_target(name: str, modules: set[str]) -> str | None:
    """The test module ``name`` names, walking up from an imported attribute."""
    probe = name
    while probe:
        if probe in modules:
            return probe
        probe = probe.rpartition(".")[0]
    return None


def edges(tests: Path = TESTS) -> frozenset[str]:
    """Every test-to-test import across suites, one entry per module pair."""
    files = _test_files(tests)
    modules = {_module(path, tests) for path in files}
    found: set[str] = set()
    for path in files:
        module = _module(path, tests)
        source_suite = _suite(module)
        imported = imported_modules(path.read_text(encoding="utf-8"), module=module)
        for name in imported:
            target = _test_target(name, modules)
            if target is None or target == module or _suite(target) == source_suite:
                continue
            found.add(f"{module} -> {target}")
    return frozenset(found)


def write_baseline() -> None:
    """Record today's edges as the baseline (review the diff: deletions only)."""
    BASELINE.write_text(json.dumps(sorted(edges()), indent=1) + "\n", encoding="utf-8")


def _baseline() -> list[str]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_no_new_cross_suite_import_and_the_baseline_only_shrinks() -> None:
    current = edges()
    baseline = set(_baseline())
    new = sorted(current - baseline)
    fixed = sorted(baseline - current)
    assert new == [], (
        "This test module imports another suite's test module. Import the shared "
        "helper from tests/support, move the test into the suite it depends on, or "
        f"delete the entry from {BASELINE.name} in the change that removes the "
        f"import: {new}"
    )
    assert fixed == [], (
        f"These cross-suite imports are gone; delete them from {BASELINE.name} so "
        f"the ratchet keeps the gain: {fixed}"
    )


def test_the_baseline_is_sorted_and_unique() -> None:
    baseline = _baseline()
    assert baseline == sorted(set(baseline)), f"Regenerate {BASELINE.name} with write_baseline()"


_SYNTHETIC_TAIL = (
    "from tests.support.helpers import anything\n"
    "\n"
    "\n"
    "def test_case() -> None:\n"
    "    assert anything\n"
)


def _write_module(tests: Path, relative: str, content: str) -> None:
    path = tests / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("style", "statement"),
    [
        ("absolute", "from tests.db.test_other import helper\n"),
        ("relative", "from ..db.test_other import helper\n"),
        (
            "dynamic",
            'import importlib\n\nimported = importlib.import_module("tests.db.test_other")\n',
        ),
    ],
)
def test_a_new_cross_suite_import_is_caught_in_each_style(
    tmp_path: Path, style: str, statement: str
) -> None:
    """Negative controls: each import style fires, so a green run is not vacuous."""
    _write_module(tmp_path, "support/helpers.py", "anything = 1\n")
    _write_module(tmp_path, "db/test_other.py", "def helper() -> None:\n    return None\n")
    _write_module(tmp_path, "stress/test_importer.py", statement + _SYNTHETIC_TAIL)
    assert edges(tmp_path) == frozenset({"tests.stress.test_importer -> tests.db.test_other"}), (
        f"the {style} import form must fail the ratchet"
    )


def test_a_same_suite_import_is_not_a_violation(tmp_path: Path) -> None:
    """The boundary is the suite, not the test-module import itself."""
    _write_module(tmp_path, "db/test_other.py", "def helper() -> None:\n    return None\n")
    _write_module(tmp_path, "db/test_importer.py", "from tests.db.test_other import helper\n")
    assert edges(tmp_path) == frozenset()
