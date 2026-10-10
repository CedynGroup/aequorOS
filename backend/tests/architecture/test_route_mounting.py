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
Every module under ``app/`` that declares a route on a module-level router —
``@<name>.get(...)`` for ANY ``<name>`` that is not the module's own
``FastAPI(...)`` instance — must have THAT router passed to an
``include_router`` call in one of the three aggregators: the tenant API's
``app/api/router.py``, ``app/main.py``, or the operator API's
``app/operator/main.py``. Purely static, so it needs no database, no settings and
no app instance, which is what lets it run in the hermetic suite beside the other
architecture guards.

Two blind spots the first version had (audit A360-2), now closed
----------------------------------------------------------------
* It matched only a router literally named ``router``, so a module that called
  its router anything else declared nothing as far as the guard could see — and
  the ``>= 100`` population floor cannot notice ONE module going missing. The
  collector now records WHICH name each module declares routes on, the resolver
  records which ``(module, name)`` each aggregator includes, and a separate census
  requires every module-level ``APIRouter(...)`` to be either routed-and-seen or an
  aggregator, so a router in a shape the collector cannot read fails by name.
* Its self-proof re-implemented the resolver inline, so the two could drift and
  the proof would keep passing over the copy. It now calls the real functions
  against a synthetic tree.

Limits, stated rather than implied
----------------------------------
It proves a router is INCLUDED, not that any particular path is reachable: a
dependency that always refuses, or a flag that is off in every deployment, is
invisible here and is what the route sweeps in ``tests/api/`` are for. It also
resolves ``include_router(name, …)`` only when ``name`` is a plain alias of an
imported name — an aggregator that built a list of routers and looped over it
would defeat it. That limit is cheap to live with today because every aggregator
uses the one shape; if that changes, this guard must change with it rather than
be relaxed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
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


def _module_name(path: Path, root: Path = APP_ROOT) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _route_owners(tree: ast.Module) -> set[str]:
    """The names routes are declared on: ``@X.get(...)`` yields ``X``, whatever X is."""

    owners: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.attr in _ROUTE_METHODS
            ):
                owners.add(target.value.id)
    return owners


def _declares_a_route(tree: ast.Module) -> bool:
    return bool(_route_owners(tree))


def _constructed(tree: ast.Module, callee: str, *, deep: bool = False) -> set[str]:
    """Names bound to ``<callee>(...)`` — at module level, or anywhere when ``deep``."""

    found: set[str] = set()
    nodes: Iterable[ast.AST] = ast.walk(tree) if deep else tree.body
    for node in nodes:
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != callee:
            continue
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name):
                found.add(target.id)
    return found


def _router_constructors(tree: ast.Module) -> set[str]:
    return _constructed(tree, "APIRouter")


def _application_constructors(tree: ast.Module) -> set[str]:
    """Names bound to ``FastAPI(...)`` at ANY depth — the entrypoints build theirs
    inside ``create_app()``. Routes declared on these are served by the
    application itself and have nothing to be mounted into."""

    return _constructed(tree, "FastAPI", deep=True)


def _parse_tree(root: Path) -> dict[str, ast.Module]:
    trees: dict[str, ast.Module] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            trees[_module_name(path, root)] = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails louder elsewhere
            continue
    return trees


def _routed_modules(root: Path = APP_ROOT) -> dict[str, set[str]]:
    """Module -> the ROUTER names it declares routes on.

    A route declared directly on a module-level ``FastAPI(...)`` instance
    (``@app.get("/health")`` in an entrypoint) is served by that application and
    is excluded: there is no include_router call for it to be missing.
    """

    routed: dict[str, set[str]] = {}
    for module, tree in _parse_tree(root).items():
        owners = _route_owners(tree) - _application_constructors(tree)
        if owners:
            routed[module] = owners
    return routed


def _constructed_routers(root: Path = APP_ROOT) -> dict[str, set[str]]:
    """Module -> the module-level names it binds to ``APIRouter(...)``."""

    return {
        module: names
        for module, tree in _parse_tree(root).items()
        if (names := _router_constructors(tree))
    }


def _included_modules(
    aggregators: Iterable[Path] = tuple(BACKEND_ROOT / relative for relative in _AGGREGATORS),
) -> set[tuple[str, str]]:
    """``(module, name)`` pairs an aggregator passes to ``include_router``.

    ``from app.features.x import router as x_router`` then
    ``include_router(x_router)`` yields ``("app.features.x", "router")``; the
    imported name is recorded, not assumed, so a module that calls its router
    ``api`` is resolved exactly as one that calls it ``router``.
    """

    included: set[tuple[str, str]] = set()
    for path in aggregators:
        tree = ast.parse(path.read_text())
        alias_to_target: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    alias_to_target[alias.asname or alias.name] = (node.module, alias.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id in alias_to_target:
                    included.add(alias_to_target[first.id])
    return included


def _unmounted(
    routed: dict[str, set[str]], included: set[tuple[str, str]], justified: Iterable[str] = ()
) -> list[str]:
    skip = set(justified)
    return sorted(
        module
        for module, owners in routed.items()
        if module not in skip and not any((module, owner) in included for owner in owners)
    )


_ROUTED = _routed_modules()
_INCLUDED = _included_modules()
_CONSTRUCTED = _constructed_routers()
_AGGREGATOR_MODULES = frozenset(_module_name(BACKEND_ROOT / relative) for relative in _AGGREGATORS)


def test_every_module_that_declares_a_route_has_its_router_included() -> None:
    """A feature whose router nothing includes answers 404 for every path it declares."""

    unmounted = _unmounted(_ROUTED, _INCLUDED, _JUSTIFIED_UNMOUNTED)
    assert unmounted == [], (
        "these modules declare routes that NO aggregator mounts, so every path "
        f"they define 404s: {unmounted}. Add the include_router call, or remove "
        "the routes."
    )


def test_every_constructed_router_is_either_routed_or_an_aggregator() -> None:
    """The census that catches a router the collector cannot read.

    ``>= 100`` below is a floor; it cannot notice ONE module going missing. This
    can: a module that binds a module-level ``APIRouter(...)`` and, as far as
    the collector sees, declares no route on it is either an aggregator (it only
    includes other routers — the three known ones) or a shape the collector has
    stopped matching, which would silently exempt it from the mount check above.
    """

    unaccounted = sorted(
        f"{module}:{name}"
        for module, names in _CONSTRUCTED.items()
        if module not in _AGGREGATOR_MODULES
        for name in sorted(names)
        if name not in _ROUTED.get(module, set())
    )
    assert unaccounted == [], (
        "these modules build an APIRouter the route collector sees no route on: "
        f"{unaccounted}. Either the router is unused (delete it), or routes are "
        "declared in a shape _route_owners does not read — fix the collector, never "
        "exempt the module."
    )


def test_the_subject_is_the_whole_application_and_not_a_handful() -> None:
    """Anti-vacuity: the collector must actually be finding the route modules.

    If a refactor changed the decorator shape, ``_route_owners`` would quietly
    return nothing everywhere and the test above would pass over an empty set.
    """

    assert len(_ROUTED) >= 100, (
        f"only {len(_ROUTED)} route-declaring modules found, which is far below the "
        "known population — the AST collector has probably stopped matching the "
        "decorator shape, making the mount check vacuous"
    )
    assert "app.features.read_bi" in _ROUTED
    assert "app.identity.api.auth" in _ROUTED or any(m.startswith("app.api.v1.") for m in _ROUTED)
    # The census has a population too: every routed module constructs its router.
    assert set(_ROUTED) <= set(_CONSTRUCTED), sorted(set(_ROUTED) - set(_CONSTRUCTED))


def _synthetic_tree(root: Path) -> Path:
    """One mounted router, one orphan, one mounted router NOT named ``router``,
    and an entrypoint declaring a route on its ``FastAPI`` instance.

    Returns the aggregator path. The third module is the blind spot the first
    version of this file had: with the router called ``api`` it declared nothing
    as far as the guard could see, and so could never be convicted OR acquitted.
    The fourth is the shape widening the collector then convicted on the real
    tree (``app/operator/main.py``'s ``@app.get``): served by construction, so
    it must be neither routed nor unmounted.
    """

    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "entry.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return None\n"
    )
    (pkg / "mounted.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/a')\n"
        "def a():\n"
        "    return None\n"
    )
    (pkg / "orphan.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/b')\n"
        "def b():\n"
        "    return None\n"
    )
    (pkg / "renamed.py").write_text(
        "from fastapi import APIRouter\n"
        "api = APIRouter(prefix='/c')\n"
        "@api.get('/c')\n"
        "def c():\n"
        "    return None\n"
    )
    aggregator = root / "aggregate.py"
    aggregator.write_text(
        "from pkg.mounted import router as mounted_router\n"
        "from pkg.orphan import router as orphan_router\n"
        "from pkg.renamed import api as renamed_router\n"
        "app = object()\n"
        "app.include_router(mounted_router)\n"
        "app.include_router(renamed_router)\n"
    )
    return aggregator


def test_the_check_would_convict_an_unmounted_router(tmp_path: Path) -> None:
    """Self-proving case, through the REAL collector and resolver.

    A guard that has never fired proves only that it is present. This builds one
    mounted router, one unmounted, and one mounted under a name other than
    ``router``, and asserts — using the same functions the tests above use, never
    a re-implementation — that exactly the orphan is convicted.
    """

    aggregator = _synthetic_tree(tmp_path)
    routed = _routed_modules(tmp_path / "pkg")
    included = _included_modules([aggregator])

    # The collector reads every module, including the one whose router is not
    # called ``router`` — the blind spot the first version had — and leaves out
    # the entrypoint whose route hangs off its own FastAPI instance.
    assert routed == {
        "pkg.mounted": {"router"},
        "pkg.orphan": {"router"},
        "pkg.renamed": {"api"},
    }, routed
    assert "pkg.entry" not in routed, "a route on a FastAPI instance is served, not mounted"
    assert _constructed_routers(tmp_path / "pkg") == routed
    # The resolver records the imported NAME, so ``api`` is matched to ``api``.
    assert included == {("pkg.mounted", "router"), ("pkg.renamed", "api")}, included
    # Exactly the orphan.
    assert _unmounted(routed, included) == ["pkg.orphan"], (
        "the guard did not single out the unmounted router, so the check above "
        "cannot detect the defect it exists for"
    )
    assert _unmounted(routed, included, justified=["pkg.orphan"]) == []
