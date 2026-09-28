"""Every registered job type must have an enqueue site a request can reach.

Why this guard exists
---------------------
``bi_alert_evaluate`` shipped with a job type, a lane, a stale-window override,
a handler, a model, a full evaluation service and its tests — and NO enqueue
site anywhere in ``app/``. Alerts therefore never evaluated and ``on_new_data``
never fired. Nothing caught it: the parity test in
``tests/services/test_desk_capture_job.py`` proves ``JOB_TYPES`` and ``HANDLERS``
cover each other (the defect it was written for, an orphaned type nobody could
CLAIM), but a type that is claimable and never ENQUEUED looks identical to a
healthy one from every registry's point of view. The feature is simply inert.

So this guard asks the other half of the question, and it asks it about
REACHABILITY rather than mere presence. A call site is not enough: the alerts
defect HAD a call site — ``alerts.enqueue_evaluation`` existed and was complete
— it just had no caller. A guard that only grepped for the job-type string, or
only found ``job_queue.enqueue`` calls, would have passed on the broken code.

What it does
------------
1. Parses every module under ``app/`` and builds a static call graph.
2. Finds every ``job_queue.enqueue`` call and resolves its ``job_type``
   argument through module-level string constants, across modules.
3. Walks the graph from the platform's real entry points: route handlers
   (``@router.get`` … ``@router.post``), the worker module, and any function
   named as a VALUE (a handler table entry, a ``Depends``, a callback) — since
   those are how the platform actually invokes work.
4. Requires every job type to have at least one enqueue site inside a function
   the walk reaches.

The BI job modules bind their services through ``importlib`` rather than an
``import`` statement (the plane-boundary guard forbids the statement, so
``app/jobs/*`` reaches its service through ``bi_common.load_module`` or an
accessor returning ``importlib.import_module(...)``). The resolver understands
both shapes, because otherwise every BI enqueue site would be a false negative
and the guard would have to be switched off for exactly the tracks that needed
it most.

Limits, stated honestly
-----------------------
This is static analysis: it proves a PATH EXISTS, not that the path executes
(a feature flag defaulting to off still makes the job inert at run time, which
is what ``any_scheduling_enabled`` and the deployment notes in AGENTS.md are
for). It can also be defeated by a dispatch shape it does not model. It is a
floor, not a proof — but the floor it sets is the one the alerts defect fell
through.

One gap in particular is NOT this file's and must not be assumed away: a
``@router.post`` decorator counts as an entry point here **without any check that
``app/api/router.py`` includes the router it hangs off**. That is the same defect
shape one level up, and building the commentary surface demonstrated it — this
guard was green while 26 of that feature's own route tests were red, because the
routes existed and nothing served them. ``test_route_mounting.py`` is the sibling
that closes it. Neither file is sufficient alone.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

from app.services.job_queue import JOB_TYPES

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: The registries themselves. Naming a job type here is declaring it exists, not
#: enqueueing it, so a site found in these files would be circular.
_REGISTRY_FILES = frozenset({"services/job_queue.py", "worker.py"})

#: Decorator attributes that mark a function as reachable from a request.
_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "websocket", "on_event"})
_ROUTE_OWNERS = frozenset({"router", "app"})

#: A job type whose only enqueue site is unreachable, with the reason it is
#: nonetheless correct. EMPTY, and it should stay that way: an entry here is a
#: feature that cannot be triggered, which is the defect this file exists to
#: catch. Add one only with a reason a reviewer can check, never to get green.
_JUSTIFIED_INERT: dict[str, str] = {}


def _module_name(path: Path, root: Path = APP_ROOT) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Index:
    """Everything the walk needs, computed once over the whole application."""

    def __init__(self, root: Path = APP_ROOT) -> None:
        self.trees: dict[str, ast.Module] = {}
        self.rel: dict[str, str] = {}
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            name = _module_name(path, root)
            self.trees[name] = ast.parse(path.read_text())
            self.rel[name] = str(path.relative_to(root))
        self.consts = {m: _string_constants(t) for m, t in self.trees.items()}
        self.imports = {m: _imports(t, m) for m, t in self.trees.items()}
        self.defs: dict[str, ast.AST] = {}
        self.owner: dict[int, str] = {}
        for module, tree in self.trees.items():
            self._collect_defs(module, tree.body, "")
        self.module_aliases = {m: self._aliases(m) for m in self.trees}

    def _collect_defs(self, module: str, body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{module}:{prefix}{node.name}"
                self.defs[qualname] = node
                for descendant in ast.walk(node):
                    self.owner.setdefault(id(descendant), qualname)
                self._collect_defs(module, node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.ClassDef):
                self._collect_defs(module, node.body, f"{prefix}{node.name}.")

    def _aliases(self, module: str) -> dict[str, str]:
        """Local name -> module it refers to, including the importlib seams."""

        _, aliases = self.imports[module]
        resolved = dict(aliases)
        tree = self.trees[module]
        # 1. bi_alerts = bi_common.load_module("app.services.bi.alerts")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            dotted = self._dynamic_module(node.value, module)
            if dotted is not None:
                resolved[target.id] = dotted
        # 2. def _alerts_module(): return importlib.import_module(ALERTS_MODULE)
        for qualname, node in self.defs.items():
            if not qualname.startswith(f"{module}:"):
                continue
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            body = [stmt for stmt in node.body if not isinstance(stmt, ast.Expr)]
            if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
                continue
            dotted = self._dynamic_module(body[0].value, module)
            if dotted is not None:
                resolved[qualname.split(":", 1)[1]] = dotted
        return resolved

    def _dynamic_module(self, expr: ast.expr, module: str) -> str | None:
        """``importlib.import_module(x)`` / ``*.load_module(x)`` -> the module x names."""

        if not isinstance(expr, ast.Call) or not expr.args:
            return None
        func = expr.func
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr not in {"import_module", "load_module"}:
            return None
        dotted = self.string(expr.args[0], module)
        return dotted if dotted in self.trees else None

    def string(self, expr: ast.expr | None, module: str) -> str | None:
        """Resolve an expression to a string, following module-level constants."""

        if expr is None:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name):
            return self._name_string(expr, module)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            return self._attribute_string(expr, module)
        return None

    def _name_string(self, expr: ast.Name, module: str) -> str | None:
        """A bare name: this module's own constant, or one it imported."""

        local = self.consts.get(module, {}).get(expr.id)
        if local is not None:
            return local
        names, _ = self.imports[module]
        if expr.id not in names:
            return None
        source, original = names[expr.id]
        return self.consts.get(source, {}).get(original)

    def _attribute_string(self, expr: ast.Attribute, module: str) -> str | None:
        """``other_module.CONSTANT``, however ``other_module`` was bound here."""

        names, aliases = self.imports[module]
        assert isinstance(expr.value, ast.Name)  # noqa: S101 - guarded by the caller
        for candidate in _alias_candidates(expr.value.id, names, aliases):
            value = self.consts.get(candidate, {}).get(expr.attr)
            if value is not None:
                return value
        return None

    def call_targets(self, func: ast.expr, module: str) -> list[str]:
        names, _ = self.imports[module]
        aliases = self.module_aliases[module]
        found: list[str] = []
        if isinstance(func, ast.Name):
            found.append(f"{module}:{func.id}")
            if func.id in names:
                source, original = names[func.id]
                found.append(f"{source}:{original}")
        elif isinstance(func, ast.Attribute):
            holder = func.value
            # module.function(...), including an importlib-bound alias
            if isinstance(holder, ast.Name):
                for candidate in _alias_candidates(holder.id, names, aliases):
                    found.append(f"{candidate}:{func.attr}")
            # _alerts_module().function(...)
            elif isinstance(holder, ast.Call) and isinstance(holder.func, ast.Name):
                target = aliases.get(holder.func.id)
                if target is not None:
                    found.append(f"{target}:{func.attr}")
        return [qualname for qualname in found if qualname in self.defs]

    def is_enqueue(self, func: ast.expr, module: str) -> bool:
        names, aliases = self.imports[module]
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "enqueue"
            and isinstance(func.value, ast.Name)
        ):
            return any(
                candidate.endswith("job_queue")
                for candidate in _alias_candidates(func.value.id, names, aliases)
            )
        if isinstance(func, ast.Name):
            source_and_name = names.get(func.id)
            return source_and_name is not None and source_and_name[0].endswith("services.job_queue")
        return False


def _alias_candidates(
    name: str, names: dict[str, tuple[str, str]], aliases: dict[str, str]
) -> list[str]:
    candidates: list[str] = []
    if name in aliases:
        candidates.append(aliases[name])
    if name in names:
        source, original = names[name]
        candidates.append(f"{source}.{original}")
    return candidates


def _string_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.target.id] = node.value.value
    return out


def _imports(tree: ast.Module, module: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    names: dict[str, tuple[str, str]] = {}
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = module.rsplit(".", node.level)[0] if "." in module else "app"
                base = f"{package}.{base}" if base else package
            for alias in node.names:
                local = alias.asname or alias.name
                names[local] = (base, alias.name)
                aliases[local] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[-1]] = alias.name
    return names, aliases


def _route_entry_points(module: str, tree: ast.Module) -> set[str]:
    """Functions a REQUEST can reach: the ``@router.<method>`` decorated ones."""

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in _ROUTE_OWNERS
                and target.attr in _ROUTE_METHODS
            ):
                found.add(f"{module}:{node.name}")
    return found


def _value_entry_points(index: _Index, module: str, tree: ast.Module) -> set[str]:
    """Functions named as a VALUE, which is how the platform invokes most work.

    ``HANDLERS["bi_export"] = bi_export.run_bi_export`` never CALLS the handler, and
    neither does ``Depends(require_bi_read)`` or a callback appended to a list. A
    call-graph walk that only followed calls would find the whole worker fleet
    unreachable, so a mention as a value counts as an entry.
    """

    names, aliases = index.imports[module]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            continue
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            for candidate in _alias_candidates(node.value.id, names, aliases):
                qualname = f"{candidate}:{node.attr}"
                if qualname in index.defs:
                    found.add(qualname)
        elif isinstance(node, ast.Name) and node.id in names:
            source, original = names[node.id]
            qualname = f"{source}:{original}"
            if qualname in index.defs:
                found.add(qualname)
    return found


def _enqueue_argument(node: ast.Call) -> ast.expr | None:
    """``job_type`` is the third positional parameter of ``job_queue.enqueue``."""

    for keyword in node.keywords:
        if keyword.arg == "job_type":
            return keyword.value
    return node.args[2] if len(node.args) >= 3 else None


def _analyse(root: Path = APP_ROOT) -> tuple[dict[str, set[str]], set[str], list[str]]:
    """Return (job type -> enqueueing functions, reachable functions, unresolved sites)."""

    index = _Index(root)
    worker_module = f"{root.name}.worker"
    graph: dict[str, set[str]] = {}
    entry_points: set[str] = {q for q in index.defs if q.startswith(f"{worker_module}:")}
    sites: dict[str, set[str]] = {}
    unresolved: list[str] = []

    for module, tree in index.trees.items():
        in_registry = index.rel[module] in _REGISTRY_FILES
        entry_points |= _route_entry_points(module, tree)
        entry_points |= _value_entry_points(index, module, tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            caller = index.owner.get(id(node))
            if caller is not None:
                for target in index.call_targets(node.func, module):
                    graph.setdefault(caller, set()).add(target)
            if in_registry or caller is None or not index.is_enqueue(node.func, module):
                continue
            job_type = index.string(_enqueue_argument(node), module)
            if job_type is None:
                unresolved.append(f"{module}:{node.lineno}")
            else:
                sites.setdefault(job_type, set()).add(caller)

    reachable: set[str] = set()
    pending = deque(entry_points)
    while pending:
        current = pending.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(graph.get(current, ()))
    return sites, reachable, unresolved


_SITES, _REACHABLE, _UNRESOLVED = _analyse()


def test_every_enqueue_call_names_a_resolvable_job_type() -> None:
    """A job type computed at run time cannot be checked by anything.

    ``job_queue.enqueue`` validates the name against ``JOB_TYPES`` at the insert,
    so a dynamic name fails loudly rather than corrupting the queue — but it also
    means no static check, this one included, can tell which feature owns it.
    Keep every call site's type a literal or a module constant.
    """

    assert _UNRESOLVED == [], (
        "these job_queue.enqueue calls pass a job_type this guard cannot resolve, "
        f"so the type they enqueue is unverifiable: {_UNRESOLVED}"
    )


def test_every_job_type_has_an_enqueue_site() -> None:
    """A type nothing enqueues is a feature that never runs (the alerts defect)."""

    missing = sorted(set(JOB_TYPES) - set(_SITES))
    assert missing == [], (
        "these job types are registered, handled and claimable, but NOTHING in "
        f"app/ enqueues them, so they can never run: {missing}. Either add the "
        "enqueue site the feature needs, or remove the registration."
    )


def test_every_job_types_enqueue_site_is_reachable() -> None:
    """And a site nothing calls is the same defect wearing a call site.

    ``alerts.enqueue_evaluation`` was complete, correct and called by nobody.
    """

    unreachable = sorted(
        job_type
        for job_type, callers in _SITES.items()
        if job_type in JOB_TYPES and job_type not in _JUSTIFIED_INERT and not (callers & _REACHABLE)
    )
    assert unreachable == [], (
        "every enqueue site for these job types sits in a function no route, "
        "handler or callback reaches, so the feature cannot be triggered: "
        f"{ {job: sorted(_SITES[job]) for job in unreachable} }"
    )


def test_no_enqueue_site_is_registered_for_an_unknown_job_type() -> None:
    """The reverse direction: a site for a type ``JOB_TYPES`` does not list."""

    unknown = sorted(set(_SITES) - set(JOB_TYPES))
    assert unknown == [], f"these job types are enqueued but not registered in JOB_TYPES: {unknown}"


def test_the_guard_can_actually_see_the_bi_importlib_seam() -> None:
    """Negative control: without the importlib resolution this guard is vacuous.

    ``app/jobs/*`` may not write an ``import`` statement for its service (the
    plane-boundary guard forbids it), so every BI enqueue site is reached only
    through ``bi_common.load_module`` or an ``importlib.import_module``
    accessor. If this assertion fails, the resolver stopped following that seam
    and the reachability test above is passing for the wrong reason.
    """

    assert "app.jobs.bi_mart_refresh:run_bi_mart_refresh" in _REACHABLE
    alert_callers = _SITES.get("bi_alert_evaluate", set())
    assert alert_callers, "the alerts enqueue site disappeared"
    assert alert_callers & _REACHABLE, (
        "the mart build's alert enqueue is no longer reachable, which is exactly "
        "the defect this file was written for"
    )


def _synthetic_app(root: Path) -> None:
    """A miniature application with one reachable and one unreachable enqueue.

    Deliberately exercises all four shapes the resolver has to understand: a
    module-level constant as the job type, an enqueue reached through a route
    decorator, an enqueue reached only through the ``importlib`` seam, and an
    enqueue in a function nobody calls.
    """

    (root / "services").mkdir(parents=True)
    (root / "jobs").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "services" / "__init__.py").write_text("")
    (root / "jobs" / "__init__.py").write_text("")
    (root / "services" / "job_queue.py").write_text(
        "JOB_TYPES = ('reachable_type', 'seam_type', 'orphan_type')\n"
        "def enqueue(db, organization_id, job_type, **kw):\n"
        "    return None\n"
    )
    (root / "services" / "work.py").write_text(
        "from app.services import job_queue\n"
        "REACHABLE = 'reachable_type'\n"
        "SEAM = 'seam_type'\n"
        "ORPHAN = 'orphan_type'\n"
        "def enqueue_reachable(db, org):\n"
        "    return job_queue.enqueue(db, org, REACHABLE)\n"
        "def enqueue_through_the_seam(db, org):\n"
        "    return job_queue.enqueue(db, org, SEAM)\n"
        "def enqueue_orphan(db, org):\n"
        "    return job_queue.enqueue(db, org, ORPHAN)\n"
    )
    # The routed entry point, two calls deep, so the walk has to traverse.
    (root / "features.py").write_text(
        "from app.services import work\n"
        "router = object()\n"
        "@router.post('/x')\n"
        "def handler(db, org):\n"
        "    return _inner(db, org)\n"
        "def _inner(db, org):\n"
        "    return work.enqueue_reachable(db, org)\n"
    )
    # The importlib seam: a job module that may not import its service.
    (root / "jobs" / "runner.py").write_text(
        "import importlib\n"
        "WORK_MODULE = 'app.services.work'\n"
        "def _work():\n"
        "    return importlib.import_module(WORK_MODULE)\n"
        "def run_runner(db, job):\n"
        "    return _work().enqueue_through_the_seam(db, job)\n"
    )
    (root / "worker.py").write_text(
        "from app.jobs import runner\nHANDLERS = {'seam_type': runner.run_runner}\n"
    )


def test_the_guard_convicts_an_unreachable_site_and_acquits_a_reachable_one(
    tmp_path: Path,
) -> None:
    """The self-proving case. A guard that has never fired proves only that it is present.

    This is the negative control for the whole file: it builds a tiny application
    in which one job type is enqueued from a routed handler, one only through the
    ``importlib`` seam a BI job module must use, and one from a function nobody
    calls — then asserts the analysis separates them. If this test ever passes
    vacuously, the three assertions over the real application above are worthless.
    """

    root = tmp_path / "app"
    _synthetic_app(root)
    sites, reachable, unresolved = _analyse(root)

    assert unresolved == [], "the resolver failed to read a module-level constant"
    assert set(sites) == {"reachable_type", "seam_type", "orphan_type"}, sites

    # ACQUITTED: reached from a route decorator, two calls deep.
    assert sites["reachable_type"] & reachable, (
        "a site reached from a routed handler was judged unreachable, so the walk "
        "does not follow calls and every acquittal in this file is meaningless"
    )
    # ACQUITTED: reached only through importlib, from a handler named as a value.
    assert sites["seam_type"] & reachable, (
        "a site reached only through the importlib seam was judged unreachable; "
        "without this the guard would have to be switched off for every BI job"
    )
    # CONVICTED: the whole point.
    assert not (sites["orphan_type"] & reachable), (
        "an enqueue site in a function NOTHING calls was judged reachable, so this "
        "guard cannot detect the defect it exists for"
    )
