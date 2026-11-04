"""Reviewed PostgreSQL guarantees layered on the existing execution owners."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Mapping, Sequence, Set
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict, cast

CATALOGUE = Path(__file__).with_name("database_contracts.json")
FAKE_FIXTURE = "current_annex_repository"
FAKE_FORBIDDEN_PATHS = ("tests/db/", "tests/storage/", "tests/api/", "tests/identity/")
DATABASE_FIXTURES = frozenset(
    {
        "db_session",
        "db_client",
        "committing_db_session",
        "_test_database",
        "_rollback_db_session",
        "_bound_test_sessionmaker",
        "test_db_connections",
        "test_database_connections",
    }
)


class _Group(TypedDict):
    category: str
    reason: str
    cases: list[str]


@cache
def _catalogue(path: Path) -> Mapping[str, str]:
    groups = cast(dict[str, list[_Group]], json.loads(path.read_text()))["contracts"]
    protected: dict[str, str] = {}
    for group in groups:
        if not group["reason"].strip() or not group["cases"]:
            raise ValueError("Protected database contracts require cases and a reason")
        for nodeid in group["cases"]:
            if "::" not in nodeid or nodeid in protected:
                raise ValueError(f"Invalid or duplicated protected database contract: {nodeid}")
            protected[nodeid] = group["category"] + ": " + group["reason"]
    return MappingProxyType(protected)


def catalogue() -> Mapping[str, str]:
    return _catalogue(CATALOGUE)


def is_protected(nodeid: str, *, marked: bool = False) -> bool:
    return marked or nodeid in catalogue()


@cache
def _primary_contracts(path: Path) -> frozenset[str]:
    source = cast(dict[str, list[str]], json.loads(path.read_text()))
    return frozenset(source.get("primary_opt_in", []))


def primary_contracts() -> frozenset[str]:
    primary = _primary_contracts(CATALOGUE)
    if not primary <= catalogue().keys():
        raise ValueError("Primary opt-in identities must be protected database contracts")
    return primary


def must_execute(nodeid: str, *, marked: bool = False) -> bool:
    if nodeid in primary_contracts() and not os.getenv("REAL_DATA_DATABASE_URL"):
        return False
    return is_protected(nodeid, marked=marked)


def check_classification(
    classified: Mapping[str, str], *, protected: Mapping[str, str] | None = None
) -> None:
    for nodeid, reason in (catalogue() if protected is None else protected).items():
        if nodeid not in classified:
            raise ValueError(f"Protected database contract disappeared: {nodeid}; {reason}")
        if classified[nodeid] != "integration":
            raise ValueError(
                f"Protected database contract must remain integration: {nodeid}; {reason}"
            )


def check_collection(
    nodeid: str, suite: str | None, fixtures: Sequence[str], *, marked: bool = False
) -> None:
    protected = is_protected(nodeid, marked=marked)
    if protected and suite == "unit":
        raise ValueError(f"Protected database contract assigned to unit: {nodeid}")
    module = nodeid.split("::", 1)[0]
    forbidden_path = module.startswith(FAKE_FORBIDDEN_PATHS) or "/api/" in module
    if FAKE_FIXTURE in fixtures and (protected or forbidden_path):
        raise ValueError(f"Current-annex fake is forbidden for database contracts: {nodeid}")
    if suite == "unit" and (database := DATABASE_FIXTURES.intersection(fixtures)):
        raise ValueError(
            f"Unit test requests database resource fixtures: {nodeid}: {sorted(database)}"
        )


def postgres_required() -> bool:
    return os.getenv("CI") == "true" or os.getenv("CI_REQUIRE_DATABASE_CONTRACTS") == "1"


def check_backend(nodeid: str, url: str | None, *, marked: bool = False) -> None:
    if not postgres_required() or not must_execute(nodeid, marked=marked):
        return
    if nodeid in primary_contracts():
        url = os.getenv("REAL_DATA_DATABASE_URL")
    if url is None or not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError(f"Protected database contract requires PostgreSQL: {nodeid}")


def verify_execution(
    reports: Sequence[Path],
    selected: Set[str],
    identity: Callable[[str], tuple[str, str]],
    *,
    protected: Mapping[str, str] | None = None,
) -> None:
    contracts = (
        {nodeid: reason for nodeid, reason in catalogue().items() if must_execute(nodeid)}
        if protected is None
        else protected
    )
    expected = {identity(nodeid): nodeid for nodeid in selected if nodeid in contracts}
    actual: Counter[tuple[str, str]] = Counter()
    for path in reports:
        for case in ET.parse(path).iter("testcase"):
            key = (case.get("classname", ""), case.get("name", ""))
            if key not in expected:
                continue
            owner = path.parent.name
            if not (
                owner.startswith(("pg-schema-", "pg-suite-"))
                or owner in {"pg-locks", "storage-tests"}
            ):
                raise ValueError(
                    f"Protected database contract has a non-Postgres owner: {expected[key]}"
                )
            if any(case.find(tag) is not None for tag in ("skipped", "error", "failure")):
                raise ValueError(
                    f"Protected database contract did not execute successfully: {expected[key]}"
                )
            actual[key] += 1
    if actual != Counter({key: 1 for key in expected}):
        raise ValueError("Protected database contracts were dropped or duplicated in execution")
