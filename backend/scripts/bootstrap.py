"""Prepare and verify a fresh AequorOS checkout.

``mise run setup`` and ``mise run verify`` are the two entry points. This module
holds only the logic mise cannot express directly:

* env templates are copied only when the local file is absent, so a second run
  never overwrites local values;
* an existing ``core.hooksPath`` chain is preserved instead of being rewritten;
* optional capabilities (PostgreSQL, S3/MinIO, OpenBao, Docker) are reported
  separately from core readiness, so a checkout without services still verifies;
* git identity, push access and API credentials are named accurately and never
  invented, guessed, or printed.

``verify`` never installs anything: it uses the dependencies ``setup`` left in
place and exits 0 only when the core checks pass. Exit code 1 means a named
requirement is missing or a check failed; 2 is reserved for usage errors.

``mise`` runs this module with whatever ``python3`` is on PATH, which before
``mise install`` can be older than the pinned interpreter. ``main`` therefore
refuses Python below 3.11 with the detected version and the remedy instead of
letting the first ``tomllib`` import fail as a traceback.
"""

from __future__ import annotations

import argparse
import importlib.resources
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

TOOL_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "node": ("node", ("--version",)),
    "pnpm": ("pnpm", ("--version",)),
    "python": ("python3", ("--version",)),
    "uv": ("uv", ("--version",)),
}

REQUIRED_HOOKS = ("pre-commit", "commit-msg", "pre-push")

#: ``(template, local file, purpose)``. The local file is never overwritten.
ENV_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("backend/.env.example", "backend/.env", "API, database and storage settings"),
    (
        "backend/dashboard/.env.example",
        "backend/dashboard/.env.local",
        "bank product UI settings",
    ),
    ("console/.env.example", "console/.env.local", "staff console settings"),
    ("frontend/.env.example", "frontend/.env.local", "marketing site settings"),
)

#: Values committed to the templates. A local file still holding one of these has
#: not been configured, and no check may treat it as ready.
PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "change-me-openssl-rand-base64-32",
        "change-me-openssl-rand-base64-48",
    }
)

State = Literal["ok", "missing", "skip", "info"]


@dataclass(frozen=True)
class Check:
    """One named readiness fact. ``missing`` is the only failing state."""

    label: str
    state: State
    detail: str
    remedy: str | None = None


_STATUS_LABELS: dict[State, str] = {
    "ok": "ok",
    "missing": "MISSING",
    "skip": "skipped",
    "info": "info",
}


def _print_check(check: Check) -> None:
    print(f"  {_STATUS_LABELS[check.state]:<8} {check.label}: {check.detail}")
    if check.remedy:
        for line in check.remedy.splitlines():
            print(f"           {line}")


def _print_section(title: str, checks: Sequence[Check]) -> list[Check]:
    print(f"\n{title}")
    if not checks:
        print("  (nothing to report)")
        return []
    for check in checks:
        _print_check(check)
    return [check for check in checks if check.state == "missing"]


def _capture(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 20,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=None if env is None else dict(env),
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:g}s"
    return completed.returncode, (completed.stdout or completed.stderr).strip()


def _stream(args: Sequence[str], *, cwd: Path, timeout: float) -> int:
    print(f"\n$ {' '.join(args)}")
    try:
        return subprocess.run(list(args), cwd=cwd, timeout=timeout, check=False).returncode
    except FileNotFoundError:
        print(f"FAIL     {args[0]}: command not found")
        return 127
    except subprocess.TimeoutExpired:
        print(f"FAIL     timed out after {timeout:g}s")
        return 124


def _pins(root: Path) -> dict[str, str] | None:
    """The ``[tools]`` table, or None when mise.toml is missing or unreadable."""
    import tomllib  # noqa: PLC0415 - 3.11+; main() refuses older interpreters first.

    path = root / "mise.toml"
    if not path.is_file():
        return None
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    tools = document.get("tools")
    if not isinstance(tools, dict):
        return {}
    typed = cast(dict[str, object], tools)
    return {name: value for name, value in typed.items() if isinstance(value, str)}


def _version_matches(actual: str, pin: str) -> bool:
    expected = pin.split("-", maxsplit=1)[0].split(".")
    if not any(part.isdigit() for part in expected):
        return True  # A non-numeric pin (e.g. an alias) cannot be compared here.
    return actual.split(".")[: len(expected)] == expected


def _first_version(text: str) -> str | None:
    match = re.search(r"\d+(?:\.\d+)*", text)
    return match.group(0) if match else None


def toolchain_checks(root: Path) -> list[Check]:
    pins = _pins(root)
    if pins is None:
        return [
            Check(
                "Toolchain",
                "missing",
                f"{root / 'mise.toml'} is missing or is not valid TOML",
                "restore the repository mise.toml",
            )
        ]
    if not pins:
        return [
            Check(
                "Toolchain",
                "missing",
                f"{root / 'mise.toml'} does not pin any tools",
                "restore the [tools] table in the repository mise.toml",
            )
        ]
    checks: list[Check] = []
    for tool, (command, args) in TOOL_COMMANDS.items():
        pin = pins.get(tool)
        if pin is None:
            checks.append(
                Check(
                    tool,
                    "missing",
                    "not pinned in mise.toml",
                    f"add {tool} to [tools] so the version is repeatable",
                )
            )
            continue
        if shutil.which(command) is None:
            checks.append(
                Check(
                    tool,
                    "missing",
                    f"not installed (pinned {pin})",
                    f"mise install  # installs {tool}@{pin}",
                )
            )
            continue
        code, output = _capture([command, *args], cwd=root)
        version = _first_version(output)
        if code != 0 or version is None:
            checks.append(
                Check(
                    tool,
                    "missing",
                    f"could not report a version (pinned {pin})",
                    f"mise install  # reinstalls {tool}@{pin}",
                )
            )
            continue
        if not _version_matches(version, pin):
            checks.append(
                Check(
                    tool,
                    "missing",
                    f"{version} does not match the pinned {pin}",
                    f"mise install  # installs {tool}@{pin}",
                )
            )
            continue
        checks.append(Check(tool, "ok", f"{version} (pinned {pin})"))
    return checks


def dependency_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    if (root / "node_modules" / ".modules.yaml").is_file():
        checks.append(Check("JavaScript", "ok", "workspace dependencies installed by pnpm"))
    else:
        checks.append(
            Check(
                "JavaScript",
                "missing",
                "workspace dependencies are not installed",
                "mise run setup",
            )
        )
    if (root / "backend" / ".venv" / "pyvenv.cfg").is_file():
        checks.append(Check("Python", "ok", "backend virtual environment installed by uv"))
    else:
        checks.append(
            Check(
                "Python",
                "missing",
                "backend virtual environment is not installed",
                "mise run setup",
            )
        )
    return checks


def _git_config(root: Path, key: str) -> str | None:
    code, output = _capture(["git", "config", "--get", key], cwd=root)
    return output if code == 0 and output else None


def _git_path(root: Path, name: str) -> Path | None:
    code, output = _capture(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", name], cwd=root
    )
    return Path(output) if code == 0 and output else None


def _installed_python(root: Path) -> Path:
    return root / "backend" / ".venv" / "bin" / "python"


def check_hook_artifacts(directory: Path) -> int:
    template = (importlib.resources.files("pre_commit") / "resources" / "hook-tmpl").read_text(
        encoding="UTF-8"
    )
    before, rest = template.split("# start templated\n")
    _, after = rest.split("# end templated\n")
    missing: list[str] = []
    for name in REQUIRED_HOOKS:
        args = ["hook-impl", "--config=.pre-commit-config.yaml", f"--hook-type={name}"]
        expected = (
            before
            + "# start templated\n"
            + f"INSTALL_PYTHON={shlex.quote(sys.executable)}\n"
            + f"ARGS=({shlex.join(args)})\n"
            + "# end templated\n"
            + after
        )
        hook = directory / name
        if not hook.is_file() or not os.access(hook, os.X_OK) or hook.read_text() != expected:
            missing.append(name)
    if missing:
        print("missing or incorrect pre-commit dispatchers: " + ", ".join(missing))
        return 1
    return 0


def _external_hook_check(root: Path) -> Check | None:
    code, managed = _capture(["git", "config", "--get", "core.hooksPath"], cwd=root)
    if code != 0:
        return None
    commands: list[str] = []
    for name in REQUIRED_HOOKS:
        command = shlex.join(
            [
                str(_installed_python(root)),
                "-m",
                "pre_commit",
                "hook-impl",
                "--config",
                str(root / ".pre-commit-config.yaml"),
                "--hook-type",
                name,
                "--",
            ]
        )
        commands.append(f'{name}: {command} "$@"')
    return Check(
        "Hooks",
        "missing",
        f"unverified: externally configured core.hooksPath ({managed!r}) left untouched",
        "Configure your hook manager to run each command below from "
        + str(root)
        + ", forwarding hook arguments and pre-push stdin; "
        "then verify enforcement under your manager.\n" + "\n".join(commands),
    )


def hook_checks(root: Path) -> list[Check]:
    external = _external_hook_check(root)
    if external is not None:
        return [external]
    directory = _git_path(root, "hooks")
    python = _installed_python(root)
    if directory is None or not python.is_file() or not os.access(python, os.X_OK):
        return [
            Check(
                "Hooks",
                "missing",
                "hooks directory or installed Python unavailable",
                "mise run setup",
            )
        ]
    code, output = _capture(
        [
            str(python),
            "-c",
            "import sys; from pathlib import Path; "
            "from scripts.bootstrap import check_hook_artifacts; "
            "sys.exit(check_hook_artifacts(Path(sys.argv[1])))",
            str(directory),
        ],
        cwd=root / "backend",
    )
    if code != 0:
        return [
            Check(
                "Hooks", "missing", output or "cannot validate pre-commit hooks", "mise run setup"
            )
        ]
    return [Check("Hooks", "ok", f"all required pre-commit dispatchers installed in {directory}")]


def install_hooks(root: Path) -> int:
    external = _external_hook_check(root)
    if external is not None:
        _print_check(external)
        return 0

    args = [
        str(_installed_python(root)),
        "-m",
        "pre_commit",
        "install",
        "--config",
        str(root / ".pre-commit-config.yaml"),
        "--install-hooks",
    ]
    for name in REQUIRED_HOOKS:
        args.extend(["--hook-type", name])
    code = _stream(args, cwd=root, timeout=300)
    if code != 0:
        return code
    checks = hook_checks(root)
    for check in checks:
        _print_check(check)
    return int(any(check.state == "missing" for check in checks))


def _create_absent(source: Path, destination: Path) -> bool:
    """Copy ``source`` to ``destination`` only when absent. Returns True if created."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            _ = handle.write(source.read_text(encoding="utf-8"))
    except FileExistsError:
        return False
    return True


def copy_env_templates(root: Path) -> int:
    print("Environment files")
    failures: list[str] = []
    for template, target, purpose in ENV_TEMPLATES:
        source = root / template
        destination = root / target
        if not source.is_file():
            print(f"  MISSING  {target}: template {template} is missing")
            failures.append(template)
            continue
        if destination.exists():
            print(f"  kept     {target}: exists, left unchanged ({purpose})")
        elif _create_absent(source, destination):
            print(f"  created  {target}: from {template} ({purpose})")
        else:
            print(f"  kept     {target}: created by another run, left unchanged ({purpose})")
    if failures:
        print("  FAIL     Environment files: a committed template is missing")
        return 1
    print(
        "  info     Copied files hold template placeholders only; they prove no API "
        "or authentication readiness."
    )
    return 0


def env_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    for template, target, _purpose in ENV_TEMPLATES:
        template_path = root / template
        if not template_path.is_file():
            checks.append(
                Check(
                    target,
                    "missing",
                    f"committed template {template} is missing",
                    "restore the template from version control",
                )
            )
        elif (root / target).is_file():
            checks.append(Check(target, "ok", f"present (from {template})"))
        else:
            checks.append(
                Check(
                    target,
                    "missing",
                    f"not created yet (template {template} exists)",
                    "mise run setup",
                )
            )
    return checks


def git_checks(root: Path, *, probe_remote: bool) -> list[Check]:
    checks: list[Check] = []
    name = _git_config(root, "user.name")
    email = _git_config(root, "user.email")
    if name and email:
        checks.append(Check("Commit identity", "ok", f"{name} <{email}>"))
    else:
        unset = [key for key, value in (("user.name", name), ("user.email", email)) if not value]
        checks.append(
            Check(
                "Commit identity",
                "missing",
                f"{' and '.join(unset)} unset; set it yourself (never guessed):",
                'git config --local user.name "<your name>"\n'
                'git config --local user.email "<your email>"',
            )
        )
    if not probe_remote:
        return checks
    remote = _git_config(root, "remote.origin.url")
    if not remote:
        checks.append(
            Check(
                "Push access",
                "missing",
                "no origin remote; push access is unavailable",
                "git remote add origin <url>",
            )
        )
        return checks
    code, output = _capture(
        ["git", "ls-remote", "--exit-code", "origin", "HEAD"],
        cwd=root,
        timeout=10,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if code == 0:
        checks.append(
            Check(
                "Push access",
                "info",
                "origin is read-reachable; push permission was NOT verified "
                "(no test push is performed)",
            )
        )
    else:
        detail = output.splitlines()[0] if output else f"git ls-remote exited {code}"
        checks.append(
            Check(
                "Push access",
                "info",
                f"origin read check failed ({detail}); push access unverified",
                "check network and git credentials, then: git ls-remote origin HEAD",
            )
        )
    return checks


def _env_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _configured(value: str | None) -> bool:
    return value is not None and value.strip() not in PLACEHOLDER_VALUES


def credential_checks(root: Path) -> list[Check]:
    path = root / "backend" / ".env"
    if not path.is_file():
        return [
            Check(
                "API credentials",
                "skip",
                "backend/.env is absent; nothing was probed",
                "mise run setup",
            )
        ]
    values = _env_values(path)
    database_url = values.get("DATABASE_URL")
    jwt_secret = values.get("AUTH_JWT_SECRET")
    return [
        Check(
            "DATABASE_URL",
            "info",
            "set (presence only; the connection was not tested)"
            if _configured(database_url)
            else "unset or still a template placeholder",
        ),
        Check(
            "AUTH_JWT_SECRET",
            "info",
            "set (presence only; no authentication was attempted)"
            if _configured(jwt_secret)
            else "still a template placeholder; API auth is not ready",
        ),
        Check(
            "Note",
            "info",
            "values are never printed; presence does not prove credentials work",
        ),
    ]


def _tcp_reachable(host: str, port: int, *, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _endpoint(endpoint: str) -> tuple[str, int] | None:
    candidate = endpoint if "//" in endpoint else f"//{endpoint}"
    parts = urlsplit(candidate)
    if not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return parts.hostname, port


def _postgres_check(values: Mapping[str, str]) -> Check:
    test_database_url = os.environ.get("TEST_DATABASE_URL") or values.get("TEST_DATABASE_URL")
    if _configured(test_database_url):
        return Check(
            "PostgreSQL",
            "info",
            "TEST_DATABASE_URL is set (presence only; not probed)",
        )
    listening = next(
        (port for port in (5432, 15432) if _tcp_reachable("127.0.0.1", port)),
        None,
    )
    if listening is not None:
        return Check(
            "PostgreSQL",
            "ok",
            f"TCP reachable on 127.0.0.1:{listening} (not authenticated)",
        )
    return Check(
        "PostgreSQL",
        "skip",
        "unavailable: skipped risk-service:test-postgres*, "
        "risk-service:test-integration, risk-service:test-storage and dashboard e2e",
        "start a service with backend/scripts/local_services.py or Docker",
    )


def _storage_check(values: Mapping[str, str]) -> Check:
    s3_endpoint = os.environ.get("S3_ENDPOINT") or values.get("S3_ENDPOINT")
    target = _endpoint(s3_endpoint or "")
    if target is not None and _tcp_reachable(*target):
        return Check(
            "S3/MinIO",
            "ok",
            f"TCP reachable at {target[0]}:{target[1]} (not authenticated)",
        )
    return Check(
        "S3/MinIO",
        "skip",
        "unavailable: skipped risk-service:test-storage and dashboard e2e/e2e:contracts",
        "start MinIO with backend/scripts/local_services.py or Docker",
    )


def _openbao_check(values: Mapping[str, str]) -> Check:
    openbao = os.environ.get("OPENBAO_TEST_ADDR") or values.get("OPENBAO_TEST_ADDR")
    if _configured(openbao):
        return Check("OpenBao", "info", "OPENBAO_TEST_ADDR is set (presence only; not probed)")
    if shutil.which(os.environ.get("AQS_OPENBAO_BIN") or "bao") is not None:
        return Check(
            "OpenBao",
            "info",
            "bao binary present; disposable OpenBao integration flows are available",
        )
    return Check(
        "OpenBao",
        "skip",
        "unavailable: skipped bank-key/OpenBao integration flows",
        "install OpenBao or set AQS_OPENBAO_BIN",
    )


def _docker_check(root: Path) -> Check:
    if shutil.which("docker") is None:
        return Check(
            "Docker",
            "skip",
            "not installed; native services via backend/scripts/local_services.py remain available",
        )
    code, output = _capture(["docker", "info"], cwd=root, timeout=10)
    if code == 0:
        return Check("Docker", "ok", "daemon reachable")
    detail = output.splitlines()[0] if output else f"docker info exited {code}"
    return Check(
        "Docker",
        "skip",
        f"installed but the daemon is unavailable ({detail}); native services remain available",
    )


def capability_checks(root: Path) -> list[Check]:
    values = _env_values(root / "backend" / ".env")
    return [
        _postgres_check(values),
        _storage_check(values),
        _openbao_check(values),
        _docker_check(root),
    ]


def backend_smoke(root: Path) -> Check:
    test_path = root / "backend/tests/architecture/test_ci_task_wiring.py"
    if not test_path.is_file():
        return Check(
            "Backend smoke",
            "missing",
            "backend/tests/architecture/test_ci_task_wiring.py is missing",
            "restore backend/tests/architecture/test_ci_task_wiring.py",
        )
    backend = root / "backend"
    python = _installed_python(root)
    if not python.is_file() or not os.access(python, os.X_OK):
        return Check(
            "Backend smoke", "missing", "installed backend Python unavailable", "mise run setup"
        )
    code = _stream(
        [
            str(python),
            "-m",
            "pytest",
            "-q",
            test_path.relative_to(backend).as_posix(),
        ],
        cwd=backend,
        timeout=300,
    )
    if code != 0:
        return Check(
            "Backend smoke",
            "missing",
            f"{test_path.relative_to(root)} failed (exit {code})",
            "read the pytest output above; fix the wiring or reinstall dependencies",
        )
    return Check("Backend smoke", "ok", f"{test_path.relative_to(root)} passed")


def frontend_check(root: Path) -> Check:
    code = _stream(
        ["pnpm", "--filter", "@aequoros/dashboard", "typecheck"],
        cwd=root,
        timeout=900,
    )
    if code != 0:
        return Check(
            "Frontend check",
            "missing",
            f"dashboard typecheck failed (exit {code})",
            "read the TypeScript output above; run mise run setup if dependencies are stale",
        )
    return Check("Frontend check", "ok", "dashboard typecheck passed")


def verify(root: Path) -> int:
    print("AequorOS verify")
    blocking: list[Check] = []
    blocking += _print_section("Toolchain", toolchain_checks(root))
    blocking += _print_section("Dependencies", dependency_checks(root))
    blocking += _print_section("Hooks", hook_checks(root))
    blocking += _print_section("Setup artifacts", env_checks(root))

    print("\nCore checks")
    smoke = backend_smoke(root)
    _print_check(smoke)
    frontend = frontend_check(root)
    _print_check(frontend)
    blocking += [smoke, frontend]

    _print_section("Git", git_checks(root, probe_remote=True))
    _print_section("API credentials", credential_checks(root))
    _print_section("Optional capabilities", capability_checks(root))

    failed = [check for check in blocking if check.state == "missing"]
    if failed:
        print("\nVERIFY: NOT READY")
        for check in failed:
            print(f"  - {check.label}: {check.detail}")
        return 1
    print("\nVERIFY: READY")
    print("Core checks passed; optional capabilities above are reported separately.")
    return 0


def setup_report(root: Path) -> int:
    print("AequorOS setup report")
    blocking: list[Check] = []
    blocking += _print_section("Toolchain", toolchain_checks(root))
    blocking += _print_section("Dependencies", dependency_checks(root))
    blocking += _print_section("Hooks", hook_checks(root))
    blocking += _print_section("Environment files", env_checks(root))

    requirements = _print_section("Git", git_checks(root, probe_remote=True))
    _print_section("API credentials", credential_checks(root))
    _print_section("Optional capabilities", capability_checks(root))

    if blocking:
        print("\nSETUP INCOMPLETE")
        for check in blocking:
            print(f"  - {check.label}: {check.detail}")
        return 1
    if requirements:
        print("\nSETUP COMPLETE with named requirements still unmet:")
        for check in requirements:
            print(f"  - {check.label}: {check.detail}")
        return 0
    print("\nSETUP COMPLETE: core checkout ready.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # mise runs this with whatever ``python3`` is on PATH, which before
    # ``mise install`` can be older than the interpreter pinned in mise.toml.
    if sys.version_info < (3, 11):  # noqa: UP036 - system interpreter guard
        detected = f"{sys.version_info[0]}.{sys.version_info[1]}"
        print(
            f"FAIL     python3 is {detected}; this script needs Python 3.11 or newer.\n"
            "         Run: mise install"
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "action",
        choices=("env", "hooks", "report", "verify"),
        help="env copies absent templates, hooks installs them, report summarizes setup, "
        "verify proves core readiness",
    )
    _ = parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="checkout root (defaults to this repository)",
    )
    arguments = parser.parse_args(argv)
    action = cast(str, arguments.action)
    root = cast(Path, arguments.root).resolve()
    if action == "env":
        return copy_env_templates(root)
    if action == "hooks":
        return install_hooks(root)
    if action == "report":
        return setup_report(root)
    return verify(root)


if __name__ == "__main__":
    sys.exit(main())
