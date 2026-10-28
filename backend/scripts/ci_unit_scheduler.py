"""Distribute independent unit cases while retaining the storage audit sequence."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import cast

import pytest


def scheduler(config: pytest.Config, log: object) -> object:
    # xdist exposes this constructor at runtime without a py.typed package.
    factory = cast(
        Callable[[pytest.Config, object], object],
        import_module("xdist.scheduler.loadscope").LoadScopeScheduling,
    )
    result = factory(config, log)

    def scope(nodeid: str) -> str:
        # This contract's audit assertion consumes earlier cases through one
        # class-scoped log. Every other case is an independent scheduling unit.
        contract = "tests/storage/test_inmemory_contract.py::TestInMemoryStorageContract::"
        return contract if nodeid.startswith(contract) else nodeid

    patch: Callable[[object, str, object], None] = setattr
    patch(result, "_split_scope", scope)
    return result
