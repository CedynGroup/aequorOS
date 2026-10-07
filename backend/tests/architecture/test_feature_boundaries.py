"""Feature boundaries: every ``app/`` module belongs to one feature, and features
import each other only downward, through a declared interface.

The risk service is moving from a layer-first tree (``services/``, ``domain/``,
``models/``, ``schemas/``, ``features/`` ...) to one package per feature,
``app/<feature>/``, each with a small ``public.py`` (see CODEBASE_CONVENTIONS.md
§5). This guard is the rail that migration runs on. It encodes three rules:

1. **Layering.** ``LAYERS`` orders the features; a module never imports a
   feature on a higher layer. Features on the same layer import each other only
   along a declared ``PEER_EDGES`` direction.
2. **Interfaces.** A cross-feature import targets the other feature's
   ``public`` module or its pure ``domain`` engines, never its services,
   models, schemas or routes.
3. **The kernel imports no feature.** The kernel is layer 0, so rule 1 already
   says so; it is called out because ``api/deps.py`` is today's largest leak.

The composition root (``main``, ``worker``, ``api/router``, the ``app.models``
registry and the scheduler) wires everything and is exempt as an importer.

**Ownership.** A module under ``app/<feature>/`` belongs to that feature. A
module still in the layered tree is assigned by ``FEATURE_RULES``, a
transitional table of path patterns where the first match wins. Every module
must have an owner, and every rule must still match a module, so the table
shrinks as each feature moves and cannot keep a dead pattern behind.

**Ratchet.** Today's tree breaks these rules in about a thousand places. Each
violation is recorded once per (importing module, imported module) pair in
``feature_boundary_baseline.json``. A violation not in the baseline fails, and
so does a baseline entry that no longer occurs, so the file only shrinks and
every improvement is recorded by the change that makes it. An import through
the ``app.models`` aggregator is charged to the submodule that defines the
name, so the registry cannot launder a dependency.

After fixing violations, rewrite the baseline with::

    uv run python -c "import tests.architecture.test_feature_boundaries as t; t.write_baseline()"

and review the diff: it must only delete lines.

Scanning is the shared AST scanner in ``_planes.py``; its limits (runtime-built
dynamic import names are invisible) are documented there.
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from pathlib import Path

from tests.architecture._planes import APP, imported_modules, module_name

BASELINE = Path(__file__).with_name("feature_boundary_baseline.json")

KERNEL = "kernel"
COMPOSITION = "composition"

#: Lower layers never import higher ones. ``kernel`` is the shared platform;
#: ``composition`` is the root that wires every feature together.
LAYERS: dict[str, int] = {
    KERNEL: 0,
    "identity": 1,
    "policy": 2,
    "notifications": 2,
    "data_engine": 3,
    "market_data": 3,
    "live": 4,
    "ai": 4,
    "liquidity": 5,
    "capital": 5,
    "credit": 5,
    "irrbb": 5,
    "fx": 5,
    "ftp": 5,
    "forecasting": 5,
    "behavioral": 5,
    "rating": 5,
    "stress": 6,
    "attestation": 6,
    "reporting": 7,
    "icaap": 8,
    "bi": 9,
    "case": 9,
    "operator": 10,
    COMPOSITION: 11,
}

#: Same-layer dependencies that are allowed, as (importer, imported). Engines
#: read liquidity and capital results; market data writes through the Data
#: Engine. Any other same-layer import is a violation.
PEER_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("market_data", "data_engine"),
        ("irrbb", "liquidity"),
        ("ftp", "liquidity"),
        ("fx", "liquidity"),
        ("credit", "liquidity"),
        ("forecasting", "liquidity"),
        ("liquidity", "capital"),
        ("forecasting", "capital"),
        ("credit", "capital"),
        ("rating", "capital"),
        ("liquidity", "behavioral"),
    }
)

#: Transitional ownership for code still in the layered tree: patterns on the
#: path relative to ``app/`` without ``.py``. First match wins. Delete a rule in
#: the change that moves the last module it matches.
FEATURE_RULES: tuple[tuple[str, str], ...] = (
    # ---- composition root
    (r"^(main|worker|api/router|models/__init__|services/scheduler)$", COMPOSITION),
    # ---- shared kernel
    (r"^__init__$", KERNEL),
    (r"^(core|db|storage|integrations)(/|$)", KERNEL),
    (r"^api/(__init__|deps|health)$", KERNEL),
    (r"^api/v1/__init__$", KERNEL),
    (r"^(services|schemas|models|features|domain|jobs|ml)/__init__$", KERNEL),
    (r"^services/(audit|mailer|job_queue|jobs|public_ids)$", KERNEL),
    (r"^models/(audit_event|organization)$", KERNEL),
    (r"^schemas/(common|text|health|jobs)$", KERNEL),
    (r"^features/track_jobs$", KERNEL),
    (r"^domain/(risk_constants|authority/.*|workflow/.*)$", KERNEL),
    # ---- staff operator console
    (r"^(models|schemas)/operator$", "operator"),
    # ---- identity and access
    (r"^api/v1/auth$", "identity"),
    (
        r"^services/(authentication|authorization|auth_throttle|scoped_authorization"
        r"|grant_administration|membership|sso_config|integration_keys"
        r"|organization_ownership|banks|institution_profile)$",
        "identity",
    ),
    (
        r"^models/(authorization|user|refresh_token|sso_connection|integration_key"
        r"|institution_profile)$",
        "identity",
    ),
    (
        r"^schemas/(auth|authorization|banks|integration_keys|institution_profile"
        r"|feature_flags)$",
        "identity",
    ),
    (
        r"^features/(manage_authorization|manage_banks|manage_integration_keys"
        r"|list_organization_users|manage_institution_profile|read_feature_flags)$",
        "identity",
    ),
    # ---- policy: jurisdiction, regime, parameter registers
    (
        r"^services/(regulatory_parameters|jurisdictions|institution_types|module_scope"
        r"|parameter_register|params|sdi_regime)$",
        "policy",
    ),
    (r"^models/(regulatory_parameter|jurisdiction|institution_type)$", "policy"),
    (r"^domain/policy/", "policy"),
    # ---- notifications
    (
        r"^services/(notifications|notification_email_mirror|reporting_deadline_scan)$",
        "notifications",
    ),
    (
        r"^(models/notification|schemas/notifications|features/manage_notifications)$",
        "notifications",
    ),
    # ---- live engine and the facts spine
    (
        r"^services/(fact_derivation|pipeline|live_.*|freshness|alerts|reporting_periods"
        r"|regulatory_dashboard_batching|data_activation)$",
        "live",
    ),
    (r"^models/(facts|live|regulatory|regulatory_run)$", "live"),
    (r"^(schemas/live|features/manage_live_engine)$", "live"),
    (r"^domain/(positions|reporting|gl)/", "live"),
    # ---- data engine
    (r"^adapters/__init__$", "data_engine"),
    (r"^adapters/(api_push|database_direct|excel_csv|temenos_t24)(/|$)", "data_engine"),
    (r"^etl(/|$)", "data_engine"),
    (r"^domain/ingestion/", "data_engine"),
    (
        r"^services/(ingestion|push_ingestion|database_connections|database_direct_jobs"
        r"|temenos_connections|temenos_jobs|system_of_record|canonical_withdrawal"
        r"|withdrawal_impact|history_loader|etl_dedup_jobs|etl_model_training"
        r"|document_uploads|reconciliation)$",
        "data_engine",
    ),
    (
        r"^models/(canonical|canonical_withdrawal|ingestion|database_connection|temenos"
        r"|system_of_record|reconciliation)$",
        "data_engine",
    ),
    (
        r"^schemas/(ingestion|push|database_connection|temenos_connections|system_of_record"
        r"|reconciliation|data_activation)$",
        "data_engine",
    ),
    (r"^api/v1/database_connections$", "data_engine"),
    (
        r"^features/(ingest_data|push_data|manage_temenos_connections"
        r"|manage_system_of_record|manage_reconciliation)$",
        "data_engine",
    ),
    # ---- market data
    (r"^adapters/market_data(/|$)", "market_data"),
    (r"^domain/curves/", "market_data"),
    (r"^services/(market_data.*|market_desk(/.*)?)$", "market_data"),
    (
        r"^models/(market_data.*|market_desk.*|desk_operating_environment|entitlements)$",
        "market_data",
    ),
    (r"^schemas/(market_data.*|market_desk.*|fx_forward)$", "market_data"),
    (
        r"^features/(manage_market_data_.*|market_data_sources|read_market_data_views)$",
        "market_data",
    ),
    # ---- legacy case vertical
    (
        r"^services/(cases|case_plane|case_types|calculations|capital|liquidity|financial_.*"
        r"|financial_mapping/.*|assessments|documents|findings|reports|scoring|scenarios"
        r"|scenario_semantics)$",
        "case",
    ),
    (r"^models/(risk|calculation|capital|financial|scenario)$", "case"),
    (
        r"^schemas/(cases|calculations|capital|liquidity|financial_workspace.*|assessments"
        r"|documents|findings|taxonomy|scenarios)$",
        "case",
    ),
    (
        r"^features/(bulk_update_cases|generate_case_reports|list_case_taxonomy|list_taxonomy"
        r"|manage_capital|manage_documents|read_financial_workspace|record_case_decisions"
        r"|review_cases|review_findings|review_liquidity|run_assessments|run_calculations"
        r"|manage_scenarios)$",
        "case",
    ),
    # ---- regulatory engines
    (
        r"^(services/(regulatory_liquidity|liquidity_cfp|liquidity_ewi|liquidity_thresholds"
        r"|behavioral_liquidity|cashflow_window|window_analytics)|domain/liquidity/.*"
        r"|models/liquidity_cfp|schemas/(regulatory_liquidity|liquidity_cfp"
        r"|liquidity_thresholds|cashflow_window|window_analytics)"
        r"|features/(run_regulatory_liquidity|manage_liquidity_cfp|manage_liquidity_thresholds"
        r"|read_liquidity_monitoring|read_cashflow_window|read_window_analytics))$",
        "liquidity",
    ),
    (
        r"^(services/regulatory_irr(_sf)?|domain/irr/.*|schemas/regulatory_irr(_sf)?"
        r"|features/run_regulatory_irr)$",
        "irrbb",
    ),
    (
        r"^(services/regulatory_fx|domain/fx/.*|schemas/regulatory_fx"
        r"|features/run_regulatory_fx)$",
        "fx",
    ),
    (
        r"^(services/regulatory_ftp|domain/ftp/.*|schemas/regulatory_ftp"
        r"|features/run_regulatory_ftp)$",
        "ftp",
    ),
    (
        r"^(services/(regulatory_capital|sdi_capital.*|sdi_views|sdi_readiness|capital_plan)"
        r"|domain/capital/.*|models/capital_plan|schemas/(regulatory_capital|sdi|capital_plan)"
        r"|features/(run_regulatory_capital|manage_capital_plan|read_sdi_diagnostics))$",
        "capital",
    ),
    (
        r"^(services/(regulatory_credit|credit_.*|loan_classification)|domain/credit/.*"
        r"|schemas/(regulatory_credit|credit_params)"
        r"|features/(run_regulatory_credit|manage_credit_params))$",
        "credit",
    ),
    (
        r"^(services/(implied_rating|sdi_rating)|domain/rating/.*|models/implied_rating"
        r"|schemas/implied_rating|features/run_implied_rating)$",
        "rating",
    ),
    (
        r"^(services/behavioral_models|ml/behavioral/.*|schemas/behavioral_models"
        r"|features/read_behavioral_models)$",
        "behavioral",
    ),
    (
        r"^(services/(regulatory_forecasting|cashflow_forecast)|domain/forecasting/.*"
        r"|schemas/(forecasting|cashflow_forecast)"
        r"|features/(run_forecasting|read_cashflow_forecast)|ml/.*)$",
        "forecasting",
    ),
    # ---- stress, attestation, filing, ICAAP, BI, AI
    (
        r"^(services/(stress_scenarios|macro_scenarios|default_macro_scenarios"
        r"|enterprise_stress.*|enterprise_run_visibility|reverse_stress"
        r"|management_action_plans|analysis_workbench|scenario_catalog"
        r"|scenario_workbench_authorization)|domain/(stress|scenarios)/.*"
        r"|models/(stress|scenario_workbench)|schemas/(stress|enterprise_stress.*"
        r"|reverse_stress|management_actions|scenario_workbench)"
        r"|features/(manage_stress_scenarios|manage_macro_scenarios|manage_enterprise_stress.*"
        r"|run_reverse_stress|manage_management_actions|run_scenario_analysis))$",
        "stress",
    ),
    (
        r"^(services/(attestation/.*|attestation_api)|models/attestation|schemas/attestation"
        r"|features/manage_attestation)$",
        "attestation",
    ),
    (
        r"^(services/(regulatory_reporting/.*|filing_workflow/.*|filing_reconciliation"
        r"|report_comparison|examiner_mode)|domain/filing/.*"
        r"|models/(regulatory_reporting|filing_workflow)"
        r"|schemas/(regulatory_reporting|filing_workflow|report_comparison|examiner)"
        r"|features/(manage_regulatory_reporting|manage_filing_workflow|examiner_surfaces"
        r"|manage_package_attachments))$",
        "reporting",
    ),
    (
        r"^(services/icaap/.*|domain/icaap/.*|models/icaap.*|schemas/icaap.*"
        r"|features/(manage_icaap.*|export_icaap_drafts))$",
        "icaap",
    ),
    (
        r"^(services/bi/.*|domain/bi/.*|models/bi.*|schemas/bi.*|jobs/bi.*"
        r"|features/(ask_bi|export_bi|manage_bi_.*|read_bi.*))$",
        "bi",
    ),
    (r"^(services/ai/.*|domain/ai/.*|models/ai|schemas/ai|features/manage_ai_settings)$", "ai"),
)
_COMPILED_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), feature) for pattern, feature in FEATURE_RULES
)

#: A feature's interface: the re-export module other features call.
_PUBLIC = "public"
#: Pure engines, importable from any higher layer (``app/<feature>/domain/``
#: once moved, ``app/domain/<area>/`` until then).
_DOMAIN = "domain"


def _app_files() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py") if "__pycache__" not in path.parts)


def _module(path: Path) -> str:
    """Dotted module name, naming a package by its directory."""
    return module_name(path).removesuffix(".__init__")


def feature_of(path: Path) -> str | None:
    """The feature that owns ``path``: its ``app/<feature>/`` directory, else the rules."""
    relative = path.relative_to(APP).with_suffix("").as_posix()
    head = relative.split("/", 1)[0]
    if head in LAYERS and head not in {KERNEL, COMPOSITION}:
        return head
    for pattern, feature in _COMPILED_RULES:
        if pattern.search(relative):
            return feature
    return None


@cache
def _owners() -> dict[str, str]:
    """Every owned module's dotted name -> its feature."""
    return {
        _module(path): feature for path in _app_files() if (feature := feature_of(path)) is not None
    }


@cache
def _model_reexports() -> dict[str, str]:
    """``app.models.<Name>`` -> the submodule that defines it.

    ``app.models`` is the metadata registry, not an API: an import through it is
    charged to the real owner rather than to the composition root.
    """
    tree = ast.parse((APP / "models" / "__init__.py").read_text(encoding="utf-8"))
    return {
        f"app.models.{alias.asname or alias.name}": node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
        for alias in node.names
    }


def _defining_module(target: str) -> str | None:
    """The owned module ``target`` names, walking up from an imported attribute."""
    owners = _owners()
    probe = target
    while probe:
        if probe in owners:
            return probe
        probe = probe.rpartition(".")[0]
    return None


def _without_parent_packages(imports: set[str]) -> set[str]:
    """Drop names that only prefix another imported name.

    ``from app.fx import service`` yields both ``app.fx`` and ``app.fx.service``; the
    package is named only to reach the module, so the module is the dependency.
    """
    return {name for name in imports if not any(other.startswith(f"{name}.") for other in imports)}


def _is_interface(target: str, feature: str) -> bool:
    """Whether ``target`` is a part of ``feature`` other features may import."""
    parts = target.split(".")
    if parts[1:2] == [_DOMAIN]:
        return True
    return parts[1:2] == [feature] and parts[2:3] in ([_PUBLIC], [_DOMAIN])


def _violation(source: str, dest: str, target: str) -> str | None:
    """The rule an import of ``target`` (owned by ``dest``) from ``source`` breaks."""
    if source in (dest, COMPOSITION) or dest == KERNEL:
        return None
    if LAYERS[dest] > LAYERS[source]:
        return "layer"
    if LAYERS[dest] == LAYERS[source] and (source, dest) not in PEER_EDGES:
        return "layer"
    return None if _is_interface(target, dest) else "private"


def violations() -> frozenset[str]:
    """Every boundary violation in ``app/``, one entry per module-to-module import."""
    owners = _owners()
    reexports = _model_reexports()
    found: set[str] = set()
    for path in _app_files():
        module = _module(path)
        source = owners[module]
        imports = imported_modules(path.read_text(encoding="utf-8"), module=module_name(path))
        for name in _without_parent_packages(imports):
            imported = reexports.get(name, name)
            if imported == "app.models" or not imported.startswith("app."):
                continue
            target = _defining_module(imported)
            if target is None or target == module:
                continue
            dest = owners[target]
            if (kind := _violation(source, dest, target)) is not None:
                found.add(f"{kind} {source}->{dest}: {module} -> {target}")
    return frozenset(found)


def write_baseline() -> None:
    """Record today's violations as the baseline (review the diff: deletions only)."""
    BASELINE.write_text(json.dumps(sorted(violations()), indent=1) + "\n", encoding="utf-8")


def _baseline() -> list[str]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_every_app_module_has_an_owner() -> None:
    unowned = sorted(
        path.relative_to(APP).as_posix() for path in _app_files() if feature_of(path) is None
    )
    assert unowned == [], (
        "These modules belong to no feature. Put new code under app/<feature>/, or "
        f"extend FEATURE_RULES while the tree is still layered: {unowned}"
    )


def test_every_ownership_rule_still_owns_a_module() -> None:
    """A rule that wins no module is dead: its code moved, or an earlier rule shadows it."""
    winning: set[str] = set()
    for path in _app_files():
        relative = path.relative_to(APP).with_suffix("").as_posix()
        winner = next((p.pattern for p, _f in _COMPILED_RULES if p.search(relative)), None)
        if winner is not None:
            winning.add(winner)
    dead = [pattern for pattern, _feature in FEATURE_RULES if pattern not in winning]
    assert dead == [], f"Delete these FEATURE_RULES; they own no module: {dead}"


def test_layers_and_peer_edges_name_known_features() -> None:
    features = set(LAYERS)
    assert {feature for _pattern, feature in FEATURE_RULES} <= features
    for importer, imported in PEER_EDGES:
        assert {importer, imported} <= features
        assert LAYERS[importer] == LAYERS[imported], (importer, imported)
        assert (imported, importer) not in PEER_EDGES, "a peer edge runs one way"


def test_the_baseline_is_sorted_and_unique() -> None:
    baseline = _baseline()
    assert baseline == sorted(set(baseline)), f"Regenerate {BASELINE.name} with write_baseline()"


def test_no_new_boundary_violation_and_the_baseline_only_shrinks() -> None:
    current = violations()
    baseline = set(_baseline())
    new = sorted(current - baseline)
    fixed = sorted(baseline - current)
    assert new == [], (
        "These imports break the feature layout (CODEBASE_CONVENTIONS.md §5). Import "
        "the other feature's public module or pure domain engine, or move the code "
        f"to the right layer: {new}"
    )
    assert fixed == [], (
        f"These violations are gone; delete them from {BASELINE.name} so the "
        f"ratchet keeps the gain: {fixed}"
    )


def test_the_scanner_charges_each_rule() -> None:
    """Negative controls: each rule fires on a synthetic import, so a green run is not vacuous."""
    assert _violation("identity", "live", "app.live.public") == "layer"
    assert _violation("liquidity", "fx", "app.fx.public") == "layer"
    assert _violation(KERNEL, "icaap", "app.icaap.public") == "layer"
    assert _violation("stress", "fx", "app.services.regulatory_fx") == "private"
    assert _violation("stress", "fx", "app.fx.service") == "private"
    assert _violation("stress", "fx", "app.fx.public") is None
    assert _violation("stress", "fx", "app.fx.domain.engine") is None
    assert _violation("stress", "fx", "app.domain.fx.engine") is None
    assert _violation("fx", "liquidity", "app.liquidity.public") is None
    assert _violation(COMPOSITION, "icaap", "app.services.icaap.workspace") is None
    assert _violation("icaap", KERNEL, "app.services.audit") is None
    assert _defining_module("app.services.regulatory_fx.any_name") == "app.services.regulatory_fx"
    assert _model_reexports()["app.models.Bank"] == "app.models.regulatory"
