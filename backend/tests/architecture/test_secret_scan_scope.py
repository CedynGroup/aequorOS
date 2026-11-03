"""The required secret scan reads the change under review, not the whole history.

gitleaks used to walk every commit the checkout held, so a pull request failed on
a finding in history it never authored: PRs 761, 765 and 766 were failed by two
reviewed false positives in ``backend/app/case/service/documents.py``.
``.gitleaksignore`` cannot close that gap, because its fingerprints are keyed to
commit SHAs and a rebase rewrites them.

These tests execute the workflow's own scan scripts against fixture repositories,
with a stub ``docker`` recording the arguments gitleaks would receive, and assert
the commit range each event scans.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "secret-scan.yml"
PULL_REQUEST_STEP = "Scan the pull request's commits"
PUSH_STEP = "Scan the pushed commits"
ZERO_SHA = "0" * 40


def _steps() -> dict[str, dict[str, object]]:
    document = cast(
        dict[str, dict[str, object]],
        yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader),
    )
    steps: dict[str, dict[str, object]] = {}
    for job in cast(dict[str, dict[str, object]], document["jobs"]).values():
        for step in cast(list[dict[str, object]], job["steps"]):
            name = step.get("name")
            if isinstance(name, str):
                steps[name] = step
    return steps


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _commit(root: Path, filename: str, body: str, message: str) -> str:
    _ = (root / filename).write_text(body, encoding="utf-8")
    _ = _git(root, "add", ".")
    _ = _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _ = _git(root, "init", "-q", "-b", "main")
    _ = _git(root, "config", "user.email", "ci@example.invalid")
    _ = _git(root, "config", "user.name", "CI fixture")
    _ = _commit(root, "reviewed.py", "BASE = 1\n", "base branch commit")
    return root


def _scan(name: str, root: Path, tmp_path: Path, **event: str) -> list[str]:
    """Run one workflow scan step and return the gitleaks arguments it passed."""
    binary = tmp_path / "bin"
    binary.mkdir(exist_ok=True)
    calls = tmp_path / "docker-calls.txt"
    stub = binary / "docker"
    _ = stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n', encoding="utf-8")
    stub.chmod(0o755)
    script = cast(str, _steps()[name]["run"])
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(root),
            "GITLEAKS_IMAGE": "gitleaks-test-image",
            **event,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    recorded = [line for line in calls.read_text(encoding="utf-8").splitlines() if line]
    assert len(recorded) == 1, recorded
    return shlex.split(recorded[0])


def _log_opts(arguments: list[str]) -> str:
    options = [argument for argument in arguments if argument.startswith("--log-opts=")]
    assert len(options) == 1, arguments
    return options[0].removeprefix("--log-opts=")


def _scanned(root: Path, log_opts: str) -> list[str]:
    return _git(root, "rev-list", log_opts).split()


def test_a_pull_request_scans_only_its_own_commits(repository: Path, tmp_path: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    first = _commit(repository, "feature.py", "FIRST = 1\n", "first pull-request commit")
    second = _commit(repository, "feature.py", "FIRST = 1\nSECOND = 2\n", "second change")

    arguments = _scan(PULL_REQUEST_STEP, repository, tmp_path, BASE_SHA=base, BASE_REF="main")
    log_opts = _log_opts(arguments)

    assert _scanned(repository, log_opts) == [second, first]
    assert "--config" in arguments
    assert ".gitleaks.toml" in arguments
    assert "--redact" in arguments


def test_a_merge_checkout_scans_the_pull_request_and_no_newer_base_commit(
    repository: Path, tmp_path: Path
) -> None:
    """Actions checks out ``refs/pull/<n>/merge``: HEAD is a merge commit."""
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    first = _commit(repository, "feature.py", "FIRST = 1\n", "first pull-request commit")
    second = _commit(repository, "feature.py", "FIRST = 1\nSECOND = 2\n", "second change")
    _ = _git(repository, "checkout", "-q", "main")
    base = _commit(repository, "reviewed.py", "BASE = 1\nMERGED = 2\n", "another pull request")
    _ = _git(repository, "merge", "-q", "--no-ff", "-m", "merge pull request", "feature")
    merge = _git(repository, "rev-parse", "HEAD")

    log_opts = _log_opts(
        _scan(PULL_REQUEST_STEP, repository, tmp_path, BASE_SHA=base, BASE_REF="main")
    )

    assert _scanned(repository, log_opts) == [merge, second, first]
    assert base not in _scanned(repository, log_opts)


def test_a_push_scans_only_the_pushed_commits(repository: Path, tmp_path: Path) -> None:
    before = _git(repository, "rev-parse", "HEAD")
    pushed = _commit(repository, "reviewed.py", "BASE = 2\n", "pushed commit")

    log_opts = _log_opts(
        _scan(PUSH_STEP, repository, tmp_path, BEFORE_SHA=before, AFTER_SHA=pushed)
    )

    assert log_opts == f"{before}..{pushed}"
    assert _scanned(repository, log_opts) == [pushed]


def test_a_new_branch_push_scans_its_own_history_only(repository: Path, tmp_path: Path) -> None:
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    tip = _commit(repository, "feature.py", "FEATURE = 1\n", "first commit on a new branch")
    _ = _git(repository, "checkout", "-q", "main")
    unrelated = _commit(repository, "unrelated.py", "OTHER = 1\n", "commit on another branch")

    log_opts = _log_opts(_scan(PUSH_STEP, repository, tmp_path, BEFORE_SHA=ZERO_SHA, AFTER_SHA=tip))

    assert log_opts == tip
    assert unrelated not in _scanned(repository, log_opts)


def test_an_unknown_previous_tip_never_fails_the_check(repository: Path, tmp_path: Path) -> None:
    """A rewritten server history may drop the previous tip from the clone."""
    after = _commit(repository, "reviewed.py", "BASE = 3\n", "pushed commit")

    log_opts = _log_opts(
        _scan(PUSH_STEP, repository, tmp_path, BEFORE_SHA="1" * 40, AFTER_SHA=after)
    )

    assert log_opts == after


def test_each_range_scan_runs_only_for_its_own_event() -> None:
    steps = _steps()
    assert steps[PULL_REQUEST_STEP]["if"] == "github.event_name == 'pull_request'"
    assert steps[PUSH_STEP]["if"] == "github.event_name == 'push'"
