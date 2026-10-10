"""Move risk-service code into the feature layout and rewrite its references.

Usage, rebase workflow, frozen-identifier and guard-review requirements are owned by
``CODEBASE_CONVENTIONS.md`` §5, "Moving code".
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import sys
import textwrap
import tokenize
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: The cumulative move ledger, shared with ``tests/architecture/test_feature_boundaries.py``:
#: ``[old, new]`` pairs of exact dotted module names in move order. Append only.
LEDGER = Path("scripts") / "feature_module_moves.json"

#: Tracked files the rewrite reads, by suffix.
_SCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx"})
_TEXT_SUFFIXES = (
    frozenset({".py", ".md", ".toml", ".yml", ".yaml", ".ini", ".cfg"}) | _SCRIPT_SUFFIXES
)
_SKIPPED_PREFIXES = ("packages/",)
#: Files that name old paths on purpose: this script and its own tests.
_FROZEN_FILES = frozenset({"scripts/feature_moves.py", "tests/scripts/test_feature_moves.py"})
#: The architecture guards address source files relative to ``app/``.
_ARCHITECTURE_DIR = "tests/architecture/"
_LINE_LENGTH = 100
#: The frozen calculation-engine identifiers: the keys of this module's table.
_ENGINE_TABLE = "app.domain.authority.engines"
_ENGINE_TABLE_NAME = "ENGINE_LOCATIONS"


@dataclass(frozen=True)
class Renamer:
    """Applies ``moves`` in order to a dotted name; earlier moves feed later ones."""

    moves: tuple[tuple[str, str], ...]

    def __call__(self, name: str) -> str:
        for old, new in self.moves:
            if name == old or name.startswith(f"{old}."):
                name = new + name[len(old) :]
        return name

    def locations(self, name: str) -> list[str]:
        """Current and historical locations, from most recent to oldest."""
        locations = [name]
        for old, new in reversed(self.moves):
            if name == new or name.startswith(f"{new}."):
                name = old + name[len(new) :]
                locations.append(name)
        return locations

    @property
    def roots(self) -> frozenset[str]:
        return frozenset(name.split(".", 1)[0] for move in self.moves for name in move)


# --------------------------------------------------------------------------
# Moving files
# --------------------------------------------------------------------------


def _paths(backend: Path, old: str, new: str) -> tuple[Path, Path]:
    """The source and destination of a move: package directories, or ``.py`` modules."""
    source = backend / old.replace(".", "/")
    destination = backend / new.replace(".", "/")
    if source.is_dir() and _exists(source):
        return source, destination
    return source.with_suffix(".py"), destination.with_suffix(".py")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    ).stdout


def _ensure_packages(backend: Path, destination: Path, source: Path) -> None:
    """Create the destination's parent directories, as packages where the source was one."""
    package = (source.parent / "__init__.py").is_file()
    missing: list[Path] = []
    directory = destination.parent
    while directory != backend and not (directory / "__init__.py").is_file():
        missing.append(directory)
        directory = directory.parent
    for created in reversed(missing):
        created.mkdir(exist_ok=True)
        if package:
            init = created / "__init__.py"
            init.write_text(f'"""The ``{created.name}`` package (CODEBASE_CONVENTIONS.md §5)."""\n')
            _git(backend, "add", str(init))


def _exists(path: Path) -> bool:
    """Whether a module or package is present; a directory of bytecode alone is not."""
    if path.is_dir():
        return any(
            child.is_file() and (child.suffix != ".pyc" or child.parent.name != "__pycache__")
            for child in path.rglob("*")
        )
    return path.is_file()


def load_ledger(backend: Path) -> list[tuple[str, str]]:
    return [(old, new) for old, new in json.loads((backend / LEDGER).read_text(encoding="utf-8"))]


def write_ledger(backend: Path, pairs: Sequence[tuple[str, str]]) -> None:
    """Write the ledger one pair per line; this script owns the file's formatting."""
    lines = ",\n".join(f"  {json.dumps(list(pair))}" for pair in pairs)
    (backend / LEDGER).write_text(f"[\n{lines}\n]\n" if pairs else "[]\n", encoding="utf-8")


def module_pairs(backend: Path, old: str, new: str) -> list[tuple[str, str]]:
    """The ledger pairs a move adds: every module it carries, a package before its modules."""
    source = backend / old.replace(".", "/")
    if not source.is_dir() or not _exists(source):
        return [(old, new)]
    suffixes: list[tuple[str, ...]] = []
    for path in source.rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        suffixes.append(parts[:-1] if parts[-1] == "__init__" else parts)
    return [
        (".".join((old, *suffix)), ".".join((new, *suffix)))
        for suffix in sorted(suffixes, key=lambda suffix: (len(suffix), suffix))
    ]


def plan_moves(
    backend: Path, requests: Sequence[tuple[str, str]]
) -> tuple[list[tuple[Path, Path]], list[tuple[str, str]]]:
    """Validate the whole batch before creating packages or moving files.

    Overlapping moves must be separate commands, with each recorded before the next.
    """
    paths: list[tuple[Path, Path]] = []
    added: list[tuple[str, str]] = []
    tracked = set(_git(backend, "ls-files", "-z").split("\0"))
    for old, new in requests:
        source, destination = _paths(backend, old, new)
        relative = source.relative_to(backend).as_posix()
        tracked_source = relative in tracked or any(
            name.startswith(f"{relative}/") for name in tracked
        )
        parents = destination.relative_to(backend).parents
        destination_module = backend / new.replace(".", "/")
        blocked_parent = any(
            (backend / parent).is_file() or (backend / parent).with_suffix(".py").is_file()
            for parent in parents
        )
        if (
            not _exists(source)
            or not tracked_source
            or _exists(destination_module)
            or _exists(destination_module.with_suffix(".py"))
            or (not source.is_dir() and destination.exists())
            or blocked_parent
        ):
            raise SystemExit(f"cannot move {old} -> {new}: check {source} and {destination}")
        for path in (source, destination):
            others = [other for pair in paths for other in pair]
            others.append(destination if path == source else source)
            module = path.with_suffix("") if path.suffix == ".py" else path
            modules = [
                other.with_suffix("") if other.suffix == ".py" else other for other in others
            ]
            if any(
                module.is_relative_to(other) or other.is_relative_to(module) for other in modules
            ):
                raise SystemExit(f"cannot move {old} -> {new}: overlapping moves")
        paths.append((source, destination))
        added += module_pairs(backend, old, new)
    return paths, added


def move_files(backend: Path, paths: Sequence[tuple[Path, Path]]) -> None:
    """``git mv`` each validated module or package, leaving no compatibility shim."""
    for source, destination in paths:
        if destination.is_dir():
            shutil.rmtree(destination)  # stale bytecode; git mv would nest the package in it
        _ensure_packages(backend, destination, source)
        _git(backend, "mv", str(source), str(destination))


def _module_exists(backend: Path, module: str) -> bool:
    base = backend / module.replace(".", "/")
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def unmoved_modules(backend: Path, ledger: Sequence[tuple[str, str]]) -> list[str]:
    """Ledger sources that still exist although the ledger says they moved away."""
    rename = Renamer(tuple(ledger))
    return [old for old, _new in ledger if rename(old) != old and _module_exists(backend, old)]


# --------------------------------------------------------------------------
# Rewriting text
# --------------------------------------------------------------------------


def _offsets(text: str) -> list[int]:
    """Character offset of the start of each line (1-based line numbers index it)."""
    starts = [0, 0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _char_offset(text: str, starts: list[int], line: int, byte_column: int) -> int:
    """Turn an AST position (line, UTF-8 byte column) into a character offset."""
    start = starts[line]
    end = starts[line + 1] if line + 1 < len(starts) else len(text)
    prefix = text[start:end].encode("utf-8")[:byte_column]
    return start + len(prefix.decode("utf-8"))


def _comments(segment: str) -> list[str]:
    """The ``#`` comments inside an import statement's source."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(segment).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    return [token.string for token in tokens if token.type == tokenize.COMMENT]


def _import_line(module: str, names: Sequence[str], comments: Sequence[str], indent: str) -> str:
    """``from module import names``, wrapped in parentheses when it is too long."""
    comment = f"  {' '.join(comments)}" if comments else ""
    flat = f"from {module} import {', '.join(names)}"
    if len(indent) + len(flat) <= _LINE_LENGTH:
        return flat + comment
    body = "".join(f"{indent}    {name},\n" for name in names)
    return f"from {module} import ({comment}\n{body}{indent})"


def _alias(name: str, asname: str | None) -> str:
    return f"{name} as {asname}" if asname else name


def _package_of(module: str, is_package: bool) -> str:
    return module if is_package else module.rpartition(".")[0]


def _resolve(package: str, level: int, module: str | None) -> str:
    parts = package.split(".")
    if level > len(parts):
        return ""
    anchor = parts[: len(parts) - (level - 1)]
    return ".".join([*anchor, *([module] if module else [])])


class UnresolvedImport(ValueError):
    """A relative import has no uniquely resolvable location in the move ledger."""


def _import_source(backend: Path, module: str, rename: Renamer) -> Path | None:
    for location in rename.locations(rename(module)):
        path = backend / location.replace(".", "/")
        for source in (path / "__init__.py", path.with_suffix(".py")):
            if source.is_file():
                return source
    return None


def _declares(node: ast.AST, name: str) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            local = alias.asname or alias.name
            if isinstance(node, ast.Import) and not alias.asname:
                local = local.split(".", 1)[0]
            if local == name:
                return True
        return False
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id == name
    return any(_declares(child, name) for child in ast.iter_child_nodes(node))


def _import_available(backend: Path, base: str, node: ast.ImportFrom, rename: Renamer) -> bool:
    if not base:
        return False
    source = _import_source(backend, base, rename)
    tree = ast.parse(source.read_text(encoding="utf-8")) if source is not None else None
    return all(
        _import_source(backend, f"{base}.{alias.name}", rename) is not None
        or (tree is not None and (alias.name == "*" or _declares(tree, alias.name)))
        for alias in node.names
    )


def _absolute_base(
    node: ast.ImportFrom, module: str | None, is_package: bool, rename: Renamer, backend: Path
) -> str | None:
    """The absolute module a ``from`` import reads from, or ``None`` to leave it alone.

    Require one canonical target across current and historical locations.
    """
    if not node.level:
        return node.module
    if module is None:
        return None
    current_base = _resolve(_package_of(module, is_package), node.level, node.module)
    candidates: dict[tuple[str, ...], str] = {}
    for location in rename.locations(module):
        candidate = _resolve(_package_of(location, is_package), node.level, node.module)
        if _import_available(backend, candidate, node, rename):
            target = tuple(rename(f"{candidate}.{a.name}") for a in node.names)
            candidates.setdefault(target, candidate)
    if len(candidates) != 1:
        raise UnresolvedImport(f"cannot resolve relative import in {module}: {ast.unparse(node)}")
    base = next(iter(candidates.values()))
    new_base = _resolve(_package_of(rename(module), is_package), node.level, node.module)
    still_resolves = (
        base == current_base
        and rename(base) == new_base
        and all(
            rename(f"{base}.{alias.name}") == f"{new_base}.{alias.name}" for alias in node.names
        )
    )
    return None if still_resolves else base


def _regroup(
    node: ast.ImportFrom, base: str, rename: Renamer
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the imported names into those still read from ``base`` and moved modules.

    A moved module comes back as ``(new parent, "leaf as local")`` so the importing
    code keeps its local name.
    """
    new_module = rename(base)
    kept: list[str] = []
    moved: list[tuple[str, str]] = []
    for alias in node.names:
        target = rename(f"{base}.{alias.name}")
        if alias.name == "*" or target == f"{new_module}.{alias.name}":
            kept.append(_alias(alias.name, alias.asname))
            continue
        parent, _, leaf = target.rpartition(".")
        local = alias.asname or alias.name
        moved.append((parent, _alias(leaf, None if local == leaf else local)))
    return kept, moved


def _statement_span(
    text: str, starts: list[int], node: ast.ImportFrom
) -> tuple[int, int, str, list[str]] | None:
    """The statement's character span, indentation and comments (trailing one included).

    ``None`` when the statement shares its line with other code, which a textual
    replacement could corrupt; a subsequent dotted/path rewrite may still handle it.
    """
    start = _char_offset(text, starts, node.lineno, node.col_offset)
    end = _char_offset(text, starts, node.end_lineno or node.lineno, node.end_col_offset or 0)
    indent = text[starts[node.lineno] : start]
    end_of_line = text.find("\n", end)
    line_end = len(text) if end_of_line == -1 else end_of_line
    trailing = text[end:line_end].strip()
    if indent.strip() or (trailing and not trailing.startswith("#")):
        return None
    comments = _comments(text[start:end])
    if trailing:
        comments.append(trailing)
        end = line_end
    return start, end, indent, comments


def rewrite_imports(
    text: str, module: str | None, is_package: bool, rename: Renamer, backend: Path
) -> str:
    """Rewrite the ``from ... import`` statements a move affects.

    ``module`` is the file's current dotted name. A ``from`` import of a moved
    module becomes its own statement that keeps the local name, so the rest of the
    file (and every ``monkeypatch.setattr`` on that name) is unchanged.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    starts = _offsets(text)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (base := _absolute_base(node, module, is_package, rename, backend)) is None:
            continue
        kept, moved = _regroup(node, base, rename)
        if not moved and not node.level:
            continue
        if (span := _statement_span(text, starts, node)) is None:
            continue
        start, end, indent, comments = span
        statements = [_import_line(rename(base), kept, comments, indent)] if kept else []
        statements += [_import_line(parent, [name], comments, indent) for parent, name in moved]
        edits.append((start, end, f"\n{indent}".join(statements)))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def rewrite_dotted(text: str, rename: Renamer) -> str:
    """Rename every dotted reference: imports, strings, patch targets and prose."""
    pattern = re.compile(
        rf"(?<![\w.])((?:{'|'.join(sorted(map(re.escape, rename.roots)))})(?:\.\w+)+)"
    )
    return pattern.sub(lambda match: rename(match.group(1)), text)


def _rename_path(path: str, rename: Renamer, prefix: str = "") -> str:
    suffix = ".py" if path.endswith(".py") else ""
    dotted = (prefix + path.removesuffix(suffix)).replace("/", ".")
    renamed = rename(dotted)
    if renamed == dotted:
        return path
    return renamed.removeprefix(prefix.replace("/", ".")).replace(".", "/") + suffix


def rewrite_paths(text: str, rename: Renamer, *, app_relative: bool = False) -> str:
    """Rename slash paths to moved files (``app/x/y.py``, ``backend/tests/z/``).

    With ``app_relative`` it also renames paths written relative to ``app/``
    (``services/x.py``), the form the architecture guards glob with.
    """
    roots = "|".join(sorted(map(re.escape, rename.roots)))
    pattern = re.compile(rf"(?<![\w.\-])((?:{roots})(?:/\w+)+(?:\.py)?)(?![\w\-])")
    text = pattern.sub(lambda match: _rename_path(match.group(1), rename), text)
    if not app_relative:
        return text
    relative = re.compile(r"(?<![\w.\-/])(\w+(?:/\w+)+(?:\.py)?)(?![\w\-*])")
    return relative.sub(lambda match: _rename_path(match.group(1), rename, "app/"), text)


def _module_of(relative: str) -> tuple[str | None, bool]:
    """A backend-relative ``.py`` path's dotted module name, and whether it is a package."""
    path = Path(relative)
    if path.suffix != ".py":
        return None, False
    if path.name == "__init__.py":
        return ".".join(path.parent.parts), True
    return ".".join(path.with_suffix("").parts), False


def _rewrite_import_snippets(text: str, rename: Renamer, backend: Path) -> str:
    imports = re.compile(r"(?m)^([ \t]*)from[ \t]+[\w.]+[ \t]+import\b")
    edits: list[tuple[int, int, str]] = []
    for match in imports.finditer(text):
        tail = text[match.start() + len(match.group(1)) :]
        try:
            end = next(
                token.end
                for token in tokenize.generate_tokens(io.StringIO(tail).readline)
                if token.type == tokenize.NEWLINE
            )
        except (StopIteration, tokenize.TokenError, IndentationError):
            continue
        stop = match.start() + len(match.group(1)) + _offsets(tail)[end[0]] + end[1]
        statement = text[match.start() : stop]
        updated = rewrite_imports(textwrap.dedent(statement), None, False, rename, backend)
        updated = textwrap.indent(updated, match.group(1))
        if updated != statement:
            edits.append((match.start(), stop, updated))
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text


def rewrite_embedded_python(text: str, rename: Renamer, backend: Path) -> str:
    """Rewrite absolute Python imports inside JavaScript string literals."""
    literals = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')

    def replace(match: re.Match[str]) -> str:
        literal = match.group()
        if literal.startswith("`"):
            body = literal[1:-1]
            updated = _rewrite_import_snippets(body, rename, backend)
            return f"`{updated}`"
        try:
            body = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            return literal
        updated = _rewrite_import_snippets(body, rename, backend)
        return json.dumps(updated, ensure_ascii=False) if updated != body else literal

    return literals.sub(replace, text)


def rewrite_python_strings(text: str, rename: Renamer, backend: Path) -> str:
    """Apply the shared import rewrite to Python-hosted program strings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    starts = _offsets(text)
    edits: list[tuple[int, int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.JoinedStr) or (
            isinstance(node, ast.Constant) and isinstance(node.value, str)
        ):
            changed = False
            for value in ast.walk(node):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    updated = _rewrite_import_snippets(value.value, rename, backend)
                    if updated != value.value:
                        value.value = updated
                        changed = True
            if changed:
                start = _char_offset(text, starts, node.lineno, node.col_offset)
                end = _char_offset(
                    text, starts, node.end_lineno or node.lineno, node.end_col_offset or 0
                )
                edits.append((start, end, ast.unparse(node)))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def frozen_identifiers(backend: Path, rename: Renamer) -> frozenset[str]:
    """The calculation-engine identifiers no rewrite may touch.

    They are the keys of ``ENGINE_LOCATIONS``: filed packages carry them, so they keep
    the path an engine had when it was registered while its location follows the code.
    """
    path = backend / (rename(_ENGINE_TABLE).replace(".", "/") + ".py")
    if not path.is_file():
        return frozenset()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        else:
            continue
        named = any(isinstance(t, ast.Name) and t.id == _ENGINE_TABLE_NAME for t in targets)
        if named and isinstance(value, ast.Dict):
            return frozenset(
                key.value
                for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return frozenset()


def _mask(text: str, frozen: frozenset[str]) -> tuple[str, Callable[[str], str]]:
    """Hide ``frozen`` strings behind placeholders; return the text and the restorer."""
    if not frozen:
        return text, lambda masked: masked
    ordered = sorted(frozen, key=len, reverse=True)
    pattern = re.compile("|".join(rf"{re.escape(item)}(?!\w)" for item in ordered))
    index = {item: position for position, item in enumerate(ordered)}
    masked = pattern.sub(lambda match: f"\ue000{index[match.group(0)]}\ue001", text)
    placeholder = re.compile("\ue000(\\d+)\ue001")
    return masked, lambda rewritten: placeholder.sub(
        lambda match: ordered[int(match.group(1))], rewritten
    )


def rewrite_text(
    relative: str,
    text: str,
    rename: Renamer,
    backend: Path,
    frozen: frozenset[str] = frozenset(),
) -> str:
    """Every rewrite pass for one backend-relative file, leaving ``frozen`` strings alone."""
    module, is_package = _module_of(relative)
    path = Path(relative)
    if (
        module is not None
        and relative.startswith("app/")
        and ("schemas" in path.parts or path.stem == "schemas")
    ):
        # Pydantic publishes class docstrings as OpenAPI descriptions. A file move
        # must preserve those strings so generated clients do not change.
        starts = _offsets(text)
        descriptions: set[str] = set()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ClassDef) or not node.body:
                continue
            first = node.body[0]
            if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
                continue
            value = first.value
            if not isinstance(value.value, str):
                continue
            start = _char_offset(text, starts, value.lineno, value.col_offset)
            end = _char_offset(
                text, starts, value.end_lineno or value.lineno, value.end_col_offset or 0
            )
            literal = text[start:end]
            opening = re.match(r"(?i)[ru]*('''|\"\"\"|'|\")", literal)
            if opening is not None:
                descriptions.add(literal[opening.end() : -len(opening.group(1))])
        frozen = frozen | frozenset(description for description in descriptions if description)
    text, restore = _mask(text, frozen)
    if module is not None:
        text = rewrite_imports(text, module, is_package, rename, backend)
        text = rewrite_python_strings(text, rename, backend)
    elif Path(relative).suffix in _SCRIPT_SUFFIXES:
        text = rewrite_embedded_python(text, rename, backend)
    text = rewrite_dotted(text, rename)
    text = rewrite_paths(text, rename, app_relative=relative.startswith(_ARCHITECTURE_DIR))
    return restore(text)


def tracked_text_files(backend: Path) -> list[Path]:
    """Tracked files the rewrite may touch, by name, suffix and location."""
    repository = Path(_git(backend, "rev-parse", "--show-toplevel").strip())
    files: list[Path] = []
    listed = _git(repository, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for line in sorted(set(listed.split("\0"))):
        if not line or line.startswith(_SKIPPED_PREFIXES):
            continue
        path = repository / line
        if (
            path.suffix in _TEXT_SUFFIXES
            or path.name == "Dockerfile"
            or path.name.startswith("Dockerfile.")
            or path.suffix.lower() == ".dockerfile"
        ):
            files.append(path)
    return files


def plan_rewrites(backend: Path, rename: Renamer) -> dict[Path, str]:
    """Every tracked text file the passes change, with its rewritten text."""
    planned: dict[Path, str] = {}
    if not rename.moves:
        return planned
    frozen = frozen_identifiers(backend, rename)
    for path in tracked_text_files(backend):
        if not path.is_file():
            continue
        relative = (
            path.relative_to(backend).as_posix() if path.is_relative_to(backend) else path.name
        )
        if relative in _FROZEN_FILES:
            continue
        original = path.read_text(encoding="utf-8")
        updated = rewrite_text(relative, original, rename, backend, frozen)
        if updated != original:
            planned[path] = updated
    return planned


# --------------------------------------------------------------------------
# Guard coverage and formatting
# --------------------------------------------------------------------------

_GUARD_LITERAL = re.compile(r"[\w*]+(?:/[\w*.]+)*")


def _guard_glob(backend: Path, literal: str) -> tuple[Path, str] | None:
    """Where a guard's path literal points: a file, a glob, or a whole directory."""
    root = backend if literal.startswith("app/") else backend / "app"
    if "*" in literal or literal.endswith(".py"):
        return root, literal
    if (root / literal).is_dir():
        return root, f"{literal}/**/*.py"
    return None


def guard_coverage(backend: Path) -> dict[str, dict[str, int]]:
    """Per architecture guard: each path-like string literal -> the ``.py`` files it matches.

    Literals name files, globs or directories, relative to ``app/`` or written from
    ``app/``. A guard whose literal matches fewer files after a move scans less.
    """
    coverage: dict[str, dict[str, int]] = {}
    for guard in sorted((backend / _ARCHITECTURE_DIR).glob("*.py")):
        literals: dict[str, int] = {}
        for node in ast.walk(ast.parse(guard.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not _GUARD_LITERAL.fullmatch(node.value):
                continue
            if (target := _guard_glob(backend, node.value)) is not None:
                root, pattern = target
                literals[node.value] = sum(1 for path in root.glob(pattern) if path.suffix == ".py")
        coverage[guard.name] = literals
    return coverage


def coverage_drops(
    before: dict[str, dict[str, int]], after: dict[str, dict[str, int]], rename: Renamer
) -> list[str]:
    """Guard literals that match fewer files after a move (following their own rename)."""
    drops: list[str] = []
    for guard, literals in sorted(before.items()):
        for literal, count in sorted(literals.items()):
            prefix = "" if literal.startswith("app/") else "app/"
            renamed = _rename_path(literal, rename, prefix)
            now = after.get(guard, {}).get(renamed, 0)
            if now < count:
                drops.append(f"{guard}: {literal!r} matched {count} files, now {now}")
    return drops


def _ruff(backend: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
    )


def formatted_files(backend: Path, files: Iterable[Path]) -> set[Path]:
    """The ``.py`` files among ``files`` that Ruff's formatter leaves unchanged."""
    python = [path for path in files if path.suffix == ".py"]
    if not python:
        return set()
    result = _ruff(backend, "format", "--check", *map(str, python))
    unformatted = {
        (backend / line.removeprefix("Would reformat: ").strip()).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("Would reformat: ")
    }
    return {path for path in python if path.resolve() not in unformatted}


def format_python(backend: Path, files: Iterable[Path], formatted: set[Path]) -> None:
    """Sort imports in ``files``; Ruff-format the ones that were formatted before.

    Not every module in the tree is Ruff-formatted, so formatting the rest would bury
    the rewrite in unrelated churn.
    """
    if python := [str(path) for path in files if path.suffix == ".py"]:
        _ruff(backend, "check", "--select", "I", "--fix", "--quiet", *python)
    if clean := sorted(map(str, formatted)):
        _ruff(backend, "format", "--quiet", *clean)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _rewrite(backend: Path, planned: dict[Path, str], echo: Callable[[str], None]) -> None:
    formatted = formatted_files(backend, planned)
    for path, text in planned.items():
        path.write_text(text, encoding="utf-8")
        echo(f"rewrote {path.relative_to(backend.parent)}")
    format_python(backend, planned, formatted)


def move(
    backend: Path, requests: Sequence[tuple[str, str]], echo: Callable[[str], None] = print
) -> int:
    """Move each ``(old, new)`` module or package, record it, and rewrite every reference."""
    backend = backend.resolve()
    ledger = load_ledger(backend)
    paths, added = plan_moves(backend, requests)
    ledger += added
    rename = Renamer(tuple(ledger))
    planned = plan_rewrites(backend, rename)
    relocated: dict[Path, str] = {}
    for path, text in planned.items():
        target = path
        for source, destination in paths:
            if path.is_relative_to(source):
                target = destination / path.relative_to(source)
                break
        relocated[target] = text
    coverage = guard_coverage(backend)
    move_files(backend, paths)
    for old, new in requests:
        echo(f"moved {old} -> {new}")
    write_ledger(backend, ledger)
    _rewrite(backend, relocated, echo)
    for drop in coverage_drops(coverage, guard_coverage(backend), rename):
        echo(f"guard scans fewer files, review it: {drop}")
    return 0


def rewrite(backend: Path, echo: Callable[[str], None] = print) -> int:
    """Rewrite every reference to a module the ledger moved."""
    backend = backend.resolve()
    _rewrite(backend, plan_rewrites(backend, Renamer(tuple(load_ledger(backend)))), echo)
    return 0


def check(backend: Path, echo: Callable[[str], None] = print) -> int:
    """Return 1 when a moved module is back at its old path or anything uses an old name."""
    backend = backend.resolve()
    ledger = load_ledger(backend)
    for module in unmoved_modules(backend, ledger):
        echo(f"module still at its old path: {module}")
    try:
        stale = plan_rewrites(backend, Renamer(tuple(ledger)))
    except UnresolvedImport as error:
        echo(str(error))
        return 1
    for path in stale:
        echo(f"old name still used: {path.relative_to(backend.parent)}")
    return 1 if stale or unmoved_modules(backend, ledger) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    move_parser = commands.add_parser("move", help="move modules or packages, then rewrite")
    move_parser.add_argument("names", nargs="+", metavar="OLD NEW", help="dotted name pairs")
    commands.add_parser("rewrite", help="rewrite references to every recorded move")
    commands.add_parser("check", help="fail if an old name is still used")
    args = parser.parse_args(argv)
    if args.command == "rewrite":
        return rewrite(BACKEND)
    if args.command == "check":
        return check(BACKEND)
    if len(args.names) % 2:
        parser.error("move takes OLD NEW pairs")
    return move(BACKEND, list(zip(args.names[::2], args.names[1::2], strict=True)))


if __name__ == "__main__":
    raise SystemExit(main())
