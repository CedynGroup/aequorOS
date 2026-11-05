"""Executable bootstrap contracts that need no external tool or service.

Cases that drive real ``git``, ``mise`` and ``uv`` processes live in the
``_integration`` sibling, so the filename alone tells a maintainer which lane
runs them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
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


class BootstrapTests(BootstrapFixture):
    def test_env_creation_is_repeatable_and_preserves_values(self) -> None:
        self.assertEqual(bootstrap.main(["env", "--root", str(self.root)]), 0)
        local = self.root / "backend/.env"
        local.write_text("AUTH_JWT_SECRET=private-local-value\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bootstrap.main(["env", "--root", str(self.root)]), 0)
        self.assertEqual(local.read_text(), "AUTH_JWT_SECRET=private-local-value\n")
        self.assertNotIn("private-local-value", output.getvalue())
        self.assertTrue(all(check.state == "ok" for check in bootstrap.env_checks(self.root)))

    def test_external_managers_remain_untouched_and_unverified(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()
        helper = shared / "helper"
        helper.write_text("#!/bin/sh\necho manager >> hook-log\n")
        helper.chmod(0o755)
        for name in (*bootstrap.REQUIRED_HOOKS, "post-checkout"):
            hook = shared / name
            hook.write_text('#!/bin/sh\n"$(dirname "$0")/helper"\n')
            hook.chmod(0o755)
        before = {path.name: (path.read_bytes(), path.stat().st_mode) for path in shared.iterdir()}
        global_config = self.root / "gitconfig"
        global_config.write_text(f"[core]\n hooksPath = {shared}\n")
        with isolated_git(self.root, PRE_COMMIT_HOME=str(self.root / "cache")):
            for name in ("first", "second"):
                root = self.root / name
                self.prepare_hook_checkout(root)
                config = root / ".git/config"
                initial_config = config.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(bootstrap.install_hooks(root), 0)
                    self.assertEqual(bootstrap.install_hooks(root), 0)
                check = bootstrap.hook_checks(root)[0]
                self.assertEqual(check.state, "missing")
                self.assertIn("unverified", check.detail)
                self.assertIn("forwarding hook arguments and pre-push stdin", output.getvalue())
                self.assertEqual(config.read_bytes(), initial_config)
                self.assertFalse((root / "backend/.venv/hooks").exists())
                self.run_required_hooks(root)
                subprocess.run([str(shared / "post-checkout")], cwd=root, check=True)
                self.assertEqual((root / "hook-log").read_text().splitlines(), ["manager"] * 4)
                with contextlib.ExitStack() as stack:
                    for function in (
                        "toolchain_checks",
                        "dependency_checks",
                        "env_checks",
                        "git_checks",
                        "credential_checks",
                        "capability_checks",
                    ):
                        stack.enter_context(patch.object(bootstrap, function, return_value=[]))
                    for function in ("backend_smoke", "frontend_check"):
                        stack.enter_context(
                            patch.object(
                                bootstrap,
                                function,
                                return_value=bootstrap.Check("core", "ok", "passed"),
                            )
                        )
                    result = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(bootstrap.verify(root), 1)
                    self.assertEqual(bootstrap.setup_report(root), 1)
                    self.assertIn("VERIFY: NOT READY", result.getvalue())
                    self.assertIn("unverified", result.getvalue())
        self.assertEqual(
            before,
            {path.name: (path.read_bytes(), path.stat().st_mode) for path in shared.iterdir()},
        )
        self.assertEqual(global_config.read_text(), f"[core]\n hooksPath = {shared}\n")

    def test_mise_verify_does_not_install_but_setup_explicitly_installs(self) -> None:
        mise = shutil.which("mise")
        self.assertIsNotNone(mise, "mise is required for the entrypoint regression")
        assert mise is not None
        config = cast(
            MiseConfig, tomllib.loads((Path(bootstrap.DEFAULT_ROOT) / "mise.toml").read_text())
        )
        profile = self.root / "mise-profile"
        profile.mkdir()
        install_log = profile / "install-log"
        commands = profile / "command-log"
        # The commands the setup array invokes are provided by the tools mise
        # installs, exactly as on a machine where they are not yet on PATH.
        providers = {
            name: (
                "#!/bin/sh\n"
                'echo "$*" >> "$AQS_TEST_COMMAND_LOG"\n'
                + (
                    'case "$*" in *verify*) echo "VERIFY: NOT READY"; exit 1;; esac\n'
                    if name != "aqs-fixture"
                    else ""
                )
                + "exit 0\n"
            )
            for name in ("python3", "pnpm", "aqs-fixture")
        }
        plugin = profile / "plugin/bin"
        plugin.mkdir(parents=True)
        install = plugin / "install"
        install.write_text(
            "#!/bin/sh\nset -eu\n"
            'echo installed >> "$AQS_TEST_INSTALL_LOG"\n'
            'mkdir -p "$ASDF_INSTALL_PATH/bin"\n'
            + "".join(
                f'cat > "$ASDF_INSTALL_PATH/bin/{name}" <<'
                f"'AQS_PROVIDER_EOF'\n{body}AQS_PROVIDER_EOF\n"
                f'chmod +x "$ASDF_INSTALL_PATH/bin/{name}"\n'
                for name, body in providers.items()
            )
        )
        install.chmod(0o755)
        (plugin / "list-all").write_text("#!/bin/sh\necho 1.0.0\n")
        (plugin / "list-all").chmod(0o755)
        tasks = config["tasks"]
        settings = config["settings"]
        (profile / "aqs-test.toml").write_text(
            "[settings]\nauto_install = "
            + str(settings["auto_install"]).lower()
            + "\n[tools]\n"
            + "".join(f'"{name}" = "1.0.0"\n' for name in providers)
            + "[tasks.setup]\nrun = "
            + json.dumps(tasks["setup"]["run"])
            + "\n"
            + "".join(
                f'env."{key}" = {json.dumps(value)}\n'
                for key, value in (tasks["setup"].get("env") or {}).items()
            )
            + "[tasks.verify]\nrun = "
            + json.dumps(tasks["verify"]["run"])
            + '\n[tasks."risk-service:sync"]\nrun = "pnpm sync-boundary"\n'
            + '[tasks."risk-service:hooks"]\nrun = "pnpm hooks-boundary"\n'
        )
        environment = {
            key: value
            for key, value in isolated_environment(plugin.parent).items()
            if not key.startswith("MISE_")
        }
        environment.update(
            {
                "MISE_DATA_DIR": str(profile / "data"),
                "MISE_CACHE_DIR": str(profile / "cache"),
                "MISE_CONFIG_DIR": str(profile / "config"),
                "MISE_STATE_DIR": str(profile / "state"),
                "MISE_SYSTEM_CONFIG_DIR": str(profile / "system"),
                "MISE_GLOBAL_CONFIG_FILE": str(profile / "global.toml"),
                "MISE_OVERRIDE_CONFIG_FILENAMES": "aqs-test.toml",
                "MISE_TRUSTED_CONFIG_PATHS": str(profile),
                "AQS_TEST_INSTALL_LOG": str(install_log),
                "AQS_TEST_COMMAND_LOG": str(commands),
            }
        )

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [mise, *args],
                cwd=profile,
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )

        with isolated_git(plugin.parent):
            subprocess.run(["git", "init", "--template=", "-q", str(plugin.parent)], check=True)
            for name in providers:
                linked = run("plugins", "link", name, str(plugin.parent))
                self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
        verify = run("run", "verify")
        self.assertNotEqual(verify.returncode, 0)
        self.assertFalse(install_log.exists(), verify.stdout + verify.stderr)
        self.assertFalse((profile / "data/installs/aqs-fixture/1.0.0").exists())
        setup = run("run", "setup")
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        self.assertEqual(install_log.read_text().splitlines(), ["installed"] * len(providers))
        recorded = commands.read_text().splitlines()
        # Each post-install entry must reach the tool installed by this same array,
        # which only happens when it runs through a refreshed mise environment.
        self.assertIn("backend/scripts/bootstrap.py env", recorded)
        self.assertIn("install --frozen-lockfile", recorded)
        self.assertIn("backend/scripts/bootstrap.py report", recorded)
        self.assertIn("sync-boundary", recorded)
        self.assertIn("hooks-boundary", recorded)

    def test_interpreters_before_311_get_a_named_remedy(self) -> None:
        output = io.StringIO()
        with (
            patch.object(bootstrap.sys, "version_info", (3, 9, 0)),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(bootstrap.main(["verify", "--root", str(self.root)]), 1)
        printed = output.getvalue()
        self.assertIn("python3 is 3.9", printed)
        self.assertIn("mise install", printed)
        self.assertNotIn("Traceback", printed)

    def test_fixture_git_never_reaches_a_caller_repository(self) -> None:
        decoy = self.root / "decoy"
        fixture = self.root / "fixture"
        identity = [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
        ]
        redirect = {
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy / ".git/index"),
        }
        with isolated_git(self.root):
            subprocess.run(["git", "init", "--template=", "-q", str(decoy)], check=True)
            subprocess.run(["git", "init", "--template=", "-q", str(fixture)], check=True)
            decoy_commit = [
                "git",
                *identity,
                "commit",
                "--allow-empty",
                "--no-verify",
                "-qm",
                "decoy",
            ]
            subprocess.run(decoy_commit, cwd=decoy, check=True)
            decoy_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=decoy, text=True)
            with patch.dict(os.environ, redirect, clear=False), isolated_git(fixture):
                for name in redirect:
                    self.assertNotIn(name, os.environ)
                resolved = subprocess.check_output(
                    ["git", "rev-parse", "--absolute-git-dir"], cwd=fixture, text=True
                ).strip()
                self.assertEqual(Path(resolved), (fixture / ".git").resolve())
                subprocess.run(
                    ["git", *identity, "commit", "--allow-empty", "--no-verify", "-qm", "fixture"],
                    cwd=fixture,
                    check=True,
                )
                self.assertTrue(
                    subprocess.check_output(
                        ["git", "rev-parse", "--verify", "HEAD"], cwd=fixture, text=True
                    ).strip()
                )
            self.assertEqual(
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=decoy, text=True),
                decoy_head,
            )

    def test_missing_tools_have_named_diagnostics(self) -> None:
        (self.root / "mise.toml").write_text(
            '[tools]\nnode="24"\npnpm="11"\npython="3.13"\nuv="0.11"\n'
        )
        with patch.object(bootstrap.shutil, "which", return_value=None):
            checks = bootstrap.toolchain_checks(self.root)
        self.assertEqual({check.label for check in checks}, {"node", "pnpm", "python", "uv"})
        self.assertTrue(all(check.state == "missing" and check.remedy for check in checks))

    def test_optional_services_and_openbao_override(self) -> None:
        binary = self.root / "custom-bao"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        with patch.dict(os.environ, {"AQS_OPENBAO_BIN": str(binary)}, clear=True):
            self.assertEqual(
                next(
                    check
                    for check in bootstrap.capability_checks(self.root)
                    if check.label == "OpenBao"
                ).state,
                "info",
            )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(bootstrap.shutil, "which", return_value=None),
            patch.object(bootstrap, "_tcp_reachable", return_value=False),
        ):
            checks = bootstrap.capability_checks(self.root)
        self.assertEqual(
            {check.label for check in checks}, {"PostgreSQL", "S3/MinIO", "OpenBao", "Docker"}
        )
        self.assertTrue(all(check.state == "skip" for check in checks))

    def test_verify_readiness_includes_required_env_and_core_checks(self) -> None:
        bootstrap.copy_env_templates(self.root)
        ok = bootstrap.Check("test", "ok", "passed")
        with contextlib.ExitStack() as stack:
            for name in (
                "toolchain_checks",
                "dependency_checks",
                "hook_checks",
                "git_checks",
                "credential_checks",
                "capability_checks",
            ):
                stack.enter_context(patch.object(bootstrap, name, return_value=[]))
            smoke = stack.enter_context(patch.object(bootstrap, "backend_smoke", return_value=ok))
            stack.enter_context(patch.object(bootstrap, "frontend_check", return_value=ok))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(bootstrap.main(["verify", "--root", str(self.root)]), 0)
            self.assertIn("VERIFY: READY", output.getvalue())
            smoke.return_value = bootstrap.Check("Backend smoke", "missing", "failed")
            self.assertEqual(bootstrap.verify(self.root), 1)
            smoke.return_value = ok
            for _, target, _ in bootstrap.ENV_TEMPLATES:
                local = self.root / target
                content = local.read_text()
                local.unlink()
                self.assertEqual(bootstrap.verify(self.root), 1)
                local.write_text(content)
            self.assertIn("VERIFY: NOT READY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
