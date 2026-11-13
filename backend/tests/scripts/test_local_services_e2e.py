"""Tests for test_local_services."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from scripts import local_services


def _has_exited(pid: int) -> bool:
    """Return whether ``pid`` is gone or a zombie.

    A killed orphan stays a zombie (state ``Z``) until its reaper collects it,
    and some container init processes take seconds to do so. ``os.kill(pid, 0)``
    still succeeds for a zombie even though the process no longer runs.
    """
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
    except OSError:
        # No procfs (macOS) or no such entry: os.kill separates the two.
        pass
    else:
        # The state letter follows the ")" that closes the command name.
        return stat.rsplit(")", 1)[1].split()[0] in {"Z", "X"}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_has_exited_reports_a_live_process_without_procfs(monkeypatch):
    def no_procfs(self: Path, *args: object, **kwargs: object) -> str:
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "read_text", no_procfs)

    assert not _has_exited(os.getpid())


def test_has_exited_reports_a_reaped_process():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()

    assert _has_exited(child.pid)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="Requires procfs")
def test_has_exited_treats_an_unreaped_zombie_as_stopped():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 10
        tick = Event()
        while not _has_exited(child.pid):
            assert time.monotonic() < deadline, "child never exited"
            tick.wait(0.05)
        # Not yet waited on, so the kernel still lists it as a zombie.
        os.kill(child.pid, 0)
    finally:
        child.wait()


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
            tick = Event()
            while not ready.exists():
                assert wrapper.poll() is None, (tmp_path / "output.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "output.log").read_text()
                tick.wait(0.1)
            pid = int(server_pid.read_text())
            wrapper.send_signal(signal.SIGTERM)
            assert wrapper.wait(timeout=30) == 130
            # The real Playwright runner owns a detached server group; the
            # wrapper must leave it enough time to tear that group down.
            assert _has_exited(pid), f"detached web server {pid} survived the wrapper"
        finally:
            if wrapper.poll() is None:
                wrapper.terminate()
                wrapper.wait(timeout=30)
            if server_pid.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(server_pid.read_text()), signal.SIGTERM)
