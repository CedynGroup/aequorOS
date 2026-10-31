"""Ratchet direct DB construction out of tests; support/database.py owns it.

The temporary allowlist records counts by path and constructor, not line
numbers. New calls fail immediately; removing calls requires shrinking it.
"""

from __future__ import annotations

import argparse
import ast
import symtable
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


class ConstructorVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.table: symtable.SymbolTable = symtable.symtable(source, "<test>", "exec")
        self.aliases: dict[str, str] = {}
        self.enclosing = self.aliases
        self.module_aliases = self.aliases
        self.calls: Counter[str] = Counter()
        self.children: dict[symtable.SymbolTable, list[symtable.SymbolTable]] = {}
        self.functions: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
                symtable.SymbolTable,
                dict[str, str],
            ]
        ] = []

    def qualified(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, "")
        if isinstance(node, ast.Attribute):
            return f"{self.qualified(node.value)}.{node.attr}"
        return ""

    def child_table(self, node: ast.expr | ast.stmt, name: str) -> symtable.SymbolTable:
        children = self.children.setdefault(self.table, self.table.get_children())
        index = next(
            index
            for index, child in enumerate(children)
            if child.get_name() == name and child.get_lineno() == node.lineno
        )
        child = children.pop(index)
        if child.get_type() not in {"function", "class"}:
            child = next(
                nested
                for nested in child.get_children()
                if nested.get_name() == name and nested.get_lineno() == node.lineno
            )
        return child

    def visit_Call(self, node: ast.Call) -> None:
        name = self.qualified(node.func)
        if name in CONSTRUCTORS:
            self.calls[name] += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = (
                f"{node.module}.{alias.name}" if node.level == 0 else ""
            )

    def bindings(self, target: ast.expr, value: ast.expr | None = None) -> dict[str, str]:
        if isinstance(target, ast.Name):
            return {target.id: self.qualified(value) if value is not None else ""}
        if isinstance(target, (ast.Tuple, ast.List)):
            values = value.elts if isinstance(value, (ast.Tuple, ast.List)) else []
            result: dict[str, str] = {}
            for index, item in enumerate(target.elts):
                result.update(
                    self.bindings(item, values[index] if len(values) == len(target.elts) else None)
                )
            return result
        if isinstance(target, ast.Starred):
            return self.bindings(target.value)
        self.visit(target)
        return {}

    def bind(self, target: ast.expr, value: ast.expr | None = None) -> None:
        self.aliases.update(self.bindings(target, value))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.aliases[node.id] = ""

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        name = self.qualified(node.value)
        for target in node.targets:
            self.bind(target, node.value)
            if isinstance(target, ast.Name):
                self.aliases[target.id] = name

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self.bind(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.bind(node.target, node.value)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self.bind(node.target)
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name is not None:
            self.aliases[node.name] = ""
        for statement in node.body:
            self.visit(statement)
        if node.name is not None:
            self.aliases[node.name] = ""

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self.functions.append((node, self.child_table(node, node.name), self.enclosing))
        self.aliases[node.name] = ""

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.functions.append((node, self.child_table(node, "lambda"), self.enclosing))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        previous_table, previous_aliases = self.table, self.aliases
        self.table = self.child_table(node, node.name)
        self.aliases = self.aliases.copy()
        for statement in node.body:
            self.visit(statement)
        self.table, self.aliases = previous_table, previous_aliases
        self.aliases[node.name] = ""

    def visit_comprehension_scope(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        previous_table, previous_aliases, previous_enclosing = (
            self.table,
            self.aliases,
            self.enclosing,
        )
        if isinstance(node, ast.GeneratorExp):
            self.table = self.child_table(node, "genexpr")
        self.aliases = self.enclosing.copy()
        self.enclosing = self.aliases
        for generator in node.generators:
            self.bind(generator.target)
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.bind(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for expression in [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]:
            self.visit(expression)
        self.table, self.aliases, self.enclosing = (
            previous_table,
            previous_aliases,
            previous_enclosing,
        )

    visit_ListComp = visit_comprehension_scope
    visit_SetComp = visit_comprehension_scope
    visit_DictComp = visit_comprehension_scope
    visit_GeneratorExp = visit_comprehension_scope

    def scan(self, tree: ast.Module) -> Counter[str]:
        self.visit(tree)
        for node, table, enclosing in self.functions:
            self.table = table
            self.aliases = enclosing.copy()
            for symbol in table.get_symbols():
                if symbol.is_local():
                    self.aliases[symbol.get_name()] = ""
                elif symbol.is_global():
                    self.aliases[symbol.get_name()] = self.module_aliases.get(symbol.get_name(), "")
            self.enclosing = self.aliases
            if isinstance(node, ast.Lambda):
                self.visit(node.body)
            else:
                for statement in node.body:
                    self.visit(statement)
        return self.calls


def direct_constructors(source: str) -> Counter[str]:
    """Resolve constructor bindings within each lexical scope in statement order."""
    return ConstructorVisitor(source).scan(ast.parse(source))


def inventory(root: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in sorted((root / "tests").rglob("*.py")):
        if path == root / "tests/support/database.py":
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
