"""Pull-request secret scans read the change under review, not the whole history.

gitleaks used to walk every commit the checkout held, so a pull request failed on
a finding in history it never authored: PRs 761, 765 and 766 were failed by two
reviewed false positives in ``backend/app/case/service/documents.py``.
``.gitleaksignore`` cannot close that gap, because its fingerprints are keyed to
commit SHAs and a rebase rewrites them.

These tests execute the workflow's own scan scripts against fixture repositories,
with a stub ``docker`` recording the arguments gitleaks would receive, and assert
the PR commit range, the files staged for a content scan, and the event
conditions on all scan steps.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "secret-scan.yml"
PULL_REQUEST_STEP = "Scan the pull request's commits"
PULL_REQUEST_FILES_STEP = "Scan the pull request's changed files"


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
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(body, encoding="utf-8")
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
    _ = stub.write_text(
        f'#!/bin/sh\nprintf "%s\\0" "$@" >> {shlex.quote(str(calls))}\n', encoding="utf-8"
    )
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
    recorded = calls.read_bytes().split(b"\0")
    assert recorded[-1] == b"", recorded
    return [argument.decode() for argument in recorded[:-1]]


def _log_opts(arguments: list[str]) -> str:
    options = [argument for argument in arguments if argument.startswith("--log-opts=")]
    assert len(options) == 1, arguments
    return options[0].removeprefix("--log-opts=")


def _scanned(root: Path, log_opts: str) -> list[str]:
    return _git(root, "log", *shlex.split(log_opts), "--format=%H", "--no-patch").split()


def _patches(root: Path, log_opts: str) -> str:
    return _git(root, "log", "-p", "-U0", *shlex.split(log_opts))


def test_a_pull_request_scans_only_its_own_commits(repository: Path, tmp_path: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    first = _commit(repository, "feature.py", "FIRST = 1\n", "first pull-request commit")
    second = _commit(repository, "feature.py", "FIRST = 1\nSECOND = 2\n", "second change")

    arguments = _scan(PULL_REQUEST_STEP, repository, tmp_path, BASE_SHA=base)
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

    log_opts = _log_opts(_scan(PULL_REQUEST_STEP, repository, tmp_path, BASE_SHA=base))

    assert _scanned(repository, log_opts) == [merge, second, first]
    assert base not in _scanned(repository, log_opts)
    patches = _patches(repository, log_opts)
    assert "+FIRST = 1" in patches
    assert "+SECOND = 2" in patches
    assert "MERGED = 2" not in patches


def test_a_merge_resolution_credential_is_scanned_without_base_changes(
    repository: Path, tmp_path: Path
) -> None:
    credential = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
    base_credential = "ghp_" + "k1L2m3N4o5P6q7R8s9T0" * 2
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    feature = _commit(repository, "reviewed.py", "BASE = 2\n", "feature edit")
    _ = _git(repository, "checkout", "-q", "main")
    _ = _commit(repository, "base_only.py", f'TOKEN = "{base_credential}"\n', "base credential")
    _ = _commit(repository, "reviewed.py", "BASE = 3\n", "conflicting base edit")
    _ = _git(repository, "checkout", "-q", "feature")
    conflict = subprocess.run(
        ["git", "-C", str(repository), "merge", "--no-ff", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert conflict.returncode == 1, conflict.stdout + conflict.stderr
    resolution = _commit(
        repository, "reviewed.py", f'BASE = 4\nTOKEN = "{credential}"\n', "resolve conflict"
    )
    deletion = _commit(repository, "reviewed.py", "BASE = 4\n", "remove credential")
    _ = _git(repository, "checkout", "-q", "main")
    base = _commit(repository, "newer_base.py", "UNRELATED = 1\n", "newer base edit")
    _ = _git(repository, "merge", "-q", "--no-ff", "-m", "merge pull request", "feature")
    checkout = _git(repository, "rev-parse", "HEAD")

    log_opts = _log_opts(_scan(PULL_REQUEST_STEP, repository, tmp_path, BASE_SHA=base))
    patches = _patches(repository, log_opts)

    assert set(_scanned(repository, log_opts)) == {checkout, deletion, resolution, feature}
    assert f'+TOKEN = "{credential}"' in patches
    assert base_credential not in patches
    assert "UNRELATED = 1" not in patches
    assert credential not in _git(repository, "show", "HEAD:reviewed.py")


def test_a_pull_request_scans_the_complete_content_of_only_its_changed_files(
    repository: Path, tmp_path: Path
) -> None:
    """Unchanged files are never read; changed files are read whole, not as diffs."""
    base = _git(repository, "rev-parse", "HEAD")
    _ = _git(repository, "checkout", "-q", "-b", "feature")
    _ = _commit(repository, "settings/keys.py", "NAME = 'api_key'\nVALUE = 'plain'\n", "add keys")
    _ = _commit(repository, "notes.txt", "first\n", "add notes")

    arguments = _scan(
        PULL_REQUEST_FILES_STEP, repository, tmp_path, BASE_SHA=base, RUNNER_TEMP=str(tmp_path)
    )

    content = tmp_path / "pull-request-content"
    staged = sorted(
        path.relative_to(content).as_posix() for path in content.rglob("*") if path.is_file()
    )
    assert staged == ["notes.txt", "settings/keys.py"]
    assert (content / "settings/keys.py").read_text(encoding="utf-8") == (
        "NAME = 'api_key'\nVALUE = 'plain'\n"
    )
    assert "--no-git" in arguments
    assert arguments[arguments.index("--source") + 1] == "."
    assert "/workspace/.gitleaks.toml" in arguments
    assert "--redact" in arguments


def test_each_range_scan_runs_only_for_its_own_event() -> None:
    steps = _steps()
    assert steps[PULL_REQUEST_STEP]["if"] == "github.event_name == 'pull_request'"
    assert steps[PULL_REQUEST_FILES_STEP]["if"] == "github.event_name == 'pull_request'"
    assert steps["Scan working tree"]["if"] == "github.event_name == 'push'"
    assert steps["Scan git history"]["if"] == "github.event_name == 'push'"
