"""Shared scaffolding for the bootstrap entrypoint suite siblings.

``tests/scripts/test_bootstrap.py`` owns the hermetic cases and
``tests/scripts/test_bootstrap_integration.py`` owns the cases that drive real
``git``, ``mise`` and ``uv`` processes. Both need the same temporary root and
hook-checkout fixtures, so the scaffolding lives here rather than being copied.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Generator
from pathlib import Path
from typing import NotRequired, TypedDict
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import bootstrap


class TaskConfig(TypedDict):
    """One ``mise`` task definition, as far as these fixtures inspect it."""

    run: str | list[str]
    env: NotRequired[dict[str, str]]


class SettingsConfig(TypedDict):
    """The ``[settings]`` table of a ``mise`` configuration."""

    auto_install: bool


class MiseConfig(TypedDict):
    """The subset of ``mise.toml`` these fixtures type."""

    tasks: dict[str, TaskConfig]
    settings: SettingsConfig


#: Variables that relocate the repository git operates on. A hook runner or caller
#: shell can export these; inheriting them makes a fixture command act on the real
#: checkout no matter which ``cwd`` the test passes.
GIT_LOCATION_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
    }
)


def isolated_environment(root: Path, **extra: str) -> dict[str, str]:
    """The inherited environment minus caller git configuration and location."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_CONFIG_") and key not in GIT_LOCATION_VARIABLES
    }
    environment.update(
        {"GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1", **extra}
    )
    return environment


@contextlib.contextmanager
def isolated_git(root: Path, **extra: str) -> Generator[None]:
    """Run with only this fixture's git configuration, never the caller's.

    A harness may inject GIT_CONFIG_COUNT/KEY/VALUE (or GIT_CONFIG_PARAMETERS) to
    redirect core.hooksPath for its own checkouts, and a hook that runs pytest may
    export GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE. Inheriting either makes fixture
    git commands read or write the caller's repository and makes a fixture checkout
    look externally managed, failing these contracts for reasons that have nothing
    to do with the code under test.
    """
    with patch.dict(os.environ, isolated_environment(root, **extra), clear=True):
        yield


class BootstrapFixture(unittest.TestCase):
    """A temporary project root plus the hook-checkout helpers both suites use."""

    temporary: tempfile.TemporaryDirectory[str]
    root: Path

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for template, _, _ in bootstrap.ENV_TEMPLATES:
            path = self.root / template
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("AUTH_JWT_SECRET=change-me-openssl-rand-base64-32\n")

    def prepare_hook_checkout(self, root: Path) -> None:
        """Create a git checkout whose hook runner records every hook it runs."""
        root.mkdir(parents=True, exist_ok=True)
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "--template=", "-q", str(root)], check=True)
        scripts = root / "backend/scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(Path(bootstrap.__file__), scripts / "bootstrap.py")
        python = root / "backend/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        (root / "record.py").write_text(
            "from pathlib import Path\nimport sys\n"
            'with Path("hook-log").open("a") as log: log.write(sys.argv[1] + "\\n")\n'
        )
        hooks = "\n".join(
            f"    - id: {name}\n      name: {name}\n"
            f"      entry: {shlex.quote(sys.executable)} record.py {name}\n"
            f"      language: system\n      stages: [{name}]\n"
            "      always_run: true\n      pass_filenames: false"
            for name in bootstrap.REQUIRED_HOOKS
        )
        (root / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: local\n    hooks:\n" + hooks + "\n"
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "--no-verify",
                "-qm",
                "fixture",
            ],
            cwd=root,
            check=True,
        )

    def run_required_hooks(self, root: Path) -> None:
        """Run every required hook against a throwaway revision."""
        (root / "message").write_text("test: fixture\n")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        arguments = {"pre-commit": [], "commit-msg": ["message"], "pre-push": ["origin", "unused"]}
        for name, args in arguments.items():
            hook = subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks/" + name],
                cwd=root,
                text=True,
            ).strip()
            subprocess.run(
                [hook, *args],
                cwd=root,
                input=f"refs/heads/main {revision} refs/heads/main {'0' * 40}\n",
                text=True,
                check=True,
            )
