"""BI reads a governed limit through ONE door, and that door records nothing.

``app/bi/service/limits.py`` is the only BI module that may speak to the
regulatory-parameter control plane or to the tenant's board register (D-069,
mirroring ``app/icaap/service/parameters.py`` and the guard in
``test_icaap_boundaries.py``). Two separate invariants are pinned here, and they
fail for different reasons:

**The ledger (D-078).** ``RegulatoryRun.parameter_provenance`` is drained from an
ambient session-scoped ledger at the moment a run row is built, so a governed-row
read taken anywhere in that session is credited to whichever run seals NEXT. BI
is a dispatch plane: it seals no run, and one dashboard resolves the limits of
every widget on the canvas across every return family. A recording read from BI
would therefore move a sealed package's ``content_digest`` — the value every
signer signs — as a function of which dashboard somebody happened to open. So no
BI module may call a recording-capable entry point at all, and the one module
that resolves limits passes ``record=False``.

**The door.** A limit must be resolved in one place or the platform states two
limits for one number: the engine's and BI's. The precedence (board register,
then the regulatory row, tighten-only between them) lives in ``limits.py`` and
nowhere else in BI, so no other BI module may reach the resolver, the board
register models, or the tighten rules — including by way of a hand-rolled query.

This is an import-and-call-graph test on purpose: it fails the moment the
dependency is WRITTEN, not months later when a limit line turns out to have been
drawn from a second opinion.
"""

from __future__ import annotations

import ast
import re
from inspect import signature
from pathlib import Path

import pytest

from app.bi.service import limits
from app.policy.service import regulatory_parameters
from tests.architecture._planes import APP, imported_modules, module_name, referenced_names
from tests.architecture.test_bi_plane_boundary import BI_OWNED

BACKEND = APP.parent

#: The one BI module permitted to speak to either register.
PARAMETER_DOOR = "app/bi/service/limits.py"

#: The mart builder's build FINGERPRINT covers the grid parameters a build read,
#: so a staff change to a provisioning rate rebuilds the marts rather than
#: silently changing what an unchanged fingerprint claims. It reads them through
#: ``seed_values``. The exception is one file, one function, and is not a limit:
#: it never reaches a measure's ``thresholds_source``.
FINGERPRINT_EXCEPTION: tuple[str, str] = ("app/bi/service/mart_builder.py", "seed_values")

#: Entry points DERIVED from the resolver module rather than listed, so a new
#: read path is covered the day it is written. A function defined elsewhere and
#: re-exported (``tighten``) is excluded here and caught by the door rule below.
_READ_PATTERN = re.compile(
    r"(try_)?resolve(_\w+)?|(seed|control)_values|clamp_overrides|consume_parameter_provenance"
)


def _read_entry_points() -> frozenset[str]:
    found: set[str] = set()
    for name in dir(regulatory_parameters):
        if name.startswith("_"):
            continue
        member = getattr(regulatory_parameters, name)
        if not callable(member):
            continue
        if getattr(member, "__module__", None) != regulatory_parameters.__name__:
            continue
        if _READ_PATTERN.fullmatch(name):
            found.add(name)
    return frozenset(found)


READ_ENTRY_POINTS: frozenset[str] = _read_entry_points()

#: The floor the derivation may grow past but never silently fall below. A
#: renamed function would otherwise empty the scan and every test here would
#: pass for the wrong reason.
PINNED_ENTRY_POINTS: frozenset[str] = frozenset(
    {
        "try_resolve",
        "resolve",
        "resolve_class_value",
        "resolve_decimal",
        "resolve_many",
        "resolve_hqla_parameters",
        "seed_values",
        "control_values",
        "clamp_overrides",
        "consume_parameter_provenance",
    }
)

#: Modules that hold either register, or the rules for combining them. Outside
#: the door, a BI module may not import one.
DOOR_ONLY_MODULES: tuple[str, ...] = (
    "app.policy.service.regulatory_parameters",
    "app.policy.service.params",
    "app.policy.service.parameter_register",
    "app.policy.domain",
    "app.policy.models.regulatory_parameter",
)

#: Names that reach a register without importing its service: the board register
#: tables, the control-plane table, the prefetching resolver, and the
#: tighten-only rules. ``referenced_names`` sees an attribute access and a
#: ``getattr`` with a literal name, so re-export through ``app.models`` does not
#: dodge this.
DOOR_ONLY_NAMES: frozenset[str] = frozenset(
    {
        "RegulatoryParameter",
        "ParamCapitalThreshold",
        "ParamLiquidityThreshold",
        "ParamConcentrationLimit",
        "ParamCreditThreshold",
        "PrefetchedParameterResolver",
        "PrefetchedActiveParams",
        "get_active_params",
        "prefetch_active_params",
        "policy_scope",
        "tighten",
        "direction_for",
    }
)


def _bi_files() -> list[Path]:
    return sorted(BACKEND / relative for relative in BI_OWNED)


BI_FILES = _bi_files()
BI_IDS = [str(path.relative_to(BACKEND)) for path in BI_FILES]
OTHER_FILES = [path for path in BI_FILES if str(path.relative_to(BACKEND)) != PARAMETER_DOOR]
OTHER_IDS = [str(path.relative_to(BACKEND)) for path in OTHER_FILES]


def _module_qualified_calls(tree: ast.AST) -> set[tuple[str, str]]:
    """``alias.attr(...)`` call targets, as ``(alias, attr)``.

    The alias rather than the module name, because the resolver is imported
    under one (``from app.services import regulatory_parameters as rp`` in the
    mart builder). Bare names are ignored: a module's own helper of the same name
    is not this one.
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            found.add((target.value.id, target.attr))
    return found


def _resolver_aliases(source: str, tree: ast.AST) -> set[str]:
    """Every local name bound to the resolver module, including ``as`` aliases."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.policy.service.regulatory_parameters":
                    aliases.add(alias.asname or alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom) and (
            (node.module or "").startswith(("app.services", "app.policy.service"))
            or node.module == "app.policy.public"
        ):
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "regulatory_parameters"
            )
    if "regulatory_parameters" in source:
        aliases.add("regulatory_parameters")
    return aliases


def _entry_point_calls(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    aliases = _resolver_aliases(source, tree)
    return {
        attr
        for alias, attr in _module_qualified_calls(tree)
        if alias in aliases and attr in READ_ENTRY_POINTS
    }


# --- the scan is real -------------------------------------------------------


def test_the_scan_covers_the_bi_plane_and_excludes_only_the_door() -> None:
    assert PARAMETER_DOOR in BI_IDS, "the limit resolver is not in the scanned plane"
    assert PARAMETER_DOOR not in OTHER_IDS
    assert len(OTHER_IDS) > 20, f"the BI plane scan collapsed to {len(OTHER_IDS)} files"
    assert (BACKEND / FINGERPRINT_EXCEPTION[0]).is_file()


def test_the_entry_point_set_is_derived_and_cannot_shrink() -> None:
    assert PINNED_ENTRY_POINTS <= READ_ENTRY_POINTS, sorted(PINNED_ENTRY_POINTS - READ_ENTRY_POINTS)
    for name in READ_ENTRY_POINTS:
        assert callable(getattr(regulatory_parameters, name))


# --- the ledger -------------------------------------------------------------


@pytest.mark.parametrize("path", OTHER_FILES, ids=OTHER_IDS)
def test_no_bi_module_but_the_door_reads_a_governed_parameter(path: Path) -> None:
    relative = str(path.relative_to(BACKEND))
    allowed = {FINGERPRINT_EXCEPTION[1]} if relative == FINGERPRINT_EXCEPTION[0] else set()
    offending = sorted(_entry_point_calls(path) - allowed)
    assert not offending, (
        f"{relative} calls regulatory_parameters.{offending} directly. BI seals no "
        f"RegulatoryRun, so its reads must not enter the session consumption ledger and a "
        f"limit must be resolved in one place — go through app.bi.service.limits."
    )


def test_the_door_exposes_no_way_to_opt_into_recording() -> None:
    """``record`` is not a parameter of anything the door publishes.

    A caller must not be able to choose the plane from outside: the decision is
    this module's, once, which is the whole point of having a door.
    """
    for entry_point in (
        limits.LimitResolver,
        limits.LimitResolver.load,
        limits.LimitResolver.for_measure,
        limits.LimitResolver.for_measures,
        limits.measure_limit,
        limits.GovernedLimit,
        limits.GovernedLimit.provenance,
        limits.NoGovernedLimit,
        limits.NoGovernedLimit.provenance,
        limits.is_governed,
        limits.absence_copy,
        limits.conversion_factors,
    ):
        assert "record" not in signature(entry_point).parameters


# --- the door ---------------------------------------------------------------


@pytest.mark.parametrize("path", OTHER_FILES, ids=OTHER_IDS)
def test_no_bi_module_but_the_door_imports_a_register(path: Path) -> None:
    relative = str(path.relative_to(BACKEND))
    source = path.read_text(encoding="utf-8")
    imported = imported_modules(source, module=module_name(path))
    imported = {
        name.replace(
            "app.policy.public.regulatory_parameters", "app.policy.service.regulatory_parameters"
        ).replace("app.policy.public.params", "app.policy.service.params")
        for name in imported
    }
    allowed = (
        {"app.policy.service.regulatory_parameters"}
        if relative == FINGERPRINT_EXCEPTION[0]
        else set()
    )
    offending = sorted(
        name
        for name in imported - allowed
        if any(name == module or name.startswith(f"{module}.") for module in DOOR_ONLY_MODULES)
    )
    assert not offending, (
        f"{relative} imports {offending}. The board register, the control plane and the "
        f"tighten-only rules are reached through app.bi.service.limits, so BI states one "
        f"limit per figure rather than two."
    )


@pytest.mark.parametrize("path", OTHER_FILES, ids=OTHER_IDS)
def test_no_bi_module_but_the_door_names_a_register_table_or_rule(path: Path) -> None:
    relative = str(path.relative_to(BACKEND))
    offending = sorted(referenced_names(path.read_text(encoding="utf-8")) & DOOR_ONLY_NAMES)
    assert not offending, (
        f"{relative} names {offending}. A hand-rolled query against a register is the same "
        f"second opinion as a hand-rolled resolve — go through app.bi.service.limits."
    )


# --- the guards convict --------------------------------------------------------


VIOLATIONS: dict[str, str] = {
    "recording_read": (
        "from app.policy.service import regulatory_parameters\n"
        "\n"
        "\n"
        "def limit(db, bank, code):\n"
        "    return regulatory_parameters.try_resolve(db, bank, code)\n"
    ),
    "aliased_recording_read": (
        "from app.policy.service import regulatory_parameters as rp\n"
        "\n"
        "\n"
        "def limit(db, bank, code):\n"
        "    return rp.resolve(db, bank, code)\n"
    ),
    "hand_rolled_register_query": (
        "from sqlalchemy import select\n\n"
        "from app.models import ParamCapitalThreshold\n\n\n"
        "def limit(db, code):\n"
        "    return db.scalars(select(ParamCapitalThreshold)).all()\n"
    ),
    "second_prefetched_resolver": (
        "from app.policy.service import regulatory_parameters\n"
        "\n"
        "\n"
        "def limits(db, bank, dates):\n"
        "    return regulatory_parameters.PrefetchedParameterResolver.load(\n"
        "        db, bank, as_of_dates=dates, record=True\n"
        "    )\n"
    ),
}


@pytest.mark.parametrize("name", sorted(VIOLATIONS))
def test_the_guards_catch_a_deliberate_violation(name: str, tmp_path: Path) -> None:
    """Proof each guard REPORTS rather than merely being present."""
    path = tmp_path / "leaky.py"
    path.write_text(VIOLATIONS[name], encoding="utf-8")
    source = path.read_text(encoding="utf-8")
    convicted = False

    if _entry_point_calls(path):
        convicted = True
    imported = imported_modules(source)
    if any(
        module == candidate or module.startswith(f"{candidate}.")
        for module in imported
        for candidate in DOOR_ONLY_MODULES
    ):
        convicted = True
    if referenced_names(source) & DOOR_ONLY_NAMES:
        convicted = True
    assert convicted, f"the {name} violation walks through every guard"


def test_the_fingerprint_exception_does_not_admit_a_second_function() -> None:
    """The mart builder may read ``seed_values`` and nothing else."""
    builder = BACKEND / FINGERPRINT_EXCEPTION[0]
    assert _entry_point_calls(builder) == {FINGERPRINT_EXCEPTION[1]}
