"""The regulatory plane must never depend on the legacy case plane (ARCH-5).

`docs/forensic_calculation_audit_2026-08-21.md` documents two parallel systems
that share vocabulary and share nothing else:

* the BANK-scoped regulatory plane — `BankFinancialFact` -> the pure engines in
  `app/domain/**` -> `RegulatoryRun` -> BoG returns, filed and signed; and
* the CASE-scoped financial workspace — `Financial*` records -> `CalculationRun`
  -> `CapitalProjection` / case liquidity findings, advisory and never filed.

Both compute things called "liquidity" and "capital" from different inputs with
different formulas. A single import across the line is enough to put an
advisory number inside a regulatory return, and that defect is invisible in
review precisely because the names match. These are import-graph tests, so they
catch the dependency at the moment it is written rather than when a filed
number turns out wrong.

Scanning is by AST, so the string literals in `app/domain/authority/registry.py`
— which NAME the case plane in order to forbid it — are correctly not
dependencies.

What counts as "the case plane" comes from `tests/architecture/_planes.py`,
shared with `test_case_plane_boundary.py` (CF-4). This file used to keep its own
six-module list; CF-4 kept a nine-module list and a SIXTEEN-module owner list,
and the divergence meant an ordinary absolute import of a case-plane service
passed both guards. The residual limits of the scanning — no import-graph
traversal, dynamic imports only when the name is literal, raw SQL only when
static — are stated in full in `test_case_plane_boundary.py`'s module docstring
and on the helpers themselves.
"""

from __future__ import annotations

from pathlib import Path

from tests.architecture import test_case_plane_boundary as case_plane
from tests.architecture._planes import (
    APP,
    CASE_PLANE_MEMBERS,
    CASE_PLANE_SYMBOLS,
    CASE_PLANE_TABLES,
    imported_modules,
    referenced_names,
    sql_table_references,
)
from tests.architecture._planes import (
    module_name as _module_name,
)

#: Modules that own case-scoped, advisory, never-filed calculation state.
#: Derived — see `_planes.CASE_PLANE_GLOBS`. Shared with CF-4 so the two guards
#: cannot disagree about what the case plane is, which is exactly how the hole
#: this replaces was opened.
CASE_PLANE_MODULES: frozenset[str] = CASE_PLANE_MEMBERS

#: Everything that produces or seals a filed regulatory number.
REGULATORY_PLANE: tuple[str, ...] = (
    "domain",
    "services/regulatory_reporting",
    "services/regulatory_capital.py",
    "services/regulatory_liquidity.py",
    "services/regulatory_irr.py",
    "services/regulatory_fx.py",
    "services/regulatory_ftp.py",
    "services/regulatory_forecasting.py",
    "services/regulatory_parameters.py",
    "services/sdi_capital.py",
    "services/sdi_capital_assurance.py",
    "services/sdi_capital_checks.py",
    "services/fact_derivation.py",
    "services/pipeline.py",
    "services/enterprise_stress.py",
    "services/reverse_stress.py",
)


def _python_files(relative: str) -> list[Path]:
    target = APP / relative
    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*.py") if "__pycache__" not in path.parts)


def case_plane_dependencies(source: str, module: str | None = None) -> set[str]:
    """Every reference this file makes to the case-scoped plane.

    Module paths (absolute, relative, or literal dynamic imports), case-plane
    symbols (including `getattr` reflection), and case-plane tables named in raw
    SQL. Pass ``module`` — the dotted name of the file — or relative imports
    cannot be resolved against its package.
    """
    offending = {
        imported
        for imported in imported_modules(source, module=module)
        if any(imported == case or imported.startswith(f"{case}.") for case in CASE_PLANE_MODULES)
    }
    return (
        offending
        | (referenced_names(source) & CASE_PLANE_SYMBOLS)
        | sql_table_references(source, CASE_PLANE_TABLES)
    )


def _scan(paths: list[Path]) -> dict[str, set[str]]:
    scanned: dict[str, set[str]] = {}
    for path in paths:
        module = _module_name(path)
        if dependencies := case_plane_dependencies(path.read_text(), module):
            scanned[module] = dependencies
    return scanned


def test_regulatory_reporting_never_reads_a_case_calculation_run() -> None:
    """A filed return must be traceable to ``RegulatoryRun``, only.

    ``CalculationRun`` snapshots case-local balances and scenario assumptions
    that no examiner has ever seen and that carry no ``input_hash`` lineage into
    the regulatory spine.
    """
    offenders = _scan(_python_files("services/regulatory_reporting"))

    assert offenders == {}, f"Regulatory reporting reached into the case plane: {offenders}"


def test_the_official_bank_run_never_reads_case_financial_records() -> None:
    """Official runs derive from ``BankFinancialFact`` and nothing else.

    ``Financial*`` rows belong to a risk case: they are hand-editable, are not
    ingestion-traced, and are not part of any ``input_hash``. Reading one would
    make a sealed run unreproducible.
    """
    paths = [
        path
        for relative in REGULATORY_PLANE
        if relative != "services/regulatory_reporting"
        for path in _python_files(relative)
    ]

    assert _scan(paths) == {}, f"Official-run path reached into the case plane: {_scan(paths)}"


def test_the_pure_domain_layer_imports_no_application_state() -> None:
    """`app/domain/**` is pure by contract (CLAUDE.md) — it is what the golden
    suites pin and what a second product segment will reuse."""
    impure: dict[str, set[str]] = {}
    for path in _python_files("domain"):
        module = _module_name(path)
        # Module-aware: `app/domain/**` carries relative imports, and a purity
        # check that cannot see them is a purity check with a hole in it.
        modules = imported_modules(path.read_text(), module=module)
        stateful = {
            module
            for module in modules
            if module.startswith(("app.services", "app.models", "app.api", "app.features"))
        }
        if stateful:
            impure[module] = stateful

    assert impure == {}, f"app/domain must stay pure: {impure}"


def test_the_boundary_scanner_catches_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the guard reports rather than merely being present."""
    violation = '''"""A regulatory module that reaches into the case plane.

    Mentioning CalculationRun in prose must not count.
    """

from app.models.calculation import CalculationRun
from app.services.calculations import calculate_forecast


def build() -> None:
    _ = (CalculationRun, calculate_forecast)
'''

    found = case_plane_dependencies(violation)

    assert "app.models.calculation" in found
    assert "app.services.calculations" in found
    assert "CalculationRun" in found


def test_naming_the_case_plane_in_order_to_forbid_it_is_not_a_dependency() -> None:
    """`app/domain/authority/registry.py` lists the forbidden symbols as data.

    A grep-based guard would convict it; an import-graph guard must not.
    """
    registry = (APP / "domain" / "authority" / "registry.py").read_text()

    assert "app.models.calculation:CalculationRun" in registry
    assert case_plane_dependencies(registry) == set()


def test_the_scanner_catches_the_evasions_the_2026_08_22_audit_demonstrated() -> None:
    """Each of these passed the previous rule. Named individually so a
    regression says which door re-opened.

    The old rule was `isinstance(node, ast.ImportFrom) and node.module and
    node.level == 0` over a six-module list, plus a symbol check that ignored
    reflection and never looked at SQL.
    """
    evasions = {
        "relative level=1": (
            "from .calculations import calculate_forecast\n",
            "app.services.regulatory_capital",
        ),
        "relative level=2": (
            "from ..models.capital import CapitalProjection\n",
            "app.services.regulatory_liquidity",
        ),
        "importlib literal": (
            'import importlib\n\nm = importlib.import_module("app.services.liquidity")\n',
            None,
        ),
        "__import__ literal": ('m = __import__("app.models.financial")\n', None),
        "assembled from literal parts": (
            'import importlib\n\nm = importlib.import_module("app.models" + ".calculation")\n',
            None,
        ),
        "getattr reflection": (
            'import app.models as m\n\nrun = getattr(m, "CalculationRun")\n',
            None,
        ),
        "raw SQL, no import at all": (
            'rows = db.execute(text("SELECT id FROM calculation_runs")).all()\n',
            None,
        ),
        "case-plane service the old forbidden set omitted": (
            "from app.services.case_plane import compute_headroom\n",
            None,
        ),
        "case-plane feature the old forbidden set omitted": (
            "from app.services.financial_workspace import load_workspace\n",
            None,
        ),
    }

    missed = [
        label
        for label, (source, module) in evasions.items()
        if not case_plane_dependencies(source, module)
    ]

    assert missed == [], f"These evasions still walk through the guard: {missed}"


def test_the_two_guards_share_one_definition_of_the_case_plane() -> None:
    """ARCH-5 and CF-4 must not be able to disagree again.

    They kept separate lists until 2026-08-22 — six modules here, nine plus a
    sixteen-module owner list there. Anything named by one and not the other was
    a gap, and the gap was invisible because each list read as reasonable on its
    own page.
    """
    assert CASE_PLANE_MODULES is case_plane.CASE_PLANE_MODULES
    assert CASE_PLANE_MODULES is case_plane.CASE_PLANE_OWNERS
