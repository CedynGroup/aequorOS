"""The typing ratchet stands on a strict configuration and a baseline that only shrinks.

``scripts/typing_ratchet.py`` runs basedpyright and is the ``risk-service:typecheck``
gate (CODEBASE_CONVENTIONS.md §1, "Typing ratchet"). A full type check takes about a
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

from scripts import typing_ratchet as ratchet
from scripts.typing_ratchet import Diagnostic, Drift

BACKEND = ratchet.BACKEND

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
        "The ratchet counts only what strict mode reports. Fix or baseline errors "
        f"instead of relaxing rules: {sorted(relaxed - RELAXED_RULES)}"
    )
    assert "ignore" not in config, "An ignored path escapes the ratchet entirely."
    assert config["exclude"] == [".venv"]


@pytest.mark.parametrize("task_name", ["risk-service:typecheck", "risk-service:smoke"])
@pytest.mark.parametrize("ratchet_exit", [0, 1])
def test_the_typecheck_gate_runs_the_ratchet(
    tmp_path: Path, task_name: str, ratchet_exit: int
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
        "if sys.argv[1:] == ['run', 'python', 'scripts/typing_ratchet.py', 'check']:\n"
        "    sys.exit(int(os.environ['RATCHET_EXIT']))\n"
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
            "RATCHET_EXIT": str(ratchet_exit),
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
    ratchet_call = ["run", "python", "scripts/typing_ratchet.py", "check"]
    assert calls.count(ratchet_call) == 1
    assert all(call[:2] != ["run", "basedpyright"] for call in calls)
    assert result.returncode == ratchet_exit, result.stderr
    if ratchet_exit:
        assert calls[-1] == ratchet_call


def test_the_baseline_is_canonical() -> None:
    baseline = ratchet.load_baseline()
    assert ratchet.BASELINE.read_text(encoding="utf-8") == ratchet.render(baseline), (
        "Regenerate the baseline with `uv run python scripts/typing_ratchet.py update`."
    )
    assert all(errors > 0 for rules in baseline.values() for errors in rules.values())
    assert not any(ratchet.UNRULED in rules for rules in baseline.values())


def test_strict_modules_exist_and_have_no_baseline_entry() -> None:
    for module in ratchet.STRICT_MODULES:
        path = BACKEND.joinpath(*module.split("."))
        assert path.is_dir() or path.with_suffix(".py").is_file(), f"{module} is gone"
    covered = [module for module in ratchet.load_baseline() if ratchet.is_strict_module(module)]
    assert covered == []


def test_modules_are_named_by_their_current_location() -> None:
    assert ratchet.module_of(BACKEND / "app" / "forecasting" / "__init__.py") == "app.forecasting"
    assert ratchet.module_of(BACKEND / "app" / "db" / "base.py") == "app.db.base"


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
    assert ratchet.parse_report(output) == [
        Diagnostic("app.fx.service", "reportAny", "app/fx/service.py:5:3", "m"),
        Diagnostic("app.live.public", "reportDeprecated", "app/live/public.py", "m"),
        Diagnostic("app.live.public", ratchet.UNRULED, "app/live/public.py", "m"),
    ]


def test_the_comparison_charges_each_rule() -> None:
    """Negative controls: each rule fires on a synthetic count, so a green run is not vacuous."""
    baseline = {"app.legacy": {"reportAny": 2}}
    assert ratchet.compare({"app.legacy": {"reportAny": 2}}, baseline).clean

    fresh = ratchet.compare(
        {"app.legacy": {"reportAny": 2}, "app.fresh": {"reportAny": 1}}, baseline
    )
    assert fresh.grown == (Drift("app.fresh", "reportAny", 1, 0),)
    grown = ratchet.compare({"app.legacy": {"reportAny": 3, "reportPrivateUsage": 1}}, baseline)
    assert grown.grown == (
        Drift("app.legacy", "reportAny", 3, 2),
        Drift("app.legacy", "reportPrivateUsage", 1, 0),
    )
    assert ratchet.compare({}, baseline).shrunk == (Drift("app.legacy", "reportAny", 0, 2),)

    strict = ratchet.compare({}, {"app.forecasting.service": {"reportAny": 1}})
    assert strict.strict_entries == ("app.forecasting.service",)
    assert not ratchet.is_strict_module("app.forecasting_legacy")


@pytest.fixture
def scratch_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    baseline = tmp_path / "typing_baseline.json"
    baseline.write_text(ratchet.render({"app.legacy": {"reportAny": 2}}), encoding="utf-8")
    monkeypatch.setattr(ratchet, "BASELINE", baseline)
    return baseline


def _report(monkeypatch: pytest.MonkeyPatch, *diagnostics: Diagnostic) -> None:
    monkeypatch.setattr(ratchet, "run_basedpyright", lambda: list(diagnostics))


@pytest.mark.parametrize(
    ("old", "current"),
    [
        ("tests.api.helpers", "tests.support.helpers"),
        ("app.services.regulatory_forecasting", "app.forecasting.engine"),
    ],
)
def test_moved_modules_cannot_inherit_legacy_allowances(
    scratch_baseline: Path, monkeypatch: pytest.MonkeyPatch, old: str, current: str
) -> None:
    scratch_baseline.write_text(ratchet.render({old: {"reportAny": 1}}), encoding="utf-8")
    before = scratch_baseline.read_bytes()
    output = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": str(BACKEND.joinpath(*current.split(".")).with_suffix(".py")),
                    "severity": "error",
                    "message": "m",
                    "rule": "reportAny",
                }
            ]
        }
    )

    def checker(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["basedpyright", "--outputjson"], 1, output, "")

    monkeypatch.setattr(ratchet.subprocess, "run", checker)
    assert ratchet.main(["check"]) == 1
    assert ratchet.main(["update"]) == 1
    assert scratch_baseline.read_bytes() == before

    output = json.dumps({"generalDiagnostics": []})
    assert ratchet.main(["check"]) == 1
    assert ratchet.main(["update"]) == 0
    assert ratchet.load_baseline() == {}
    assert ratchet.main(["check"]) == 0


def test_check_fails_on_growth_and_on_unrecorded_fixes(
    scratch_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _diagnostic("app.legacy")
    _report(monkeypatch, legacy, legacy)
    assert ratchet.main(["check"]) == 0
    _report(monkeypatch, legacy, legacy, _diagnostic("app.fresh"))
    assert ratchet.main(["check"]) == 1
    _report(monkeypatch, legacy)
    assert ratchet.main(["check"]) == 1


def test_update_records_fixes_and_never_grows_the_baseline(
    scratch_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = scratch_baseline.read_text(encoding="utf-8")
    _report(monkeypatch, _diagnostic("app.legacy"), _diagnostic("app.fresh"))
    assert ratchet.main(["update"]) == 1
    assert scratch_baseline.read_text(encoding="utf-8") == before

    _report(monkeypatch, _diagnostic("app.legacy"))
    assert ratchet.main(["update"]) == 0
    assert ratchet.load_baseline() == {"app.legacy": {"reportAny": 1}}

    _report(monkeypatch)
    assert ratchet.main(["update"]) == 0
    assert ratchet.load_baseline() == {}
