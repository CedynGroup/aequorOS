"""Ratchet direct DB construction out of tests; support/database.py owns it.

The temporary allowlist records counts by path and constructor, not line
numbers. New calls fail immediately; removing calls requires shrinking it.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

BACKEND = Path(__file__).resolve().parents[1]
ALLOWLIST = BACKEND / "tests/architecture/test_database_connection_allowlist.json"
CONSTRUCTORS = frozenset(
    {
        "sqlalchemy.create_engine",
        "sqlalchemy.engine.create_engine",
        "sqlalchemy.engine.create.create_engine",
        "sqlalchemy.ext.asyncio.create_async_engine",
        "sqlalchemy.ext.asyncio.engine.create_async_engine",
        "sqlalchemy.orm.sessionmaker",
        "sqlalchemy.orm.session.sessionmaker",
        "sqlalchemy.ext.asyncio.async_sessionmaker",
        "psycopg.connect",
        "psycopg.Connection.connect",
        "psycopg.AsyncConnection.connect",
    }
)


def direct_constructors(source: str) -> Counter[str]:
    """Resolve imported aliases and assigned aliases before checking calls."""
    tree = ast.parse(source)
    aliases: dict[str, str] = {}

    def qualified(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        return ""

    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    # Fixed point covers chained aliases without depending on traversal order.
    for _ in range(len(nodes)):
        previous = aliases.copy()
        for node in nodes:
            if isinstance(node, ast.Assign):
                target_name = qualified(node.value)
                if target_name in CONSTRUCTORS or target_name.startswith(("sqlalchemy", "psycopg")):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = target_name
        if aliases == previous:
            break
    return Counter(
        name
        for node in nodes
        if isinstance(node, ast.Call) and (name := qualified(node.func)) in CONSTRUCTORS
    )


def inventory(root: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in sorted((root / "tests").rglob("*.py")):
        if path.is_relative_to(root / "tests/support"):
            continue
        found = direct_constructors(path.read_text(encoding="utf-8"))
        if found:
            counts[path.relative_to(root).as_posix()] = dict(sorted(found.items()))
    return counts


class GuardArguments(argparse.Namespace):
    root: Path = BACKEND
    allowlist: Path = ALLOWLIST
    strict: bool = False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--root", type=Path, default=BACKEND)
    _ = parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    _ = parser.add_argument("--strict", action="store_true", help="Reject even allowlisted calls")
    args = parser.parse_args(argv, namespace=GuardArguments())
    root, allowlist, strict = args.root, args.allowlist, args.strict
    actual = inventory(root)
    expected = (
        {}
        if strict
        else TypeAdapter(dict[str, dict[str, int]]).validate_json(allowlist.read_text())
    )
    if actual != expected:
        for path in sorted(actual.keys() | expected.keys()):
            if actual.get(path) != expected.get(path):
                print(
                    f"{path}: direct DB constructors {actual.get(path, {})}; "
                    f"allowlisted {expected.get(path, {})}. Use tests.support.database."
                )
        return 1
    total = sum(sum(calls.values()) for calls in actual.values())
    if total:
        print(
            f"WARNING: {total} legacy DB constructor calls in {len(actual)} test files "
            "remain allowlisted; migrate to tests.support.database."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
