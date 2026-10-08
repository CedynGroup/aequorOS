"""The type-check baseline gate stands on a strict configuration and a shrink-only baseline.

``scripts/type_check_baseline.py`` runs basedpyright and is the ``risk-service:typecheck``
gate (CODEBASE_CONVENTIONS.md §1, "Type-check baseline"). A full type check takes about a
minute, so these hermetic guards check what that gate relies on without running it:
the configuration it reads is strict, the baseline is canonical and never covers a
strict module, and its comparison and ``update`` rules fire on synthetic reports.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from pydantic import TypeAdapter

from scripts import type_check_baseline as type_check
from scripts.type_check_baseline import Diagnostic, Drift

BACKEND = type_check.BACKEND

#: Strict-mode rules the configuration may relax, and why. ``Mapped[dict[str, Any]]``
#: is the documented JSON-column type (CODEBASE_CONVENTIONS.md §1); ``reportAny``
#: still flags every use of such a value.
RELAXED_RULES = frozenset({"reportExplicitAny"})

#: Diagnostic levels strict mode leaves at full strength.
_STRICT_LEVELS = frozenset({"error", True})


class _Tool(TypedDict):
    basedpyright: dict[str, object]


class _Pyproject(TypedDict):
    tool: _Tool


class _MiseTask(TypedDict):
    run: NotRequired[str | list[str]]


class _MiseFile(TypedDict):
    tasks: dict[str, _MiseTask]


def _basedpyright_config() -> dict[str, object]:
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return TypeAdapter(_Pyproject).validate_python(pyproject)["tool"]["basedpyright"]


def _diagnostic(module: str, rule: str = "reportAny") -> Diagnostic:
    return Diagnostic(module=module, rule=rule, location=f"{module}:1:1", message="m")


def test_basedpyright_is_configured_strict() -> None:
    config = _basedpyright_config()
    assert config["typeCheckingMode"] == "strict"
    assert config["reportAny"] == "error"
    relaxed = {
        rule
        for rule, level in config.items()
        if rule.startswith("report") and level not in _STRICT_LEVELS
    }
    assert relaxed == RELAXED_RULES, (
        "The baseline check counts only what strict mode reports. Fix or baseline errors "
        f"instead of relaxing rules: {sorted(relaxed - RELAXED_RULES)}"
    )
    assert "ignore" not in config, "An ignored path escapes the baseline check entirely."
    assert config["exclude"] == [".venv"]


@pytest.mark.parametrize("task_name", ["risk-service:typecheck", "risk-service:smoke"])
@pytest.mark.parametrize("check_exit", [0, 1])
def test_the_typecheck_gate_runs_the_baseline_check(
    tmp_path: Path, task_name: str, check_exit: int
) -> None:
    """Execute the configured task bodies and observe invocation and failure propagation."""
    tasks = TypeAdapter(_MiseFile).validate_python(
        tomllib.loads((BACKEND / "mise.toml").read_text(encoding="utf-8"))
    )["tasks"]
    run = tasks[task_name].get("run")
    assert run is not None
    commands = [run] if isinstance(run, str) else run
    invocations = tmp_path / "invocations.jsonl"
    uv = tmp_path / "uv"
    stub = (
        "import json, os, sys\n"
        "with open(os.environ['INVOCATIONS'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] == ['run', 'python', 'scripts/type_check_baseline.py', 'check']:\n"
        "    sys.exit(int(os.environ['BASELINE_CHECK_EXIT']))\n"
    )
    uv.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(stub)} "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-ec", "\n".join(commands)],
        cwd=BACKEND,
        env={
            "PATH": str(tmp_path),
            "INVOCATIONS": str(invocations),
            "BASELINE_CHECK_EXIT": str(check_exit),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (
        [
            TypeAdapter(list[str]).validate_json(line)
            for line in invocations.read_text(encoding="utf-8").splitlines()
        ]
        if invocations.exists()
        else []
    )
    check_call = ["run", "python", "scripts/type_check_baseline.py", "check"]
    assert calls.count(check_call) == 1
    assert all(call[:2] != ["run", "basedpyright"] for call in calls)
    assert result.returncode == check_exit, result.stderr
    if check_exit:
        assert calls[-1] == check_call


def test_the_baseline_is_canonical() -> None:
    baseline = type_check.load_baseline()
    assert type_check.BASELINE.read_text(encoding="utf-8") == type_check.render(baseline), (
        "Regenerate the baseline with `uv run python scripts/type_check_baseline.py update`."
    )
    assert all(errors > 0 for rules in baseline.values() for errors in rules.values())
    assert not any(type_check.UNRULED in rules for rules in baseline.values())


def test_strict_modules_exist_and_have_no_baseline_entry() -> None:
    for module in type_check.STRICT_MODULES:
        path = BACKEND.joinpath(*module.split("."))
        assert path.is_dir() or path.with_suffix(".py").is_file(), f"{module} is gone"
    baseline = type_check.load_baseline()
    covered = [module for module in baseline if type_check.is_strict_module(module)]
    assert covered == []


def test_modules_are_named_by_their_current_location() -> None:
    package = BACKEND / "app" / "forecasting" / "__init__.py"
    assert type_check.module_of(package) == "app.forecasting"
    assert type_check.module_of(BACKEND / "app" / "db" / "base.py") == "app.db.base"


def test_the_report_parser_keeps_errors_and_warnings() -> None:
    def raw(path: str, severity: str, **extra: object) -> dict[str, object]:
        return {"file": str(BACKEND / path), "severity": severity, "message": "m\nmore", **extra}

    start = {"start": {"line": 4, "character": 2}}
    output = json.dumps(
        {
            "generalDiagnostics": [
                raw("app/fx/service.py", "error", rule="reportAny", range=start),
                raw("app/live/public.py", "warning", rule="reportDeprecated"),
                raw("app/live/public.py", "information"),
                raw("app/live/public.py", "error"),
            ]
        }
    )
    assert type_check.parse_report(output, [("app.services.legacy_fx", "app.fx.service")]) == [
        Diagnostic("app.services.legacy_fx", "reportAny", "app/fx/service.py:5:3", "m"),
        Diagnostic("app.live.public", "reportDeprecated", "app/live/public.py", "m"),
        Diagnostic("app.live.public", type_check.UNRULED, "app/live/public.py", "m"),
    ]


def test_the_comparison_charges_each_rule() -> None:
    """Negative controls: each rule fires on a synthetic count, so a green run is not vacuous."""
    baseline = {"app.legacy": {"reportAny": 2}}
    assert type_check.compare({"app.legacy": {"reportAny": 2}}, baseline).clean

    fresh = type_check.compare(
        {"app.legacy": {"reportAny": 2}, "app.fresh": {"reportAny": 1}}, baseline
    )
    assert fresh.grown == (Drift("app.fresh", "reportAny", 1, 0),)
    grown = type_check.compare({"app.legacy": {"reportAny": 3, "reportPrivateUsage": 1}}, baseline)
    assert grown.grown == (
        Drift("app.legacy", "reportAny", 3, 2),
        Drift("app.legacy", "reportPrivateUsage", 1, 0),
    )
    assert type_check.compare({}, baseline).shrunk == (Drift("app.legacy", "reportAny", 0, 2),)

    strict = type_check.compare({}, {"app.forecasting.service": {"reportAny": 1}})
    assert strict.strict_entries == ("app.forecasting.service",)
    assert not type_check.is_strict_module("app.forecasting_legacy")


@pytest.fixture
def scratch_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    baseline = tmp_path / "type_check_baseline.json"
    baseline.write_text(type_check.render({"app.legacy": {"reportAny": 2}}), encoding="utf-8")
    monkeypatch.setattr(type_check, "BASELINE", baseline)
    return baseline


def _report(monkeypatch: pytest.MonkeyPatch, *diagnostics: Diagnostic) -> None:
    monkeypatch.setattr(type_check, "run_basedpyright", lambda: list(diagnostics))


def test_only_modules_outside_strict_paths_keep_their_name_across_moves() -> None:
    moves = [
        ("app.services.legacy_fx", "app.fx.engine"),
        ("app.fx.engine", "app.fx.service"),
        ("app.services.legacy_forecasting", "app.forecasting.engine"),
    ]
    assert type_check.baseline_module("app.fx.service", moves) == "app.services.legacy_fx"
    assert type_check.baseline_module("app.fx.public", moves) == "app.fx.public"
    assert type_check.baseline_module("app.forecasting.engine", moves) == "app.forecasting.engine"


def _moved_module_report(module: str) -> str:
    path = BACKEND.joinpath(*module.split(".")).with_suffix(".py")
    diagnostic = {"file": str(path), "severity": "error", "message": "m", "rule": "reportAny"}
    return json.dumps({"generalDiagnostics": [diagnostic]})


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "feature_module_moves.json"
    monkeypatch.setattr(type_check, "LEDGER", path)
    return path


def _checker_reports(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    def checker(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["basedpyright", "--outputjson"], 1, output, "")

    monkeypatch.setattr(type_check.subprocess, "run", checker)


def test_a_move_outside_strict_paths_keeps_its_legacy_allowance(
    scratch_baseline: Path, ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A codemod move stays a pure rename (CODEBASE_CONVENTIONS.md §5)."""
    old, current = "app.services.legacy_fx", "app.fx.service"
    ledger.write_text(json.dumps([[old, current]]), encoding="utf-8")
    scratch_baseline.write_text(type_check.render({old: {"reportAny": 1}}), encoding="utf-8")
    before = scratch_baseline.read_bytes()
    _checker_reports(monkeypatch, _moved_module_report(current))

    assert type_check.main(["check"]) == 0
    assert type_check.main(["update"]) == 0
    assert scratch_baseline.read_bytes() == before


def test_a_move_into_a_strict_path_never_inherits_a_legacy_allowance(
    scratch_baseline: Path, ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, current = "app.services.legacy_forecasting", "app.forecasting.engine"
    ledger.write_text(json.dumps([[old, current]]), encoding="utf-8")
    scratch_baseline.write_text(type_check.render({old: {"reportAny": 1}}), encoding="utf-8")
    before = scratch_baseline.read_bytes()
    _checker_reports(monkeypatch, _moved_module_report(current))

    assert type_check.main(["check"]) == 1
    assert type_check.main(["update"]) == 1
    assert scratch_baseline.read_bytes() == before

    _checker_reports(monkeypatch, json.dumps({"generalDiagnostics": []}))
    assert type_check.main(["check"]) == 1
    assert type_check.main(["update"]) == 0
    assert type_check.load_baseline() == {}
    assert type_check.main(["check"]) == 0


def test_check_fails_on_growth_and_on_unrecorded_fixes(
    scratch_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _diagnostic("app.legacy")
    _report(monkeypatch, legacy, legacy)
    assert type_check.main(["check"]) == 0
    _report(monkeypatch, legacy, legacy, _diagnostic("app.fresh"))
    assert type_check.main(["check"]) == 1
    _report(monkeypatch, legacy)
    assert type_check.main(["check"]) == 1


def test_update_records_fixes_and_never_grows_the_baseline(
    scratch_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = scratch_baseline.read_text(encoding="utf-8")
    _report(monkeypatch, _diagnostic("app.legacy"), _diagnostic("app.fresh"))
    assert type_check.main(["update"]) == 1
    assert scratch_baseline.read_text(encoding="utf-8") == before

    _report(monkeypatch, _diagnostic("app.legacy"))
    assert type_check.main(["update"]) == 0
    assert type_check.load_baseline() == {"app.legacy": {"reportAny": 1}}

    _report(monkeypatch)
    assert type_check.main(["update"]) == 0
    assert type_check.load_baseline() == {}
