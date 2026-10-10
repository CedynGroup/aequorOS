"""Scoped data-truth lint gate; extend MIGRATED_MODULES with each engine migration.

The syntax guard complements strict basedpyright: it prevents float construction,
nullable figure contracts, bare missing results and catch-all status handling. The
type checker proves that the terminal assert_never actually receives Never.
It does not infer units in arbitrary runtime expressions or legacy modules.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MIGRATED_MODULES: tuple[str, ...] = ("app.core.data_truth",)
FIGURE_TYPES = frozenset(
    {
        "Decimal",
        "Money",
        "Rate",
        "Ratio",
        "Percentage",
        "BasisPoints",
        "Value",
        "CalculationResult",
        "NumericKind",
        "ResultRead",
    }
)


@dataclass(frozen=True)
class Violation:
    line: int
    rule: str


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _annotation(annotation: ast.AST) -> ast.AST:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return ast.parse(annotation.value, mode="eval")
    return annotation


def _figure(annotation: ast.AST, aliases: dict[str, str]) -> bool:
    return any(
        aliases.get(_name(node), _name(node)) in FIGURE_TYPES
        for node in ast.walk(_annotation(annotation))
    )


def _nullable(annotation: ast.AST, aliases: dict[str, str]) -> bool:
    return any(
        (isinstance(node, ast.Constant) and node.value is None)
        or aliases.get(_name(node), _name(node)) == "Optional"
        for node in ast.walk(_annotation(annotation))
    )


def _exhaustive(node: ast.Match) -> bool:
    last = node.cases[-1]
    if not isinstance(last.pattern, ast.MatchAs) or last.pattern.pattern is not None:
        return False
    if last.guard is not None or len(last.body) != 1:
        return False
    statement = last.body[0]
    call = statement.value if isinstance(statement, ast.Expr) else None
    return (
        isinstance(call, ast.Call)
        and _name(call.func) == "assert_never"
        and len(call.args) == 1
        and ast.dump(call.args[0]) == ast.dump(node.subject)
    )


def scan(source: str) -> list[Violation]:
    """Lint a module's source; syntax errors are failures, not skipped files."""
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    violations: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            violations.add((node.lineno, "float-financial-value"))
        if (
            isinstance(node, (ast.Name, ast.Attribute))
            and aliases.get(_name(node), _name(node)) == "float"
        ):
            violations.add((node.lineno, "float-financial-value"))
        if isinstance(node, ast.Match) and not _exhaustive(node):
            violations.add((node.lineno, "non-exhaustive-match"))
        annotation = None
        if isinstance(node, (ast.AnnAssign, ast.arg)):
            annotation = node.annotation
        elif isinstance(node, ast.TypeAlias):
            annotation = node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        if (
            isinstance(
                node, (ast.AnnAssign, ast.arg, ast.TypeAlias, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and annotation is not None
            and _figure(annotation, aliases)
            and _nullable(annotation, aliases)
        ):
            violations.add((node.lineno, "nullable-figure"))
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.returns is not None
            and _figure(node.returns, aliases)
        ):
            for child in _own_body(node):
                if isinstance(child, ast.Return) and (
                    child.value is None
                    or (isinstance(child.value, ast.Constant) and child.value.value is None)
                ):
                    violations.add((child.lineno, "bare-missing-result"))
    return [Violation(line, rule) for line, rule in sorted(violations)]


def _own_body(node: ast.AST) -> list[ast.AST]:
    """A nested callback's return is not its enclosing calculation's result."""
    result: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        result.append(child)
        result.extend(_own_body(child))
    return result


def scoped_files() -> list[Path]:
    files: set[Path] = set()
    for module in MIGRATED_MODULES:
        path = BACKEND.joinpath(*module.split("."))
        if path.is_dir():
            files.update(path.rglob("*.py"))
        elif path.with_suffix(".py").is_file():
            files.add(path.with_suffix(".py"))
        else:
            raise ValueError(f"Data-truth scope no longer exists: {module}")
    if not files:
        raise ValueError("Data-truth scope must not be empty.")
    return sorted(files)


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(argv) if argv is not None else sys.argv[1:]
    files = [Path(path) for path in paths] if paths else scoped_files()
    failed = False
    for path in files:
        try:
            violations = scan(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"{path}:{exc.lineno}: invalid Python syntax")
            failed = True
            continue
        for violation in violations:
            print(f"{path}:{violation.line}: {violation.rule}")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
