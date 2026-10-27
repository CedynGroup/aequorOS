"""Preserve Python isolation flags while instrumenting isolated inline programs."""

from __future__ import annotations

import os
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
