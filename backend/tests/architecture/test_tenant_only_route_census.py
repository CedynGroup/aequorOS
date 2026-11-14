"""Every tenant-only route is recorded with the reason it is safe.

A tenant-only route is one whose FastAPI dependency set carries identity only —
no permission dependency and no scalar role dependency — so whatever decides
access lives in the service body, or the route serves no protected data at all.
The scalar route gates (``get_mutation_tenant_context`` /
``get_approver_tenant_context``) and every ``require_*`` permission dependency
are outside this census: they are route-level gates with their own guards, and
``get_scoped_mutation_tenant_context``'s documented contract is that the product
service requires the exact stored binding before any write.

The reviewed table below is that decision, executable. Two failure modes are
pinned, because both are how a gap ships:

* a NEW tenant-only route with no recorded reason, and
* a recorded entry (or known gap) whose route no longer exists or has gained a
  route-level gate — a stale entry that would otherwise rot into a lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, FastAPI

from app.api.deps import (
    DbSession,
    Tenant,
    get_current_principal,
)
from tests.support.route_authorization import (
    RegisteredEndpoint,
    registered_endpoints,
)

#: The identity-only dependency vocabulary. A tenant-only route's dependency
#: callables are a subset of this set. ``get_scoped_mutation_tenant_context`` is
#: deliberately absent — see the module docstring.
TENANT_ONLY_DEPENDENCIES = frozenset(
    {
        "app.api.deps:get_current_principal",
        "app.api.deps:get_tenant_context",
        "app.api.deps:get_tenant_db_session",
        "app.api.deps:resolve_tenant_bank",
        "fastapi.security.http:HTTPBearer",
    }
)

_CITATION = re.compile(r"app\.[a-z_][a-z_.0-9]*\.[a-z_][a-z_0-9]*")
_ISSUE_REFERENCE = re.compile(r"#\d+")


@dataclass(frozen=True)
class CensusEntry:
    """Why one tenant-only route is safe, and where the decision lives."""

    reason: Literal["scoped_in_service", "benign"]
    #: ``scoped_in_service`` cites ``module:function`` — the service code that
    #: evaluates the scoped binding. ``benign`` states why no data is exposed.
    detail: str


#: The reviewed census. Every tenant-only route appears exactly once, here or in
#: ``KNOWN_GAPS``. Add a route here only after reading the service it calls.
CENSUS: dict[tuple[str, str], CensusEntry] = {
    ("GET", "/api/v1/banks/{bank_id}"): CensusEntry(
        "scoped_in_service",
        "app.identity.service.banks.require_institution_coverage",
    ),
    ("GET", "/api/v1/banks/{bank_id}/alerts"): CensusEntry(
        "scoped_in_service",
        "app.live.service.alerts.get_bank_alerts (live_access.allowed_modules)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/analytics/window"): CensusEntry(
        "scoped_in_service",
        "app.liquidity.service.window_analytics.compute_window (live_access.allowed_modules)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/audit-log"): CensusEntry(
        "scoped_in_service",
        "app.identity.service.audit_log.read_log (require_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/canonical-positions"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/restricted)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/canonical-positions/facets"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/restricted)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/canonical-withdrawal-scope"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/confidential, in the handler)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/canonical-withdrawals"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/confidential, in the handler)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/data-activations"): CensusEntry(
        "scoped_in_service",
        "app.live.service.data_activation.list_bank_data_activations"
        " (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/database-direct/connections"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.database_connections.list_connections (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/database-direct/connections/{connection_id}"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.database_connections.get_connection"
        " (require_data_access, opaque)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/enterprise-stress/latest"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.enterprise_stress.get_latest_enterprise_stress"
        " (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/enterprise-stress/runs"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.enterprise_stress.list_enterprise_stress_runs"
        " (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/enterprise-stress/runs/{run_id}"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.enterprise_stress.get_enterprise_stress_run"
        " (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/enterprise-stress/signoffs"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.enterprise_stress_signoff.list_signoffs"
        " (scope_authorization.require_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.enterprise_stress_signoff._get_signoff_or_404"
        " (scope_authorization.require_permission, opaque)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/freshness"): CensusEntry(
        "scoped_in_service",
        "app.live.service.freshness.get_bank_freshness (live_access.allowed_modules)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/ingestion-batches"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.ingestion.list_batches (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/ingestion-batches/{batch_id}"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.ingestion.get_batch (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures"): (
        CensusEntry(
            "scoped_in_service",
            "app.data_engine.service.ingestion.list_translation_failures"
            " (require_data_access, opaque)",
        )
    ),
    ("GET", "/api/v1/banks/{bank_id}/ingestion-summary"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.ingestion.get_ingestion_summary (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/institution-profile"): CensusEntry(
        "scoped_in_service",
        "app.identity.service.institution_profile.get_authorized_profile"
        " (institution_profile_access.view_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/live-snapshots"): CensusEntry(
        "scoped_in_service",
        "app.live.service.live_view.list_live_snapshots (live_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/live-summary"): CensusEntry(
        "scoped_in_service",
        "app.live.service.live_view.get_live_summary (live_access.allowed_modules)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/mapping-configs"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.ingestion.list_mapping_configs (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/reconciliation/exceptions"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/confidential, in the handler)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-packages"): CensusEntry(
        "scoped_in_service",
        "app.reporting.service.family_access.hidden_families (per-family view filter)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation"): CensusEntry(
        "scoped_in_service",
        "app.attestation.service.api._get_package (package-family gate)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/placements"): (
        CensusEntry(
            "scoped_in_service",
            "app.attestation.service.api.resolved_placements"
            " (_get_package family gate, then EDIT authority)",
        )
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/verify"): (
        CensusEntry(
            "scoped_in_service",
            "app.attestation.service.api.verify (_get_package family gate)",
        )
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-runs"): CensusEntry(
        "scoped_in_service",
        "app.liquidity.service.regulatory_liquidity.list_regulatory_runs"
        " (per-module evaluate_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-runs/{run_id}"): CensusEntry(
        "scoped_in_service",
        "app.liquidity.service.regulatory_liquidity.require_regulatory_run_read",
    ),
    ("GET", "/api/v1/banks/{bank_id}/reporting-obligations"): CensusEntry(
        "scoped_in_service",
        "app.reporting.service.family_access.hidden_families (per-family view filter)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/reporting-periods"): CensusEntry(
        "scoped_in_service",
        "app.identity.service.banks.list_reporting_periods (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/reporting-periods/{period_id}/facts"): CensusEntry(
        "scoped_in_service",
        "app.identity.service.banks.get_period_facts (require_resolved_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/reports/comparison"): CensusEntry(
        "scoped_in_service",
        "app.reporting.service.report_comparison.build_comparison"
        " (require_regulatory_run_read)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/return-anchors"): CensusEntry(
        "scoped_in_service",
        "app.reporting.service.family_access.can_view (per-family view decision)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.analysis_workbench.list_analyses"
        " (scenario_workbench_authorization.require_module_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses/{analysis_id}"): (
        CensusEntry(
            "scoped_in_service",
            "app.stress.service.analysis_workbench.get_analysis"
            " (scenario_workbench_authorization.require_module_permission, opaque 404)",
        )
    ),
    ("GET", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.stress_scenarios.list_catalogue"
        " (scenario_workbench_authorization.require_module_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/sdi/readiness"): CensusEntry(
        "scoped_in_service",
        "app.capital.service.sdi_readiness.readiness_for_caller"
        " (READINESS_VIEW_MODULES, per-module aggregated view)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/submissions/bsd2"): CensusEntry(
        "scoped_in_service",
        "app.capital.service.regulatory_capital.get_bsd2_preview"
        " (require_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/submissions/bsd3"): CensusEntry(
        "scoped_in_service",
        "app.liquidity.service.regulatory_liquidity.get_bsd3_preview"
        " (require_bank_permission)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/system-of-record"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/confidential, in the handler)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/system-of-record-assessment"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.access.require_data_access (view/confidential, in the handler)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/temenos/connections"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.temenos_connections.list_connections (require_data_access)",
    ),
    ("GET", "/api/v1/banks/{bank_id}/temenos/domains"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.temenos_connections.list_domains (require_data_access)",
    ),
    ("GET", "/api/v1/lineage/{lineage_id}"): CensusEntry(
        "scoped_in_service",
        "app.data_engine.service.ingestion.walk_lineage (require_data_access, opaque)",
    ),
    ("GET", "/api/v1/macro-scenarios"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.macro_scenarios.list_scenarios"
        " (scope_authorization.visible_scopes row filter)",
    ),
    ("GET", "/api/v1/macro-scenarios/{scenario_id}"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.macro_scenarios._get_scenario_or_404"
        " (scope_authorization.require_permission, opaque)",
    ),
    ("GET", "/api/v1/macro-scenarios/{scenario_id}/translation/{module}"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.macro_scenarios.translate_scenario"
        " (scope_authorization.require_permission, opaque)",
    ),
    ("GET", "/api/v1/management-action-plans"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.management_action_plans.list_plans"
        " (scope_authorization.visible_scopes row filter)",
    ),
    ("GET", "/api/v1/management-action-plans/{plan_id}"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.management_action_plans._get_plan_or_404"
        " (scope_authorization.require_permission, opaque)",
    ),
    ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis"): CensusEntry(
        "scoped_in_service",
        "app.stress.service.analysis_workbench.run_analysis"
        " (scenario_workbench_authorization.require_module_permission)",
    ),
    ("GET", "/api/v1/assessment-types"): CensusEntry(
        "benign",
        "Static case-taxonomy vocabulary; no tenant, bank or configuration data.",
    ),
    ("GET", "/api/v1/risk-types"): CensusEntry(
        "benign",
        "Static case-taxonomy vocabulary; no tenant, bank or configuration data.",
    ),
    ("GET", "/api/v1/taxonomies/cases"): CensusEntry(
        "benign",
        "Static case-taxonomy vocabulary; no tenant, bank or configuration data.",
    ),
    ("GET", "/api/v1/auth/effective-authority"): CensusEntry(
        "benign",
        "The caller's own authority projection; reveals nothing about anyone else.",
    ),
    ("GET", "/api/v1/auth/me"): CensusEntry(
        "benign",
        "The caller's own profile.",
    ),
    ("PATCH", "/api/v1/auth/me"): CensusEntry(
        "benign",
        "The caller's own profile; only the caller's row is touched.",
    ),
    ("GET", "/api/v1/authorization/access-requests/mine"): CensusEntry(
        "benign",
        "The caller's own access requests, filtered by requester id.",
    ),
    ("POST", "/api/v1/authorization/access-requests"): CensusEntry(
        "benign",
        "Self-service request creation; writes only the caller's own request row.",
    ),
    ("GET", "/api/v1/organization/institutions/access-request"): CensusEntry(
        "benign",
        "The organization's institution directory, the minimum needed to target"
        " an access request; no financial or configuration data.",
    ),
    ("GET", "/api/v1/feature-flags"): CensusEntry(
        "benign",
        "Deployment feature flags; not tenant, bank or configuration data.",
    ),
    ("GET", "/api/v1/notifications"): CensusEntry(
        "benign",
        "The caller's own notifications, scoped to the persisted locator.",
    ),
    ("GET", "/api/v1/banks"): CensusEntry(
        "benign",
        "The caller's own organization's bank directory (membership navigation).",
    ),
    ("GET", "/api/v1/regulatory-reporting/templates"): CensusEntry(
        "benign",
        "The regulator return-template registry; the optional bank filter is"
        " coverage-checked in app.reporting.service.packages.list_return_templates.",
    ),
}

#: Gaps still open on origin/main, each naming the pull request that closes it.
#: Entries only ever leave this set — a closed gap becomes a CENSUS entry.
KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/banks/{bank_id}/regulatory-reporting/channel-configs/{channel}"): (
        "open; fix in PR #910"
    ),
    ("GET", "/api/v1/banks/{bank_id}/regulatory-reporting/settings"): "open; fix in PR #910",
    ("GET", "/api/v1/attestation/signature-placements"): "open; fix in PR #910",
    ("GET", "/api/v1/attestation/signing-policies"): "open; fix in PR #910",
    ("GET", "/api/v1/banks/{bank_id}/filing-workflow-templates"): "open; fix in PR #908",
}


def _registered_tenant_routes(
    applications: dict[str, FastAPI],
) -> tuple[dict[tuple[str, str], RegisteredEndpoint], list[str]]:
    """Every tenant-app route, keyed by (method, path)."""
    rows, errors = registered_endpoints(applications, {"tenant": get_current_principal})
    population: dict[tuple[str, str], RegisteredEndpoint] = {}
    for row in rows:
        key = (row.key[1], row.key[2])
        if key in population:
            errors.append(f"{key}: duplicate registered endpoint")
            continue
        population[key] = row
    return population, errors


def _tenant_only_subset(
    population: dict[tuple[str, str], RegisteredEndpoint],
) -> dict[tuple[str, str], RegisteredEndpoint]:
    subset: dict[tuple[str, str], RegisteredEndpoint] = {}
    for key, row in population.items():
        path = key[1]
        if not path.startswith("/api/v1"):
            continue
        if path.startswith("/api/v1/cases"):
            continue
        if set(row.dependencies) <= TENANT_ONLY_DEPENDENCIES:
            subset[key] = row
    return subset


def census_errors(
    population: dict[tuple[str, str], RegisteredEndpoint],
    tenant_only: dict[tuple[str, str], RegisteredEndpoint],
    *,
    recorded: dict[tuple[str, str], CensusEntry] | None = None,
    gaps: dict[tuple[str, str], str] | None = None,
) -> list[str]:
    """Everything wrong with the census against this route population."""
    recorded = CENSUS if recorded is None else recorded
    gaps = KNOWN_GAPS if gaps is None else gaps
    errors: list[str] = []
    overlap = recorded.keys() & gaps.keys()
    errors.extend(f"{key}: recorded twice (census entry and known gap)" for key in sorted(overlap))
    for key, entry in sorted(recorded.items()):
        route = population.get(key)
        if route is None:
            errors.append(f"{key}: stale census entry — the route no longer exists")
            continue
        if key not in tenant_only:
            errors.append(f"{key}: stale census entry — the route is no longer tenant-only")
        if entry.reason == "scoped_in_service" and not _CITATION.search(entry.detail):
            errors.append(f"{key}: scoped_in_service must cite an app path, got {entry.detail!r}")
        if entry.reason == "benign" and len(entry.detail) < 20:
            errors.append(f"{key}: benign needs a stated reason, got {entry.detail!r}")
    for key, issue in sorted(gaps.items()):
        if key not in population:
            errors.append(f"{key}: stale known gap — the route no longer exists")
            continue
        if key not in tenant_only:
            errors.append(f"{key}: stale known gap — the route is no longer tenant-only")
        if not _ISSUE_REFERENCE.search(issue):
            errors.append(f"{key}: known gap must name its fix PR or issue, got {issue!r}")
    for key in sorted(tenant_only.keys() - recorded.keys() - gaps.keys()):
        errors.append(
            f"{key}: tenant-only route without a recorded reason; read the service it"
            " calls and add it to CENSUS or KNOWN_GAPS in"
            " tests/architecture/test_tenant_only_route_census.py"
        )
    return errors


def _app() -> FastAPI:
    from app.main import create_app  # noqa: PLC0415 - import at call site like the coverage test

    return create_app()


def test_every_tenant_only_route_has_a_recorded_reason() -> None:
    population, errors = _registered_tenant_routes({"tenant": _app()})
    assert not errors, errors
    problems = census_errors(population, _tenant_only_subset(population))
    assert not problems, (
        "The tenant-only route census failed. Review the exact route and the service"
        " it calls before editing this table; never record a route as safe by default:\n  "
        + "\n  ".join(problems)
    )


def test_a_new_tenant_only_route_fails_the_census() -> None:
    """A synthetic tenant-only route with no recorded reason fails, by name."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/api/v1/banks/{bank_id}/widget-index")
    def widget_index(bank_id: str, db: DbSession, ctx: Tenant) -> dict[str, str]:
        return {"bank_id": bank_id}

    app.include_router(router)
    population, _ = _registered_tenant_routes({"tenant": app})
    tenant_only = _tenant_only_subset(population)
    assert ("GET", "/api/v1/banks/{bank_id}/widget-index") in tenant_only
    problems = census_errors(population, tenant_only)
    assert any("/api/v1/banks/{bank_id}/widget-index" in problem for problem in problems), problems


def test_a_recorded_route_that_no_longer_exists_fails_the_census() -> None:
    population, _ = _registered_tenant_routes({"tenant": _app()})
    tenant_only = _tenant_only_subset(population)
    retired = ("GET", "/api/v1/banks/{bank_id}/retired-surface")
    problems = census_errors(
        population,
        tenant_only,
        recorded={
            **CENSUS,
            retired: CensusEntry("benign", "A surface that was removed without cleaning up."),
        },
    )
    assert any("stale census entry — the route no longer exists" in p for p in problems), problems


def test_a_recorded_route_that_gained_a_route_gate_fails_the_census() -> None:
    """A recorded entry for a route that is no longer tenant-only fails."""
    population, _ = _registered_tenant_routes({"tenant": _app()})
    tenant_only = _tenant_only_subset(population)
    gated = ("GET", "/api/v1/banks/{bank_id}/credit/dashboard")
    assert gated in population, "the probe route must exist on the app"
    assert gated not in tenant_only, "the probe route must not itself be tenant-only"
    problems = census_errors(
        population,
        tenant_only,
        recorded={
            **CENSUS,
            gated: CensusEntry(
                "scoped_in_service", "app.credit.service.credit_dashboard.dashboard"
            ),
        },
    )
    assert any("no longer tenant-only" in p for p in problems), problems


def test_a_stale_known_gap_fails_the_census() -> None:
    population, _ = _registered_tenant_routes({"tenant": _app()})
    tenant_only = _tenant_only_subset(population)
    problems = census_errors(
        population,
        tenant_only,
        gaps={
            **KNOWN_GAPS,
            ("GET", "/api/v1/banks/{bank_id}/retired-surface"): "open; fix in PR #1",
        },
    )
    assert any("stale known gap — the route no longer exists" in p for p in problems), problems


def test_known_gaps_name_open_gaps_only() -> None:
    """Every known gap is a real unrecorded tenant-only route naming its fix."""
    for key in KNOWN_GAPS:
        assert key not in CENSUS, f"{key} is both recorded and a known gap"
    # The readiness route is gated by this repository's own change; it must not
    # sit in the gap set.
    assert ("GET", "/api/v1/banks/{bank_id}/sdi/readiness") not in KNOWN_GAPS
