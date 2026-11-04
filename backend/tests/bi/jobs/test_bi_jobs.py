"""The three ``bi`` lane handlers, with the mart builder stubbed at its one seam.

The builder (``app.bi.service.mart_builder``) lands in a later wave; these
tests prove the handlers' own contract — run gate, version rule (P1-B5),
tenant/bank resolution, dispatch signatures, the backfill's self-re-enqueue
and termination, and error classification — against a stub bound through
``bi_common.load_builder``.

This module's other-suite cases live in ``test_bi_jobs_integration.py``.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from app.bi.jobs import bi_common


def test_the_builder_is_imported_lazily_from_the_named_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``load_builder`` is an importlib call at run time, so the worker boots
    without the module and a test can bind a stub via ``sys.modules``."""
    import sys  # noqa: PLC0415

    stub = SimpleNamespace(BUILDER_VERSION=42)
    monkeypatch.setitem(sys.modules, bi_common.BUILDER_MODULE, stub)
    assert bi_common.load_builder() is stub
    assert bi_common.BUILDER_MODULE == "app.bi.service.mart_builder"


def _runtime_import_nodes(tree: ast.AST) -> list[ast.stmt]:
    """Every import that EXECUTES when the module is imported.

    An import inside ``if TYPE_CHECKING:`` is not one: Python never evaluates
    that branch, so such an import cannot pull a service into a worker's boot and
    cannot couple two deployments — which is the entire property this guard
    protects. Excluding it makes the guard precise rather than weaker; a runtime
    import is still convicted, which ``test_the_module_level_import_guard_still_bites``
    proves against a constructed violation.
    """
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        names = {test.id} if isinstance(test, ast.Name) else set()
        if isinstance(test, ast.Attribute):
            names = {test.attr}
        if "TYPE_CHECKING" not in names:
            continue
        for inner in node.body:
            for descendant in ast.walk(inner):
                skipped.add(id(descendant))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom) and id(node) not in skipped
    ]


def _offending_bi_imports(source: str) -> list[str]:
    offenders: list[str] = []
    for node in _runtime_import_nodes(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.bi.service"):
            offenders.append(str(node.module))
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if alias.name.startswith("app.bi.service")
            )
    return offenders


def test_no_bi_handler_imports_the_builder_at_module_level() -> None:
    """The registration must ship a release before the builder is enabled."""
    from pathlib import Path  # noqa: PLC0415

    jobs_dir = Path(bi_common.__file__).parent
    for path in sorted(jobs_dir.glob("bi_*.py")):
        offenders = _offending_bi_imports(path.read_text(encoding="utf-8"))
        if offenders:
            pytest.fail(f"{path.name} imports {', '.join(offenders)} at module level")


def test_the_module_level_import_guard_still_bites() -> None:
    """The negative control. Narrowing a guard without one is how it stops working."""
    convicted = _offending_bi_imports("from app.bi.service.mart_builder import BUILDER_VERSION\n")
    assert convicted == ["app.bi.service.mart_builder"], convicted

    acquitted = _offending_bi_imports(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.bi.service.mart_builder import BUILDER_VERSION\n"
    )
    assert acquitted == [], acquitted

    # A FUNCTION-LEVEL import is convicted too, and that is deliberate rather than
    # over-strict: `sys.modules` cannot stand in for a name bound by an import
    # statement, so a test could not prove the handler boots without the service.
    # The sanctioned form is `importlib.import_module` on a named constant, which
    # is why `bi_common.load_builder` and `bi_commentary.load_commentary` exist and
    # why neither appears here.
    deferred = _offending_bi_imports(
        "def run():\n    from app.bi.service.mart_builder import BUILDER_VERSION\n"
    )
    assert deferred == ["app.bi.service.mart_builder"], deferred

    # And the sanctioned form is acquitted, because it is not an import statement.
    sanctioned = _offending_bi_imports(
        "import importlib\n"
        "_M = 'app.bi.service.mart_builder'\n"
        "def load():\n    return importlib.import_module(_M)\n"
    )
    assert sanctioned == [], sanctioned
