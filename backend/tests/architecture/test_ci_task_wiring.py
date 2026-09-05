"""Every CI gate is a `mise run` away from doing nothing at all.

Audit finding P0-15: commit ``f33e869`` moved ``backend/mise.toml`` to
``backend/docs/mise.toml``. The root tasks delegate with ``dir = "backend"`` and
``run = "mise run risk-service:<task>"``, which had resolved to the backend
file; with that file gone every task re-resolved to ITSELF. `mise run
risk-service:lint` recursed until the runner gave up — lint, typecheck, tests,
the Postgres suites and the API-freshness hook all stopped executing, and
nothing about the output said so. Backend CI was dead at HEAD.

The failure mode is silent by construction, so it needs a test rather than a
convention.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[3]
ROOT_TASKS = REPO_ROOT / "mise.toml"
BACKEND_TASKS = REPO_ROOT / "backend" / "mise.toml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_DELEGATION = re.compile(r"^mise\s+run\s+(\S+)")
_WORKFLOW_INVOCATION = re.compile(r"mise\s+run\s+([A-Za-z0-9_.:-]+)")
# Full-line comments are stripped before a workflow is scanned. A header that
# EXPLAINS this machinery is documentation, not a gate, and conflating the two
# forced every comment about it to be written in circumlocution — which is a
# large part of why the machinery went unexplained until it broke.
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _tasks(path: Path) -> dict[str, dict[str, object]]:
    document = tomllib.loads(path.read_text())
    tasks = document.get("tasks", {})
    assert isinstance(tasks, dict)
    return {name: body for name, body in tasks.items() if isinstance(body, dict)}


def _needs(job: dict[str, object]) -> list[str]:
    needs = job.get("needs")
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, list):
        return [item for item in needs if isinstance(item, str)]
    return []


def _workflow_body(path: Path) -> str:
    """Workflow text with full-line comments removed."""
    return _COMMENT_LINE.sub("", path.read_text())


def _run_commands(body: dict[str, object]) -> list[str]:
    run = body.get("run")
    if isinstance(run, str):
        return [run]
    if isinstance(run, list):
        return [item for item in run if isinstance(item, str)]
    return []


def test_the_backend_task_file_is_where_the_root_tasks_delegate_to() -> None:
    assert BACKEND_TASKS.is_file(), (
        f"{BACKEND_TASKS} is missing. Root tasks delegate into backend/ with "
        "`mise run risk-service:<task>`; without this file every one of them "
        "resolves back to itself and recurses instead of running anything."
    )


def test_no_root_task_delegates_to_itself() -> None:
    """The exact shape of the outage: a delegation that resolves to its caller.

    The invariant, in full: for every task in the root ``mise.toml`` whose body
    delegates to its OWN name, ``backend/mise.toml`` must define a task of that
    name whose body does NOT delegate to that name. The first half catches what
    ``f33e869`` shipped — the counterpart file moved away. The second half
    catches the variant nobody has hit yet: a counterpart that exists but is
    itself a proxy, so the chain still terminates in nothing.

    Deliberately NOT asserted: that the two files hold the same task set. Root
    has two tasks backend does not — ``risk-service:api-typecheck`` runs pnpm in
    the workspace root and ``risk-service:api-fresh`` composes it with the client
    generator. Neither delegates into backend/, and a set-equality assertion
    would convict both wrongly.
    """
    backend_tasks = _tasks(BACKEND_TASKS)
    self_delegating: list[str] = []
    unresolved: list[str] = []
    proxy_to_a_proxy: list[str] = []

    for name, body in _tasks(ROOT_TASKS).items():
        if body.get("dir") != "backend":
            continue
        for command in _run_commands(body):
            match = _DELEGATION.match(command.strip())
            if match is None:
                continue
            target = match.group(1)
            if target not in backend_tasks:
                unresolved.append(f"{name} -> {target}")
                if target == name:
                    self_delegating.append(name)
                continue
            counterpart = backend_tasks[target]
            for inner in _run_commands(counterpart):
                inner_match = _DELEGATION.match(inner.strip())
                if inner_match is not None and inner_match.group(1) == target:
                    proxy_to_a_proxy.append(f"{name} -> {target} -> {target}")

    assert self_delegating == [], (
        "These root tasks invoke themselves instead of the backend task of the "
        f"same name: {', '.join(self_delegating)}."
    )
    assert unresolved == [], (
        f"Root tasks delegate to backend tasks that do not exist: {', '.join(unresolved)}."
    )
    assert proxy_to_a_proxy == [], (
        "These delegation chains never reach a real command — the backend task "
        f"is itself a proxy: {', '.join(proxy_to_a_proxy)}."
    )


def test_every_backend_task_ends_in_something_runnable() -> None:
    """A task with no command and no dependencies runs nothing and exits 0."""
    empty = [
        name
        for name, body in _tasks(BACKEND_TASKS).items()
        if not _run_commands(body) and not body.get("depends")
    ]
    assert empty == [], f"backend/mise.toml tasks with neither a command nor dependencies: {empty}"


def test_the_wiring_is_also_guarded_outside_the_task_system() -> None:
    """This module cannot be the only guard, because reaching it needs a proxy.

    `risk-service:test-architecture` is one of the tasks that recurses when
    `backend/mise.toml` goes missing, so on the day the file moved this file was
    unreachable — a guard that only runs when the thing it guards is healthy.
    `.github/scripts/mise_wiring_preflight.py` asserts the same invariant in
    stdlib Python with no mise and no uv, and every job in risk-service.yml
    depends on the job that runs it.
    """
    preflight = REPO_ROOT / ".github" / "scripts" / "mise_wiring_preflight.py"
    assert preflight.is_file(), f"{preflight} is missing; the wiring guard is behind the wiring."

    workflow = (WORKFLOWS / "risk-service.yml").read_text()
    assert "mise_wiring_preflight.py" in workflow, (
        "risk-service.yml no longer runs the mise wiring preflight; a moved "
        "backend/mise.toml would again surface as a recursion that reads like a hang."
    )
    body = yaml.safe_load(workflow)
    jobs = body["jobs"]
    assert "preflight" in jobs, "risk-service.yml has no `preflight` job."
    mise_driven = {
        name
        for name, job in jobs.items()
        if any("mise run" in str(step.get("run", "")) for step in job.get("steps", []))
    }
    ungated = sorted(name for name in mise_driven if "preflight" not in _needs(jobs[name]))
    assert ungated == [], (
        f"These jobs drive mise tasks without depending on `preflight`: {ungated}. "
        "They would recurse instead of failing fast."
    )


def test_every_task_ci_invokes_exists() -> None:
    """A task defined only in backend/mise.toml is "task not found" in CI.

    CI runs `mise run` from the repository root, so the root file is the one
    that has to carry every task a workflow names.
    """
    root_tasks = _tasks(ROOT_TASKS)
    missing: list[str] = []

    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for task in _WORKFLOW_INVOCATION.findall(_workflow_body(workflow)):
            if task not in root_tasks:
                missing.append(f"{workflow.name}: {task}")

    assert missing == [], f"Workflows invoke tasks the root mise.toml does not define: {missing}"


def test_dashboard_journeys_are_manual_only() -> None:
    """The long browser suite is maintainer-dispatched, never routine CI."""
    journey_workflow = WORKFLOWS / "dashboard-journeys.yml"
    document = yaml.load(journey_workflow.read_text(), Loader=yaml.BaseLoader)
    assert set(document["on"]) == {"workflow_dispatch"}, (
        "dashboard-journeys.yml must not be enqueued by pull requests or pushes."
    )

    risk_document = yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text())
    risk_jobs = risk_document["jobs"]
    assert "journeys" not in risk_jobs, (
        "The dashboard journeys belong only in the manual workflow, not the "
        "default Risk Service CI job graph."
    )
    assert "journeys" not in _needs(risk_jobs["gate"]), (
        "The routine Risk service gate must not require the manual journey suite."
    )


def test_the_backend_gates_ci_must_run_are_all_defined() -> None:
    """Naming them here is what stops a gate quietly disappearing from the list."""
    required = {
        "risk-service:lint",
        "risk-service:typecheck",
        "risk-service:test",
        "risk-service:test-postgres",
        "risk-service:test-postgres-migrations",
        "risk-service:test-postgres-locks",
        "risk-service:api-fresh",
    }

    assert required <= set(_tasks(ROOT_TASKS))


def test_ci_actually_runs_every_required_gate() -> None:
    invoked = set(_WORKFLOW_INVOCATION.findall(_workflow_body(WORKFLOWS / "risk-service.yml")))
    required = {
        "risk-service:lint",
        "risk-service:typecheck",
        "risk-service:test",
        "risk-service:test-architecture",
        # Supersedes test-postgres-migrations: the same chain plus the RLS
        # policies and server defaults it installs.
        "risk-service:test-postgres-schema",
        "risk-service:test-postgres-locks",
        # SQLite is not production confidence: the hermetic schema is built by
        # Base.metadata.create_all and has no row-level security at all.
        "risk-service:test-postgres-suite",
        "risk-service:api-fresh",
    }

    assert required <= invoked, f"Not gated in CI: {sorted(required - invoked)}"


# --------------------------------------------------------------------------
# A gate can also be dead by ADDRESS: defined, invoked, and pointed at paths
# that do not contain the tests it exists to run.
# --------------------------------------------------------------------------

TESTS = Path(__file__).parents[1]
_REAL_DATA_TASK = "risk-service:test-real-data"
_PYTEST_PATHS = re.compile(r"(?:^|\s)(tests/[A-Za-z0-9_./-]*)")


def _real_data_modules() -> set[Path]:
    """Test modules gated on ``requires_real_data``.

    By IMPORT, not by substring: a file that merely mentions the marker — this
    one names it in order to look for it — is not gated on it. The same
    distinction the boundary and RLS guards had to learn.
    """
    found: set[Path] = set()
    for path in sorted(TESTS.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        imports_marker = any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "requires_real_data" for alias in node.names)
            for node in ast.walk(tree)
        )
        if imports_marker:
            found.add(path.relative_to(TESTS.parent))
    return found


def _task_paths(name: str) -> list[str]:
    body = _tasks(BACKEND_TASKS)[name]
    return [match for command in _run_commands(body) for match in _PYTEST_PATHS.findall(command)]


def test_the_real_data_task_can_actually_reach_every_real_data_test() -> None:
    """Audit finding D3 (2026-08-22).

    `risk-service:test-real-data` ran `tests/api tests/features tests/services`
    while three opt-in modules lived elsewhere:
    `tests/adapters/market_data/manual_upload/test_api.py` and both
    `tests/equivalence/` run-parity suites. Twenty-three tests — including the
    regulatory report and forecast parity checks — were unreachable even with
    `REAL_DATA_DATABASE_URL` set. They did not fail and did not skip; the task
    simply never collected them, which is the quietest way for a gate to be
    dead.

    Deriving the requirement from the marker means a new opt-in module in a new
    directory turns this red instead of silently going ungated.
    """
    selected = [Path(path) for path in _task_paths(_REAL_DATA_TASK)]
    unreachable = sorted(
        str(module)
        for module in _real_data_modules()
        if not any(module == path or path in module.parents for path in selected)
    )

    assert unreachable == [], (
        f"`{_REAL_DATA_TASK}` collects {[str(p) for p in selected]}, which cannot reach these "
        f"opt-in modules: {unreachable}. They would never run, in CI or locally. "
        "Add the containing directory to the task's pytest paths."
    )


def test_the_real_data_task_selects_no_marker_that_does_not_exist() -> None:
    """`-m 'not slow'` filtered on a marker no test carries and pytest does not
    register — inert, and it implied a tier of excluded tests that was empty.

    A filter naming a real marker is fine; one naming a marker nothing uses is
    either a leftover or a mistake, and both should be visible.
    """
    body = _tasks(BACKEND_TASKS)[_REAL_DATA_TASK]
    selectors = re.findall(r"-m\s+'([^']+)'", " ".join(_run_commands(body)))
    marked = {
        marker
        for path in TESTS.rglob("test_*.py")
        if "__pycache__" not in path.parts
        for marker in re.findall(r"@pytest\.mark\.([a-z_]+)", path.read_text())
    }
    phantom = [
        name
        for selector in selectors
        for name in re.findall(r"[a-z_]+", selector)
        if name not in {"not", "and", "or"} and name not in marked
    ]

    assert phantom == [], f"`{_REAL_DATA_TASK}` filters on markers no test carries: {phantom}"

# --------------------------------------------------------------------------
# A step can also be dead by EXIT CODE: it ran, it printed, it reported success,
# and it executed nothing. Three ways that happens here, all guarded below.
# --------------------------------------------------------------------------


def _workflow_run_blocks(path: Path) -> list[tuple[str, str, str]]:
    """(job, step label, script) for every `run:` in a workflow."""
    document = yaml.safe_load(path.read_text())
    blocks: list[tuple[str, str, str]] = []
    for job_name, job in (document.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if isinstance(script, str):
                blocks.append((job_name, step.get("name") or f"step {index}", script))
    return blocks


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _pipes(script: str) -> bool:
    """True when a shell block contains a real pipeline.

    Three things look like a pipe and are not: a `||`, a `|` inside a quoted
    string (every regex this repository passes to a script is an alternation),
    and a `#` comment. Miscounting any of them turns this guard into noise, and
    a noisy guard gets deleted.
    """
    code = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))
    code = _QUOTED.sub("", code).replace("||", "")
    return "|" in code


def test_no_ci_step_hides_an_exit_code_behind_a_pipe() -> None:
    """`pytest <gone> | tail` reports the exit code of `tail`, which is 0.

    pytest treats a path argument that does not exist as a USAGE error: it exits
    4 having collected nothing. Piped, that becomes a cheerful green step with a
    tidy tail of output — a gate that ran zero tests and said so to nobody. It
    has bitten this programme more than once, so the rule is mechanical: any
    multi-command shell block that pipes must turn on `pipefail`.
    """
    offenders: list[str] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for job, label, script in _workflow_run_blocks(workflow):
            if not _pipes(script):
                continue
            if "pipefail" in script:
                continue
            offenders.append(f"{workflow.name}:{job}:{label}")

    assert offenders == [], (
        "These CI steps pipe without `set -o pipefail`, so a failing command "
        f"upstream of the pipe reports success: {offenders}"
    )


def test_every_pytest_path_a_task_names_exists() -> None:
    """A task pointed at a path that has moved runs nothing and blames nobody."""
    missing: list[str] = []
    for config in (ROOT_TASKS, BACKEND_TASKS):
        for name, body in _tasks(config).items():
            for command in _run_commands(body):
                if "pytest" not in command:
                    continue
                for path in _PYTEST_PATHS.findall(command):
                    if not (TESTS.parent / path).exists():
                        missing.append(f"{config.name}:{name} -> {path}")

    assert missing == [], (
        "These tasks name pytest paths that do not exist. pytest exits 4 having "
        f"run ZERO tests when given one: {missing}"
    )


def test_the_hermetic_run_still_proves_it_collected_every_module() -> None:
    """The collection-equality step is the only check that CI SAW every test.

    Everything else in the workflow answers "did the command pass?". Losing this
    step would not turn anything red — which is exactly why it needs a test of
    its own.
    """
    workflow = (WORKFLOWS / "risk-service.yml").read_text()
    assert "--collect-only" in workflow, (
        "risk-service.yml no longer compares the test modules on disk against "
        "the ones pytest collects. A module that collects nowhere would again "
        "be invisible."
    )
