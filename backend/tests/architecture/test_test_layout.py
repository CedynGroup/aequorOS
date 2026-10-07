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

from pathlib import Path
from types import ModuleType

import pytest
from _pytest.fixtures import FixtureManager

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
    """Top-level test directories holding Python; a leftover of bytecode alone is not one."""
    return {path.name for path in TESTS.iterdir() if path.is_dir() and any(path.rglob("*.py"))}


def _root_fixture_names(manager: FixtureManager, baseid: str) -> set[str]:
    return {
        definition.argname
        for definitions in manager._arg2fixturedefs.values()
        for definition in definitions
        if definition.has_location and definition.baseid == baseid
    }


def _registered_plugins(manager: pytest.PytestPluginManager) -> set[str]:
    return {
        plugin.__name__
        for plugin in manager.get_plugins()
        if isinstance(plugin, ModuleType)
        and len(parts := plugin.__name__.split(".")) == 3
        and parts[0] == "tests"
        and parts[2] == "fixtures"
    }


def test_new_test_directories_are_named_for_a_feature() -> None:
    unknown = sorted(_test_directories() - FEATURES - SHARED_DIRECTORIES - LAYERED_DIRECTORIES)
    assert unknown == [], (
        f"Put tests under tests/<feature>/ (a feature in LAYERS), not in new layer "
        f"directories: {unknown}"
    )


def test_the_layered_directory_list_only_shrinks() -> None:
    emptied = sorted(LAYERED_DIRECTORIES - _test_directories())
    assert emptied == [], f"Remove these from LAYERED_DIRECTORIES; they are gone: {emptied}"


def test_the_root_conftest_defines_only_shared_fixtures(request: pytest.FixtureRequest) -> None:
    baseid = "/".join(ROOT_CONFTEST.parent.relative_to(request.config.rootpath).parts)
    defined = _root_fixture_names(request._fixturemanager, baseid)
    feature_specific = sorted(defined - GLOBAL_FIXTURES)
    assert feature_specific == [], (
        "Move these fixtures to tests/<feature>/fixtures.py and register the module in "
        f"pytest_plugins: {feature_specific}"
    )


def test_every_feature_fixture_module_is_registered_and_exists(pytestconfig: pytest.Config) -> None:
    on_disk = {
        f"tests.{path.parent.name}.fixtures"
        for path in TESTS.glob("*/fixtures.py")
        if path.parent.name in FEATURES
    }
    registered = _registered_plugins(pytestconfig.pluginmanager)
    assert registered == on_disk, "pytest must load exactly the feature fixture modules"


@pytest.mark.parametrize("baseid", ["tests", ""])
def test_fixture_ownership_uses_registered_names_and_all_definitions(baseid: str) -> None:
    root = ModuleType("probe_root")
    exec(
        "from pytest import fixture as alias\n"
        "@alias(name='registered_alias')\n"
        "def implementation_name():\n"
        "    return 1\n"
        "@alias\n"
        "async def async_fixture():\n"
        "    return 2\n"
        "if False:\n"
        "    @alias\n"
        "    def dead_fixture():\n"
        "        return 3\n",
        root.__dict__,
    )
    child = ModuleType("probe_child")
    exec(
        "import pytest\n"
        "@pytest.fixture(name='registered_alias')\n"
        "def child_implementation():\n"
        "    return 4\n"
        "@pytest.fixture\n"
        "def child_only():\n"
        "    return 5\n",
        child.__dict__,
    )
    config = pytest.Config.fromdictargs({}, ["--noconftest"])
    try:
        manager = FixtureManager(pytest.Session.from_config(config))
        child_baseid = f"{baseid}/fx".lstrip("/")
        manager.parsefactories(root, baseid)
        manager.parsefactories(child, child_baseid)
        assert _root_fixture_names(manager, baseid) == {"registered_alias", "async_fixture"}
        assert _root_fixture_names(manager, child_baseid) == {"registered_alias", "child_only"}
    finally:
        config._ensure_unconfigure()


def test_feature_plugins_are_identified_by_loaded_modules() -> None:
    manager = pytest.PytestPluginManager()
    manager.register(ModuleType("tests.fx.fixtures"), name="alias")
    manager.register(ModuleType("tests.unowned.fixtures"))
    manager.register(ModuleType("unrelated_plugin"))
    manager.set_blocked("tests.credit.fixtures")
    assert _registered_plugins(manager) == {"tests.fx.fixtures", "tests.unowned.fixtures"}
