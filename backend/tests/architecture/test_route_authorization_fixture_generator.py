"""The route authorization fixture stays generated from the registered apps.

The committed catalog must equal what ``scripts.route_authorization_dump``
derives from the live route tables, byte for byte, without a database or the
network, and the freshness check must refuse a hand-edited copy. The CLI runs
in subprocesses so the generator tests never mutate the surrounding pytest
environment; the pin-validation rules are exercised directly on synthetic
registrations.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.route_authorization_dump import (
    FIXTURE_PATH,
    PINNED,
    _check_unique_keys,
    _default_rules,
    _dependency_group_name,
    _PinError,
    _pinned_dependency_rules,
    _public_rules,
    _Rule,
    _unused_public_reviews,
)
from tests.fixtures.public_routes import REVIEWED_PUBLIC_ROUTES
from tests.support.route_authorization import RegisteredEndpoint

COMMITTED = FIXTURE_PATH.read_bytes()
BACKEND_ROOT = Path(__file__).parents[2]


def _run_cli(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.route_authorization_dump", *arguments],
        capture_output=True,
        cwd=BACKEND_ROOT,
        env=dict(os.environ) if env is None else env,
        check=False,
    )


def _generate(tmp_path: Path) -> bytes:
    output = tmp_path / "route_authorization.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = _run_cli("--output", str(output))
    assert completed.returncode == 0, completed.stderr.decode()
    return output.read_bytes()


def test_generator_reproduces_the_committed_fixture(tmp_path: Path) -> None:
    assert _generate(tmp_path) == COMMITTED


def test_generator_output_is_deterministic(tmp_path: Path) -> None:
    first = _generate(tmp_path / "first")
    second = _generate(tmp_path / "second")
    assert first == second


def test_generator_runs_without_database_environment(tmp_path: Path) -> None:
    """No DATABASE_URL, no TEST_DATABASE_URL, no services: still byte-exact."""
    output = tmp_path / "route_authorization.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "TEST_DATABASE_URL"}
    }
    completed = _run_cli("--output", str(output), env=env)
    assert completed.returncode == 0, completed.stderr.decode()
    assert output.read_bytes() == COMMITTED


def test_freshness_check_accepts_the_committed_fixture() -> None:
    completed = _run_cli("--check")
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"matches the registered routes" in completed.stdout


def _rule_routes(rule: dict[str, object]) -> list[object]:
    routes = rule.get("routes")
    assert isinstance(routes, list)
    return routes


def _populated_rule(catalog: dict[str, object]) -> dict[str, object]:
    """Any rule with two routes, so one can be removed or moved regardless of
    which pins the catalog carries as it evolves."""
    rules = catalog["rules"]
    assert isinstance(rules, list)
    for item in rules:
        if isinstance(item, dict) and len(_rule_routes(item)) >= 2:
            return item
    raise AssertionError("the catalog has no rule with two routes")


def test_freshness_check_reports_a_removed_route(tmp_path: Path) -> None:
    stale = tmp_path / "route_authorization.json"
    catalog = json.loads(COMMITTED)
    rule = _populated_rule(catalog)
    rule_routes = _rule_routes(rule)
    assert len(rule_routes) >= 2
    del rule_routes[1:]
    stale.write_bytes((json.dumps(catalog, indent=2) + "\n").encode())

    completed = _run_cli("--check", "--fixture", str(stale))
    assert completed.returncode == 1
    assert b"risk-service:route-authorization" in completed.stderr


def test_freshness_check_reports_a_moved_route(tmp_path: Path) -> None:
    stale = tmp_path / "route_authorization.json"
    catalog = json.loads(COMMITTED)
    source = _populated_rule(catalog)
    moved = _rule_routes(source).pop(0)
    rules = catalog["rules"]
    assert isinstance(rules, list)
    target: dict[str, object] | None = None
    for item in rules:
        if isinstance(item, dict) and item is not source and len(_rule_routes(item)) >= 1:
            target = item
            break
    assert target is not None
    _rule_routes(target).append(moved)
    stale.write_bytes((json.dumps(catalog, indent=2) + "\n").encode())

    completed = _run_cli("--check", "--fixture", str(stale))
    assert completed.returncode == 1
    assert b"risk-service:route-authorization" in completed.stderr


_PINNED_DEPS = ("app.api.deps:get_current_principal", "app.api.deps:require_scoped_mutation_tenant")


def _pinned_rows() -> tuple[
    dict[tuple[str, str, str], RegisteredEndpoint], list[tuple[str, str, str, str]]
]:
    """Synthetic registrations that satisfy every pinned dependency group, so
    individual pins can be drifted in isolation. Every route of a group shares
    one dependency set, as the committed catalog requires."""
    by_key: dict[tuple[str, str, str], RegisteredEndpoint] = {}
    target_routes: list[tuple[str, str, str, str]] = []
    for name, spec in PINNED.items():
        routes = spec.get("routes")
        assert isinstance(routes, list) and routes
        for route in routes:
            key = (route[0], route[1], route[2])
            assert key not in by_key, f"pinned route {key} appears in two groups"
            by_key[key] = RegisteredEndpoint(
                key=key,
                endpoint=route[3],
                dependencies=_PINNED_DEPS,
                authentication=frozenset({route[0]}),
            )
        if name == "tenant-dependencies-liq-scoped-configure":
            target_routes = routes
    return by_key, target_routes


def test_pinned_dependency_rules_accept_matching_registrations() -> None:
    by_key, routes = _pinned_rows()
    rules, assigned = _pinned_dependency_rules(by_key)
    name = "tenant-dependencies-liq-scoped-configure"
    assert set(rules) == set(PINNED)
    assert assigned == set(by_key)
    assert rules[name].dependencies == list(_PINNED_DEPS)


def test_pinned_dependency_rules_reject_a_changed_handler() -> None:
    by_key, routes = _pinned_rows()
    first = routes[0]
    drifted = by_key[(first[0], first[1], first[2])]
    by_key[(first[0], first[1], first[2])] = RegisteredEndpoint(
        key=drifted.key,
        endpoint="app.liquidity.api.manage_liquidity_thresholds:some_new_handler",
        dependencies=drifted.dependencies,
        authentication=drifted.authentication,
    )
    with pytest.raises(_PinError, match="records app\\.liquidity"):
        _pinned_dependency_rules(by_key)


def test_pinned_dependency_rules_reject_a_lost_authenticator() -> None:
    by_key, routes = _pinned_rows()
    first = routes[0]
    drifted = by_key[(first[0], first[1], first[2])]
    by_key[(first[0], first[1], first[2])] = RegisteredEndpoint(
        key=drifted.key,
        endpoint=drifted.endpoint,
        dependencies=drifted.dependencies,
        authentication=frozenset(),
    )
    with pytest.raises(_PinError, match="no longer registers the tenant authenticator"):
        _pinned_dependency_rules(by_key)


def test_pinned_dependency_rules_reject_a_drifted_dependency_set() -> None:
    by_key, routes = _pinned_rows()
    first = routes[0]
    drifted = by_key[(first[0], first[1], first[2])]
    by_key[(first[0], first[1], first[2])] = RegisteredEndpoint(
        key=drifted.key,
        endpoint=drifted.endpoint,
        dependencies=("app.api.deps:get_current_principal", "app.api.deps:new_dependency"),
        authentication=drifted.authentication,
    )
    with pytest.raises(_PinError, match="different registered dependency sets"):
        _pinned_dependency_rules(by_key)


def test_public_rules_reject_a_handler_outside_its_review() -> None:
    key = ("tenant", "GET", "/openapi.json")
    review = REVIEWED_PUBLIC_ROUTES[key]
    drifted = RegisteredEndpoint(
        key=key,
        endpoint="app.main:some_new_handler",
        dependencies=(),
        authentication=frozenset(),
    )
    with pytest.raises(_PinError, match="the review records"):
        _public_rules([drifted])
    rules = _public_rules(
        [
            RegisteredEndpoint(
                key=key,
                endpoint=review.endpoint,
                dependencies=(),
                authentication=frozenset(),
            )
        ]
    )
    assert f"public-{review.endpoint.replace(':', '-')}" in rules


def test_public_rules_reject_a_registered_authenticator() -> None:
    key = ("tenant", "GET", "/openapi.json")
    review = REVIEWED_PUBLIC_ROUTES[key]
    wired = RegisteredEndpoint(
        key=key,
        endpoint=review.endpoint,
        dependencies=(),
        authentication=frozenset({"tenant"}),
    )
    with pytest.raises(_PinError, match="cannot conceal authentication wiring"):
        _public_rules([wired])


def test_public_rules_reject_a_merged_dependency_profile() -> None:
    """Two reviewed routes that share a handler must not inherit each other's
    dependencies through a union."""
    schema = REVIEWED_PUBLIC_ROUTES[("tenant", "GET", "/openapi.json")]
    rows = [
        RegisteredEndpoint(
            key=("tenant", "GET", "/openapi.json"),
            endpoint=schema.endpoint,
            dependencies=("fastapi.applications:FastAPI.setup.<locals>.openapi",),
            authentication=frozenset(),
        ),
        RegisteredEndpoint(
            key=("tenant", "HEAD", "/openapi.json"),
            endpoint=schema.endpoint,
            dependencies=(),
            authentication=frozenset(),
        ),
    ]
    with pytest.raises(_PinError, match="different registered dependency sets"):
        _public_rules(rows)


def _public_rows() -> list[RegisteredEndpoint]:
    return [
        RegisteredEndpoint(
            key=key,
            endpoint=review.endpoint,
            dependencies=(),
            authentication=frozenset(),
        )
        for key, review in REVIEWED_PUBLIC_ROUTES.items()
    ]


def test_default_rules_reject_an_ambiguous_authenticator_set() -> None:
    """Grouping must not depend on frozenset iteration order."""
    ambiguous = RegisteredEndpoint(
        key=("tenant", "GET", "/api/v1/example"),
        endpoint="app.example.api:list_example",
        dependencies=("app.api.deps:get_current_principal",),
        authentication=frozenset({"tenant", "operator"}),
    )
    with pytest.raises(_PinError, match="expected exactly one"):
        _default_rules([ambiguous], set(), {})


def test_unused_public_reviews_are_rejected() -> None:
    assert _unused_public_reviews(_public_rows()) == []
    first_key = next(iter(REVIEWED_PUBLIC_ROUTES))
    missing = _unused_public_reviews(_public_rows()[1:])
    assert missing == [first_key]


def test_default_rules_refuse_to_reuse_a_pinned_dependency_group_name() -> None:
    """A new route with the pinned mapping-config dependency profile cannot
    silently join the pinned rule; generation fails until the pin is updated."""
    catalog = json.loads(COMMITTED)
    pinned = next(
        item
        for item in catalog["rules"]
        if item["name"] == "tenant-dependencies-ee37d1b6984c"
    )
    dependencies = tuple(pinned["dependencies"])
    assert _dependency_group_name("tenant", list(dependencies)) == (
        "tenant-dependencies-ee37d1b6984c"
    )
    intruder = RegisteredEndpoint(
        key=("tenant", "POST", "/api/v1/banks/{bank_id}/mapping-configs-import"),
        endpoint="app.mapping.api.manage_mapping_configs:import_mapping_configs",
        dependencies=dependencies,
        authentication=frozenset({"tenant"}),
    )
    pinned_rule = _Rule(
        name="tenant-dependencies-ee37d1b6984c",
        kind="dependency",
        basis="pinned basis",
        references=["tests/fixtures/public_routes.py:REVIEWED_PUBLIC_ROUTES"],
        authentication="tenant",
        dependencies=list(dependencies),
        routes=[],
    )
    with pytest.raises(_PinError, match="collides with an existing rule"):
        _default_rules([intruder], set(), {"tenant-dependencies-ee37d1b6984c": pinned_rule})


def test_duplicate_route_keys_are_rejected() -> None:
    """A duplicated registration must stop generation instead of silently
    collapsing into one row while both rows reach the grouping."""
    row = RegisteredEndpoint(
        key=("tenant", "GET", "/api/v1/example"),
        endpoint="app.example.api:list_example",
        dependencies=("app.api.deps:get_current_principal",),
        authentication=frozenset({"tenant"}),
    )
    with pytest.raises(_PinError, match="appear more than once"):
        _check_unique_keys([row, row])
    _check_unique_keys([row])


def test_importing_the_generator_does_not_mutate_the_environment() -> None:
    """The environment guards belong to the run, not to the import. The check
    runs in a child process with the guard keys cleared there, so neither the
    pytest process environment nor the module bindings of the other tests are
    touched."""
    guards = ("RUN_INPROCESS_WORKER", "OPERATOR_DATABASE_URL", "OPERATOR_DEV_AUTH_ENABLED")
    code = "\n".join(
        [
            "import os, sys",
            "for name in ('RUN_INPROCESS_WORKER', 'OPERATOR_DATABASE_URL',",
            "             'OPERATOR_DEV_AUTH_ENABLED'):",
            "    os.environ.pop(name, None)",
            "import scripts.route_authorization_dump",
            "leaked = [name for name in ('RUN_INPROCESS_WORKER',",
            "         'OPERATOR_DATABASE_URL', 'OPERATOR_DEV_AUTH_ENABLED')",
            "         if name in os.environ]",
            "assert leaked == [], leaked",
        ]
    )
    env = {key: value for key, value in os.environ.items() if key not in guards}
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
