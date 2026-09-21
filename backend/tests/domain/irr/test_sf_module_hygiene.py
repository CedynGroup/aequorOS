"""The Standardised Framework modules carry no values and touch no neighbours.

Two guards, each replacing a class of mistake that reads as harmless in review.

**No regulatory number in code (D-024).** A shock size, a cap or a threshold
written into an engine is a value no operator can correct and no reviewer can
find. The only numerics these modules may contain are conventions — zero and
one, a pair, months in a year, days in a year, per cent and basis points — plus
a short list of structural constants that each say why they are not regulatory.
Every calibration must arrive from the control plane.

**Isolation from the legacy engine (design §1.7).** ``app/domain/irr/engine.py``
computes the returns banks have already filed. The Standardised Framework is a
second engine, deliberately built alongside rather than folded in, and an
import either way would let a change to one move the other's numbers. The
import graph is checked rather than trusted.

**No country in the code.** The framework's calibration differs by currency,
and which currency is which is DATA. A currency code, regulator or country name
appearing in these modules would be a jurisdiction the control plane cannot
change.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[3]
IRR = BACKEND / "app" / "domain" / "irr"
SF_MODULES: tuple[str, ...] = (
    "standardised.py",
    "standardised_params.py",
    "standardised_cash_flows.py",
)
LEGACY = "engine.py"

#: Conventions, not calibrations: identity and emptiness, a pair, the calendar,
#: per cent and basis points.
CONVENTIONS: frozenset[int] = frozenset({0, 1, 2, 12, 100, 365, 10000})

#: Structural constants, each with the reason it is not a regulatory value.
#: Adding a line here is a review decision, not a workaround.
STRUCTURAL: dict[str, dict[int | str, str]] = {
    "standardised.py": {
        34: "Decimal working precision for the exponentials and survival powers",
        "0.000001": "reported precision, six decimal places — a presentation quantum",
    },
}

#: Country, regulator and currency identity.
IDENTITY = re.compile(
    r"\b("
    r"GH|GHS|NG|NGN|KE|KES|ZA|ZAR|USD|EUR|GBP|CNY|JPY"
    r"|BoG|BOG|CBN|CBK|SARB|Basel"
    r"|Bank of Ghana|Central Bank of Nigeria|Central Bank of Kenya"
    r"|Ghana|Ghanaian|Nigeria|Nigerian|Kenya|Kenyan"
    r"|cedi|cedis|naira|shilling"
    r")\b"
)


def _tree(name: str) -> ast.AST:
    return ast.parse((IRR / name).read_text())


def _numeric_literals(tree: ast.AST) -> list[tuple[int, int | float]]:
    found: list[tuple[int, int | float]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        if isinstance(node.value, (int, float)):
            found.append((node.lineno, node.value))
    return found


def _decimal_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Every ``Decimal("…")`` argument — where a value would actually hide."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Decimal":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant):
                found.append((node.lineno, str(argument.value)))
    return found


@pytest.mark.parametrize("name", SF_MODULES)
def test_no_regulatory_number_is_written_into_the_engine(name: str) -> None:
    allowed = STRUCTURAL.get(name, {})
    offenders = [
        (line, value)
        for line, value in _numeric_literals(_tree(name))
        if value not in CONVENTIONS and value not in allowed
    ]

    assert offenders == [], f"{name} carries ungoverned numbers at {offenders}"


@pytest.mark.parametrize("name", SF_MODULES)
def test_no_value_hides_inside_a_decimal_constructor(name: str) -> None:
    allowed = STRUCTURAL.get(name, {})
    offenders: list[tuple[int, str]] = []
    for line, text in _decimal_strings(_tree(name)):
        if text in allowed:
            continue
        try:
            numeric = int(text)
        except ValueError:
            offenders.append((line, text))
            continue
        if numeric not in CONVENTIONS:
            offenders.append((line, text))

    assert offenders == [], f"{name} carries ungoverned values at {offenders}"


def test_every_structural_exemption_says_why_it_is_not_regulatory() -> None:
    for name, entries in STRUCTURAL.items():
        for value, reason in entries.items():
            assert len(reason) > len(str(value)), f"{name}: {value} has no reason"


def test_the_guard_would_catch_a_shock_size_written_into_the_engine() -> None:
    """Proof the guard reports rather than merely being present."""
    tree = ast.parse('SHOCK = Decimal("450")\nCAP = 90\n')

    numbers = [value for _, value in _numeric_literals(tree) if value not in CONVENTIONS]
    values = [text for _, text in _decimal_strings(tree)]

    assert numbers == [90]
    assert values == ["450"]


# --- isolation from the engine that files today's returns --------------------


def _imported(name: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


@pytest.mark.parametrize("name", SF_MODULES)
def test_the_standardised_framework_never_reaches_into_the_legacy_engine(name: str) -> None:
    offenders = {
        module for module in _imported(name) if module.endswith("irr.engine")
    }

    assert offenders == set(), f"{name} imports the legacy engine: {offenders}"


def test_the_legacy_engine_never_reaches_into_the_standardised_framework() -> None:
    """Its goldens are filed numbers; nothing new may be able to move them."""
    offenders = {module for module in _imported(LEGACY) if "standardised" in module}

    assert offenders == set(), f"the legacy engine imports {offenders}"


@pytest.mark.parametrize("name", (*SF_MODULES, LEGACY))
def test_the_irr_domain_stays_pure(name: str) -> None:
    """Restated locally so a domain-only run still proves it."""
    stateful = {
        module
        for module in _imported(name)
        if module.startswith(("app.services", "app.models", "app.api", "app.features"))
    }

    assert stateful == set(), f"{name} imports application state: {stateful}"


@pytest.mark.parametrize("name", SF_MODULES)
def test_no_country_or_currency_is_named_in_the_engine(name: str) -> None:
    """Only string literals and identifiers: prose explains, it does not print."""
    tree = _tree(name)
    docstrings = {
        ast.get_docstring(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
    }
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            if match := IDENTITY.search(node.value):
                offenders.append((node.lineno, match.group(0)))

    assert offenders == [], f"{name} names a jurisdiction at {offenders}"
