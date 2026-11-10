"""Exercise the release-binary cache contract of the composite CI setup actions.

The MinIO and OpenBao setup actions download a pinned release binary. When the
release CDN answers 504 (run 42567029227), the whole CI job dies with it. These
tests pin the contract the fix relies on: the binary is restored from
`actions/cache` keyed by version, platform and checksum; the download runs only
on a cache miss; a checksum-verified binary is the only artifact that ever
enters the cache path; and the checksum is enforced on every run, cache hit or
not, with a failed download failing the step loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

BACKEND = Path(__file__).parents[2]
REPO = BACKEND.parent
ACTIONS = REPO / ".github/actions"

# The verify checksum is the one enforced on every run: for MinIO the release
# binary itself, for OpenBao the extracted `bao` binary from the verified
# tarball (whose own pinned sha256 is checked on the download path).
EXPECTED: dict[str, dict[str, str]] = {
    "setup-minio": {
        "directory": "aequoros-ci-minio",
        "version": "RELEASE.2025-09-07T16-13-09Z",
        "verify_sha256": "7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f",
    },
    "setup-openbao": {
        "directory": "aequoros-ci-openbao",
        "version": "v2.6.1",
        "verify_sha256": "736b8ecf354fda6b2af62e4ae064f12fe6c52d7db8425b9c6de22f286a5485ec",
    },
}


def action_document(action: str) -> dict[str, Any]:
    path = ACTIONS / action / "action.yml"
    document = cast(dict[str, Any], yaml.safe_load(path.read_text()))
    return document


def steps(action: str) -> list[dict[str, Any]]:
    runs = cast(dict[str, Any], action_document(action)["runs"])
    return cast(list[dict[str, Any]], runs["steps"])


def step_script(step: dict[str, Any]) -> str:
    return str(step.get("run", ""))


def cache_step(action: str) -> dict[str, Any]:
    return next(step for step in steps(action) if "actions/cache" in str(step.get("uses", "")))


def download_step(action: str) -> dict[str, Any]:
    return next(step for step in steps(action) if "releases/download" in step_script(step))


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_the_binary_is_restored_from_actions_cache(action: str) -> None:
    expected = EXPECTED[action]
    cache = cache_step(action)
    assert cache["uses"] == "actions/cache@v6", (
        "the setup actions must pin actions/cache the way the workflows do"
    )
    inputs = cast(dict[str, str], cache["with"])
    assert inputs["path"] == "${{ runner.temp }}/" + expected["directory"]
    key = inputs["key"]
    assert expected["version"] in key, "the cache key must pin the tool version"
    assert "${{ runner.os }}" in key and "${{ runner.arch }}" in key, (
        "the cache key must pin the platform"
    )
    assert expected["verify_sha256"] in key, "the cache key must pin the checksum"


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_the_release_download_only_runs_on_a_cache_miss(action: str) -> None:
    download = download_step(action)
    cache = cache_step(action)
    assert download["if"] == f"steps.{cache['id']}.outputs.cache-hit != 'true'", (
        "the download step must be skipped on a cache hit"
    )


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_a_failed_download_fails_loudly_and_never_caches_partial_data(action: str) -> None:
    expected = EXPECTED[action]
    script = step_script(download_step(action))
    assert script.startswith("set -euo pipefail"), "a download failure must abort the step"
    assert "--fail" in script, "curl must fail on HTTP errors instead of saving them"
    assert "--retry" in script, "curl keeps its existing retry budget"
    assert "|| true" not in script and "set +e" not in script, "no silent continue"
    # The download lands outside the cached directory and only a checksum-
    # verified artifact is moved into it, so a failed download cannot poison
    # the cache with a partial file.
    output = next(line.split()[-1] for line in script.splitlines() if "--output" in line)
    assert expected["directory"] not in output
    check_line = next(
        i for i, line in enumerate(script.splitlines()) if "sha256sum --check" in line
    )
    move_line = next(i for i, line in enumerate(script.splitlines()) if "mv " in line)
    assert check_line < move_line, "verification must happen before the file enters the cache path"


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_the_binary_is_integrity_checked_on_every_run(action: str) -> None:
    expected = EXPECTED[action]
    verify = next(
        step
        for step in steps(action)
        if "sha256sum --check --strict" in step_script(step)
        and "cache-hit" not in str(step.get("if", ""))
    )
    assert "if" not in verify, "integrity verification must also cover a cache hit"
    script = step_script(verify)
    assert expected["verify_sha256"] in script
    assert f'"{expected["directory"]}' in script or expected["directory"] in script
    assert script.startswith("set -euo pipefail")


@pytest.mark.parametrize(
    ("action", "downloaded_sha256"),
    [
        ("setup-minio", "7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f"),
        ("setup-openbao", "ca8d836eb3a5c80407e45e762300b64e7138c419e78826955f2e4ba4ce6d8a6b"),
    ],
)
def test_the_download_path_verifies_the_pinned_release_checksum(
    action: str, downloaded_sha256: str
) -> None:
    script = step_script(download_step(action))
    assert downloaded_sha256 in script, (
        "the download must be verified against the official release checksum"
    )


def test_every_composite_action_downloading_a_release_binary_caches_it() -> None:
    """A future setup action downloading a release binary must cache it too."""
    offenders = [
        f"{path.parent.name}"
        for path in sorted(ACTIONS.glob("*/action.yml"))
        if "releases/download" in path.read_text()
        and "actions/cache" not in path.read_text()
    ]
    assert offenders == [], (
        "these composite actions download release binaries without a cache: "
        f"{offenders}"
    )
