"""A router nobody includes serves nothing, and every other guard still passes.

Why this exists
---------------
``tests/architecture/test_job_enqueue_reachability.py`` asks whether a registered
job type is enqueued from somewhere a request can reach, and it walks from
``@router.get`` / ``@router.post`` decorators to find those places. Building the
AI commentary surface exposed the hole in that walk: it treats a decorated
function as reachable **without ever asking whether ``app/api/router.py``
includes the router the decorator hangs off**. Measured at the time: the
reachability guard was green while 26 of the feature's own route tests were red,
because the routes existed and nothing served them.

That is the same defect shape the other guard was written for, one level up — a
thing correctly registered in its own module and never wired to the application
— so it gets the same treatment rather than a comment.

What it checks
--------------
Every module under ``app/`` that declares a route on a module-level ``router``
must have that router passed to an ``include_router`` call in one of the three
aggregators: the tenant API's ``app/api/router.py``, ``app/main.py``, or the
operator API's ``app/operator/main.py``. Purely static, so it needs no database,
no settings and no app instance, which is what lets it run in the hermetic suite
beside the other architecture guards.

Limits, stated rather than implied
----------------------------------
It proves a router is INCLUDED, not that any particular path is reachable: a
dependency that always refuses, or a flag that is off in every deployment, is
invisible here and is what the route sweeps in ``tests/api/`` are for. It also
resolves ``include_router(name, …)`` only when ``name`` is a plain alias imported
as ``router`` — an aggregator that built a list of routers and looped over it
would defeat it. Both limits are cheap to live with today because all 108 routed
modules use the one shape; if that changes, this guard must change with it rather
than be relaxed.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"

#: The three entrypoints that mount routers. The operator API is deliberately a
#: separate application (never mounted on the tenant API — a route-isolation test
#: pins that), so its own aggregator counts too: a router included there is
#: served, just not to tenants.
_AGGREGATORS = (
    "app/api/router.py",
    "app/main.py",
    "app/operator/main.py",
)

_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "websocket"})

#: A routed module that is deliberately not mounted, with the reason. EMPTY, and
#: it should stay that way: an entry here is a set of routes nothing serves.
_JUSTIFIED_UNMOUNTED: dict[str, str] = {}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _declares_a_route(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "router"
                and target.attr in _ROUTE_METHODS
            ):
                return True
    return False


def _routed_modules() -> set[str]:
    found: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails louder elsewhere
            continue
        if _declares_a_route(tree):
            found.add(_module_name(path))
    return found


def _included_modules() -> set[str]:
    """Modules whose ``router`` an aggregator passes to ``include_router``."""

    included: set[str] = set()
    for relative in _AGGREGATORS:
        tree = ast.parse((BACKEND_ROOT / relative).read_text())
        # `from app.features.x import router as x_router` -> alias -> module
        alias_to_module: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if alias.name == "router":
                        alias_to_module[alias.asname or alias.name] = node.module
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id in alias_to_module:
                    included.add(alias_to_module[first.id])
    return included


_ROUTED = _routed_modules()
_INCLUDED = _included_modules()


def test_every_module_that_declares_a_route_has_its_router_included() -> None:
    """A feature whose router nothing includes answers 404 for every path it declares."""

    unmounted = sorted(_ROUTED - _INCLUDED - set(_JUSTIFIED_UNMOUNTED))
    assert unmounted == [], (
        "these modules declare routes that NO aggregator mounts, so every path "
        f"they define 404s: {unmounted}. Add the include_router call, or remove "
        "the routes."
    )


def test_the_subject_is_the_whole_application_and_not_a_handful() -> None:
    """Anti-vacuity: the collector must actually be finding the route modules.

    If a refactor changed the decorator shape, ``_declares_a_route`` would quietly
    return False everywhere and the test above would pass over an empty set.
    """

    assert len(_ROUTED) >= 100, (
        f"only {len(_ROUTED)} route-declaring modules found, which is far below the "
        "known population — the AST collector has probably stopped matching the "
        "decorator shape, making the mount check vacuous"
    )
    assert "app.features.read_bi" in _ROUTED
    assert "app.api.v1.auth" in _ROUTED or any(m.startswith("app.api.v1.") for m in _ROUTED)


def test_the_check_would_convict_an_unmounted_router(tmp_path: Path) -> None:
    """Self-proving case, on a synthetic aggregator and a synthetic feature.

    A guard that has never fired proves only that it is present. This builds one
    mounted and one unmounted router and asserts the difference is visible.
    """

    feature = tmp_path / "mounted.py"
    feature.write_text("router = object()\n@router.get('/a')\ndef a():\n    return None\n")
    orphan = tmp_path / "orphan.py"
    orphan.write_text("router = object()\n@router.post('/b')\ndef b():\n    return None\n")
    aggregator = tmp_path / "aggregate.py"
    aggregator.write_text(
        "from pkg.mounted import router as mounted_router\n"
        "from pkg.orphan import router as orphan_router\n"
        "app = object()\n"
        "app.include_router(mounted_router)\n"
    )

    assert _declares_a_route(ast.parse(feature.read_text()))
    assert _declares_a_route(ast.parse(orphan.read_text()))

    tree = ast.parse(aggregator.read_text())
    alias_to_module = {
        alias.asname or alias.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
        if alias.name == "router"
    }
    included = {
        alias_to_module[node.args[0].id]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in alias_to_module
    }
    assert included == {"pkg.mounted"}, included
    assert "pkg.orphan" not in included, (
        "the resolver counted an unmounted router as mounted, so the check above "
        "cannot detect the defect it exists for"
    )
