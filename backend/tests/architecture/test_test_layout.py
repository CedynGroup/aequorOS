"""Tests mirror the feature layout, and the root conftest holds only shared fixtures.

The source tree is moving to ``app/<feature>/`` (CODEBASE_CONVENTIONS.md §5) and the
tests follow it to ``tests/<feature>/``. Two rules keep new tests from landing in
the old shape while the move is under way:

* a new top-level test directory is named for a feature (``LAYERS`` in
  ``test_feature_boundaries.py``) or is one of the suites that cut across
  features. The layer-named directories the tests still use are listed so the
  list can only shrink: once one is empty it must leave the list;
* ``tests/conftest.py`` defines only what every feature shares. A fixture that
  belongs to one feature goes in ``tests/<feature>/fixtures.py``, registered by
  the root ``pytest_plugins``, and each such module must be registered.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.test_feature_boundaries import COMPOSITION, KERNEL, LAYERS

TESTS = Path(__file__).parents[1]
ROOT_CONFTEST = TESTS / "conftest.py"

#: Suites that cut across features by design: the guards, shared helpers and data,
#: the read-only primary-database suite, cross-engine equivalence, and the tests of
#: ``backend/scripts``. ``core``, ``db`` and ``storage`` mirror kernel directories.
SHARED_DIRECTORIES = frozenset(
    {
        "architecture",
        "core",
        "db",
        "equivalence",
        "fixtures",
        "live_data",
        "scripts",
        "storage",
        "support",
    }
)

#: Layer-named directories the tests still use. Remove an entry once the feature
#: moves have emptied it; never add one.
LAYERED_DIRECTORIES = frozenset(
    {
        "adapters",
        "api",
        "domain",
        "etl",
        "factories",
        "features",
        "ml",
        "models",
        "schemas",
        "services",
    }
)

#: Settings, the database, session and client, storage fakes, the demo tenants and
#: the hermetic guards: the only fixtures every feature's tests share.
GLOBAL_FIXTURES = frozenset(
    {
        "_bound_test_sessionmaker",
        "_committing_test_database",
        "_forbid_real_model_clients",
        "_real_bound_sessionmaker",
        "_rollback_db_client",
        "_rollback_db_session",
        "_shared_app",
        "_shared_committing_database",
        "_shared_test_database",
        "api_factories",
        "clear_settings_cache",
        "client",
        "committing_db_client",
        "committing_db_session",
        "db_client",
        "db_session",
        "db_settings",
        "fake_storage",
        "fresh_settings_cache",
        "hermetic_etl_models",
        "real_client",
        "real_session",
        "storage_engine",
        "tenant_ctx",
        "test_settings",
    }
)

FEATURES = frozenset(LAYERS) - {KERNEL, COMPOSITION}


def _test_directories() -> set[str]:
    return {path.name for path in TESTS.iterdir() if path.is_dir() and path.name != "__pycache__"}


def _is_fixture(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr == "fixture"


def _root_conftest() -> ast.Module:
    return ast.parse(ROOT_CONFTEST.read_text(encoding="utf-8"))


def _registered_plugins() -> list[str]:
    for node in _root_conftest().body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            return [ast.literal_eval(element) for element in node.value.elts]
    return []


def test_new_test_directories_are_named_for_a_feature() -> None:
    unknown = sorted(_test_directories() - FEATURES - SHARED_DIRECTORIES - LAYERED_DIRECTORIES)
    assert unknown == [], (
        f"Put tests under tests/<feature>/ (a feature in LAYERS), not in new layer "
        f"directories: {unknown}"
    )


def test_the_layered_directory_list_only_shrinks() -> None:
    emptied = sorted(LAYERED_DIRECTORIES - _test_directories())
    assert emptied == [], f"Remove these from LAYERED_DIRECTORIES; they are gone: {emptied}"


def test_the_root_conftest_defines_only_shared_fixtures() -> None:
    defined = {
        node.name
        for node in _root_conftest().body
        if isinstance(node, ast.FunctionDef) and any(map(_is_fixture, node.decorator_list))
    }
    feature_specific = sorted(defined - GLOBAL_FIXTURES)
    assert feature_specific == [], (
        "Move these fixtures to tests/<feature>/fixtures.py and register the module in "
        f"pytest_plugins: {feature_specific}"
    )


def test_every_feature_fixture_module_is_registered_and_exists() -> None:
    on_disk = sorted(
        f"tests.{path.parent.name}.fixtures"
        for path in TESTS.glob("*/fixtures.py")
        if path.parent.name in FEATURES
    )
    registered = _registered_plugins()
    assert registered == sorted(registered), "keep pytest_plugins sorted"
    assert registered == on_disk, (
        "tests/conftest.py pytest_plugins must list exactly the feature fixture modules"
    )
