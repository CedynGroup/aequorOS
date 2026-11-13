"""Ratchet unfrozen wall-clock reads out of tests; freeze the clock instead.

AQPY001 flags clock reads (``datetime.now/utcnow/today``, ``date.today``,
``time.time`` and the platform's ``utc_now`` helpers) anywhere under
``tests/`` — fixtures, ``tests/support`` and module level included — that are
not frozen: reads are accepted under a ``freeze_time`` marker on the function,
class or module ``pytestmark``, inside ``with time_machine.travel(...)``, or in
a function that takes the ``frozen_clock`` fixture. ``time.monotonic`` and
``time.perf_counter`` measure elapsed time, not the calendar, and are never
flagged. AQPY000 flags a ``# noqa: AQPY001`` or ``# noqa: TID251`` comment that
lacks the ``- <reason> (#<issue>)`` tail.

The allowlist records counts by path and rule, not line numbers. New reads fail
immediately; removing reads requires shrinking the allowlist (stale counts fail
too), and ``--strict`` rejects even allowlisted ones.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import symtable
import tokenize
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

BACKEND = Path(__file__).resolve().parents[1]
ALLOWLIST = BACKEND / "tests/architecture/test_determinism_allowlist.json"

AQPY001 = "AQPY001"
AQPY000 = "AQPY000"
TID251 = "TID251"

#: Wall-clock readers. ``datetime.*`` appears in both import styles:
#: ``from datetime import datetime`` resolves ``datetime.now`` to
#: ``datetime.datetime.now``, while ``import datetime`` writes
#: ``datetime.datetime.now()`` spelled through the module.
CLOCK_MEMBERS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "date.today",
        "datetime.date.today",
        "time.time",
    }
)

#: The platform's own wall-clock helpers (app/db/base.py and
#: app/attestation/service/api.py); anything reading them inherits the same
#: flake risk as reading ``datetime.now`` directly.
UTC_NOW_SOURCES = frozenset(
    {
        "app.db.base.utc_now",
        "app.attestation.service.api.utc_now",
    }
)

FREEZE_MARKER = "pytest.mark.freeze_time"
TRAVEL = "time_machine.travel"
FROZEN_CLOCK = "frozen_clock"

#: A surviving suppression must name its reason and its tracking issue:
#: ``# noqa: AQPY001 - <reason> (#<issue>)``.
NOQA_CODES = re.compile(r"noqa:\s*(?P<codes>[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)")
SUPERSEDED = re.compile(
    r"noqa:\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*\s*-\s*(?P<reason>[^#]+?)\s*\(#(?P<issue>\d+)\)"
)
GUARDED_CODES = frozenset({AQPY001, TID251})


class ClockVisitor(ast.NodeVisitor):
    """Resolve clock-call bindings within each lexical scope in statement order.

    Mirrors the alias machinery of ``test_database_guard.py``: imports and
    assignments feed a per-scope alias map, ``symtable`` separates locals from
    globals, and function bodies are scanned after the module pass so calls are
    judged with the bindings visible at their use site.
    """

    def __init__(self, source: str) -> None:
        self.table: symtable.SymbolTable = symtable.symtable(source, "<test>", "exec")
        self.aliases: dict[str, str] = {}
        self.enclosing = self.aliases
        self.module_aliases = self.aliases
        self.calls: Counter[str] = Counter()
        self.children: dict[symtable.SymbolTable, list[symtable.SymbolTable]] = {}
        self.frozen = False
        self.functions: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
                symtable.SymbolTable,
                dict[str, str],
                bool,
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
        if (name in CLOCK_MEMBERS or name in UTC_NOW_SOURCES) and not self.frozen:
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
                if target.id == "pytestmark" and self._carries_freeze_marker(node.value):
                    self.frozen = True

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, node.value)
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "pytestmark"
                and self._carries_freeze_marker(node.value)
            ):
                self.frozen = True

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

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.bind(item.optional_vars, item.context_expr)
        previous, self.frozen = (
            self.frozen,
            self.frozen or any(self._enters_travel(item.context_expr) for item in node.items),
        )
        for statement in node.body:
            self.visit(statement)
        self.frozen = previous

    visit_AsyncWith = visit_With

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        own = self._decorator_freezes(node.decorator_list) or _takes_frozen_clock(node.args)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        frozen = own or self.frozen
        self.functions.append((node, self.child_table(node, node.name), self.enclosing, frozen))
        self.aliases[node.name] = ""

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.functions.append((node, self.child_table(node, "lambda"), self.enclosing, self.frozen))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        own = self._decorator_freezes(node.decorator_list)
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        previous_table, previous_aliases, previous_frozen = self.table, self.aliases, self.frozen
        self.table = self.child_table(node, node.name)
        self.aliases = self.aliases.copy()
        self.frozen = self.frozen or own
        for statement in node.body:
            self.visit(statement)
        self.table, self.aliases, self.frozen = previous_table, previous_aliases, previous_frozen
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

    def _decorator_freezes(self, decorators: list[ast.expr]) -> bool:
        return any(
            isinstance(decorator, ast.Call) and self.qualified(decorator.func) == FREEZE_MARKER
            for decorator in decorators
        )

    def _carries_freeze_marker(self, value: ast.expr) -> bool:
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return any(
                isinstance(element, ast.Call) and self.qualified(element.func) == FREEZE_MARKER
                for element in value.elts
            )
        return isinstance(value, ast.Call) and self.qualified(value.func) == FREEZE_MARKER

    def _enters_travel(self, context_expr: ast.expr) -> bool:
        return isinstance(context_expr, ast.Call) and self.qualified(context_expr.func) == TRAVEL

    def scan(self, tree: ast.Module) -> Counter[str]:
        self.visit(tree)
        for node, table, enclosing, frozen in self.functions:
            self.table = table
            self.aliases = enclosing.copy()
            for symbol in table.get_symbols():
                if symbol.is_local():
                    self.aliases[symbol.get_name()] = ""
                elif symbol.is_global():
                    self.aliases[symbol.get_name()] = self.module_aliases.get(symbol.get_name(), "")
            self.enclosing = self.aliases
            self.frozen = frozen
            if isinstance(node, ast.Lambda):
                self.visit(node.body)
            else:
                for statement in node.body:
                    self.visit(statement)
        return self.calls


def _takes_frozen_clock(args: ast.arguments) -> bool:
    names = [argument.arg for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    return FROZEN_CLOCK in names


def clock_reads(source: str) -> Counter[str]:
    """Resolve clock-read bindings within each lexical scope in statement order."""
    return ClockVisitor(source).scan(ast.parse(source))


def unreasoned_suppressions(source: str) -> int:
    """Count ``noqa`` comments for guarded codes that lack a reason and issue."""
    count = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        codes = NOQA_CODES.search(token.string)
        if codes is None:
            continue
        named = {*codes.group("codes").replace(" ", "").split(",")}
        if named & GUARDED_CODES and SUPERSEDED.search(token.string) is None:
            count += 1
    return count


def inventory(root: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in sorted((root / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        reads: Counter[str] = clock_reads(source)
        suppressions = unreasoned_suppressions(source)
        found = {AQPY001: sum(reads.values()), AQPY000: suppressions}
        found = {rule: count for rule, count in found.items() if count}
        if found:
            counts[path.relative_to(root).as_posix()] = found
    return counts


class GuardArguments(argparse.Namespace):
    root: Path = BACKEND
    allowlist: Path = ALLOWLIST
    strict: bool = False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--root", type=Path, default=BACKEND)
    _ = parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    _ = parser.add_argument("--strict", action="store_true", help="Reject even allowlisted reads")
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
                    f"{path}: unfrozen clock reads {actual.get(path, {})}; "
                    "allowlisted "
                    f"{expected.get(path, {})}. Freeze the clock "
                    "(@pytest.mark.freeze_time / frozen_clock / time_machine.travel) "
                    "or suppress with '# noqa: AQPY001 - <reason> (#<issue>)'."
                )
        return 1
    total = sum(sum(counts.values()) for counts in actual.values())
    if total:
        print(
            f"WARNING: {total} unfrozen clock reads in {len(actual)} test files remain "
            "allowlisted; freeze the clock or shrink the allowlist."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
