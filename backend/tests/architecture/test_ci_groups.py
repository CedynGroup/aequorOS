"""The CI group registry assigns every integration module to exactly one execution job."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.test_suites import GROUP_RULES, GROUPS, PG_SHARD_GROUP, group, selected_modules

BACKEND = Path(__file__).parents[2]


def test_rules_name_known_groups_and_existing_paths() -> None:
    for prefix, name in GROUP_RULES:
        assert name in GROUPS, prefix
        assert name not in {"unit", "e2e", PG_SHARD_GROUP}, f"{prefix} cannot claim {name}"
        assert (BACKEND / prefix).exists(), f"Stale group rule: {prefix}"


def test_every_rule_owns_at_least_one_integration_module() -> None:
    owned = {group(module) for module in selected_modules("integration")}
    assert {name for _, name in GROUP_RULES} <= owned


def test_every_integration_module_has_exactly_one_group() -> None:
    modules = selected_modules("integration")
    assert modules
    assert {group(module) for module in modules} <= set(GROUPS) - {"unit", "e2e"}


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("tests/db/test_rls_integration.py::test_a", "schema"),
        ("tests/storage/test_object_lock_integration.py::test_a[x]", "storage"),
        ("tests/storage/test_key_rotation_concurrency_integration.py::test_a", "locks"),
        ("tests/case/api/test_liquidity_integration.py::test_a", "locks"),
        ("tests/identity/api/test_banks_integration.py::test_a", PG_SHARD_GROUP),
        ("tests/storage/test_object_lock.py::test_a", "unit"),
        ("tests/scripts/test_local_services_e2e.py::test_a", "e2e"),
    ],
)
def test_group_resolves_first_match_and_suite(nodeid: str, expected: str) -> None:
    assert group(nodeid) == expected
