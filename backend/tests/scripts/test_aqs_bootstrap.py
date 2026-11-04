"""Executable bootstrap contracts, isolated from developer tools and services."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import aqs_bootstrap as bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for template, _, _ in bootstrap.ENV_TEMPLATES:
            path = self.root / template
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('AUTH_JWT_SECRET=change-me-openssl-rand-base64-32\n')

    def test_env_creation_is_repeatable_and_preserves_values(self) -> None:
        self.assertEqual(bootstrap.main(['env', '--root', str(self.root)]), 0)
        local = self.root / 'backend/.env'
        local.write_text('AUTH_JWT_SECRET=private-local-value\n')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bootstrap.main(['env', '--root', str(self.root)]), 0)
        self.assertEqual(local.read_text(), 'AUTH_JWT_SECRET=private-local-value\n')
        self.assertNotIn('private-local-value', output.getvalue())
        self.assertTrue(all(check.state == 'ok' for check in bootstrap.env_checks(self.root)))

    def test_managed_hooks_are_installed_preserving_chain(self) -> None:
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', 'config', 'core.hooksPath', '.managed'], cwd=self.root, check=True)
        directory = self.root / '.managed'
        directory.mkdir()
        original = directory / 'pre-push'
        original.write_text('#!/bin/sh\nexit 0\n')
        original.chmod(0o755)
        self.assertEqual(bootstrap.hook_checks(self.root)[0].state, 'missing')
        with patch.object(bootstrap, '_stream', return_value=0):
            self.assertEqual(bootstrap.install_hooks(self.root), 0)
            self.assertEqual(bootstrap.install_hooks(self.root), 0)
        self.assertEqual((directory / 'pre-push.legacy').read_text(), '#!/bin/sh\nexit 0\n')
        self.assertEqual(bootstrap.hook_checks(self.root)[0].state, 'ok')
        configured = subprocess.check_output(['git', 'config', 'core.hooksPath'], cwd=self.root, text=True)
        self.assertEqual(configured.strip(), '.managed')
        python = self.root / 'backend/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        module = self.root / 'pre_commit.py'
        module.write_text('import sys\nfrom pathlib import Path\nPath("dispatch").write_text(repr(sys.argv[1:]) + "\\n" + sys.stdin.read())\n')
        for name in bootstrap.REQUIRED_HOOKS:
            subprocess.run([str(directory / name), 'argument'], cwd=self.root, input='push-input', text=True, check=True)
            dispatch = (self.root / 'dispatch').read_text()
            self.assertIn(repr(name), dispatch)
            self.assertIn(repr(str(directory)), dispatch)
            self.assertIn(repr('argument'), dispatch)
            self.assertTrue(dispatch.endswith('push-input'))
        (directory / 'commit-msg').chmod(0o644)
        self.assertEqual(bootstrap.hook_checks(self.root)[0].state, 'missing')

    def test_missing_tools_have_named_diagnostics(self) -> None:
        (self.root / 'mise.toml').write_text('[tools]\nnode="24"\npnpm="11"\npython="3.13"\nuv="0.11"\n')
        with patch.object(bootstrap.shutil, 'which', return_value=None):
            checks = bootstrap.toolchain_checks(self.root)
        self.assertEqual({check.label for check in checks}, {'node', 'pnpm', 'python', 'uv'})
        self.assertTrue(all(check.state == 'missing' and check.remedy for check in checks))

    def test_optional_services_and_openbao_override(self) -> None:
        binary = self.root / 'custom-bao'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        with patch.dict(os.environ, {'AQS_OPENBAO_BIN': str(binary)}, clear=True):
            self.assertEqual(bootstrap._openbao_check({}).state, 'info')
        with patch.dict(os.environ, {}, clear=True), patch.object(bootstrap.shutil, 'which', return_value=None), patch.object(bootstrap, '_tcp_reachable', return_value=False):
            checks = bootstrap.capability_checks(self.root)
        self.assertEqual({check.label for check in checks}, {'PostgreSQL', 'S3/MinIO', 'OpenBao', 'Docker'})
        self.assertTrue(all(check.state == 'skip' for check in checks))

    def test_verify_readiness_includes_required_env_and_core_checks(self) -> None:
        bootstrap.copy_env_templates(self.root)
        ok = bootstrap.Check('test', 'ok', 'passed')
        with contextlib.ExitStack() as stack:
            for name in ('toolchain_checks', 'dependency_checks', 'hook_checks', 'git_checks', 'credential_checks', 'capability_checks'):
                stack.enter_context(patch.object(bootstrap, name, return_value=[]))
            smoke = stack.enter_context(patch.object(bootstrap, 'backend_smoke', return_value=ok))
            stack.enter_context(patch.object(bootstrap, 'frontend_check', return_value=ok))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(bootstrap.main(['verify', '--root', str(self.root)]), 0)
            self.assertIn('VERIFY: READY', output.getvalue())
            smoke.return_value = bootstrap.Check('Backend smoke', 'missing', 'failed')
            self.assertEqual(bootstrap.verify(self.root), 1)
            smoke.return_value = ok
            for _, target, _ in bootstrap.ENV_TEMPLATES:
                local = self.root / target
                content = local.read_text()
                local.unlink()
                self.assertEqual(bootstrap.verify(self.root), 1)
                local.write_text(content)
            self.assertIn('VERIFY: NOT READY', output.getvalue())


if __name__ == '__main__':
    unittest.main()
