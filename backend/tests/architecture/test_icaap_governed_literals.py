"""Guard: no regulatory number is written into ICAAP code (founder directive D-024).

"Don't hardcode any number but fetch from console." Every floor, buffer and cap
the ICAAP surfaces apply or print is a governed parameter resolved through
``app/services/regulatory_parameters.py`` — the rows staff edit in the operator
console. Numbers may live only in the seed catalogue (``SEED_PARAMETERS``) and a
migration's pinned seed rows.

This scans what the ICAAP work owns for the capital-regime values it used to
restate — 13 / 10 / 6.5 / 8 / 6 / 3 / 1.5 / 2 as a percentage floor, buffer or
cap:

* the pure stress builders and the capital-plan service, for ``Decimal`` literals
  of those values;
* the ICAAP generators in ``generation.py``, for the same;
* the rendered text of the ICAAP-facing templates, for any percentage literal;
* the ICAAP dashboard components, for a percentage literal in their source.

The frozen pre-P0 template text (``templates_legacy.py``, decision D-021) is the
one deliberate exception: it is historical wording that old packages re-export
byte-for-byte, never rendered for a new package.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from app.services.regulatory_reporting.registry import get_definition
from app.services.regulatory_reporting.templates import get_template

BACKEND = Path(__file__).parents[2]

#: The capital-regime values the ICAAP code used to restate as literals.
REGULATORY_VALUES = frozenset(Decimal(v) for v in ("13", "10", "6.5", "8", "6", "3", "1.5", "2"))

#: Whole modules the ICAAP work owns.
ICAAP_MODULES = (
    "app/domain/stress/appendix_ii.py",
    "app/domain/stress/management_actions.py",
    "app/services/capital_plan.py",
)

#: ICAAP functions inside the shared generator module.
GENERATION = "app/services/regulatory_reporting/generation.py"
GENERATION_FUNCTION = re.compile(
    r"^_(generate_icaap_stress|generate_attested_appendix2|generate_icaap_stress_appendix2"
    r"|appendix2_.*|pending_minimum_notes|minimum_basis|applied_minimum|governed_row"
    r"|weaker_minimum_findings|reverse_stress_.*|stress_frontier_rows)$"
)

#: Templates whose rendered text an ICAAP reader sees.
ICAAP_RETURNS = ("ICAAP-STRESS-APPENDIX2", "SDI-STRESS-ANNUAL", "ICAAP-STRESS", "CAR-RWA")

#: Percentages in template text that are not regulatory values: a worked example
#: of the fraction convention, and a column header's unit ("Tier 1 %").
ALLOWED_TEMPLATE_PERCENTAGES = ("(0.05 = 5%)", "Tier 1 %")

DASHBOARD = BACKEND / "dashboard"
ICAAP_COMPONENTS = (
    "components/basel/CapitalPlanProjection.tsx",
    "components/stress/AppendixIITables.tsx",
    "components/stress/SignoffPanel.tsx",
)

PERCENT_LITERAL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?%")


def _decimal_literals(node: ast.AST) -> list[tuple[int, Decimal]]:
    """Every ``Decimal("<number>")`` / ``Decimal(<number>)`` call under ``node``."""
    found: list[tuple[int, Decimal]] = []
    for call in ast.walk(node):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "Decimal"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
        ):
            continue
        try:
            found.append((call.lineno, Decimal(str(call.args[0].value))))
        except InvalidOperation:
            continue
    return found


@pytest.mark.parametrize("module", ICAAP_MODULES)
def test_icaap_modules_restate_no_regulatory_value(module: str) -> None:
    tree = ast.parse((BACKEND / module).read_text(encoding="utf-8"))
    offending = [
        (line, str(value)) for line, value in _decimal_literals(tree) if value in REGULATORY_VALUES
    ]
    assert not offending, (
        f"{module} restates a regulatory value as a literal {offending}; resolve the "
        "governed parameter through app/services/regulatory_parameters.py (D-024)"
    )


def test_icaap_generators_restate_no_regulatory_value() -> None:
    tree = ast.parse((BACKEND / GENERATION).read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and GENERATION_FUNCTION.match(node.name)
    ]
    assert len(functions) >= 10, "the ICAAP generator functions were renamed; update the guard"
    offending = [
        (function.name, line, str(value))
        for function in functions
        for line, value in _decimal_literals(function)
        if value in REGULATORY_VALUES
    ]
    assert not offending, offending


def _strings(obj: object, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for item in dataclasses.fields(obj):
            _strings(getattr(obj, item.name), out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _strings(item, out)


@pytest.mark.parametrize("return_code", ICAAP_RETURNS)
def test_icaap_template_text_prints_no_percentage_literal(return_code: str) -> None:
    definition = get_definition(return_code)
    assert definition is not None
    template = get_template(definition.template_id)
    assert template is not None
    offending = _template_percentages(template)
    assert not offending, (
        f"{return_code} template text prints {offending}; state the governed value "
        "through the report notes instead of writing it into the template (D-024)"
    )


def _template_percentages(template: object) -> list[str]:
    texts: list[str] = []
    _strings(template, texts)
    found: list[str] = []
    for text in texts:
        cleaned = text
        for allowed in ALLOWED_TEMPLATE_PERCENTAGES:
            cleaned = cleaned.replace(allowed, "")
        found += [match.group(0) for match in PERCENT_LITERAL.finditer(cleaned)]
    return found


def test_the_template_scan_sees_a_literal_where_one_exists() -> None:
    """Not vacuous: the frozen pre-P0 text (D-021) still carries the literals the
    current templates dropped, and the same scan finds them there."""
    from app.services.regulatory_reporting.templates_legacy import (  # noqa: PLC0415
        LEGACY_TEMPLATES,
    )

    assert "13%" in _template_percentages(LEGACY_TEMPLATES["bog-icaap-stress-appendix2-v1"])
    assert "1.5%" in _template_percentages(LEGACY_TEMPLATES["bog-bsd2-capital-v1"])


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


@pytest.mark.parametrize("component", ICAAP_COMPONENTS)
def test_icaap_components_print_no_regulatory_percentage(component: str) -> None:
    source = _without_comments((DASHBOARD / component).read_text(encoding="utf-8"))
    offending = [
        match.group(0)
        for match in PERCENT_LITERAL.finditer(source)
        if Decimal(match.group(1)) in REGULATORY_VALUES
    ]
    assert not offending, f"{component} prints {offending} (D-024)"
