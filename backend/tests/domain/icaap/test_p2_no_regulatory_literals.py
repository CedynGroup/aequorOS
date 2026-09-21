"""No Pillar 2 method states a regulatory or methodological number (D-024).

The founder's rule is that every threshold, band, coefficient, shock, severity
and haircut is a row staff edit in the console. A literal in a method would
mean a release to change a calibration, and — worse — two sources of truth for
the same figure.

This is a stricter scan than the architecture-wide ICAAP guard, because the
risk here is concentrated: these modules exist to apply numbers. It rejects
numeric STRINGS as well as numeric literals, so ``Decimal("13")`` cannot sneak
a rate past a scan that only looked at ``int`` and ``float``.

The only exemption is a short, reviewed table of STRUCTURAL constants — the
base of a percentage, the engine's rounding quanta, the 2 in the Gini formula.
Each entry is asserted to still exist with that exact value, so the table
cannot rot into a blanket allowance.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[3]
OWNED = (
    "app/domain/icaap/units.py",
    "app/domain/icaap/materiality.py",
    "app/domain/icaap/appetite.py",
    "app/domain/icaap/allocation.py",
    "app/domain/icaap/reconciliation.py",
    "app/domain/icaap/triggers.py",
)
PILLAR2 = "app/domain/icaap/pillar2"

#: Identity and emptiness. Everything else is either a governed value or a
#: reviewed structural constant.
TRIVIAL = frozenset({0, 1})

#: (relative path, name) -> (literal as written, why it is not regulatory).
STRUCTURAL_CONSTANTS: dict[tuple[str, str], tuple[str, str]] = {
    ("app/domain/icaap/units.py", "HUNDRED"): ("100", "the base of a percentage"),
    ("app/domain/icaap/units.py", "AMOUNT_QUANTUM"): ("0.0001", "the engine's money precision"),
    ("app/domain/icaap/units.py", "RATIO_QUANTUM"): ("0.000001", "the engine's ratio precision"),
    ("app/domain/icaap/pillar2/concentration_metrics.py", "TWO"): (
        "2",
        "the factor in the Gini rank formula",
    ),
    ("app/domain/icaap/pillar2/concentration_metrics.py", "PAIR"): (
        "2",
        "Gini needs two exposures to compare",
    ),
}

NUMERIC_STRING = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _scanned_files() -> list[Path]:
    files = [BACKEND / relative for relative in OWNED]
    files.extend(
        sorted(
            path for path in (BACKEND / PILLAR2).rglob("*.py") if "__pycache__" not in path.parts
        )
    )
    return files


SCANNED = _scanned_files()
IDS = [str(path.relative_to(BACKEND)) for path in SCANNED]


def _docstrings(tree: ast.AST) -> set[int]:
    """The id() of every node that is a docstring, so copy is not scanned."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(id(first))
    return found


def _exempt_nodes(tree: ast.AST, relative: str) -> set[int]:
    """Module-level ``NAME = <literal>`` assignments in the reviewed table."""
    exempt: set[int] = set()
    module = tree if isinstance(tree, ast.Module) else None
    if module is None:  # pragma: no cover - always a module here
        return exempt
    for statement in module.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign):
            targets, value = list(statement.targets), statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        if value is None:
            continue
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names or (relative, names[0]) not in STRUCTURAL_CONSTANTS:
            continue
        for node in ast.walk(value):
            exempt.add(id(node))
    return exempt


def violations(source: str, relative: str = "") -> list[tuple[int, str]]:
    """Every numeric literal, and every numeric string, that is not allowed."""
    tree = ast.parse(source)
    skip = _docstrings(tree) | _exempt_nodes(tree, relative)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        value = node.value
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float | complex) and (
            isinstance(value, complex) or value not in TRIVIAL
        ):
            found.append((node.lineno, repr(value)))
        elif (
            isinstance(value, str)
            and NUMERIC_STRING.match(value.strip())
            and float(value) not in TRIVIAL
        ):
            found.append((node.lineno, value))
    return found


@pytest.mark.parametrize("path", SCANNED, ids=IDS)
def test_no_p2_domain_module_states_a_regulatory_number(path: Path) -> None:
    relative = str(path.relative_to(BACKEND))
    offending = violations(path.read_text(encoding="utf-8"), relative)
    assert not offending, (
        f"{relative} states {offending}. Every threshold, band, coefficient, shock, "
        "severity and haircut is a governed parameter passed in by the service "
        "(D-024); a structural constant belongs in STRUCTURAL_CONSTANTS with its "
        "reason."
    )


def test_every_registered_structural_constant_still_exists_with_that_value() -> None:
    """An allowlist nobody re-checks becomes a blanket exemption."""
    for (relative, name), (literal, reason) in STRUCTURAL_CONSTANTS.items():
        assert reason
        tree = ast.parse((BACKEND / relative).read_text(encoding="utf-8"))
        found: list[str] = []
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                continue
            for node in ast.walk(statement.value):
                if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
                    found.append(str(node.value))
        assert found == [literal], f"{relative}:{name} is no longer {literal}"


def test_the_scan_catches_what_it_is_for() -> None:
    source = (
        'MINIMUM = Decimal("13")\n'
        "def charge(x):\n"
        '    """A docstring may explain a 13% minimum."""\n'
        "    top = x[:20]\n"
        "    for _ in range(3):\n"
        "        top = top * 0.5\n"
        '    return top, "450", -1\n'
    )
    found = {text for _line, text in violations(source)}
    assert "13" in found
    assert "0.5" in found
    assert "450" in found
    assert "20" in found
    assert "3" in found
    assert "13%" not in found


def test_the_scan_passes_clean_code() -> None:
    source = (
        "ZERO = 0\n"
        "def charge(value, governed_rate):\n"
        '    """Nothing regulatory here, and -1 means the last one."""\n'
        "    return value * governed_rate, value[-1]\n"
    )
    assert violations(source) == []
