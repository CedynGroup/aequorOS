"""One definition of the case plane, and the AST scanning both boundary guards share.

`test_dependency_boundaries.py` (ARCH-5) and `test_case_plane_boundary.py` (CF-4)
both forbid the regulatory plane from reaching into the case plane. Until
2026-08-22 each carried its OWN hand-written idea of what the case plane *is*,
and the two had drifted apart:

* `test_case_plane_boundary.CASE_PLANE_OWNERS` listed **16** modules as being the
  case plane (and therefore permitted to touch case models);
* `test_case_plane_boundary.CASE_PLANE_MODULES` listed **9** as forbidden import
  targets, and `test_dependency_boundaries.CASE_PLANE_MODULES` listed **6**.

Thirteen of the sixteen owners were absent from the forbidden sets, so an
**ordinary absolute import** walked straight through both guards::

    from app.services.case_plane import compute_headroom
    from app.features.run_calculations import compute_headroom
    from app.services.financial_workspace import compute_headroom

The mismatch was not an oversight anyone could see: two lists in two files,
each individually plausible. So the lists are gone. Membership is now DERIVED,
once, from `CASE_PLANE_GLOBS` — the on-disk description of where the case plane
lives — and the same derived set answers both questions:

* is this module allowed to reference a case-plane model?  (it is the case plane)
* may a module outside the plane import it?                (no)

There is exactly one set, so the two can no longer disagree.
`PINNED_CASE_PLANE_MODULES` is the frozen floor: the union of the three
historical lists. The derivation may grow but can never silently shrink below
what the audit pinned, which is what stops a renamed directory from emptying
the scan into a vacuous pass.

Scanning limits are documented on each function. They are real; read them
before trusting a green run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).parents[2] / "app"

# --------------------------------------------------------------------------
# Membership: derived once, from the globs
# --------------------------------------------------------------------------

#: Where the case plane lives on disk. THE source of truth for plane
#: membership — every other case-plane set in the guards is derived from it.
CASE_PLANE_GLOBS: tuple[str, ...] = (
    "models/calculation.py",
    "models/capital.py",
    "models/financial.py",
    "schemas/calculations.py",
    "schemas/capital.py",
    "schemas/liquidity.py",
    "schemas/financial_workspace*.py",
    "services/calculations.py",
    "services/capital.py",
    "services/case_plane.py",
    "services/liquidity.py",
    "services/financial_*.py",
    "services/financial_mapping/**/*.py",
    "features/manage_capital.py",
    "features/read_financial_workspace.py",
    "features/review_liquidity.py",
    "features/run_calculations.py",
)

#: The union of the three hand-maintained lists this module replaced, frozen as
#: a floor. `test_the_case_plane_derivation_cannot_shrink_below_the_audit_floor`
#: fails if the glob derivation ever stops covering one of them — a renamed
#: directory would otherwise empty the scan and every boundary test would pass
#: for the wrong reason.
PINNED_CASE_PLANE_MODULES: frozenset[str] = frozenset(
    {
        # test_dependency_boundaries.CASE_PLANE_MODULES (6)
        "app.models.calculation",
        "app.models.capital",
        "app.models.financial",
        "app.services.calculations",
        "app.services.capital",
        "app.services.liquidity",
        # ...plus test_case_plane_boundary.CASE_PLANE_MODULES (3 more)
        "app.schemas.calculations",
        "app.schemas.capital",
        "app.schemas.liquidity",
        # ...plus test_case_plane_boundary.CASE_PLANE_OWNERS (13 more)
        "app.features.manage_capital",
        "app.features.review_liquidity",
        "app.features.run_calculations",
        "app.services.case_plane",
        "app.services.financial_canonical_edits",
        "app.services.financial_cash_flows",
        "app.services.financial_covenants",
        "app.services.financial_mapping.links",
        "app.services.financial_mapping.row_mapper",
        "app.services.financial_mapping.upserts",
        "app.services.financial_validation",
        "app.services.financial_validation_rules",
        "app.services.financial_workspace",
    }
)

#: Case-plane types. Named separately from the modules because a re-export
#: through the ``app.models`` aggregator would otherwise slip past a
#: module-path check.
CASE_PLANE_SYMBOLS: frozenset[str] = frozenset(
    {
        "CalculationRun",
        "CalculationForecastPeriod",
        "LiquidityAnalysisResult",
        "CapitalProjection",
        "CapitalProjectionFinding",
        "CapitalIndicator",
        "FinancialInstitution",
        "FinancialAccount",
        "FinancialReportingPeriod",
        "FinancialBalance",
        "FinancialCashFlow",
        "FinancialObligation",
        "FinancialCovenant",
        "FinancialSourceRow",
        "FinancialRecordSourceLink",
        "FinancialManualEditHistory",
        "FinancialValidationIssue",
    }
)

#: The model files each plane declares its tables in. Used to derive the table
#: names the raw-SQL check looks for, so a new model is covered on the day it
#: is written rather than when someone remembers to extend a list.
_CASE_MODEL_FILES: tuple[str, ...] = (
    "models/calculation.py",
    "models/capital.py",
    "models/financial.py",
)
_BANK_MODEL_FILES: tuple[str, ...] = (
    "models/canonical.py",
    "models/regulatory.py",
    "models/regulatory_run.py",
    "models/regulatory_reporting.py",
)


def module_name(path: Path) -> str:
    """Dotted module name for a file under ``app/``."""
    return ".".join(("app", *path.relative_to(APP).with_suffix("").parts))


def _package_of(module: str) -> str:
    """The package a relative import inside ``module`` resolves against.

    Mirrors ``__package__``: for a plain module it is the parent, for a package
    ``__init__`` it is the package itself.
    """
    if module.endswith(".__init__"):
        return module[: -len(".__init__")]
    return module.rpartition(".")[0]


def case_plane_files() -> list[Path]:
    found: set[Path] = set()
    for pattern in CASE_PLANE_GLOBS:
        found.update(path for path in APP.glob(pattern) if "__pycache__" not in path.parts)
    return sorted(found)


def case_plane_members() -> frozenset[str]:
    """Every module that IS the case plane, derived from ``CASE_PLANE_GLOBS``."""
    return frozenset(module_name(path) for path in case_plane_files())


def _declared_tables(model_files: tuple[str, ...]) -> frozenset[str]:
    """``__tablename__`` values declared by a plane's model modules."""
    tables: set[str] = set()
    for relative in model_files:
        tree = ast.parse((APP / relative).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            ):
                tables.add(node.value.value)
    return frozenset(tables)


CASE_PLANE_MEMBERS: frozenset[str] = case_plane_members()
CASE_PLANE_TABLES: frozenset[str] = _declared_tables(_CASE_MODEL_FILES)
BANK_PLANE_TABLES: frozenset[str] = _declared_tables(_BANK_MODEL_FILES)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

#: Callables that import by name at runtime. Only a LITERAL first argument is
#: resolvable; see ``imported_modules``.
_DYNAMIC_IMPORTERS: frozenset[str] = frozenset({"import_module", "__import__"})

#: A table named as the object of a SQL clause. Deliberately narrow: it must
#: follow FROM / JOIN / UPDATE / INTO, so prose that merely mentions a table
#: name is not a match.
_SQL_TABLE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:ONLY\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)


def string_value(node: ast.expr) -> str | None:
    """The static text of an expression, or ``None`` if it is not static.

    Folds ``"a" + "b"`` because SQL split across concatenated parts was a
    demonstrated evasion of the textual scanners. Adjacent string literals are
    already folded by the parser itself.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [string_value(part) for part in node.values]
        return "".join(part if part is not None else "{?}" for part in parts)
    if isinstance(node, ast.FormattedValue):
        return "{" + node.value.id + "}" if isinstance(node.value, ast.Name) else "{?}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = string_value(node.left), string_value(node.right)
        return None if left is None or right is None else left + right
    return None


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _keyword(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return string_value(keyword.value)
    return None


def _dynamic_import_targets(tree: ast.AST) -> set[str]:
    """Modules named in ``importlib.import_module(...)`` / ``__import__(...)``.

    LIMIT: only a literal (or literal-concatenated) name is resolvable. A name
    assembled at runtime — from a variable, a config value, or ``".".join(...)``
    — is invisible here and by construction cannot be caught by AST inspection.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in _DYNAMIC_IMPORTERS:
            continue
        if not node.args or (name := string_value(node.args[0])) is None:
            continue
        if name.startswith("."):
            package = _keyword(node, "package")
            if package is None:
                continue
            level = len(name) - len(name.lstrip("."))
            parts = package.split(".")
            base = parts[: len(parts) - (level - 1)] if level > 1 else parts
            name = ".".join([*base, *([name.lstrip(".")] if name.lstrip(".") else [])])
        targets.add(name)
    return targets


def imported_modules(source: str, *, module: str | None = None) -> set[str]:
    """Fully-qualified modules a file imports, at module or function scope.

    Covers ``import x``, ``from x import y`` (yielding both ``x`` and ``x.y``),
    RELATIVE ``from . import y`` / ``from ..z import y`` when ``module`` names
    the importing module, and ``importlib.import_module("x")`` with a literal
    argument.

    LIMITS, stated rather than implied:

    * a dynamic import whose name is assembled at runtime is not detectable;
    * relative imports are resolved only when ``module`` is supplied — callers
      that pass bare source (the negative-control tests) get absolute-only
      resolution;
    * this is per-file. Reachability through a chain of modules is covered by
      the CLOSURE rule in `test_case_plane_boundary.py`, not here.
    """
    tree = ast.parse(source)
    package = _package_of(module) if module else None
    modules: set[str] = set(_dynamic_import_targets(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            elif package is None:
                continue  # unresolvable without the importing module's identity
            else:
                parts = package.split(".")
                anchor = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if not base:
                continue
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def referenced_names(source: str) -> set[str]:
    """Every name the file binds or reaches for, ignoring string literals.

    Covers ``from app.models import X`` (ImportFrom), ``m.X`` after
    ``import app.models as m`` (Attribute), a bare ``X(...)`` (Name), and the
    LITERAL second argument of ``getattr(module, "X")`` — reflection on a known
    name was a demonstrated evasion.

    It deliberately does not look inside strings generally: naming a symbol in
    prose, or in a forbid-list such as `app/domain/authority/registry.py`, is
    not a dependency on it. ``getattr`` is the narrow exception because its
    second argument IS an attribute access.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif (
            isinstance(node, ast.Call)
            and _call_name(node) == "getattr"
            and len(node.args) >= 2
            and (attribute := string_value(node.args[1])) is not None
        ):
            names.add(attribute)
    return names


def literal_strings(tree: ast.AST) -> list[str]:
    """SQL-bearing strings in a module, docstrings excluded.

    Comments never reach the AST and docstrings are dropped explicitly, so
    prose like "the UPDATE sees zero rows" cannot be read as a statement.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            docstrings.add(id(first.value))

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if id(node) not in docstrings and isinstance(node.value, str):
                found.append(node.value)
        elif (
            isinstance(node, ast.JoinedStr)
            and (text := string_value(node)) is not None
            or (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add)
                and (text := string_value(node)) is not None
            )
        ):
            found.append(text)
    return found


def sql_table_references(source: str, tables: frozenset[str]) -> set[str]:
    """Tables from ``tables`` named as the object of a SQL clause in this file.

    Raw ``text("SELECT ... FROM calculation_runs")`` reaches case-plane state
    with no import at all, so an import-graph guard alone cannot see it.

    LIMIT: only statically-known SQL. A query assembled at runtime, or read
    from a file, is not visible.
    """
    referenced: set[str] = set()
    for statement in literal_strings(ast.parse(source)):
        referenced.update(match for match in _SQL_TABLE.findall(statement) if match in tables)
    return referenced
