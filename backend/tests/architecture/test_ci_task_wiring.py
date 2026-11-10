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

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import cast

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[3]
ROOT_TASKS = REPO_ROOT / "mise.toml"
BACKEND_TASKS = REPO_ROOT / "backend" / "mise.toml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_DELEGATION = re.compile(r"^mise\s+run\s+(\S+)")


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


def _workflow_tasks(path: Path) -> set[str]:
    """Normalize direct mise commands and their fail-fast shell chains."""
    tasks: set[str] = set()
    for _, _, script in _workflow_run_blocks(path):
        lexer = shlex.shlex(script, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        first = next(lexer, "")
        if shlex.split(first) != ["mise"]:
            continue
        second = next(lexer, "")
        if shlex.split(second) != ["run"]:
            continue
        tokens = [first, second, *lexer]
        commands: list[list[str]] = [[]]
        for token in tokens:
            if token == "&&":
                commands.append([])
            else:
                commands[-1].extend(shlex.split(token))
        if all(len(command) == 3 and command[:2] == ["mise", "run"] for command in commands):
            tasks.update(command[2] for command in commands)
    return tasks


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

    `risk-service:test` is one of the tasks that recurses when
    `backend/mise.toml` goes missing, so on the day the file moved this file was
    unreachable — a guard that only runs when the thing it guards is healthy.
    `.github/scripts/mise_wiring_preflight.py` asserts the same invariant in
    stdlib Python with no mise and no uv, and every job in risk-service.yml
    depends on the job that runs it.
    """
    preflight = REPO_ROOT / ".github" / "scripts" / "mise_wiring_preflight.py"
    assert preflight.is_file(), f"{preflight} is missing; the wiring guard is behind the wiring."

    workflow = (WORKFLOWS / "risk-service.yml").read_text()
    body = yaml.safe_load(workflow)
    jobs = body["jobs"]
    assert "preflight" in jobs, "risk-service.yml has no `preflight` job."
    preflight_job = cast(dict[str, object], jobs["preflight"])
    assert "if" not in preflight_job
    assert not preflight_job.get("continue-on-error", False)
    commands: list[list[str]] = []
    for step in cast(list[dict[str, object]], preflight_job["steps"]):
        script = step.get("run")
        if not isinstance(script, str) or "if" in step or step.get("continue-on-error", False):
            continue
        if step.get("working-directory", ".") != ".":
            continue
        lexer = shlex.shlex(script.strip(), posix=True, punctuation_chars=";&|<>()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        commands.append(list(lexer))
    assert ["python3", ".github/scripts/mise_wiring_preflight.py"] in commands, (
        "The preflight job must directly execute the wiring guard and propagate its failure."
    )
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
        for task in _workflow_tasks(workflow):
            if task not in root_tasks:
                missing.append(f"{workflow.name}: {task}")

    assert missing == [], f"Workflows invoke tasks the root mise.toml does not define: {missing}"


def test_dashboard_journeys_have_an_always_reported_sharded_gate() -> None:
    """Validate the Actions job graph that supplies the required check."""
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((WORKFLOWS / "dashboard-journeys.yml").read_text(), Loader=yaml.BaseLoader),
    )
    assert set(document["on"]) == {"pull_request", "push", "workflow_dispatch", "merge_group"}
    assert document["on"]["push"]["branches"] == ["main"]
    jobs = document["jobs"]
    assert jobs["gate"]["name"] == "Dashboard journeys gate"
    assert jobs["gate"]["if"] == "always()"
    assert set(_needs(jobs["gate"])) == {"changes", "journeys"}
    assert _needs(jobs["journeys"]) == ["changes"]
    # The queue skips change detection and runs every shard; a pull request
    # runs the shards only when the journey area changed.
    assert jobs["changes"]["if"] == "github.event_name != 'merge_group'"
    assert (
        jobs["journeys"]["if"] == "${{ !cancelled() && (github.event_name == 'merge_group'"
        " || needs.changes.outputs.journeys == 'true') }}"
    )
    strategy = cast(dict[str, object], jobs["journeys"]["strategy"])
    assert cast(dict[str, object], strategy["matrix"])["shard"] == ["1", "2", "3", "4"]
    assert strategy["fail-fast"] == "false"

    risk_document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    assert "journeys" not in risk_document["jobs"]
    assert "journeys" not in _needs(risk_document["jobs"]["gate"])


def test_the_backend_gates_ci_must_run_are_all_defined() -> None:
    """Naming them here is what stops a gate quietly disappearing from the list."""
    required = {
        "risk-service:lint",
        "risk-service:typecheck",
        "risk-service:test",
        "risk-service:check",
        "risk-service:api-fresh",
    }

    assert required <= set(_tasks(ROOT_TASKS))


def _ci_pytest_all(
    job_name: str, *, allow_guarded_step: bool = False
) -> list[tuple[list[str], dict[str, object]]]:
    """Read executable direct commands, not labels, comments or echoed strings.

    ``allow_guarded_step`` admits the pytest step of a combined job whose
    execution is gated by a step-level `if` instead of the job-level one.
    """
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    matches: list[tuple[list[str], dict[str, object]]] = []
    for step in cast(list[dict[str, object]], document["jobs"][job_name]["steps"]):
        script = step.get("run")
        if not isinstance(script, str):
            continue
        tokens = shlex.split(script, comments=True)
        direct = tokens[:3] == ["uv", "run", "pytest"]
        native = tokens[:5] == ["uv", "run", "python", "scripts/local_services.py", "run"]
        if native and "--" in tokens:
            index = tokens.index("--") + 1
            direct = tokens[index : index + 3] == ["uv", "run", "pytest"]
        if not direct or "--collect-only" in tokens:
            continue
        assert step.get("working-directory") == "backend"
        assert (allow_guarded_step or "if" not in step) and not step.get(
            "continue-on-error", False
        )
        assert not any(token in {";", "&&", "||", "|"} for token in tokens)
        matches.append((tokens, step))
    return matches


def _ci_pytest(
    job_name: str, *, allow_guarded_step: bool = False
) -> tuple[list[str], dict[str, object]]:
    matches = _ci_pytest_all(job_name, allow_guarded_step=allow_guarded_step)
    assert len(matches) == 1, f"{job_name} must execute exactly one direct pytest suite"
    return matches[0]


def test_ci_actually_runs_every_required_gate() -> None:
    invoked = _workflow_tasks(WORKFLOWS / "risk-service.yml")
    required = {
        "risk-service:lint",
        "risk-service:typecheck",
        "risk-service:api-fresh",
    }
    assert required <= invoked, f"Not gated in CI: {sorted(required - invoked)}"
    for job, marker in (
        ("architecture", "unit"),
        ("static", "e2e"),
        ("postgres-locks", "integration"),
        ("real-data", None),
    ):
        tokens, _step = _ci_pytest(job, allow_guarded_step=job == "static")
        if marker is not None:
            assert tokens[tokens.index("-m") + 1] == marker, job
    pool = _ci_pytest_all("postgres-pool")
    assert [tokens[tokens.index("-m") + 1] for tokens, _step in pool] == [
        "integration",
        "integration",
    ], "postgres-pool must run both phases (serial schema, parallel suite)"


@pytest.mark.parametrize(
    ("job", "services"),
    [
        ("postgres-locks", ["--postgres"]),
        ("postgres-pool", ["--postgres", "--storage", "--role-admin"]),
    ],
)
def test_direct_ci_commands_preserve_service_and_report_owners(
    job: str, services: list[str]
) -> None:
    for tokens, step in _ci_pytest_all(job):
        separator = tokens.index("--")
        assert tokens[:separator] == [
            "uv",
            "run",
            "python",
            "scripts/local_services.py",
            "run",
            *services,
        ]
        assert tokens[separator + 1 : separator + 4] == ["uv", "run", "pytest"]
        assert not {"--lf", "--suite-only", "--collect-only"}.intersection(tokens)
        environment = cast(dict[str, str], step["env"])
        assert environment["TEST_DATABASE_URL"] == "${{ env.POSTGRES_TEST_URL }}"
        assert "CI_DATABASE_OBSERVATIONS" in environment
        assert "--junitxml=" in environment["PYTEST_ADDOPTS"]
        if job == "postgres-pool":
            assert shlex.split(environment["PYTEST_ADDOPTS"])[:2] == [
                "-p",
                "scripts.ci_postgres_plugin",
            ]
            assert environment["CI_POSTGRES_SHARD"] == "${{ matrix.shard }}"
            assert "CI_POSTGRES_FILES" in environment
            assert "CI_POSTGRES_REPORT_DIR" in environment
            assert environment["CI_POSTGRES_PHASE"] in ("schema", "suite")
            assert "report-" + environment["CI_POSTGRES_PHASE"] + ".xml" in environment[
                "PYTEST_ADDOPTS"
            ]
            assert environment["CI_POSTGRES_REPORT_DIR"] == "${{ runner.temp }}/pg-pool"
            if environment["CI_POSTGRES_PHASE"] == "schema":
                assert environment["POSTGRES_PRIVILEGE_TESTS_REQUIRED"] == "1"
                assert "tests/db" in tokens and "-n" not in tokens
            else:
                assert "POSTGRES_PRIVILEGE_TESTS_REQUIRED" not in environment
                assert tokens[tokens.index("-n") + 1] == "4"


def _isolate_runner_temp_outputs(environment: dict[str, str], scratch: Path) -> dict[str, str]:
    """Point the workflow's ``${{ runner.temp }}`` outputs at an owned scratch dir.

    Actions expands ``${{ runner.temp }}`` before its env reaches pytest. A direct
    local subprocess instead receives the literal template, and instrumentation
    that observes it (database_events.emit, the CI report writers) then creates
    ``backend/${{ runner.temp }}/...`` inside the repository. The raw env values
    stay in the asserted workflow; only this helper's subprocess gets real paths.
    """
    isolated = dict(environment)
    for key, value in environment.items():
        if "${{ runner.temp }}" not in value:
            continue
        isolated[key] = value.replace("${{ runner.temp }}", str(scratch))
    return isolated


def _ci_pytest_for_phase(
    job: str, phase: str | None, *, allow_guarded_step: bool = False
) -> tuple[list[str], dict[str, object]]:
    matches = _ci_pytest_all(job, allow_guarded_step=allow_guarded_step)
    if phase is None:
        assert len(matches) == 1, f"{job} must execute exactly one direct pytest suite"
        return matches[0]
    matches = [
        (tokens, step)
        for tokens, step in matches
        if cast(dict[str, str], step.get("env") or {}).get("CI_POSTGRES_PHASE") == phase
    ]
    assert len(matches) == 1, f"{job} must have exactly one {phase}-phase suite"
    return matches[0]


def _assert_serial_ci_owner(job: str, phase: str | None = None) -> None:
    tokens, step = _ci_pytest_for_phase(job, phase)
    arguments = tokens[tokens.index("pytest") + 1 :]
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        **cast(dict[str, str], step["env"]),
    }
    with tempfile.TemporaryDirectory() as scratch:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, sys; from _pytest.config import _prepareconfig; "
                "config = _prepareconfig(sys.argv[1:]); "
                "print(json.dumps(config.getoption('numprocesses')))",
                "-p",
                "xdist.plugin",
                "--noconftest",
                *arguments,
            ],
            cwd=REPO_ROOT / "backend",
            env=_isolate_runner_temp_outputs(environment, Path(scratch)),
            text=True,
            capture_output=True,
            check=True,
        )
    assert json.loads(result.stdout) in (None, 0), job


@pytest.mark.parametrize(
    ("job", "phase"), [("postgres-locks", None), ("postgres-pool", "schema")]
)
def test_ci_serial_owners_resolve_to_no_workers(job: str, phase: str | None) -> None:
    """The lock owner and each pool shard's schema slice require serial execution."""
    _assert_serial_ci_owner(job, phase)


@pytest.mark.parametrize(
    ("job", "phase"), [("postgres-locks", None), ("postgres-pool", "schema")]
)
@pytest.mark.parametrize(
    "injection",
    [
        "command --numprocesses=4",
        "command --numprocesses 4",
        "environment --numprocesses=4",
        "environment --numprocesses 4",
        "configuration --numprocesses=4",
        "configuration --numprocesses 4",
    ],
)
def test_serial_owner_guard_rejects_parallel_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job: str,
    phase: str | None,
    injection: str,
) -> None:
    source, option = injection.split(" ", 1)
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    _tokens, target = _ci_pytest_for_phase(job, phase)
    steps = cast(list[dict[str, object]], document["jobs"][job]["steps"])
    step = next(step for step in steps if step == target)
    if source == "command":
        step["run"] = cast(str, step["run"]) + " " + option
    elif source == "configuration":
        step["run"] = cast(str, step["run"]) + " -o " + shlex.quote("addopts=" + option)
    else:
        environment = cast(dict[str, str], step["env"])
        environment["PYTEST_ADDOPTS"] += " " + option
    _ = (tmp_path / "risk-service.yml").write_text(yaml.safe_dump(document))
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    with pytest.raises(AssertionError, match=job):
        _assert_serial_ci_owner(job, phase)


_GIT_LOCATION_OVERRIDES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
    }
)


def _git_child_environment() -> dict[str, str]:
    # A caller's GIT_DIR/GIT_WORK_TREE overrides would redirect a cwd-based Git
    # child away from this repository; the parent environment stays untouched.
    return {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_OVERRIDES}


def _untracked_repository_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        env=_git_child_environment(),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_untracked_repository_paths_ignores_caller_git_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray caller's Git location must not redirect this repository guard."""
    stray = tmp_path / "stray-repo"
    # A hostile GIT_DIR must not relocate the fixture's own metadata either.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "caller-metadata"))
    subprocess.run(["git", "init", "-q", str(stray)], env=_git_child_environment(), check=True)
    assert (stray / ".git").is_dir()
    assert not (tmp_path / "caller-metadata").exists()
    baseline = _untracked_repository_paths()
    monkeypatch.setenv("GIT_DIR", str(stray / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(stray))
    monkeypatch.setenv("GIT_INDEX_FILE", str(stray / ".git" / "index"))
    assert _untracked_repository_paths() == baseline


def test_serial_owner_validation_leaves_no_repository_byproducts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serial-owner validation must not materialize the workflow templates.

    Actions expands ``${{ runner.temp }}`` in CI, but the direct local helper
    subprocess would write observation and report byproducts through the literal
    template path into the repository. Prove the isolated helper keeps the serial
    and parallel-rejection verdicts while the repository stays clean.
    """
    before = _untracked_repository_paths()
    _assert_serial_ci_owner("postgres-locks")
    _assert_serial_ci_owner("postgres-pool", "schema")
    test_serial_owner_guard_rejects_parallel_configuration(
        tmp_path, monkeypatch, "postgres-locks", None, "command --numprocesses=4"
    )
    assert not any("${{ runner.temp }}" in path for path in _untracked_repository_paths())
    assert not (REPO_ROOT / "backend" / "${{ runner.temp }}").exists()
    assert _untracked_repository_paths() == before


@pytest.mark.parametrize(
    ("job", "phase"),
    [
        ("architecture", None),
        ("static", None),
        ("postgres-locks", None),
        ("postgres-pool", "schema"),
        ("postgres-pool", "suite"),
        ("real-data", None),
    ],
)
def test_removing_a_ci_suite_fails_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str, phase: str | None
) -> None:
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    _tokens, target = _ci_pytest_for_phase(job, phase, allow_guarded_step=job == "static")
    steps = cast(list[dict[str, object]], document["jobs"][job]["steps"])
    steps[:] = [step for step in steps if step != target]
    _ = (tmp_path / "risk-service.yml").write_text(yaml.safe_dump(document))
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    with pytest.raises(AssertionError, match=job):
        test_ci_actually_runs_every_required_gate()


def test_release_verification_runs_the_full_hermetic_suite() -> None:
    """Release publication must depend on an unconditional full-suite command."""
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((WORKFLOWS / "release.yml").read_text(), Loader=yaml.BaseLoader),
    )
    assert document["on"]["push"]["tags"] == ["v*"]
    jobs = document["jobs"]
    assert "verify" in _needs(jobs["publish-risk-service"])
    verify = jobs["verify"]
    assert "if" not in verify
    assert verify.get("continue-on-error", "false") == "false"
    steps = cast(list[dict[str, object]], verify["steps"])
    suite = next(step for step in steps if step.get("name") == "Verify hermetic test suite")
    assert "if" not in suite
    assert suite.get("continue-on-error", "false") == "false"
    assert suite["working-directory"] == "backend"
    assert shlex.split(cast(str, suite["run"])) == ["uv", "run", "pytest", "-n", "0"]
    environment = cast(dict[str, str], suite["env"])
    for variable in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        assert environment[variable] == "1"


def test_local_feedback_cannot_satisfy_release_full_suite_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((WORKFLOWS / "release.yml").read_text(), Loader=yaml.BaseLoader),
    )
    steps = cast(list[dict[str, object]], document["jobs"]["verify"]["steps"])
    suite = next(step for step in steps if step.get("name") == "Verify hermetic test suite")
    suite["run"] = "uv run pytest -n 0 -m unit"
    _ = (tmp_path / "release.yml").write_text(yaml.safe_dump(document))
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    with pytest.raises(AssertionError):
        test_release_verification_runs_the_full_hermetic_suite()


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("mise run risk-service:test", {"risk-service:test"}),
        ('mise run "risk-service:test" # gate', {"risk-service:test"}),
        (
            "mise run risk-service:lint && mise run risk-service:typecheck",
            {"risk-service:lint", "risk-service:typecheck"},
        ),
        ('echo "mise run risk-service:test"', set[str]()),
        ("# mise run risk-service:test", set[str]()),
        ("mise run risk-service:lint && echo mise run risk-service:test", set[str]()),
        ("mise run risk-service:lint '&&' mise run risk-service:test", set[str]()),
    ],
)
def test_workflow_task_invocations_follow_executable_commands(
    tmp_path: Path, script: str, expected: set[str]
) -> None:
    workflow = tmp_path / "workflow.yml"
    _ = workflow.write_text(
        yaml.safe_dump({"jobs": {"test": {"steps": [{"name": "mise run unused", "run": script}]}}})
    )
    assert _workflow_tasks(workflow) == expected


def test_echoing_the_unit_command_does_not_satisfy_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    _tokens, target = _ci_pytest("architecture")
    for step in cast(list[dict[str, object]], document["jobs"]["architecture"]["steps"]):
        if step == target:
            step["run"] = "echo " + shlex.quote(cast(str, step["run"]))
    _ = (tmp_path / "risk-service.yml").write_text(yaml.safe_dump(document))
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    with pytest.raises(AssertionError, match="architecture"):
        test_ci_actually_runs_every_required_gate()


def test_unit_suite_command_and_ci_job_are_explicit() -> None:
    tokens, unit = _ci_pytest("architecture")
    assert tokens == [
        "uv",
        "run",
        "pytest",
        "-m",
        "unit",
        "--suite-only",
        "-n",
        "auto",
        "--dist",
        "load",
        "--durations=20",
    ]
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    job = document["jobs"]["architecture"]
    assert job["name"] == "Unit tests"
    steps = cast(list[dict[str, object]], job["steps"])
    environment = cast(dict[str, str], unit["env"])
    assert "CI_UNIT_REPORT_DIR" in environment
    for variable in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        assert environment[variable] == "1"
    assert not any("setup-postgres" in str(step.get("uses", "")) for step in steps)
    assert job["if"] == "needs.preflight.outputs.guards == 'true'"
    # The gate joins every real job directly; the static job owns lint,
    # typecheck, the e2e contracts and client freshness in one place.
    assert document["jobs"]["static"]["name"] == "Lint and typecheck"
    assert {"architecture", "static"} <= set(_needs(document["jobs"]["gate"]))
    assert not {"backend-e2e", "api-fresh", "postgres", "postgres-suite"} & set(
        document["jobs"]
    )


# --------------------------------------------------------------------------
# A gate can also be dead by ADDRESS: defined, invoked, and pointed at paths
# that do not contain the tests it exists to run.
# --------------------------------------------------------------------------

TESTS = Path(__file__).parents[1]
_PYTEST_PATHS = re.compile(r"(?:^|\s)(tests/[A-Za-z0-9_./-]*)")


def _ci_paths(job: str, *, allow_guarded_step: bool = False) -> list[str]:
    matches = _ci_pytest_all(job, allow_guarded_step=allow_guarded_step)
    return [
        token
        for tokens, _step in matches
        for token in tokens
        if token.startswith("tests/")
    ]


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


def _pipeline_failure_is_visible(script: str) -> bool:
    lines = iter(
        line for line in script.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    first = shlex.split(next(lines, ""), comments=True)
    if not first or first[0] != "set":
        return False
    options = [first]
    options.extend(
        shlex.split(line, comments=True)
        for line in lines
        if line.lstrip().startswith(("set ", "set\t"))
    )
    if any(set(token) & set(";&|<>()") for command in options for token in command):
        return False
    probe = "\n".join(
        shlex.join(command) + " || exit 2\nif false | cat; then exit 1; fi" for command in options
    )
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def test_no_ci_step_hides_an_exit_code_behind_a_pipe() -> None:
    """`pytest <gone> | tail` reports the exit code of `tail`, which is 0.

    pytest treats a path argument that does not exist as a USAGE error: it exits
    4 having collected nothing. Piped, that becomes a cheerful green step with a
    tidy tail of output — a gate that ran zero tests and said so to nobody. It
    has bitten this programme more than once, so the rule is mechanical: any
    multi-command shell block that pipes must turn on `pipefail`.
    """
    for prologue, visible in (
        ("set -euo pipefail", True),
        ("# guard\nset -o pipefail # enable it", True),
        ("echo pipefail", False),
        ("# set -o pipefail", False),
        ("set -eu", False),
        ("set +o pipefail", False),
        ("set -o pipefail && set +o pipefail", False),
        ("set -o pipefail\nset +o pipefail", False),
        ("set -o pipefail\nset +o pipefail\nset -o pipefail", False),
        ("set -o pipefail\nset +e", True),
        ("set -o unknown-option", False),
    ):
        assert _pipeline_failure_is_visible(prologue + "\nfalse | cat") == visible
    offenders: list[str] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for job, label, script in _workflow_run_blocks(workflow):
            if not _pipes(script):
                continue
            if _pipeline_failure_is_visible(script):
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
    for job in (
        "architecture",
        "static",
        "postgres-locks",
        "postgres-pool",
        "real-data",
    ):
        for path in _ci_paths(job, allow_guarded_step=job == "static"):
            if not (TESTS.parent / path).exists():
                missing.append(f"risk-service.yml:{job} -> {path}")

    assert missing == [], (
        "These tasks name pytest paths that do not exist. pytest exits 4 having "
        f"run ZERO tests when given one: {missing}"
    )


def test_the_pool_ignores_exactly_what_the_lock_owner_runs() -> None:
    """Only lock-owned modules are ignored; the schema slice owns tests/db."""
    _pool_suite, _step = _ci_pytest_for_phase("postgres-pool", "suite")
    pool = _pool_suite
    assert pool[pool.index("-m") + 1] == "integration"
    ignored = {token.partition("=")[2] for token in pool if token.startswith("--ignore=")}
    tokens, _step = _ci_pytest("postgres-locks")
    assert tokens[tokens.index("-m") + 1] == "integration"
    owned = {token for token in tokens if token.startswith("tests/")}
    assert ignored == owned | {"tests/db"}
    assert all((TESTS.parent / path).exists() for path in ignored)
    schema, _step = _ci_pytest_for_phase("postgres-pool", "schema")
    assert {token for token in schema if token.startswith("tests/")} == {"tests/db"}
    assert not any(token.startswith("--ignore=") for token in schema)


def test_the_hermetic_run_still_proves_it_collected_every_module(tmp_path: Path) -> None:
    """Filename selection discovers new cases without importing other suites."""
    tests = tmp_path / "tests"
    tests.mkdir()
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths=tests\n")
    _ = (tests / "test_unit.py").write_text(
        "class TestClient:\n    def __init__(self): pass\ndef test_unit(): assert True\n"
    )
    _ = (tests / "test_db_integration.py").write_text(
        "raise RuntimeError('integration module imported')\n"
    )
    contracts = tmp_path / "contracts.json"
    _ = contracts.write_text(json.dumps({"contracts": []}))
    _ = (tmp_path / "probe_contracts.py").write_text(
        "from pathlib import Path\nfrom scripts import database_contracts\n"
        + f"database_contracts.CATALOGUE = Path({str(contracts)!r})\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(TESTS.parent)]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "",
        "CI_UNIT_REPORT_DIR": str(tmp_path / "evidence"),
    }
    for extra_module, source, succeeds in (
        ("test_extra.py", "def test_extra(): assert True\n", True),
        ("test_extra.py", "VALUE = 1\n", False),
        ("extra_test.py", "VALUE = 1\n", False),
        ("test_extra.py", "raise RuntimeError('collection failure')\n", False),
        ("test_extra.py", "def test_extra(): pass\ndef test_new(): pass\n", True),
        ("test_extra.py", "VALUE = 1\n", False),
    ):
        extra = tests / extra_module
        _ = extra.write_text(source)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--rootdir=.",
                "-p",
                "scripts.test_suites_plugin",
                "-p",
                "probe_contracts",
                "-m",
                "unit",
                "--suite-only",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
        extra.unlink()


def test_duration_shards_preserve_worker_limit_and_planned_matrix() -> None:
    suite, _step = _ci_pytest_for_phase("postgres-pool", "suite")
    assert suite[suite.index("-n") + 1] == "4"
    assert suite[suite.index("--dist") + 1] == "loadfile"
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((WORKFLOWS / "risk-service.yml").read_text()),
    )
    pool = document["jobs"]["postgres-pool"]
    strategy = cast(dict[str, object], pool["strategy"])
    assert cast(dict[str, object], strategy["matrix"]) == (
        "${{ fromJSON(needs.preflight.outputs.postgres_matrix) }}"
    )
    assert strategy["fail-fast"] is False
