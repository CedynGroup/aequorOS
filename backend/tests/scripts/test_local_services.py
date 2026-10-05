from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from botocore.exceptions import ClientError

from scripts import local_services


@pytest.mark.parametrize("full", [False, True])
def test_storage_readiness_checks_encrypted_io_before_lending_environment(
    monkeypatch, tmp_path, full
):
    calls = []

    class Client:
        def head_bucket(self, **kwargs):
            pass

        def put_object(self, **kwargs):
            assert kwargs["ServerSideEncryption"] == "aws:kms"
            assert kwargs["SSEKMSKeyId"] == "aequoros-key"
            calls.append(("put", kwargs["Key"]))
            if full:
                raise ClientError({"Error": {"Code": "XMinioStorageFull"}}, "PutObject")

        def get_object(self, **kwargs):
            calls.append(("get", kwargs["Key"]))
            return {"Body": io.BytesIO(b"ready"), "ServerSideEncryption": "aws:kms"}

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs["Key"]))

    monkeypatch.setattr(local_services, "urlopen", lambda *a, **kw: contextlib.nullcontext())
    monkeypatch.setattr(local_services.boto3, "client", lambda *a, **kw: Client())
    services = local_services.LocalServices(tmp_path)
    services.state["minio"] = {"mode": "docker", "port": 29000}
    if full:
        with pytest.raises(RuntimeError, match="insufficient free disk space"):
            services.provision_minio()
        assert [call[0] for call in calls] == ["put"]
    else:
        assert services.provision_minio()["S3_ENDPOINT"] == "http://127.0.0.1:29000"
        assert [call[0] for call in calls] == ["put", "get", "delete"]
        assert len({call[1] for call in calls}) == 1


@pytest.mark.parametrize("mode", ["native", "docker", "external"])
def test_explicit_mode_never_probes_docker(monkeypatch, mode):
    def unexpected(*args, **kwargs):
        pytest.fail("Explicit mode must not probe Docker")

    monkeypatch.setattr(local_services.shutil, "which", unexpected)
    assert local_services.select_mode(mode) == mode


@pytest.mark.parametrize(
    "failure", [None, OSError("unavailable"), subprocess.TimeoutExpired("docker", 5)]
)
def test_unavailable_docker_falls_back_to_native(monkeypatch, failure):
    monkeypatch.setattr(local_services.shutil, "which", lambda _: "/test/docker")

    def probe(*args, **kwargs):
        if failure:
            raise failure
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(local_services.subprocess, "run", probe)
    assert local_services.select_mode("auto") == "native"


def test_stopped_docker_state_does_not_prevent_native_fallback(monkeypatch, tmp_path):
    services = local_services.LocalServices(tmp_path)
    services.state = {"project": "aqs-test", "postgres": {"mode": "docker", "port": 25432}}
    monkeypatch.setattr(
        local_services.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1)
    )
    services.prepare("postgres", "native")
    assert services.state["postgres"]["mode"] == "native"
    assert services.started == ["postgres"]


@pytest.mark.parametrize("configuration", ["ci", "external", "configured"])
def test_external_environment_reaches_child_without_provisioning(
    monkeypatch, tmp_path, configuration
):
    state = tmp_path / "services"
    monkeypatch.setattr(local_services, "STATE_DIR", state)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AQS_LOCAL_SERVICES", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://test.example/test")
    monkeypatch.setenv("S3_ENDPOINT", "http://storage.example")
    mode = "external" if configuration == "external" else "auto"
    if configuration == "ci":
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("TEST_DATABASE_URL")
        monkeypatch.delenv("S3_ENDPOINT")
    result = tmp_path / "child.json"
    program = (
        "import json,os; "
        "values=[os.getenv('TEST_DATABASE_URL'),os.getenv('S3_ENDPOINT')]; "
        f"json.dump(values,open({str(result)!r},'w')); "
        "raise SystemExit(7)"
    )
    monkeypatch.setattr(
        sys, "argv", ["local-services", "run", "--mode", mode, "--", sys.executable, "-c", program]
    )
    assert local_services.main() == 7
    assert not state.exists()
    assert json.loads(result.read_text()) == (
        [None, None]
        if configuration == "ci"
        else ["postgresql://test.example/test", "http://storage.example"]
    )


@pytest.mark.parametrize("action", ["up", "env", "down"])
def test_persistent_service_actions_are_not_exposed(monkeypatch, tmp_path, action):
    state = tmp_path / "services"
    monkeypatch.setattr(local_services, "STATE_DIR", state)
    monkeypatch.setattr(sys, "argv", ["local-services", action])
    with pytest.raises(SystemExit) as error:
        local_services.main()
    assert error.value.code == 2
    assert not state.exists()


def test_stale_pid_cannot_stop_unrelated_process(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        services = local_services.LocalServices(tmp_path)
        services.state["minio"] = {
            "mode": "native",
            "pid": process.pid,
            "identity": [str(tmp_path / "minio-binary"), "server", str(tmp_path / "minio")],
        }
        services.save()
        services.stop(["minio"])
        assert process.poll() is None
        assert json.loads(services.state_file.read_text()) == {}
    finally:
        process.terminate()
        process.wait()


def test_copied_state_cannot_claim_another_worktrees_native_process(monkeypatch, tmp_path):
    services = local_services.LocalServices(tmp_path)
    identity = ["/bin/minio", "server", "/original/worktree/.local-services/minio"]
    services.state["minio"] = {"mode": "native", "pid": os.getpid(), "identity": identity}
    monkeypatch.setattr(
        local_services.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=" ".join(identity)),
    )
    assert not services.owns_process("minio")


def test_copied_state_cannot_target_another_worktrees_docker_project(tmp_path):
    services = local_services.LocalServices(tmp_path)
    services.state["project"] = "aqs-another-worktree"
    args = services.docker_args()
    assert args[args.index("-p") + 1] == services.project_name()
    assert args[args.index("-p") + 1] != "aqs-another-worktree"


@pytest.mark.parametrize("service", ["postgres", "minio"])
def test_native_data_symlinks_never_touch_an_existing_cluster(tmp_path, service):
    outside = tmp_path / "user-cluster"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("untouched")
    local = tmp_path / "local"
    local.mkdir()
    (local / service).symlink_to(outside, target_is_directory=True)
    services = local_services.LocalServices(local)
    with pytest.raises(RuntimeError, match="not a symlink"):
        (services.start_postgres if service == "postgres" else services.start_minio)("native")
    assert marker.read_text() == "untouched"
    assert list(outside.iterdir()) == [marker]
    assert not services.state_file.exists()


def test_existing_service_is_restarted_and_owned_by_run(monkeypatch, tmp_path):
    services = local_services.LocalServices(tmp_path)
    services.state["postgres"] = {"mode": "native", "port": 12345}
    monkeypatch.setattr(services, "running", lambda _: True)
    monkeypatch.setattr(local_services, "available_port", lambda: 23456)
    services.prepare("postgres", "native")
    assert services.started == ["postgres"]
    assert services.state["postgres"]["port"] == 23456


def test_failed_start_rolls_back_services_started_by_run(monkeypatch, tmp_path):
    monkeypatch.setattr(local_services, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["services", "run", "--mode", "native", "--", "unused"])
    stopped = []

    def start(self, mode):
        self.started.append("postgres")
        raise RuntimeError("init failed")

    monkeypatch.setattr(local_services.LocalServices, "start_postgres", start)
    monkeypatch.setattr(
        local_services.LocalServices, "stop", lambda self, names: stopped.extend(names)
    )
    with pytest.raises(RuntimeError, match="init failed"):
        local_services.main()
    assert stopped == ["postgres"]


def test_sigterm_stops_child_before_returning(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = Path(local_services.__file__)
    env = {**os.environ, "AQS_LOCAL_SERVICES": "external"}
    child = f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(30)"
    wrapper = subprocess.Popen(
        [sys.executable, str(script), "run", "--", sys.executable, "-c", child], env=env
    )
    try:
        deadline = time.monotonic() + 10
        while not pidfile.exists():
            assert wrapper.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        pid = int(pidfile.read_text())
        wrapper.send_signal(signal.SIGTERM)
        assert wrapper.wait(timeout=10) == 130
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            wrapper.wait(timeout=10)


@pytest.mark.skipif(
    not shutil.which("node")
    or not (local_services.ROOT / "backend/dashboard/node_modules/@playwright/test").exists(),
    reason="Dashboard Playwright dependencies not installed",
)
def test_cancellation_allows_playwright_to_stop_detached_web_server(tmp_path):
    dashboard = local_services.ROOT / "backend/dashboard"
    package = json.dumps(str(dashboard / "node_modules/@playwright/test"))
    port = local_services.available_port()
    server_pid = tmp_path / "server.pid"
    ready = tmp_path / "test-ready"
    (tmp_path / "server.cjs").write_text(
        "const fs=require('node:fs'); const http=require('node:http'); "
        f"http.createServer((req,res)=>res.end('ready')).listen({port},'127.0.0.1',"
        f"()=>fs.writeFileSync({json.dumps(str(server_pid))},String(process.pid)));"
    )
    config = tmp_path / "playwright.config.cjs"
    config.write_text(
        "module.exports={testDir:'.',testMatch:'cancel.spec.cjs',workers:1,"
        f"outputDir:{json.dumps(str(tmp_path / 'results'))},"
        "webServer:{command:'node server.cjs',"
        f"cwd:{json.dumps(str(tmp_path))},url:'http://127.0.0.1:{port}',timeout:10000}}}};"
    )
    (tmp_path / "cancel.spec.cjs").write_text(
        f"const {{test}}=require({package}); const fs=require('node:fs'); "
        "test('cancellation',async()=>{"
        f"fs.writeFileSync({json.dumps(str(ready))},'ready'); "
        "await new Promise(resolve=>setTimeout(resolve,60000));});"
    )
    with (tmp_path / "output.log").open("w") as output:
        wrapper = subprocess.Popen(
            [
                sys.executable,
                local_services.__file__,
                "run",
                "--mode",
                "external",
                "--",
                "node",
                str(dashboard / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                str(config),
                "--reporter=line",
            ],
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert wrapper.poll() is None, (tmp_path / "output.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "output.log").read_text()
                time.sleep(0.1)
            pid = int(server_pid.read_text())
            wrapper.send_signal(signal.SIGTERM)
            assert wrapper.wait(timeout=30) == 130
            # The real Playwright runner owns a detached server group; the
            # wrapper must leave it enough time to tear that group down.
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if wrapper.poll() is None:
                wrapper.terminate()
                wrapper.wait(timeout=30)
            if server_pid.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(server_pid.read_text()), signal.SIGTERM)


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose client not installed")
def test_docker_override_uses_isolated_ports_and_enables_kms(monkeypatch, tmp_path):
    services = local_services.LocalServices(tmp_path)
    services.state = {
        "postgres": {"mode": "docker", "port": 25432},
        "minio": {"mode": "docker", "port": 29000, "console_port": 29001},
    }
    (tmp_path / "kms-key").write_text("test-key")
    configurations = []

    def validate(args, **kwargs):
        # Consume the actual merged Compose model; never start Docker's daemon.
        result = subprocess.run(
            [*args[: args.index("up")], "config", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
        configurations.append(json.loads(result.stdout))
        return result

    monkeypatch.setattr(local_services, "command", validate)
    services.start_docker("postgres")
    assert (tmp_path / "compose.yml").stat().st_mode & 0o777 == 0o600
    configuration = configurations[0]
    postgres = configuration["services"]["risk-postgres"]
    minio = configuration["services"]["risk-minio"]
    assert [(p["host_ip"], p["published"], p["target"]) for p in postgres["ports"]] == [
        ("127.0.0.1", "25432", 5432)
    ]
    assert [(p["host_ip"], p["published"], p["target"]) for p in minio["ports"]] == [
        ("127.0.0.1", "29000", 9000)
    ]
    assert minio["environment"]["MINIO_KMS_SECRET_KEY"] == "aequoros-key:test-key"
    assert configuration["volumes"]["risk-postgres-data"]["name"].startswith(
        str(services.state["project"]) + "_"
    )


@pytest.mark.skipif(not shutil.which("minio"), reason="Native MinIO binary not installed")
@pytest.mark.parametrize("wait_for_exit", [False, True])
def test_native_minio_bind_collision_never_provisions_the_existing_server(
    monkeypatch, tmp_path, wait_for_exit
):
    requests = []

    class ExistingServer(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def do_HEAD(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExistingServer)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    services = local_services.LocalServices(tmp_path)
    ports = iter([server.server_port, local_services.available_port()])
    monkeypatch.setattr(services, "new_port", lambda: next(ports))
    try:
        services.start_minio("native")
        deadline = time.monotonic() + 10
        while wait_for_exit and services.owns_process("minio"):
            assert time.monotonic() < deadline, (tmp_path / "minio.log").read_text()
            time.sleep(0.05)
        with pytest.raises(RuntimeError, match="MinIO.*(exited|owned)"):
            services.provision_minio()
        assert requests == []
        assert thread.is_alive()
    finally:
        services.stop(services.started)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert services.state == {}


@pytest.mark.parametrize("service", ["postgres", "minio"])
def test_native_provisioning_rejects_an_unowned_process_before_any_connection(
    monkeypatch, tmp_path, service
):
    services = local_services.LocalServices(tmp_path)
    services.state[service] = {"mode": "native", "port": local_services.available_port()}

    def unexpected(*args, **kwargs):
        pytest.fail("Unowned native services must not receive health, S3 or database requests")

    monkeypatch.setattr(local_services, "urlopen", unexpected)
    monkeypatch.setattr(local_services.boto3, "client", unexpected)
    monkeypatch.setattr(local_services.psycopg, "connect", unexpected)
    with pytest.raises(RuntimeError, match="owned native process"):
        if service == "minio":
            services.provision_minio()
        else:
            services.provision_postgres(False)


@pytest.mark.skipif(
    not shutil.which("brew") and not os.environ.get("AQS_POSTGRES_BIN"),
    reason="Native PostgreSQL 17 binary not configured",
)
def test_native_postgres_bind_collision_never_provisions_the_existing_cluster(
    monkeypatch, tmp_path
):
    try:
        local_services.postgres_bin()
    except (RuntimeError, subprocess.CalledProcessError):
        pytest.skip("Native PostgreSQL 17 binary not installed")
    first_path, second_path = tmp_path / "first", tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = local_services.LocalServices(first_path)
    second = local_services.LocalServices(second_path)
    try:
        first.start_postgres("native")
        port = first.state["postgres"]["port"]
        monkeypatch.setattr(second, "new_port", lambda: port)
        with pytest.raises(subprocess.CalledProcessError):
            second.start_postgres("native")
        with pytest.raises(RuntimeError, match="owned native process"):
            second.provision_postgres(False)
        assert first.owns_process("postgres")
        with local_services.psycopg.connect(
            f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        ) as connection:
            assert not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname='risk_service'"
            ).fetchone()
            assert not connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='risk_service_test'"
            ).fetchone()
    finally:
        second.stop(second.started)
        first.stop(first.started)
    assert first.state == second.state == {}
