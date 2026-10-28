"""Preserve Python isolation flags while instrumenting isolated inline programs."""

from __future__ import annotations

import json
import os
import subprocess
import time
import weakref
from pathlib import Path
from typing import cast

_BOOTSTRAP = (
    "(lambda namespace: (exec(compile(open("
    + repr(str(Path(__file__).with_name("isolated_database_child.py")))
    + ", encoding='utf-8').read(), '<database instrumentation>', 'exec'), namespace), "
    + "namespace['_database_bootstrap']()))({'__name__': 'database_instrumentation'})"
)


def instrument_command(command: object) -> object:
    if not isinstance(command, (tuple, list)):
        return command
    arguments = list(cast(list[object], command))
    if not arguments or not Path(str(arguments[0])).name.startswith("python"):
        return arguments
    if not any(flag in arguments for flag in ("-I", "-S")) or "-c" not in arguments:
        return arguments
    index = arguments.index("-c") + 1
    if index >= len(arguments) or arguments[index] == _BOOTSTRAP:
        return arguments
    arguments[index : index + 1] = [_BOOTSTRAP, arguments[index]]
    return arguments


def python_path(previous: str) -> str:
    """Inherit startup instrumentation through spawn/fork-exec as well as Popen."""
    backend = Path(__file__).resolve().parents[1]
    paths = [str(backend / "scripts/ci_no_database"), str(backend), *previous.split(os.pathsep)]
    return os.pathsep.join(dict.fromkeys(paths))


def confirm_startup_before_signal(
    process: subprocess.Popen[bytes], environment: dict[str, str], command: object
) -> None:
    """Keep Popen nonblocking so callers can own cancellation and cleanup.

    Confirm instrumentation before an immediate terminate/kill instead: waiting
    inside construction can raise KeyboardInterrupt before the caller receives
    its child handle, leaving the child outside its cleanup scope.
    """
    directory = environment.get("CI_DATABASE_OBSERVATIONS")
    if not directory or not isinstance(command, (list, tuple)) or not command:
        return
    if not Path(str(cast(list[object], command)[0])).name.startswith("python"):
        return
    # A callback stored on Popen must not keep the process alive through itself.
    child = cast(subprocess.Popen[bytes], weakref.proxy(process))
    send_signal = type(process).send_signal

    def confirmed_signal(sig: int) -> None:
        _wait_for_startup(child, environment, Path(directory))
        send_signal(child, sig)

    process.send_signal = confirmed_signal


def _wait_for_startup(
    process: subprocess.Popen[bytes], environment: dict[str, str], directory: Path
) -> None:
    """Confirm instrumentation before a caller can immediately terminate Python."""
    context = cast(dict[str, object], json.loads(environment.get("CI_DATABASE_CONTEXT", "{}")))
    path = directory / f"events-{process.pid}.jsonl"
    deadline = time.monotonic() + 5
    while True:
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    event = cast(dict[str, object], json.loads(line))
                except ValueError:
                    continue
                if (
                    event.get("kind") == "instrumented"
                    and event.get("nodeid", "") == context.get("nodeid", "")
                    and event.get("fixtures", []) == context.get("fixtures", [])
                ):
                    return
        if process.poll() is not None or time.monotonic() >= deadline:
            return
        time.sleep(0.01)
