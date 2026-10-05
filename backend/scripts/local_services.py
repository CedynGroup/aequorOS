"""Worktree-local test infrastructure; never use a Homebrew service cluster.

Run through uv for psycopg/boto3. `run` lends its environment to a command and
stops services it started, including on failure. `up` keeps them for `env`/`down`.
Explicit test/S3 URLs and CI bypass local provisioning in auto mode.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import boto3
import psycopg
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / ".local-services"
PG_MAJOR = "17"  # Same major as backend/docker-compose.yml and risk-service CI.
LOCAL_PASSWORD = "postgres"


def command(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kwargs)


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def postgres_bin() -> Path:
    override = os.environ.get("AQS_POSTGRES_BIN")
    if override:
        path = Path(override).resolve()
    else:
        brew = shutil.which("brew")
        if not brew:
            raise RuntimeError("Install PostgreSQL 17 or set AQS_POSTGRES_BIN to its bin directory")
        prefix = command([brew, "--prefix", "postgresql@17"], capture_output=True, text=True)
        path = Path(prefix.stdout.strip()) / "bin"
    version = command([str(path / "postgres"), "--version"], capture_output=True, text=True)
    if version.stdout.split()[2].split(".")[0] != PG_MAJOR:
        raise RuntimeError(f"PostgreSQL {PG_MAJOR} is required, got {version.stdout.strip()}")
    return path.resolve()


def run_child(args: list[str], env: dict[str, str]) -> int:
    """Forward cancellation to the whole test stack before stopping its services."""
    process = subprocess.Popen(args, env=env, start_new_session=True)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Playwright tears down its detached web servers on SIGINT, not
        # SIGTERM. A pnpm/uv launcher may exit before that teardown finishes.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGINT)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            process.poll()  # Reap the launcher before checking its group.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return 130
            time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return 130


def select_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    docker = shutil.which("docker")
    if docker:
        try:
            result = subprocess.run([docker, "info"], capture_output=True, timeout=5, check=False)
            if result.returncode == 0:
                return "docker"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "native"


class LocalServices:
    def __init__(self, directory: Path = STATE_DIR):
        self.directory = directory
        self.state_file = directory / "state.json"
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.started: list[str] = []

    def save(self) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n")
        temporary.replace(self.state_file)

    def owns_process(self, service: str) -> bool:
        record = self.state[service]
        if "identity" not in record:
            return False
        identity = record["identity"]
        # Pools/backups can copy ignored state into a different worktree. A
        # matching PID is only ours when its data path belongs to this directory.
        expected_data = str(self.directory / service)
        expected_command = "-D" if service == "postgres" else "server"
        if len(identity) != 3 or identity[1:] != [expected_command, expected_data]:
            return False
        pid = record.get("pid")
        if service == "postgres":
            pidfile = self.directory / "postgres" / "postmaster.pid"
            if not pidfile.exists():
                return False
            pid = int(pidfile.read_text().splitlines()[0])
        if not pid:
            return False
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
        )
        # A stale PID must never kill another worktree's service (or any user process).
        expected = " ".join(record["identity"])
        actual = result.stdout.strip()
        return result.returncode == 0 and (actual == expected or actual.startswith(expected + " "))

    @staticmethod
    def project_name() -> str:
        return "aqs-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]

    def docker_args(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project_name(),
            "-f",
            str(ROOT / "backend/docker-compose.yml"),
            "-f",
            str(self.directory / "compose.yml"),
        ]

    def running(self, service: str) -> bool:
        if self.state[service]["mode"] == "native":
            return self.owns_process(service)
        try:
            result = subprocess.run(
                [*self.docker_args(), "ps", "--status", "running", "-q", f"risk-{service}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def prepare(self, service: str, mode: str) -> bool:
        if service in self.state and self.running(service):
            if self.state[service]["mode"] != mode:
                raise RuntimeError(
                    "Local services already use a different mode; run local-services-down"
                )
            return False
        self.state[service] = {"mode": mode, "port": self.new_port()}
        self.started.append(service)
        self.save()
        return True

    def new_port(self) -> int:
        allocated = {
            record[key]
            for name, record in self.state.items()
            if name in {"postgres", "minio"}
            for key in ("port", "console_port")
            if key in record
        }
        while (port := available_port()) in allocated:
            pass
        return port

    def start_postgres(self, mode: str) -> None:
        data = self.directory / "postgres"
        if mode == "native" and data.is_symlink():
            raise RuntimeError(
                "Native Postgres data must be a directory in this worktree, not a symlink"
            )
        if not self.prepare("postgres", mode):
            return
        if mode == "docker":
            self.start_docker("postgres")
            return
        binaries = postgres_bin()
        self.state["postgres"]["bin"] = str(binaries)
        self.state["postgres"]["identity"] = [str(binaries / "postgres"), "-D", str(data)]
        self.save()
        if not (data / "PG_VERSION").exists():
            password = self.directory / "pg-password"
            password.write_text(LOCAL_PASSWORD + "\n")
            password.chmod(0o600)
            try:
                command(
                    [
                        str(binaries / "initdb"),
                        "-D",
                        str(data),
                        "-U",
                        "postgres",
                        "--auth=scram-sha-256",
                        f"--pwfile={password}",
                        "--encoding=UTF8",
                        "--locale=C",
                    ],
                    stdout=sys.stderr,
                )
            finally:
                password.unlink()
        if (data / "PG_VERSION").read_text().strip() != PG_MAJOR:
            raise RuntimeError(
                "Worktree cluster has a different PostgreSQL major; retain it and move it aside"
            )
        port = self.state["postgres"]["port"]
        command(
            [
                str(binaries / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(self.directory / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {port} -k ''",
                "-w",
                "start",
            ],
            stdout=sys.stderr,
        )

    def start_minio(self, mode: str) -> None:
        data = self.directory / "minio"
        if mode == "native" and data.is_symlink():
            raise RuntimeError(
                "Native MinIO data must be a directory in this worktree, not a symlink"
            )
        if not self.prepare("minio", mode):
            return
        self.state["minio"]["console_port"] = self.new_port()
        keyfile = self.directory / "kms-key"
        if not keyfile.exists():
            keyfile.write_text(base64.b64encode(os.urandom(32)).decode())
            keyfile.chmod(0o600)
        if mode == "docker":
            self.start_docker("minio")
            return
        binary = shutil.which("minio")
        if not binary:
            raise RuntimeError(
                "Install native MinIO: brew install homebrew/core/minio homebrew/core/minio-mc"
            )
        data.mkdir(exist_ok=True)
        args = [str(Path(binary).resolve()), "server", str(data)]
        self.state["minio"]["identity"] = args
        port = self.state["minio"]["port"]
        console = self.state["minio"]["console_port"]
        with (self.directory / "minio.log").open("a") as log:
            process = subprocess.Popen(
                [
                    *args,
                    "--address",
                    f"127.0.0.1:{port}",
                    "--console-address",
                    f"127.0.0.1:{console}",
                    "--certs-dir",
                    str(self.directory / "certs"),
                ],
                env={**os.environ, **self.minio_environment()},
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        self.state["minio"]["pid"] = process.pid
        self.save()

    def minio_environment(self) -> dict[str, str]:
        return {
            "MINIO_ROOT_USER": "minioadmin",
            "MINIO_ROOT_PASSWORD": "minioadmin",
            "MINIO_KMS_SECRET_KEY": "aequoros-key:" + (self.directory / "kms-key").read_text(),
        }

    def start_docker(self, service: str) -> None:
        self.state["project"] = self.project_name()
        self.save()
        overrides = {}
        for name in ("postgres", "minio"):
            if name not in self.state:
                continue
            record = self.state[name]
            target = 5432 if name == "postgres" else 9000
            # Compose's !override removes the base file's fixed host ports.
            ports = f'ports: !override ["127.0.0.1:{record["port"]}:{target}"]'
            env = {} if name == "postgres" else self.minio_environment()
            overrides[f"risk-{name}"] = (ports, env)
        text = "services:\n"
        for name, (ports, env) in overrides.items():
            text += f"  {name}:\n    {ports}\n"
            if env:
                text += "    environment:\n" + "".join(
                    f"      {key}: {json.dumps(value)}\n" for key, value in env.items()
                )
        override = self.directory / "compose.yml"
        override.write_text(text)
        override.chmod(0o600)  # Contains the worktree's KMS key.
        command([*self.docker_args(), "up", "-d", f"risk-{service}"], stdout=sys.stderr)

    def provision_postgres(self, role_admin: bool) -> dict[str, str]:
        port = self.state["postgres"]["port"]
        url = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 60
        while True:
            try:
                connection = psycopg.connect(url, autocommit=True, connect_timeout=2)
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        with connection:
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname='risk_service'"
            ).fetchone():
                connection.execute("CREATE DATABASE risk_service")
            if not connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='risk_service_test'"
            ).fetchone():
                connection.execute(
                    "CREATE ROLE risk_service_test LOGIN PASSWORD 'risk_service_test'"
                )
            privilege = "CREATEROLE" if role_admin else "NOCREATEROLE"
            connection.execute(
                f"ALTER ROLE risk_service_test NOSUPERUSER NOCREATEDB {privilege} "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            )
            connection.execute(
                "GRANT CONNECT, CREATE, TEMPORARY ON DATABASE risk_service TO risk_service_test"
            )
        return self.postgres_environment(role_admin)

    def postgres_environment(self, role_admin: bool = False) -> dict[str, str]:
        port = self.state["postgres"]["port"]
        return {
            "TEST_DATABASE_URL": f"postgresql+psycopg://risk_service_test:risk_service_test@127.0.0.1:{port}/risk_service",
            "POSTGRES_ADMIN_URL": f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/risk_service",
            **({"POSTGRES_PRIVILEGE_TESTS_REQUIRED": "1"} if role_admin else {}),
        }

    def provision_minio(self) -> dict[str, str]:
        endpoint = f"http://127.0.0.1:{self.state['minio']['port']}"
        deadline = time.monotonic() + 60
        while True:
            try:
                with urlopen(endpoint + "/minio/health/live", timeout=2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"MinIO did not start; inspect {self.directory / 'minio.log'}"
                    ) from None
                time.sleep(0.2)
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id="minioadmin",
            aws_secret_access_key="minioadmin",
            region_name="us-east-1",
        )
        try:
            client.head_bucket(Bucket="risk-local")
        except ClientError as error:
            if error.response["Error"]["Code"] != "404":
                raise
            client.create_bucket(Bucket="risk-local")
        # Health/bucket checks still pass when the drive is full or KMS is broken.
        # Exercise the encrypted artifact path before starting a long test suite.
        key = f"local-services/readiness/{uuid.uuid4()}"
        try:
            client.put_object(
                Bucket="risk-local",
                Key=key,
                Body=b"ready",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId="aequoros-key",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "XMinioStorageFull":
                raise RuntimeError(
                    "MinIO cannot write: insufficient free disk space. Stop local services "
                    "and reclaim disposable worktree test data or dashboard/.next-e2e."
                ) from error
            raise
        try:
            response = client.get_object(Bucket="risk-local", Key=key)
            with response["Body"] as body:
                if body.read() != b"ready" or response.get("ServerSideEncryption") != "aws:kms":
                    raise RuntimeError("MinIO encrypted artifact readiness check failed")
        finally:
            client.delete_object(Bucket="risk-local", Key=key)
        return self.storage_environment()

    def storage_environment(self) -> dict[str, str]:
        endpoint = f"http://127.0.0.1:{self.state['minio']['port']}"
        return {
            "S3_ENDPOINT": endpoint,
            "S3_ACCESS_KEY": "minioadmin",
            "S3_SECRET_KEY": "minioadmin",
            "S3_BUCKET": "risk-local",
            "S3_REGION": "us-east-1",
            "STORAGE_BACKEND": "minio",
            "STORAGE_ENV": "mvp",
            "STORAGE_RETIRE_AFTER": "2099-01-01",
            "STORAGE_KMS_KEY_ID": "aequoros-key",
        }

    def stop(self, services: list[str]) -> None:
        for service in reversed(services):
            record = self.state.get(service)
            if not record:
                continue
            if record["mode"] == "docker":
                command([*self.docker_args(), "stop", f"risk-{service}"], stdout=sys.stderr)
            elif "identity" in record and self.owns_process(service):
                if service == "postgres":
                    command(
                        [
                            str(Path(record["bin"]) / "pg_ctl"),
                            "-D",
                            str(self.directory / "postgres"),
                            "-m",
                            "fast",
                            "-w",
                            "stop",
                        ],
                        stdout=sys.stderr,
                    )
                else:
                    os.kill(record["pid"], signal.SIGTERM)
                    deadline = time.monotonic() + 20
                    while self.owns_process(service):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("MinIO did not shut down; data retained")
                        time.sleep(0.1)
            del self.state[service]
            self.save()


def main() -> int:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "env", "down", "run"])
    parser.add_argument(
        "--mode",
        choices=["auto", "native", "docker", "external"],
        default=os.environ.get("AQS_LOCAL_SERVICES", "auto"),
    )
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--storage", action="store_true")
    parser.add_argument(
        "--role-admin", action="store_true", help="CI schema suite's CREATEROLE privilege"
    )
    args, child = parser.parse_known_args()
    if child[:1] == ["--"]:
        child = child[1:]
    if (args.action == "run") != bool(child):
        parser.error("run requires a command after --; other actions do not take a command")
    requested = [
        name for name, enabled in [("postgres", args.postgres), ("minio", args.storage)] if enabled
    ]
    if not requested:
        requested = ["postgres", "minio"]
    env = dict(os.environ)
    managed = requested
    if args.mode == "external" or (args.mode == "auto" and env.get("CI")):
        managed = []
    elif args.mode == "auto":
        managed = [
            name
            for name in requested
            if not env.get("TEST_DATABASE_URL" if name == "postgres" else "S3_ENDPOINT")
        ]
    if not managed and args.action == "run":
        return run_child(child, env)
    if STATE_DIR.is_symlink():
        raise RuntimeError(".local-services must be a directory in this worktree, not a symlink")
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    # A borrow must not race its owner's shutdown; serialize managed runs.
    with (STATE_DIR / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another local-services command is active in this worktree"
            ) from None
        services = LocalServices(STATE_DIR)
        if args.action == "down":
            services.stop([name for name in requested if name in services.state])
            return 0
        if args.action == "env":
            managed = [name for name in requested if name in services.state]
            if not managed:
                raise RuntimeError("No local services started; run local-services-up first")
        mode = select_mode(args.mode) if managed and args.action != "env" else args.mode
        try:
            for name in managed:
                if args.action != "env":
                    print(f"Local {name}: {mode} ({STATE_DIR})", file=sys.stderr)
                    if name == "postgres":
                        services.start_postgres(mode)
                    else:
                        services.start_minio(mode)
                elif not services.running(name):
                    raise RuntimeError(f"Local {name} is stopped; run local-services-up")
                if args.action == "env":
                    env.update(
                        services.postgres_environment(args.role_admin)
                        if name == "postgres"
                        else services.storage_environment()
                    )
                else:
                    env.update(
                        services.provision_postgres(args.role_admin)
                        if name == "postgres"
                        else services.provision_minio()
                    )
                env["AQS_LOCAL_SERVICES_MODE"] = services.state[name]["mode"]
            if args.action == "run":
                return run_child(child, env)
            for key, value in env.items():
                if os.environ.get(key) != value:
                    print(f"export {key}={shlex.quote(value)}")
            services.started.clear()  # `up` leaves services for explicit `down`.
            return 0
        finally:
            services.stop(services.started)


if __name__ == "__main__":

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    with contextlib.suppress(KeyboardInterrupt):
        try:
            sys.exit(main())
        except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
            print(f"local-services: {error}", file=sys.stderr)
            sys.exit(1)
    sys.exit(130)
