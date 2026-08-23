"""The case plane and the regulatory plane must not import each other (CF-2, CF-4).

Two systems in this repository compute things called "liquidity" and
"capital" from different inputs with different formulas:

* the BANK-scoped regulatory plane — `BankFinancialFact` -> the pure engines in
  `app/domain/**` -> `RegulatoryRun` -> BoG returns, signed and filed; and
* the CASE-scoped financial workspace — hand-entered `Financial*` records ->
  `CalculationRun` -> `CapitalProjection` and case findings, advisory and never
  filed.

`docs/forensic_calculation_audit_2026-08-21.md` L857 verified they are already
isolated at the data layer. This file makes that isolation structural, so the
next person cannot undo it with one import, and so the failure is a red test
rather than a wrong filed number nobody can see.

The primary rule is a CLOSURE over the whole `app/` tree: only the case plane
itself may reference a case-plane model. It therefore already covers the module
nobody has written yet. The remaining rules each name a direction, so a failure
says which risk it caught:

* **regulatory -> case** is the filing risk, scoped to the surfaces the audit
  called out. An advisory figure reaching a return would be sealed into a run's
  `input_hash` with no ingestion lineage and no examiner-visible provenance.
* **case -> regulatory** is the authority risk. A case screen reading a sealed
  `RegulatoryRun` would silently become a second, unsupervised presentation of
  a filed number — and would make the case plane a consumer of regulatory
  state that no one has scoped, versioned, or access-controlled for it.
* **both in one module** is the migration risk that
  `FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md` §10 names in its
  fourth bullet: case output must not be written into `BankFinancialFact`
  without a reviewed canonical adapter and a reconciliation. Nothing does this
  today. Any module that can see both a case model and a bank fact is where
  such an adapter would appear, so amending
  `test_no_module_can_write_case_output_into_a_bank_fact` is the review gate.

Directional discovery is by GLOB rather than a hand-kept list, so a newly added
`app/services/regulatory_*.py` is in scope on the day it is written. Parsing is
by AST, so a module that merely NAMES a forbidden symbol in a string literal —
`app/domain/authority/registry.py` lists them precisely in order to forbid them
— is correctly not a dependency.

`tests/architecture/test_dependency_boundaries.py` (ARCH-5) covers the same
regulatory -> case direction over an explicit module list. The overlap is
deliberate: that file pins the modules known at the time of the audit, this one
pins the shape, and neither can be deleted without the other going red. Both
now take their idea of what the case plane IS from
`tests/architecture/_planes.py`; they used to keep separate lists, and the two
had drifted far enough apart that an ordinary absolute import crossed the line
unnoticed.

WHAT THIS GUARD DOES NOT DO
---------------------------
Written down because a guard whose limits are known is worth more than one
believed to be total. An independent audit (2026-08-22) broke the previous
version fourteen ways; these are what survives.

1. **No import-graph traversal.** Every rule is per-file. Transitive
   reachability is NOT computed, and deliberately so: `app/models/__init__.py`
   imports every mapped class, so a naive closure over the import graph
   convicts essentially the whole application and stops being read. What
   substitutes for it is the CLOSURE RULE over the whole `app/` tree — a shim
   that re-exports the case plane is itself a module under `app/` that
   references the case plane, so it goes red at the shim. That covers
   indirection of any depth through modules in this repository, because every
   link in such a chain must itself reference the plane. It does NOT tell you
   which regulatory module wanted the shim; the failure names the shim.
   `test_a_shim_that_re_exports_the_case_plane_is_convicted_at_the_shim` pins
   this behaviour and its limit.
2. **Dynamic imports are only partly reachable.** A literal
   `importlib.import_module("app.services.capital")` or `__import__(...)` is
   caught, including a `+`-concatenation of literals. A module name assembled
   at runtime — from a variable, a setting, a `".".join(...)` — is not
   detectable by AST inspection at all. No amount of work on this file changes
   that; only a runtime import hook would.
3. **Raw SQL is only caught when static.** A case-plane table named in a
   literal SQL string is caught. A query built at runtime, loaded from a file,
   or reached through a database view under another name is not.
4. **Only `app/` is scanned.** Code outside it — migrations, scripts, the
   worker entrypoints — is not covered by these rules.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from tests.architecture._planes import (
    APP,
    BANK_PLANE_TABLES,
    CASE_PLANE_GLOBS,
    CASE_PLANE_MEMBERS,
    CASE_PLANE_SYMBOLS,
    CASE_PLANE_TABLES,
    PINNED_CASE_PLANE_MODULES,
    imported_modules,
    referenced_names,
    sql_table_references,
)
from tests.architecture._planes import (
    module_name as _module_name,
)

# --------------------------------------------------------------------------
# The case plane: advisory credit analysis for one borrower. Never filed.
# --------------------------------------------------------------------------

#: Modules the case plane owns, DERIVED from `CASE_PLANE_GLOBS` in
#: `tests/architecture/_planes.py`. One set answers both questions below, so
#: they can no longer drift apart the way the two hand-written lists did:
#:
#: * `CASE_PLANE_MODULES` — no module outside the plane may import one; and
#: * `CASE_PLANE_OWNERS`  — these are the only modules allowed to reference a
#:   case-plane model.
#:
#: They are the SAME set, deliberately and by identity. Before 2026-08-22 the
#: owner list held 16 modules and the forbidden list 9, so thirteen case-plane
#: modules could be imported from anywhere with an ordinary absolute import and
#: neither this guard nor ARCH-5 noticed. Adding a module to the plane is now a
#: single edit to `CASE_PLANE_GLOBS`, and it takes effect in both directions at
#: once.
CASE_PLANE_MODULES: frozenset[str] = CASE_PLANE_MEMBERS
CASE_PLANE_OWNERS: frozenset[str] = CASE_PLANE_MEMBERS

# --------------------------------------------------------------------------
# The regulatory plane: everything that computes, seals, or files a number.
# --------------------------------------------------------------------------

#: Glob patterns, not a fixed list — a new ``services/regulatory_*.py`` is
#: covered the day it is added. ``test_the_regulatory_plane_scan_is_not_vacuous``
#: proves the patterns actually resolve to the modules that matter.
REGULATORY_PLANE_GLOBS: tuple[str, ...] = (
    "domain/**/*.py",
    "services/regulatory_*.py",
    "services/regulatory_reporting/**/*.py",
    "services/sdi_*.py",
    "services/attestation/**/*.py",
    "services/fact_derivation.py",
    "services/pipeline.py",
    "services/params.py",
    "services/capital_plan.py",
    "services/enterprise_stress*.py",
    "services/reverse_stress.py",
    "services/live_*.py",
    "features/run_regulatory_*.py",
    "features/run_forecasting.py",
    "features/run_reverse_stress.py",
    "features/manage_regulatory_reporting.py",
    "features/manage_enterprise_stress*.py",
    "features/manage_capital_plan.py",
    "features/manage_attestation.py",
)

#: Bank-scoped facts and sealed run records. A case module reading one of these
#: would be presenting a filed number outside the regulatory surface.
BANK_PLANE_SYMBOLS: frozenset[str] = frozenset(
    {
        "BankFinancialFact",
        "CurrentFinancialFact",
        "CanonicalPosition",
        "CanonicalPositionSnapshot",
        "CanonicalReferenceRow",
        "RegulatoryRun",
        "RegulatoryMetricResult",
        "RegulatoryLineItem",
        "RegulatoryValidation",
        "RegulatoryPackage",
        "RegulatoryPackageArtifact",
        "GeneratedReturn",
    }
)

BANK_PLANE_MODULES: frozenset[str] = frozenset(
    {
        "app.models.canonical",
        "app.models.regulatory",
        "app.models.regulatory_run",
        "app.models.regulatory_reporting",
        "app.services.fact_derivation",
        "app.services.regulatory_capital",
        "app.services.regulatory_liquidity",
        "app.services.regulatory_forecasting",
        "app.services.regulatory_fx",
        "app.services.regulatory_irr",
        "app.services.regulatory_ftp",
        "app.services.regulatory_reporting",
        "app.domain.capital",
        "app.domain.liquidity",
        "app.domain.forecasting",
        "app.domain.irr",
        "app.domain.fx",
        "app.domain.ftp",
        "app.domain.stress",
    }
)

#: The two modules allowed to see across the line, both because they cannot DO
#: anything. `app/models/__init__.py` is the SQLAlchemy aggregator: it
#: re-exports every mapped class so `Base.metadata` is complete.
#: `app/api/router.py` is the FastAPI equivalent: it mounts every feature
#: router, four of which are the case plane's own screens, and a product that
#: serves both planes from one process cannot avoid that. Both exemptions are
#: narrow BY CONSTRUCTION — `test_the_aggregator_exemptions_stay_logic_free`
#: fails if either grows a function or a class, which is the only way an
#: adapter could hide behind one.
AGGREGATORS: frozenset[str] = frozenset({"app/models/__init__.py", "app/api/router.py"})

# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def _files(globs: tuple[str, ...]) -> list[Path]:
    found: set[Path] = set()
    for pattern in globs:
        found.update(path for path in APP.glob(pattern) if "__pycache__" not in path.parts)
    return sorted(found)


def _module_hits(source: str, module: str | None, forbidden: frozenset[str]) -> set[str]:
    return {
        imported
        for imported in imported_modules(source, module=module)
        if any(imported == item or imported.startswith(f"{item}.") for item in forbidden)
    }


def case_plane_dependencies(source: str, module: str | None = None) -> set[str]:
    """Every reference this source makes to the case-scoped plane.

    Three channels, because a boundary crossed by import is only the obvious
    one:

    * a module path — absolute, RELATIVE (resolved against ``module``), or
      named in a literal ``importlib.import_module`` / ``__import__`` call;
    * a case-plane symbol, including one reached by ``getattr(m, "X")``; and
    * a case-plane TABLE named in raw SQL, which reaches the same rows with no
      import at all.

    Pass ``module`` — the dotted name of the file being scanned — or relative
    imports and dynamic relative imports cannot be resolved.
    """
    return (
        _module_hits(source, module, CASE_PLANE_MODULES)
        | (referenced_names(source) & CASE_PLANE_SYMBOLS)
        | sql_table_references(source, CASE_PLANE_TABLES)
    )


def bank_plane_dependencies(source: str, module: str | None = None) -> set[str]:
    """Every reference this source makes to bank facts or sealed runs."""
    return (
        _module_hits(source, module, BANK_PLANE_MODULES)
        | (referenced_names(source) & BANK_PLANE_SYMBOLS)
        | sql_table_references(source, BANK_PLANE_TABLES)
    )


Detector = Callable[[str, str | None], set[str]]


def _scan(paths: list[Path], detector: Detector) -> dict[str, list[str]]:
    scanned: dict[str, list[str]] = {}
    for path in paths:
        module = _module_name(path)
        if hits := detector(path.read_text(), module):
            scanned[module] = sorted(hits)
    return scanned


def _app_modules_outside(exempt: frozenset[str]) -> list[tuple[str, Path]]:
    """Every module under ``app/`` except the named plane and the aggregators."""
    found: list[tuple[str, Path]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(APP.parent).as_posix() in AGGREGATORS:
            continue
        module = _module_name(path)
        if module in exempt:
            continue
        found.append((module, path))
    return found


# --------------------------------------------------------------------------
# Direction 1: regulatory -> case (the filing risk)
# --------------------------------------------------------------------------


def test_only_the_case_plane_itself_references_a_case_plane_model() -> None:
    """The closure rule: NOTHING outside the case plane may see its models.

    Stronger than enumerating the regulatory surfaces, because it covers the
    module nobody has written yet — a new `app/services/regulatory_xyz.py`, a
    new operator endpoint, a new export job. As of this audit exactly sixteen
    modules in `app/` reference a case model and all sixteen ARE the case plane,
    so the closure holds with no exceptions beyond the model aggregator.

    A failure here is not necessarily a bug: it may be a deliberate extension of
    the case plane. It is always a decision, which is the point — take it in
    review, then add the module to `CASE_PLANE_OWNERS` with that reasoning.
    """
    trespassers: dict[str, list[str]] = {}
    for module, path in _app_modules_outside(CASE_PLANE_OWNERS):
        if hits := case_plane_dependencies(path.read_text(), module):
            trespassers[module] = sorted(hits)

    assert trespassers == {}, (
        "A module outside the case plane referenced case-scoped calculation "
        f"state: {trespassers}. Case figures are hand-entered, advisory and "
        "never filed; if this module genuinely belongs to the case plane, add "
        "it to CASE_PLANE_OWNERS and say why in the review."
    )


def test_the_case_plane_owner_list_is_exact() -> None:
    """Every derived owner must exist on disk, so the set cannot rot.

    The set is derived from `CASE_PLANE_GLOBS` against the live tree, so this
    holds by construction — which is the point. It used to be a hand-written
    list, and a renamed or deleted module left in it was a hole the closure
    rule would not notice.
    """
    on_disk = {_module_name(path) for path in APP.rglob("*.py") if "__pycache__" not in path.parts}

    assert on_disk >= CASE_PLANE_OWNERS, (
        "CASE_PLANE_OWNERS names modules that no longer exist: "
        f"{sorted(CASE_PLANE_OWNERS - on_disk)}"
    )


def test_the_owner_and_forbidden_sets_are_one_set() -> None:
    """The mismatch that made this guard evadable cannot be re-opened.

    Until 2026-08-22 `CASE_PLANE_OWNERS` held 16 modules and
    `CASE_PLANE_MODULES` held 9. Thirteen modules were therefore declared to BE
    the case plane while not being forbidden to import, so

        from app.services.case_plane import compute_headroom

    in a regulatory module passed both this guard and ARCH-5. Two lists in two
    places, each plausible on its own, is how that happens; identity is the fix
    that survives the next editor.
    """
    assert CASE_PLANE_OWNERS is CASE_PLANE_MODULES
    assert CASE_PLANE_MODULES is CASE_PLANE_MEMBERS

    for module in ("app.services.case_plane", "app.features.run_calculations"):
        assert module in CASE_PLANE_MODULES, f"{module} is the case plane but importable"


def test_the_case_plane_derivation_cannot_shrink_below_the_audit_floor() -> None:
    """A renamed directory must not empty the derived set into a silent pass.

    `PINNED_CASE_PLANE_MODULES` is the frozen union of the three hand-written
    lists this derivation replaced. The derivation may grow; if it ever stops
    covering one of these, the globs have stopped resolving and every boundary
    assertion in this file would be passing for the wrong reason.
    """
    assert PINNED_CASE_PLANE_MODULES <= CASE_PLANE_MEMBERS, (
        "The case-plane globs no longer resolve to modules the audit pinned: "
        f"{sorted(PINNED_CASE_PLANE_MODULES - CASE_PLANE_MEMBERS)}"
    )
    assert len(CASE_PLANE_MEMBERS) >= len(PINNED_CASE_PLANE_MODULES)
    assert CASE_PLANE_TABLES, "Case-plane table derivation produced nothing."


def test_the_regulatory_plane_never_imports_a_case_plane_model() -> None:
    """Nothing that produces a filed number may read case-scoped state.

    A `CalculationRun` snapshots balances an analyst typed against one
    borrower. It carries no ingestion batch, so it is untraceable under the
    no-seeded-data order in `CLAUDE.md`, and its `input_hash` has no lineage
    into the regulatory spine. One import is enough to put such a figure inside
    a sealed run — and because both planes call the number "liquidity" or
    "capital", the defect is invisible in review.
    """
    offenders = _scan(_files(REGULATORY_PLANE_GLOBS), case_plane_dependencies)

    assert offenders == {}, f"Regulatory plane reached into the case plane: {offenders}"


def test_the_regulatory_plane_scan_is_not_vacuous() -> None:
    """A guard that scans nothing passes for the wrong reason.

    Pins the glob resolution against modules the forensic audit named, and
    against every `services/regulatory_*.py` on disk, so a renamed directory
    cannot silently empty the scan.
    """
    scanned = {_module_name(path) for path in _files(REGULATORY_PLANE_GLOBS)}

    required = {
        "app.domain.capital.engine",
        "app.domain.liquidity.engine",
        "app.domain.forecasting.engine",
        "app.services.regulatory_capital",
        "app.services.regulatory_liquidity",
        "app.services.regulatory_forecasting",
        "app.services.regulatory_reporting.generation",
        "app.services.fact_derivation",
        "app.services.sdi_capital",
    }
    on_disk = {
        _module_name(path)
        for path in APP.glob("services/regulatory_*.py")
        if "__pycache__" not in path.parts
    }

    assert required <= scanned, f"Glob missed audited modules: {sorted(required - scanned)}"
    assert on_disk <= scanned, f"Glob missed regulatory services: {sorted(on_disk - scanned)}"
    assert len(scanned) > 100, f"Only {len(scanned)} modules scanned; the globs stopped resolving."


# --------------------------------------------------------------------------
# Direction 2: case -> regulatory (the authority risk)
# --------------------------------------------------------------------------


def test_the_case_plane_never_reads_bank_facts_or_sealed_runs() -> None:
    """Case analysis must not become a second view of a filed number.

    The case plane is advisory: it has its own review workflow, its own
    thresholds, and no attestation. If it could read a `RegulatoryRun` or a
    `BankFinancialFact`, a filed figure would be re-presented on a surface with
    none of the filing controls, and any transformation on the way would be an
    unversioned alternate authority for a supervised metric.
    """
    offenders = _scan(_files(CASE_PLANE_GLOBS), bank_plane_dependencies)

    assert offenders == {}, f"Case plane reached into the regulatory plane: {offenders}"


def test_the_case_plane_scan_is_not_vacuous() -> None:
    """The reverse scan must actually resolve to the case plane."""
    scanned = {_module_name(path) for path in _files(CASE_PLANE_GLOBS)}

    required = {
        "app.models.calculation",
        "app.models.capital",
        "app.models.financial",
        "app.services.calculations",
        "app.services.capital",
        "app.services.liquidity",
        "app.features.run_calculations",
        "app.features.manage_capital",
        "app.features.review_liquidity",
    }

    assert required <= scanned, f"Glob missed case modules: {sorted(required - scanned)}"


# --------------------------------------------------------------------------
# Direction 3: no migration without a reviewed adapter
# --------------------------------------------------------------------------


def test_no_module_can_write_case_output_into_a_bank_fact() -> None:
    """`FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md` §10, bullet 4.

    "Do not migrate case output into `BankFinancialFact` without a reviewed
    canonical adapter and reconciliation." Nothing does this today, and this
    test is what keeps it that way: an adapter has to see a case model and a
    bank fact in the same module, so it cannot be written without turning this
    red first.

    If such an adapter is ever built and reviewed, amend this test in the SAME
    change and name the adapter module explicitly. That amendment is the review
    gate — it is deliberately not possible to add the adapter quietly.
    """
    straddlers: dict[str, dict[str, list[str]]] = {}
    for module, path in _app_modules_outside(frozenset()):
        names = referenced_names(path.read_text())
        case = names & CASE_PLANE_SYMBOLS
        bank = names & BANK_PLANE_SYMBOLS
        if case and bank:
            straddlers[module] = {
                "case": sorted(case),
                "bank": sorted(bank),
            }

    assert straddlers == {}, (
        "A module sees both a case model and a bank fact, which is where an "
        f"unreviewed case-to-canonical migration would live: {straddlers}"
    )


def test_the_aggregator_exemptions_stay_logic_free() -> None:
    """Both exempt modules are exempt only because they cannot do anything.

    `app/models/__init__.py` re-exports every mapped class so `Base.metadata`
    is complete. `app/api/router.py` mounts every feature router, four of which
    are the case plane's own screens. Neither contains a statement that could
    transform a number. The moment either grows a function or a class it stops
    being a re-export list and becomes a place an adapter could hide, so the
    exemption must fail with it.
    """
    grown: dict[str, list[str]] = {}
    for relative in sorted(AGGREGATORS):
        tree = ast.parse((APP.parent / relative).read_text())
        logic = [
            type(node).__name__
            for node in tree.body
            if not isinstance(node, ast.Import | ast.ImportFrom | ast.Assign | ast.Expr)
        ]
        if logic:
            grown[relative] = logic

    assert grown == {}, f"An aggregator grew logic and can no longer be exempt: {grown}"


# --------------------------------------------------------------------------
# Negative controls: each guard must convict a planted violation
# --------------------------------------------------------------------------


def test_the_forward_guard_convicts_a_planted_import() -> None:
    """Proof the regulatory -> case rule reports rather than merely existing."""
    planted = "from app.models.calculation import CalculationRun\n"

    assert case_plane_dependencies(planted) == {
        "app.models.calculation",
        "app.models.calculation.CalculationRun",
        "CalculationRun",
    }


def test_the_forward_guard_convicts_an_aliased_module_import() -> None:
    """`import app.models as m` then `m.CapitalProjection` must not slip past."""
    planted = "import app.models as m\n\n\ndef build() -> object:\n    return m.CapitalProjection\n"

    assert "CapitalProjection" in case_plane_dependencies(planted)


def test_the_reverse_guard_convicts_a_planted_import() -> None:
    """Proof the case -> regulatory rule reports."""
    planted = "from app.models.regulatory import BankFinancialFact\n"

    assert "BankFinancialFact" in bank_plane_dependencies(planted)
    assert "app.models.regulatory" in bank_plane_dependencies(planted)


def test_the_forward_guard_convicts_a_relative_import() -> None:
    """`node.level == 0` used to exclude EVERY relative import from the scan.

    `app/` carries 88 relative imports today, several inside the regulatory
    plane (`services/regulatory_reporting/bog_forms/render.py`), so this was
    not a theoretical gap: one `from ..models.calculation import ...` walked
    past both boundary guards. Resolution is against the importing module's own
    package, which is why the detectors take `module`.
    """
    one_dot = "from .calculations import compute\n"
    two_dots = "from ..models.calculation import CalculationRun as Run\n"

    assert case_plane_dependencies(one_dot, "app.services.regulatory_capital") == {
        "app.services.calculations",
        "app.services.calculations.compute",
    }
    assert "app.models.calculation" in case_plane_dependencies(
        two_dots, "app.services.regulatory_capital"
    )
    # Depth is arithmetic on the importing module's package, not a fixed rule:
    # the same two dots from one level deeper resolve somewhere else entirely.
    assert "app.models.calculation" in case_plane_dependencies(
        "from ...models.calculation import CalculationRun as Run\n",
        "app.services.regulatory_reporting.generation",
    )

    # ...and the old rule saw neither, which is what made this worth adding.
    assert _module_hits(one_dot, None, CASE_PLANE_MODULES) == set()


def test_the_forward_guard_convicts_a_literal_dynamic_import() -> None:
    """`importlib.import_module("app.services.capital")` is an import too.

    Caught only for a LITERAL name — see the limits on
    `_planes.imported_modules`. A name assembled at runtime is not detectable
    by AST inspection and is documented as residual rather than papered over.
    """
    via_importlib = 'import importlib\n\nm = importlib.import_module("app.services.capital")\n'
    via_builtin = 'm = __import__("app.models.financial")\n'
    via_parts = 'import importlib\n\nm = importlib.import_module("app.services" + ".liquidity")\n'

    assert "app.services.capital" in case_plane_dependencies(via_importlib)
    assert "app.models.financial" in case_plane_dependencies(via_builtin)
    assert "app.services.liquidity" in case_plane_dependencies(via_parts)


def test_the_forward_guard_convicts_reflection_on_a_case_symbol() -> None:
    """`getattr(models, "CalculationRun")` is an attribute access spelled as a string.

    The scanner ignores string literals in general — naming a symbol in prose or
    in a forbid-list is not a dependency — so `getattr` is a deliberate, narrow
    exception: its second argument IS the attribute.
    """
    planted = 'import app.models as m\n\nrun = getattr(m, "CalculationRun")\n'

    assert "CalculationRun" in case_plane_dependencies(planted)


def test_the_forward_guard_convicts_raw_sql_against_a_case_table() -> None:
    """No import is needed to read `calculation_runs` — `text()` is enough.

    An import-graph guard cannot see this by construction, so the table names
    are derived from the case models and matched against SQL-shaped strings.
    """
    planted = (
        "from sqlalchemy import text\n\n"
        'rows = db.execute(text("SELECT input_hash FROM calculation_runs")).all()\n'
    )

    assert "calculation_runs" in case_plane_dependencies(planted)


def test_a_table_name_mentioned_outside_sql_is_not_a_dependency() -> None:
    """The raw-SQL check must not become a grep for table names.

    A log message or an error string may legitimately name a table; only the
    object of a FROM / JOIN / UPDATE / INTO clause counts.
    """
    prose = 'MESSAGE = "calculation_runs is the case plane and must not be read here"\n'

    assert case_plane_dependencies(prose) == set()


def test_a_shim_that_re_exports_the_case_plane_is_convicted_at_the_shim() -> None:
    """The closure rule is what covers one-hop indirection.

    Renaming a case-plane export through an intermediate module does not hide
    it: the SHIM references the case plane, the closure rule scans every module
    under `app/`, and the shim is not an owner — so the build goes red there.
    The failure names the shim rather than the regulatory module that wanted
    it, which is the honest limit of a per-file scan; see the transitive note in
    the module docstring.
    """
    shim = "from app.services.case_plane import compute_headroom as headroom\n"

    assert case_plane_dependencies(shim, "app.services.shim") == {
        "app.services.case_plane",
        "app.services.case_plane.compute_headroom",
    }


def test_the_owner_forbidden_mismatch_evasions_are_now_caught() -> None:
    """The three imports the audit demonstrated against the old sets.

    Each is an ORDINARY absolute import of a module that was declared to be the
    case plane and was not in the forbidden set. All three passed both guards
    before the sets were unified.
    """
    for planted in (
        "from app.services.case_plane import compute_headroom\n",
        "from app.features.run_calculations import compute_headroom\n",
        "from app.services.financial_workspace import compute_headroom\n",
    ):
        assert case_plane_dependencies(planted), f"still evades: {planted!r}"


def test_the_reverse_guard_convicts_raw_sql_against_a_bank_table() -> None:
    """The authority risk has the same no-import path as the filing risk."""
    planted = (
        "from sqlalchemy import text\n\n"
        'rows = db.execute(text("SELECT value FROM bank_financial_facts")).all()\n'
    )

    assert "bank_financial_facts" in bank_plane_dependencies(planted)


def test_naming_a_forbidden_symbol_in_a_string_is_not_a_dependency() -> None:
    """`app/domain/authority/registry.py` lists the case plane in order to forbid it.

    A grep-based guard would convict it. An AST guard must not — otherwise the
    only correct way to document the boundary would be to break the build.
    """
    registry = (APP / "domain" / "authority" / "registry.py").read_text()

    assert "app.models.calculation:CalculationRun" in registry
    assert case_plane_dependencies(registry) == set()
