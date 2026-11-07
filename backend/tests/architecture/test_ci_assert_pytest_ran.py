"""The depth job's report check must see every promised suite, not just totals.

CodeRabbit review of the scheduled RLS-depth workflow: the assertion step's
`--min-executed 5` floor is the collected 8 minus the largest module's 3, so a
promised module holding exactly those 3 tests can stop collecting and the floor
still holds — `--max-skipped 0` cannot see it either, because absent tests are
not skips. The weekly job could then report success without one of its three
promised PostgreSQL property suites. Totals need a companion: the report must
name an executed test from each promised module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = (
    Path(__file__).parents[3] / ".github" / "scripts" / "assert_pytest_ran.py"
)
WORKFLOW = Path(__file__).parents[3] / ".github" / "workflows" / "rls-depth.yml"

# The three promised modules, with each module's test count (3 / 3 / 2 = 8).
PROMISED_MODULES = {
    "test_authorization_tenant_isolation_properties_integration": 3,
    "test_authorization_effective_access_properties_integration": 3,
    "test_authorization_object_reference_properties_integration": 2,
}


def _suite(classname: str, executed: int, skipped: int) -> str:
    cases = [
        f'<testcase classname="{classname}" name="case_{n}"/>'
        for n in range(executed)
    ]
    cases += [
        f'<testcase classname="{classname}" name="skipped_{n}"><skipped/></testcase>'
        for n in range(skipped)
    ]
    count = executed + skipped
    return (
        f'<testsuite name="{classname}" tests="{count}" skipped="{skipped}" '
        f'errors="0" failures="0">{"".join(cases)}</testsuite>'
    )


def _report(path: Path, suites: list[str]) -> Path:
    path.write_text(
        f'<?xml version="1.0"?><testsuites>{"".join(suites)}</testsuites>',
        encoding="utf-8",
    )
    return path


def _run(
    report: Path, *flags: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report), *flags],
        capture_output=True,
        text=True,
        check=False,
    )


def _complete_selection() -> list[str]:
    return [
        _suite(f"tests.db.{module}", tests, 0)
        for module, tests in PROMISED_MODULES.items()
    ]


def _required_flags() -> list[str]:
    return [
        flag
        for module in PROMISED_MODULES
        for flag in ("--required-suite", module)
    ]


def test_complete_selection_of_all_three_promised_modules_passes(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path / "report.xml", _complete_selection())

    result = _run(report, "--min-executed", "5", "--max-skipped", "0", *_required_flags())

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing", sorted(PROMISED_MODULES))
def test_each_promised_module_vanishing_fails_even_when_the_floor_still_holds(
    tmp_path: Path, missing: str
) -> None:
    # Remove the module's whole file; the other two keep the total at 5 or 6
    # executed tests — at or above the floor — with zero skips.
    remaining = [
        _suite(f"tests.db.{module}", tests, 0)
        for module, tests in PROMISED_MODULES.items()
        if module != missing
    ]
    report = _report(tmp_path / "report.xml", remaining)

    result = _run(report, "--min-executed", "5", "--max-skipped", "0", *_required_flags())

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"'{missing}'" in result.stdout


def test_a_promised_module_whose_tests_all_skip_fails_the_identity_check(
    tmp_path: Path,
) -> None:
    # Skips already trip --max-skipped 0; the identity check must not accept
    # them as execution either, so it also fails without the skip flag.
    modules = sorted(PROMISED_MODULES)
    all_skipped_first = [
        _suite(f"tests.db.{modules[0]}", 0, PROMISED_MODULES[modules[0]]),
        _suite(f"tests.db.{modules[1]}", PROMISED_MODULES[modules[1]], 0),
        _suite(f"tests.db.{modules[2]}", PROMISED_MODULES[modules[2]], 0),
    ]
    report = _report(tmp_path / "report.xml", all_skipped_first)

    without_skip_flag = _run(report, *_required_flags())

    assert without_skip_flag.returncode == 1
    assert f"'{modules[0]}'" in without_skip_flag.stdout


def test_without_required_suite_flags_the_script_keeps_its_totals_only_behavior(
    tmp_path: Path,
) -> None:
    # Other consumers (risk-service.yml, dashboard-journeys.yml) pass no
    # --required-suite; a vanishing module must stay invisible to them, i.e.
    # their existing semantics are unchanged.
    remaining = [
        _suite(f"tests.db.{module}", tests, 0)
        for module, tests in PROMISED_MODULES.items()
        if module != "test_authorization_tenant_isolation_properties_integration"
    ]
    report = _report(tmp_path / "report.xml", remaining)

    result = _run(report, "--min-executed", "5", "--max-skipped", "0")

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_report_is_still_refused(tmp_path: Path) -> None:
    result = _run(tmp_path / "absent.xml", *_required_flags())

    assert result.returncode == 1
    assert "was not written" in result.stdout


def test_the_depth_workflow_passes_an_identity_flag_for_each_promised_module() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["rls-depth"]["steps"]
    assert_step = next(
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("name", "")).startswith("The property suites must have executed")
    )
    command = assert_step["run"]

    for module in PROMISED_MODULES:
        assert f"--required-suite {module}" in command
    assert "--min-executed 5" in command
    assert "--max-skipped 0" in command
