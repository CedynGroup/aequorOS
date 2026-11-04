"""Tests for test_local_services."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from scripts import local_services


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

        def log_message(self, format: str, *args: object) -> None:
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
