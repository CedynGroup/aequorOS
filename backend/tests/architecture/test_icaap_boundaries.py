"""The ICAAP code may not read the live plane, and its domain layer stays pure.

The live plane (`live_metrics`, `live_findings`, the live fact period) is a
monitoring surface: the worker recomputes it continuously, on whatever code its
process happens to be running. A figure in a Board-approved report has to be
one that was computed once, reviewed, and can be pointed at afterwards — a
sealed run, an attested sign-off, an approved plan, a register digest.

This is an import-graph test rather than a behaviour test on purpose: it fails
the moment the dependency is WRITTEN, not months later when a filed number
turns out to have been recomputed under somebody's feet.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.icaap.frameworks import registry

BACKEND = Path(__file__).parents[2]
#: The whole ICAAP surface, not just its two packages. The route layer is
#: precisely where someone would wire ``live-summary`` into a readiness panel to
#: make a number appear, and both AI planes feed ICAAP text; until 2026-09-20 the
#: guard scanned two of the five (architecture audit M6), so its docstring's
#: promise to fail "the moment the dependency is WRITTEN" did not hold there.
SCANNED = (
    "app/domain/icaap",
    "app/services/icaap",
    "app/domain/ai",
    "app/services/ai",
)
#: Route modules live beside unrelated features, so they are named rather than
#: swept by directory.
SCANNED_FILES = tuple(
    sorted(
        str(path.relative_to(BACKEND))
        for path in (BACKEND / "app/features").glob("*.py")
        if "icaap" in path.name or path.name == "manage_ai_settings.py"
    )
)

#: Modules that hold or serve continuously-recomputed state.
FORBIDDEN_MODULES = (
    "app.services.live_block",
    "app.services.live_state",
    "app.services.live_view",
    "app.models.live",
)
#: Functions that would reach the live plane (or re-derive facts) indirectly.
FORBIDDEN_CALLS = frozenset(
    {
        "live_block",
        "get_capital_dashboard",
        "derive_facts",
        "derive_current_facts",
        "load_current_facts",
        "current_fact_period_or_409",
    }
)
FORBIDDEN_NAMES = frozenset({"LiveMetric", "LiveFinding"})
#: Pure domain may not import application state at all.
STATEFUL_PREFIXES = ("app.services", "app.models", "app.api", "app.features")


def _python_files(relative: str) -> list[Path]:
    root = BACKEND / relative
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND).with_suffix("").parts)


def _imported_modules(tree: ast.AST, module: str) -> set[str]:
    found: set[str] = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                root = f"{base}.{node.module}" if node.module else base
            else:
                root = node.module or ""
            found.add(root)
            found.update(f"{root}.{alias.name}" for alias in node.names)
    return found


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


ICAAP_FILES = [path for relative in SCANNED for path in _python_files(relative)] + [
    BACKEND / relative for relative in SCANNED_FILES
]
FILE_IDS = [str(path.relative_to(BACKEND)) for path in ICAAP_FILES]


@pytest.mark.parametrize("path", ICAAP_FILES, ids=FILE_IDS)
def test_no_icaap_module_imports_the_live_plane(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = _imported_modules(tree, _module_name(path))
    offending = sorted(
        name
        for name in imported
        if any(name == module or name.startswith(f"{module}.") for module in FORBIDDEN_MODULES)
    )
    assert not offending, f"{path.relative_to(BACKEND)} imports the live plane: {offending}"


@pytest.mark.parametrize("path", ICAAP_FILES, ids=FILE_IDS)
def test_no_icaap_module_calls_a_live_or_re_deriving_seam(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offending = sorted(_called_names(tree) & FORBIDDEN_CALLS)
    assert not offending, f"{path.relative_to(BACKEND)} calls {offending}"


@pytest.mark.parametrize("path", ICAAP_FILES, ids=FILE_IDS)
def test_no_icaap_module_names_a_live_model(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    referenced |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    offending = sorted(referenced & FORBIDDEN_NAMES)
    assert not offending, f"{path.relative_to(BACKEND)} names {offending}"


DOMAIN_FILES = _python_files("app/domain/icaap")
DOMAIN_IDS = [str(path.relative_to(BACKEND)) for path in DOMAIN_FILES]


@pytest.mark.parametrize("path", DOMAIN_FILES, ids=DOMAIN_IDS)
def test_the_icaap_domain_layer_holds_no_application_state(path: Path) -> None:
    """``app/domain/**`` is pure by contract, and this package is no exception."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = _imported_modules(tree, _module_name(path))
    stateful = sorted(name for name in imported if name.startswith(STATEFUL_PREFIXES))
    assert not stateful, f"{path.relative_to(BACKEND)} imports {stateful}"


def test_the_domain_package_reads_its_data_from_its_own_directory() -> None:
    """The framework JSON and the editor schema ship inside the image."""
    assert registry.FRAMEWORK_ROOT.is_dir()
    assert list(registry.FRAMEWORK_ROOT.glob("*/*.json")), "no framework is published"
    assert (BACKEND / "app/domain/icaap/editor_schema.json").is_file()


def test_the_scanner_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the guard reports rather than merely being present."""
    violation = tmp_path / "leaky.py"
    violation.write_text(
        "from app.services.live_block import live_block\n\n\n"
        "def read():\n    return live_block()\n",
        encoding="utf-8",
    )
    tree = ast.parse(violation.read_text(encoding="utf-8"))
    imported = _imported_modules(tree, "app.services.icaap.leaky")
    assert any(name.startswith("app.services.live_block") for name in imported)
    assert _called_names(tree) & FORBIDDEN_CALLS


# ---------------------------------------------------------------------------
# The parameter PLANE (D-078 residual; architecture audit M4)
# ---------------------------------------------------------------------------
#
# ``RegulatoryRun.parameter_provenance`` is drained from an ambient
# session-scoped ledger at the moment a run row is built, so a governed-row read
# taken anywhere in that session is credited to whichever run seals next. The
# ICAAP workspace is a REPORT plane: it seals no run and writes its own
# governed-row record onto the frozen snapshot. Every ICAAP read therefore goes
# through ``app/services/icaap/parameters.py``, which passes ``record=False``.
#
# This replaces the invariant "no ICAAP request session ever seals a
# ``RegulatoryRun``", which nothing stated and nothing tested, and which a
# "run the engine now" action on an ICAAP block would break in one line.

#: The one ICAAP module permitted to speak to the control plane directly.
PARAMETER_DOOR = "app/services/icaap/parameters.py"
#: Reads that would enter the ambient ledger unless the plane is stated.
LEDGER_ENTRY_POINTS = frozenset({"try_resolve", "resolve", "resolve_class_value", "seed_values"})

PLANE_FILES = [path for path in ICAAP_FILES if str(path.relative_to(BACKEND)) != PARAMETER_DOOR]
PLANE_IDS = [str(path.relative_to(BACKEND)) for path in PLANE_FILES]


def _module_qualified_calls(tree: ast.AST, module: str) -> set[str]:
    """``module.attr(...)`` call targets, as ``attr``. Bare names are ignored —
    a module's own helper of the same name is not this module's."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == module
        ):
            found.add(target.attr)
    return found


@pytest.mark.parametrize("path", PLANE_FILES, ids=PLANE_IDS)
def test_icaap_reads_governed_parameters_only_through_its_own_door(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offending = sorted(_module_qualified_calls(tree, "regulatory_parameters") & LEDGER_ENTRY_POINTS)
    assert not offending, (
        f"{path.relative_to(BACKEND)} calls regulatory_parameters.{offending} directly. "
        f"The ICAAP report plane seals no RegulatoryRun, so its reads must not enter the "
        f"session consumption ledger — go through app.services.icaap.parameters, which "
        f"passes record=False."
    )


@pytest.mark.parametrize("path", PLANE_FILES, ids=PLANE_IDS)
def test_an_icaap_prefetched_resolver_declares_the_report_plane(path: Path) -> None:
    """``PrefetchedParameterResolver.load`` requires ``record=``; ICAAP says False."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "load"):
            continue
        if not (
            isinstance(target.value, ast.Attribute)
            and target.value.attr == "PrefetchedParameterResolver"
        ):
            continue
        record = next((kw for kw in node.keywords if kw.arg == "record"), None)
        assert record is not None, (
            f"{path.relative_to(BACKEND)}:{node.lineno} loads a resolver without stating "
            f"its plane."
        )
        assert isinstance(record.value, ast.Constant) and record.value.value is False, (
            f"{path.relative_to(BACKEND)}:{node.lineno} loads a RECORDING resolver. The "
            f"ICAAP plane seals no run, so the rows it resolves are nobody's run "
            f"provenance — pass record=False."
        )


def test_the_plane_scanner_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the two guards above report rather than merely being present."""
    violation = tmp_path / "leaky_params.py"
    violation.write_text(
        "from app.services import regulatory_parameters\n\n\n"
        "def read(db, bank):\n"
        "    resolver = regulatory_parameters.PrefetchedParameterResolver.load(\n"
        "        db, bank, as_of_dates=[], record=True\n"
        "    )\n"
        "    return resolver, regulatory_parameters.try_resolve(db, bank, 'x')\n",
        encoding="utf-8",
    )
    tree = ast.parse(violation.read_text(encoding="utf-8"))
    assert _module_qualified_calls(tree, "regulatory_parameters") & LEDGER_ENTRY_POINTS
    loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
    ]
    assert loads
    record = next(kw for kw in loads[0].keywords if kw.arg == "record")
    assert isinstance(record.value, ast.Constant) and record.value.value is True


# --- D-038: one FX Pillar 2 definition across the filed document -------------


#: The FX outcome fields the retired overlay formula read. Table 5's
#: ``country_and_fx`` was ``Tier 1 × max(stressed NOP% − base NOP%, 0)``, the
#: change in a RATIO; the ICAAP register used the revaluation loss. Both
#: appeared in one filed ICAAP and the source-consistency control compared
#: them, so a preparer had to explain a discrepancy the platform created
#: (audit W3). D-038 recorded the extraction as done when it was not, which is
#: why this is a test rather than a note.
_RETIRED_FX_OVERLAY_FIELDS = ("stressed_nop_pct_tier1", "base_nop_pct_tier1")

_FX_OVERLAY_OWNERS = (
    "app/services/enterprise_stress.py",
    "app/domain/stress/appendix_ii.py",
)


@pytest.mark.parametrize("relative", _FX_OVERLAY_OWNERS)
def test_the_stress_overlay_does_not_restate_the_fx_addon(relative: str) -> None:
    source = (BACKEND / relative).read_text(encoding="utf-8")
    tree = ast.parse(source)
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _RETIRED_FX_OVERLAY_FIELDS
    }
    assert read == set(), (
        f"{relative} computes an FX figure from NOP percentages. The Pillar 2 FX "
        f"add-on has one definition (app/domain/icaap/pillar2/fx.py); the overlay "
        f"reads FxOutcome.pillar2_addon."
    )


def test_the_stress_orchestrator_computes_the_fx_addon_with_the_icaap_function() -> None:
    """The positive half: D-038 is true in code, not only in the decision log."""
    source = (BACKEND / "app/domain/stress/orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = _imported_modules(tree, "app.domain.stress.orchestrator")

    assert "app.domain.icaap.pillar2.fx" in imported, (
        "the enterprise stress orchestrator must derive Table 5's country_and_fx "
        "from the ICAAP Pillar 2 FX method (D-038)"
    )
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "fx_revaluation_addon" in called


def test_the_fx_guard_convicts_the_retired_overlay() -> None:
    """Proof the guard above reports rather than merely being present.

    This is the overlay body verbatim as it stood until 2026-09-20.
    """
    retired = (
        "def _pillar2_overlay(outcome, tier1, horizon_years):\n"
        "    if outcome.fx is not None:\n"
        "        nop_uplift = max(\n"
        "            outcome.fx.stressed_nop_pct_tier1 - outcome.fx.base_nop_pct_tier1, _ZERO\n"
        "        )\n"
        "        country_fx = thousands(tier1 * nop_uplift / _HUNDRED)\n"
        "    return country_fx\n"
    )
    tree = ast.parse(retired)
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _RETIRED_FX_OVERLAY_FIELDS
    }
    assert read == set(_RETIRED_FX_OVERLAY_FIELDS)
