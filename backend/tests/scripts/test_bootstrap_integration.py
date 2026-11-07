"""Bootstrap contracts that drive real ``git``, ``mise`` and ``uv`` processes.

The integration suffix selects these cases; the hermetic remainder lives in
its suffix-free ``test_bootstrap.py`` sibling.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import bootstrap
from tests.support.bootstrap_fixtures import (
    BootstrapFixture,
    MiseConfig,
    isolated_environment,
    isolated_git,
)


class BootstrapIntegrationTests(BootstrapFixture):
    def test_unmanaged_noop_hooks_fail_verification_and_install_postcheck(self) -> None:
        global_config = self.root / "gitconfig"
        global_config.write_text("")
        with isolated_git(self.root, PRE_COMMIT_HOME=str(self.root / "cache")):
            root = self.root / "checkout"
            self.prepare_hook_checkout(root)
            directory = root / ".git/hooks"
            directory.mkdir()
            for name in bootstrap.REQUIRED_HOOKS:
                hook = directory / name
                hook.write_text("#!/bin/sh\nexit 0\n")
                hook.chmod(0o755)
            self.assertEqual(bootstrap.hook_checks(root)[0].state, "missing")
            config_before = (root / ".git/config").read_bytes()
            self.assertEqual(bootstrap.install_hooks(root), 0)
            self.assertEqual(bootstrap.install_hooks(root), 0)
            self.assertEqual((root / ".git/config").read_bytes(), config_before)
            self.assertFalse((root / "backend/.venv/hooks").exists())
            self.run_required_hooks(root)
            self.assertEqual(
                set((root / "hook-log").read_text().splitlines()), set(bootstrap.REQUIRED_HOOKS)
            )
            with patch.object(bootstrap, "_stream", return_value=0):
                (directory / "pre-push").write_text("#!/bin/sh\nexit 0\n")
                self.assertEqual(bootstrap.install_hooks(root), 1)

    def test_backend_smoke_never_bootstraps_an_environment(self) -> None:
        test = self.root / ".github/scripts/mise_wiring_preflight.py"
        test.parent.mkdir(parents=True)
        test.write_text("")
        with patch.object(bootstrap, "_stream") as stream:
            self.assertEqual(bootstrap.backend_smoke(self.root).state, "missing")
            stream.assert_not_called()
        self.assertFalse((self.root / "backend/.venv").exists())
        python = self.root / "backend/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > smoke-args\n')
        python.chmod(0o755)
        self.assertEqual(bootstrap.backend_smoke(self.root).state, "ok")
        self.assertEqual(
            (self.root / "backend/smoke-args").read_text().splitlines(),
            [str(test)],
        )
        test.rename(test.with_name("mise_wiring_preflight_alternate.py"))
        relocated = self.root / "backend/mise_wiring_preflight.py"
        relocated.write_text("")
        with patch.object(bootstrap, "_stream") as stream:
            result = bootstrap.backend_smoke(self.root)
            self.assertEqual(result.state, "missing")
            self.assertIn(".github/scripts/mise_wiring_preflight.py", result.detail)
            stream.assert_not_called()

    def test_setup_refuses_a_lockfile_that_trails_project_metadata(self) -> None:
        """The setup task's own env must reject a stale ``uv.lock``, not consume it."""
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "uv is required for the lockfile-freshness regression")
        assert uv is not None
        config = cast(
            MiseConfig, tomllib.loads((Path(bootstrap.DEFAULT_ROOT) / "mise.toml").read_text())
        )
        setup_environment = dict(config["tasks"]["setup"].get("env") or {})
        self.assertTrue(setup_environment, "setup must declare its dependency environment")
        project = self.root / "uv-project"
        (project / "dependency").mkdir(parents=True)
        (project / "dependency/pyproject.toml").write_text(
            '[project]\nname = "aqs-local-dependency"\nversion = "1.0.0"\n'
            'requires-python = ">=3.11"\n'
        )
        (project / "pyproject.toml").write_text(
            '[project]\nname = "aqs-lock-probe"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = []\n'
        )
        environment = {
            key: value
            for key, value in isolated_environment(self.root).items()
            if not key.startswith("UV_")
        }
        lock = subprocess.run(
            [uv, "lock", "--offline"],
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(lock.returncode, 0, lock.stdout + lock.stderr)
        locked = (project / "uv.lock").read_text()
        # Declaring a dependency after the lock was written is exactly the drift a
        # maintainer creates by editing pyproject.toml and forgetting `uv lock`.
        (project / "pyproject.toml").write_text(
            '[project]\nname = "aqs-lock-probe"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = ["aqs-local-dependency"]\n'
            '\n[tool.uv.sources]\naqs-local-dependency = { path = "dependency" }\n'
        )

        def sync(**extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [uv, "sync", "--offline"],
                cwd=project,
                env={**environment, **extra},
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )

        refused = sync(**setup_environment)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("uv.lock", refused.stderr)
        self.assertEqual((project / "uv.lock").read_text(), locked)
        # Counterfactual: the previous frozen setting silently accepted that drift.
        accepted = sync(UV_FROZEN="1")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)


if __name__ == "__main__":
    unittest.main()
