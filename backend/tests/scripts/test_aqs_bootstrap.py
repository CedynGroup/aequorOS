"""Executable bootstrap contracts, isolated from developer tools and services."""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import shlex
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

    def prepare_hook_checkout(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if not (root / '.git').exists():
            subprocess.run(['git', 'init', '--template=', '-q', str(root)], check=True)
        scripts = root / 'backend/scripts'
        scripts.mkdir(parents=True)
        shutil.copyfile(Path(bootstrap.__file__), scripts / 'aqs_bootstrap.py')
        python = root / 'backend/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        (root / 'record.py').write_text(
            'from pathlib import Path\nimport sys\n'
            'with Path("hook-log").open("a") as log: log.write(sys.argv[1] + "\\n")\n'
        )
        hooks = '\n'.join(
            f'    - id: {name}\n      name: {name}\n'
            f'      entry: {shlex.quote(sys.executable)} record.py {name}\n'
            f'      language: system\n      stages: [{name}]\n'
            '      always_run: true\n      pass_filenames: false'
            for name in bootstrap.REQUIRED_HOOKS
        )
        (root / '.pre-commit-config.yaml').write_text('repos:\n  - repo: local\n    hooks:\n' + hooks + '\n')
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '--allow-empty', '--no-verify', '-qm', 'fixture'], cwd=root, check=True)

    def run_required_hooks(self, root: Path) -> None:
        (root / 'message').write_text('test: fixture\n')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        arguments = {'pre-commit': [], 'commit-msg': ['message'], 'pre-push': ['origin', 'unused']}
        for name, args in arguments.items():
            hook = subprocess.check_output(['git', 'rev-parse', '--path-format=absolute', '--git-path', 'hooks/' + name], cwd=root, text=True).strip()
            subprocess.run([hook, *args], cwd=root,
                           input=f'refs/heads/main {revision} refs/heads/main {"0" * 40}\n',
                           text=True, check=True)

    def test_external_managers_remain_untouched_and_unverified(self) -> None:
        shared = self.root / 'shared'
        shared.mkdir()
        helper = shared / 'helper'
        helper.write_text('#!/bin/sh\necho manager >> hook-log\n')
        helper.chmod(0o755)
        for name in (*bootstrap.REQUIRED_HOOKS, 'post-checkout'):
            hook = shared / name
            hook.write_text('#!/bin/sh\n"$(dirname "$0")/helper"\n')
            hook.chmod(0o755)
        before = {path.name: (path.read_bytes(), path.stat().st_mode) for path in shared.iterdir()}
        global_config = self.root / 'gitconfig'
        global_config.write_text(f'[core]\n hooksPath = {shared}\n')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config),
                                    'GIT_CONFIG_NOSYSTEM': '1',
                                    'PRE_COMMIT_HOME': str(self.root / 'cache')}):
            for name in ('first', 'second'):
                root = self.root / name
                self.prepare_hook_checkout(root)
                config = root / '.git/config'
                initial_config = config.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(bootstrap.install_hooks(root), 0)
                    self.assertEqual(bootstrap.install_hooks(root), 0)
                check = bootstrap.hook_checks(root)[0]
                self.assertEqual(check.state, 'missing')
                self.assertIn('unverified', check.detail)
                self.assertIn('forwarding hook arguments and pre-push stdin', output.getvalue())
                self.assertEqual(config.read_bytes(), initial_config)
                self.assertFalse((root / 'backend/.venv/hooks').exists())
                self.run_required_hooks(root)
                subprocess.run([str(shared / 'post-checkout')], cwd=root, check=True)
                self.assertEqual((root / 'hook-log').read_text().splitlines(), ['manager'] * 4)
                with contextlib.ExitStack() as stack:
                    for function in ('toolchain_checks', 'dependency_checks', 'env_checks', 'git_checks', 'credential_checks', 'capability_checks'):
                        stack.enter_context(patch.object(bootstrap, function, return_value=[]))
                    for function in ('backend_smoke', 'frontend_check'):
                        stack.enter_context(patch.object(bootstrap, function, return_value=bootstrap.Check('core', 'ok', 'passed')))
                    result = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(bootstrap.verify(root), 1)
                    self.assertEqual(bootstrap.setup_report(root), 1)
                    self.assertIn('VERIFY: NOT READY', result.getvalue())
                    self.assertIn('unverified', result.getvalue())
        self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mode) for path in shared.iterdir()})
        self.assertEqual(global_config.read_text(), f'[core]\n hooksPath = {shared}\n')

    def test_unmanaged_noop_hooks_fail_verification_and_install_postcheck(self) -> None:
        global_config = self.root / 'gitconfig'
        global_config.write_text('')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config),
                                    'GIT_CONFIG_NOSYSTEM': '1',
                                    'PRE_COMMIT_HOME': str(self.root / 'cache')}):
            root = self.root / 'checkout'
            self.prepare_hook_checkout(root)
            directory = root / '.git/hooks'
            directory.mkdir()
            for name in bootstrap.REQUIRED_HOOKS:
                hook = directory / name
                hook.write_text('#!/bin/sh\nexit 0\n')
                hook.chmod(0o755)
            self.assertEqual(bootstrap.hook_checks(root)[0].state, 'missing')
            config_before = (root / '.git/config').read_bytes()
            self.assertEqual(bootstrap.install_hooks(root), 0)
            self.assertEqual(bootstrap.install_hooks(root), 0)
            self.assertEqual((root / '.git/config').read_bytes(), config_before)
            self.assertFalse((root / 'backend/.venv/hooks').exists())
            self.run_required_hooks(root)
            self.assertEqual(set((root / 'hook-log').read_text().splitlines()), set(bootstrap.REQUIRED_HOOKS))
            with patch.object(bootstrap, '_stream', return_value=0):
                (directory / 'pre-push').write_text('#!/bin/sh\nexit 0\n')
                self.assertEqual(bootstrap.install_hooks(root), 1)

    def test_backend_smoke_never_bootstraps_an_environment(self) -> None:
        test = self.root / 'backend/tests/architecture/test_ci_task_wiring.py'
        test.parent.mkdir(parents=True)
        test.write_text('')
        with patch.object(bootstrap, '_stream') as stream:
            self.assertEqual(bootstrap.backend_smoke(self.root).state, 'missing')
            stream.assert_not_called()
        self.assertFalse((self.root / 'backend/.venv').exists())
        python = self.root / 'backend/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > smoke-args\n')
        python.chmod(0o755)
        self.assertEqual(bootstrap.backend_smoke(self.root).state, 'ok')
        self.assertEqual((self.root / 'backend/smoke-args').read_text().splitlines(),
                         ['-m', 'pytest', '-q', 'tests/architecture/test_ci_task_wiring.py'])

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
