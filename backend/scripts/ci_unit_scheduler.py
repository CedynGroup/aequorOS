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
        groups = (
            # The audit assertion consumes earlier cases through a class log.
            (
                "storage-audit",
                ("tests/storage/test_inmemory_contract.py::TestInMemoryStorageContract::",),
            ),
            # Rebuilding these immutable module fixtures on every worker costs
            # more than their independent assertions can save in parallel.
            ("openapi-schema", ("tests/api/test_openapi_contract.py::",)),
            (
                "job-reachability",
                ("tests/architecture/test_job_enqueue_reachability.py::",),
            ),
            (
                "shard-reports",
                (
                    "tests/architecture/test_ci_postgres.py::test_real_workers_partition_every_test_and_preserve_junit_names",
                    "tests/architecture/test_ci_postgres.py::test_coverage_guard_rejects_incomplete_or_inconsistent_results[",
                ),
            ),
        )
        for group, prefixes in groups:
            if nodeid.startswith(prefixes):
                return group
        return nodeid

    patch: Callable[[object, str, object], None] = setattr
    patch(result, "_split_scope", scope)
    return result
