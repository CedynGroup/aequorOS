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
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

import pytest

from app.domain.stress.appendix_ii import build_appendix_ii
from app.domain.stress.management_actions import RecognitionCaps
from app.domain.stress.orchestrator import EnterpriseStressOutcome
from app.domain.stress.projection import EnterpriseProjection
from app.domain.stress.translation import MacroPathPoint
from app.fx.domain.engine import FxPosition
from app.icaap.domain.frameworks import registry
from app.services import enterprise_stress
from tests.domain import test_stress_appendix_ii, test_stress_orchestrator
from tests.domain.stress_fixtures import severe_paths


class _AppendixFixtures(Protocol):
    _RECOGNITION_CAPS: RecognitionCaps

    def _projection(self, paths: Sequence[MacroPathPoint]) -> EnterpriseProjection: ...


class _FxFixtures(Protocol):
    def _fx_positions(self) -> tuple[FxPosition, ...]: ...

    def _short_usd(self) -> tuple[FxPosition, ...]: ...

    def _fx_run(self, positions: Sequence[FxPosition]) -> EnterpriseStressOutcome: ...


# Type only the fixture calls used here; their legacy helpers remain unchanged.
APPENDIX_FIXTURES = cast(_AppendixFixtures, test_stress_appendix_ii)
FX_FIXTURES = cast(_FxFixtures, test_stress_orchestrator)
# Reusing private test fixtures is intentional, rather than an application API.
RECOGNITION_CAPS = APPENDIX_FIXTURES._RECOGNITION_CAPS  # pyright: ignore[reportPrivateUsage]
projection = APPENDIX_FIXTURES._projection  # pyright: ignore[reportPrivateUsage]
fx_positions = FX_FIXTURES._fx_positions  # pyright: ignore[reportPrivateUsage]
short_usd = FX_FIXTURES._short_usd  # pyright: ignore[reportPrivateUsage]
fx_run = FX_FIXTURES._fx_run  # pyright: ignore[reportPrivateUsage]

BACKEND = Path(__file__).parents[2]
#: The whole ICAAP surface, not just its two packages. The route layer is
#: precisely where someone would wire ``live-summary`` into a readiness panel to
#: make a number appear, and both AI planes feed ICAAP text; until 2026-09-20 the
#: guard scanned two of the five (architecture audit M6), so its docstring's
#: promise to fail "the moment the dependency is WRITTEN" did not hold there.
SCANNED = (
    "app/icaap/domain",
    "app/icaap/service",
    "app/icaap/api",
    "app/attestation/api",
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
    "app.live.service.live_block",
    "app.live.service.live_state",
    "app.live.service.live_view",
    "app.live.models.live",
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
STATEFUL_PREFIXES = (
    "app.services",
    "app.models",
    "app.api",
    "app.features",
    "app.identity.api",
    "app.identity.models",
    "app.identity.service",
    "app.identity.public",
    "app.icaap.api",
    "app.icaap.models",
    "app.icaap.service",
    "app.icaap.public",
)


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


DOMAIN_FILES = _python_files("app/icaap/domain")
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
    assert (BACKEND / "app/icaap/domain/editor_schema.json").is_file()


def test_the_scanner_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the guard reports rather than merely being present."""
    violation = tmp_path / "leaky.py"
    violation.write_text(
        "from app.live.service.live_block import live_block\n\n\n"
        "def read():\n    return live_block()\n",
        encoding="utf-8",
    )
    tree = ast.parse(violation.read_text(encoding="utf-8"))
    imported = _imported_modules(tree, "app.icaap.service.leaky")
    assert any(name.startswith("app.live.service.live_block") for name in imported)
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
# through ``app/icaap/service/parameters.py``, which passes ``record=False``.
#
# This replaces the invariant "no ICAAP request session ever seals a
# ``RegulatoryRun``", which nothing stated and nothing tested, and which a
# "run the engine now" action on an ICAAP block would break in one line.

#: The one ICAAP module permitted to speak to the control plane directly.
PARAMETER_DOOR = "app/icaap/service/parameters.py"
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
        f"session consumption ledger — go through app.icaap.service.parameters, which "
        f"passes record=False."
    )


def test_the_plane_scanner_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the parameter-door guard reports rather than merely being present."""
    violation = tmp_path / "leaky_params.py"
    violation.write_text(
        "from app.policy.service import regulatory_parameters\n"
        "\n"
        "\n"
        "def read(db, bank):\n"
        "    return regulatory_parameters.try_resolve(db, bank, 'x')\n",
        encoding="utf-8",
    )
    tree = ast.parse(violation.read_text(encoding="utf-8"))
    assert _module_qualified_calls(tree, "regulatory_parameters") & LEDGER_ENTRY_POINTS


# --- D-038: one FX Pillar 2 definition across the filed document -------------


@pytest.mark.parametrize(
    ("short", "loss", "addon"),
    [(False, Decimal("0"), Decimal("0")), (True, Decimal("8000000"), Decimal("3500000"))],
)
def test_the_stress_orchestrator_computes_the_fx_revaluation_addon(
    short: bool, loss: Decimal, addon: Decimal
) -> None:
    outcome = fx_run(short_usd() if short else fx_positions())
    assert outcome.fx is not None
    assert outcome.fx.revaluation_loss == loss
    assert outcome.fx.pillar1_fx_capital == Decimal("4500000")
    assert outcome.fx.pillar2_addon == addon
    serialized = outcome.serialize()["fx"]
    assert isinstance(serialized, dict)
    serialized_fx = cast(dict[str, object], serialized)
    serialized_addon = serialized_fx["pillar2_addon"]
    assert isinstance(serialized_addon, str)
    assert Decimal(serialized_addon) == addon


@pytest.mark.parametrize("short", [False, True])
def test_the_stress_overlay_and_appendix_preserve_the_fx_addon(short: bool) -> None:
    outcome = fx_run(short_usd() if short else fx_positions())
    expected = Decimal("3500") if short else Decimal("0")
    # Exercise the internal overlay seam without changing its public interface.
    overlay = enterprise_stress._pillar2_overlay(outcome, 3)  # pyright: ignore[reportPrivateUsage]
    assert overlay is not None
    assert set(overlay) == {1, 2, 3}
    assert all(row.country_and_fx == expected for row in overlay.values())
    tables = build_appendix_ii(
        projection(severe_paths()),
        severe_paths(),
        currency="GHS",
        car_target_pct=Decimal("13"),
        recognition_caps=RECOGNITION_CAPS,
        pillar2_by_stress_year=overlay,
    )
    for row in tables.table5_rwa.rows:
        assert row.pillar2.country_and_fx == (
            expected if row.label.startswith("stress_y") else None
        )
    serialized = tables.serialize()["table5_rwa"]
    assert isinstance(serialized, dict)
    serialized_table = cast(dict[str, object], serialized)
    for row in cast(list[dict[str, object]], serialized_table["rows"]):
        value = cast(dict[str, object], row["pillar2"])["country_and_fx"]
        label = row["label"]
        assert isinstance(label, str)
        if label.startswith("stress_y"):
            assert isinstance(value, str)
            assert Decimal(value) == expected
        else:
            assert value is None
