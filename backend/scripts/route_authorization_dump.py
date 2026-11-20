"""Regenerate the route authorization declaration fixture from the live apps.

The fixture (``tests/fixtures/route_authorization.json``) is generated, never
hand-edited: run this script, review the diff, and commit it together with the
route change that caused it. ``mise run risk-service:route-authorization``
regenerates the file; ``mise run risk-service:route-authorization-fresh`` fails
when the committed copy no longer matches the registered routes.

Grouping and naming follow the catalog that the coverage test consumes
(``tests/support/route_authorization.py``): routes are grouped by their exact
registered dependency set, public routes come from
``tests/fixtures/public_routes.py``, and the dependency walk is the SAME
introspection the coverage test uses. Groups whose name or membership was
chosen by earlier hand curation are pinned in ``PINNED`` below so this script
reproduces the committed file byte for byte; a pin whose routes disappear is a
deliberate, reviewed edit. Route order inside a rule and rule order in the
catalog are canonical sorts, with the few historically hand-ordered rules
pinned in ``ROUTE_ORDER_PINS`` and ``RULE_ORDER_PIN``. Output is deterministic
and needs no database or network: environment is pinned before the application
imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, TypedDict

from pydantic import BaseModel, ConfigDict, ValidationError

from tests.fixtures.public_routes import REVIEWED_PUBLIC_ROUTES
from tests.support.route_authorization import (
    AuthorizationCatalog,
    RegisteredEndpoint,
    registered_endpoints,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = BACKEND_ROOT / "tests" / "fixtures" / "route_authorization.json"
PUBLIC_ROUTES_REFERENCE = "tests/fixtures/public_routes.py:REVIEWED_PUBLIC_ROUTES"
REGENERATE_TASK = "mise run risk-service:route-authorization"

RouteKey = tuple[str, str, str]
PinnedRoute = tuple[str, str, str, str]

STANDARD_BASIS: Final[dict[str, str]] = {
    "tenant": (
        "Existing tenant authentication boundary and exact registered dependency calls "
        "listed here. Named scoped/legacy gates remain unchanged; handler and service "
        "checks can narrow this access further. Presence is not policy correctness."
    ),
    "operator": (
        "Existing operator authentication boundary and exact registered dependency calls "
        "listed here. Named scoped/legacy gates remain unchanged; handler and service "
        "checks can narrow this access further. Presence is not policy correctness."
    ),
}

class _PinnedDependencyGroup(TypedDict, total=False):
    """A dependency group whose name, basis or membership was hand-curated.

    ``dependencies`` records the reviewed dependency set the group's routes
    registered when the pin was last reviewed; generation fails when a live
    route drifts from it, so a gate lost on a pinned route is an explicit pin
    update, never a silent regeneration."""

    dependencies: list[str]
    basis: str
    references: list[str]
    routes: list[PinnedRoute]
    field_order: list[str]


class _PinnedHandlerRule(TypedDict):
    """A handler/rejection rule with a pinned name, wording and route set."""

    kind: str
    basis: str
    references: list[str]
    routes: list[PinnedRoute]


#: Dependency groups whose name, membership or basis was fixed by a specific,
#: reviewed authorization change. Every other authenticated route is grouped by
#: its exact dependency set and named ``<application>-dependencies-<hash>``.
#: ``basis`` pins a hand-written sentence (the standard basis is used when
#: None); ``references`` pins hand-written references (they are derived from
#: the dependency list when None).
#: Reviewed dependency sets, recorded exactly as the committed catalog declares
#: them. Dependency pins reference these so a live registration that drifts
#: from the reviewed set fails generation instead of regenerating silently.
_TENANT_SCOPED_MUTATION_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_scoped_mutation_tenant_context",
    "app.api.deps:get_tenant_context",
    "app.api.deps:get_tenant_db_session",
    "app.api.deps:resolve_tenant_bank",
    "fastapi.security.http:HTTPBearer",
)
_TENANT_SESSION_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_tenant_context",
    "app.api.deps:get_tenant_db_session",
    "fastapi.security.http:HTTPBearer",
)
_TENANT_CONTEXT_ONLY_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_tenant_context",
    "fastapi.security.http:HTTPBearer",
)
_TENANT_JOBS_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_tenant_context",
    "app.api.deps:get_tenant_db_session",
    "app.api.deps:get_tenant_job_visibility",
    "fastapi.security.http:HTTPBearer",
)
_TENANT_NOTIFICATIONS_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_scoped_mutation_tenant_context",
    "app.api.deps:get_tenant_context",
    "app.api.deps:get_tenant_db_session",
    "fastapi.security.http:HTTPBearer",
)
_TENANT_MAPPING_CONFIG_DEPENDENCIES: Final[tuple[str, ...]] = (
    "app.api.deps:get_current_principal",
    "app.api.deps:get_scoped_mutation_tenant_context",
    "app.api.deps:get_tenant_context",
    "app.api.deps:get_tenant_db_session",
    "app.api.deps:resolve_tenant_bank",
    "app.data_engine.api.ingest_data:require_configure_access",
    "fastapi.security.http:HTTPBearer",
)


def _tenant_storage_download_dependencies(permission: str) -> list[str]:
    """The reviewed dependency set of the storage-backed single-route pins."""
    return [
        "app.api.deps:get_current_principal",
        "app.api.deps:get_tenant_context",
        "app.api.deps:get_tenant_db_session",
        f"app.api.deps:{permission}",
        "app.api.deps:resolve_tenant_bank",
        "app.storage.deps:get_http_storage",
        "fastapi.security.http:HTTPBearer",
    ]


PINNED: Final[dict[str, _PinnedDependencyGroup]] = {
    "tenant-dependencies-filing-templates-scoped": {
        "dependencies": list(_TENANT_SCOPED_MUTATION_DEPENDENCIES),
        "basis": (
            "The filing review-chain template writes require scoped mutation tenancy; "
            "the service layer enforces the complete REG/restricted sentence (configure "
            "for propose/update/submit, approve with the maker-checker condition for "
            "the decision)."
        ),
        "routes": [
            (
                "tenant",
                "PATCH",
                "/api/v1/banks/{bank_id}/filing-workflow-templates/{template_id}",
                "app.reporting.api.manage_filing_workflow:update_filing_workflow_template",
            ),
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/filing-workflow-templates",
                "app.reporting.api.manage_filing_workflow:propose_filing_workflow_template",
            ),
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/filing-workflow-templates/{template_id}/decision",
                "app.reporting.api.manage_filing_workflow:decide_filing_workflow_template",
            ),
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/filing-workflow-templates/{template_id}/submit",
                "app.reporting.api.manage_filing_workflow:submit_filing_workflow_template",
            ),
        ],
    },
    "tenant-dependencies-liq-scoped-configure": {
        "dependencies": list(_TENANT_SCOPED_MUTATION_DEPENDENCIES),
        "basis": (
            "LIQ and CREDIT configuration writes require scoped mutation tenancy; the "
            "service layer enforces the complete configure binding."
        ),
        "routes": [
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/liquidity-haircuts",
                "app.liquidity.api.manage_liquidity_thresholds:update_liquidity_haircut_schedule",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/liquidity-thresholds",
                "app.liquidity.api.manage_liquidity_thresholds:update_liquidity_threshold_register",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/liquidity/ewis",
                "app.liquidity.api.manage_liquidity_cfp:update_ewi_register",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/concentration-limits",
                "app.credit.api.manage_credit_params:update_concentration_limit_register",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/credit-thresholds",
                "app.credit.api.manage_credit_params:update_credit_threshold_register",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/crm-haircuts",
                "app.credit.api.manage_credit_params:update_crm_haircut_register",
            ),
            (
                "tenant",
                "PUT",
                "/api/v1/banks/{bank_id}/ecl-assumptions",
                "app.credit.api.manage_credit_params:update_ecl_assumption_register",
            ),
        ],
    },
    "tenant-taxonomy-authenticated": {
        "dependencies": list(_TENANT_CONTEXT_ONLY_DEPENDENCIES),
        "basis": (
            "The separately authorized taxonomy authentication change is landed. These "
            "exact static-vocabulary GETs require the existing Tenant authentication "
            "chain, with no additional role or module-scope gate."
        ),
        "references": [
            "app/case/api/list_taxonomy.py:risk_types and assessment_types",
            "app/case/api/list_case_taxonomy.py:case_taxonomy_resource",
            "tests/case/api/test_taxonomy_authentication_integration.py",
        ],
        "routes": [
            (
                "tenant",
                "GET",
                "/api/v1/assessment-types",
                "app.case.api.list_taxonomy:assessment_types",
            ),
            ("tenant", "GET", "/api/v1/risk-types", "app.case.api.list_taxonomy:risk_types"),
            (
                "tenant",
                "GET",
                "/api/v1/taxonomies/cases",
                "app.case.api.list_case_taxonomy:case_taxonomy_resource",
            ),
        ],
    },
    "tenant-dependencies-ee37d1b6984c": {
        "dependencies": list(_TENANT_MAPPING_CONFIG_DEPENDENCIES),
        "basis": (
            "Existing tenant authentication boundary and exact registered dependency calls "
            "listed here. The configure gate narrows mapping-config creation further at "
            "the dependency layer; handler and service checks can narrow this access "
            "further. Presence is not policy correctness."
        ),
        "routes": [
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/mapping-configs",
                "app.data_engine.api.ingest_data:create_mapping_config",
            ),
        ],
        # This rule was appended to the catalog by hand with a non-canonical
        # field order; pinned so regeneration stays byte-identical.
        "field_order": [
            "dependencies",
            "routes",
            "name",
            "kind",
            "basis",
            "references",
            "authentication",
        ],
    },
    "tenant-dependencies-278ad5b308ca": {
        "dependencies": _tenant_storage_download_dependencies("require_package_edit"),
        "routes": [
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attachments",
                "app.reporting.api.manage_package_attachments:upload_package_attachment",
            ),
        ],
    },
    "tenant-dependencies-6c6852407fad": {
        "dependencies": _tenant_storage_download_dependencies("require_icaap_edit"),
        "routes": [
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments",
                "app.icaap.api.manage_icaap_attachments:upload_icaap_attachment",
            ),
        ],
    },
    "tenant-dependencies-75a23b39c9be": {
        "dependencies": _tenant_storage_download_dependencies("require_icaap_view"),
        "routes": [
            (
                "tenant",
                "GET",
                "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments/{attachment_id}/download",
                "app.icaap.api.manage_icaap_attachments:download_icaap_attachment",
            ),
        ],
    },
    "tenant-dependencies-793495930b9a": {
        "dependencies": list(_TENANT_NOTIFICATIONS_DEPENDENCIES),
        "routes": [
            (
                "tenant",
                "POST",
                "/api/v1/notifications/read-all",
                "app.notifications.api.manage_notifications:mark_all_notifications_read",
            ),
            (
                "tenant",
                "POST",
                "/api/v1/notifications/{notification_id}/read",
                "app.notifications.api.manage_notifications:mark_notification_read",
            ),
        ],
    },
    "tenant-dependencies-ceca96895d81": {
        "dependencies": list(_TENANT_JOBS_DEPENDENCIES),
        "routes": [
            ("tenant", "GET", "/api/v1/jobs", "app.features.track_jobs:list_jobs"),
            ("tenant", "GET", "/api/v1/jobs/{job_id}", "app.features.track_jobs:get_job"),
        ],
    },
    "tenant-dependencies-e6bc73594197": {
        "dependencies": _tenant_storage_download_dependencies("require_icaap_create"),
        "routes": [
            (
                "tenant",
                "POST",
                "/api/v1/banks/{bank_id}/icaap/supervisory-addons",
                "app.icaap.api.manage_icaap_supervisory_addons:create_icaap_supervisory_addon",
            ),
        ],
    },
    "tenant-dependencies-7a78c6513911": {
        "dependencies": _tenant_storage_download_dependencies("require_icaap_view"),
        "routes": [
            (
                "tenant",
                "GET",
                "/api/v1/banks/{bank_id}/icaap/supervisory-addons/{addon_id}/letter",
                "app.icaap.api.manage_icaap_supervisory_addons:download_icaap_supervisory_addon_letter",
            ),
        ],
    },
}

#: Credential exchanges and signed capabilities authenticate in the handler, so
#: they carry no dependency set to group on. Their names, basis sentences and
#: references record the exact reviewed decision and are pinned; the routes and
#: dependency lists still come from the live applications.
PINNED_HANDLER_RULES: Final[dict[str, _PinnedHandlerRule]] = {
    "operator-password-exchange": {
        "kind": "handler",
        "basis": (
            "Operator password, active staff account and throttle checks precede issuance "
            "of the separate workforce session; this is not a public staff-data read."
        ),
        "references": [
            "app/operator/features/auth.py:operator_login",
            "tests/operator/test_operator_auth_integration.py",
        ],
        "routes": [
            (
                "operator",
                "POST",
                "/operator/auth/login",
                "app.operator.features.auth:operator_login",
            ),
        ],
    },
    "tenant-oidc-exchange": {
        "kind": "handler",
        "basis": (
            "Existing OIDC verification, linking/JIT and account activation checks govern "
            "token exchange; no automatic activation is authorized by this declaration."
        ),
        "references": [
            "app/identity/api/auth.py:sso_login",
            "app/identity/service/authentication.py:login_with_sso",
            "tests/identity/api/test_auth_integration.py",
        ],
        "routes": [("tenant", "POST", "/api/v1/auth/sso", "app.identity.api.auth:sso_login")],
    },
    "tenant-password-exchange": {
        "kind": "handler",
        "basis": (
            "Password verification, account status and durable throttling precede session "
            "issuance; this is a credential exchange, not an anonymous data read."
        ),
        "references": [
            "app/identity/api/auth.py:login",
            "app/identity/service/authentication.py:login_with_password",
            "tests/identity/api/test_auth_integration.py",
        ],
        "routes": [("tenant", "POST", "/api/v1/auth/login", "app.identity.api.auth:login")],
    },
    "tenant-refresh-token": {
        "kind": "handler",
        "basis": (
            "Existing refresh credential verification and rotation govern session renewal, "
            "without requiring a still-valid bearer access token."
        ),
        "references": [
            "app/identity/api/auth.py:refresh",
            "app/identity/service/authentication.py:refresh_tokens",
            "tests/identity/api/test_auth_integration.py",
        ],
        "routes": [("tenant", "POST", "/api/v1/auth/refresh", "app.identity.api.auth:refresh")],
    },
    "tenant-signed-download-capability": {
        "kind": "handler",
        "basis": (
            "NOT PUBLIC: handler verifies the signed download capability before encrypted "
            "storage read; unavailable connected bank keys remain a refusal."
        ),
        "references": [
            "app/storage/api.py:download_bank_object",
            "app/storage/downloads.py:verify",
            "tests/storage/test_bank_encryption_integration.py",
        ],
        "routes": [
            ("tenant", "GET", "/api/v1/storage/download", "app.storage.api:download_bank_object"),
        ],
    },
    "tenant-sso-internal-key": {
        "kind": "handler",
        "basis": (
            "NOT PUBLIC: handler requires a configured SSO_INTERNAL_KEY and constant-time "
            "header comparison before resolving server-to-server OIDC client "
            "configuration. Unconfigured returns 404; missing/invalid internal key "
            "returns 401."
        ),
        "references": ["app/identity/api/auth.py:sso_client_config"],
        "routes": [
            (
                "tenant",
                "GET",
                "/api/v1/auth/sso/client-config",
                "app.identity.api.auth:sso_client_config",
            ),
        ],
    },
    "unsupported-case-financial-entity-rejection": {
        "kind": "rejection",
        "basis": (
            "Explicit rejection-only fallback: valid identifiers always receive 404 "
            "without session, storage or financial operations. This declares no resource "
            "access."
        ),
        "references": [
            "app/case/api/read_financial_workspace.py:reject_unsupported_financial_entity_create",
            "app/case/api/read_financial_workspace.py:reject_unsupported_financial_entity_update",
            "tests/case/api/test_case_authorization_integration.py:test_unsupported_entities_are_public_noop_404",
        ],
        "routes": [
            (
                "tenant",
                "PATCH",
                "/api/v1/cases/{case_id}/financial-workspace/{unsupported_entity_type}/{entity_id}",
                "app.case.api.read_financial_workspace:reject_unsupported_financial_entity_update",
            ),
            (
                "tenant",
                "POST",
                "/api/v1/cases/{case_id}/financial-workspace/{unsupported_entity_type}",
                "app.case.api.read_financial_workspace:reject_unsupported_financial_entity_create",
            ),
        ],
    },
}


class _PinError(RuntimeError):
    """A pinned group no longer matches the registered applications."""


class _CommittedFixture(BaseModel):
    """The one field this script carries forward from the committed copy."""

    model_config = ConfigDict(extra="ignore")

    baseline: str


#: Route order inside a rule. Most rules list routes by the canonical
#: ``(application, method, path, endpoint)`` sort; these four grew by
#: hand-maintained feature order instead and are pinned so regeneration
#: reproduces the committed file. New routes in a pinned rule are appended
#: after the pinned ones, in canonical order. Entries naming routes that no
#: longer exist are ignored.
ROUTE_ORDER_PINS: Final[dict[str, list[str]]] = {
    "tenant-dependencies-liq-scoped-configure": [
        "PUT /api/v1/banks/{bank_id}/liquidity-haircuts",
        "PUT /api/v1/banks/{bank_id}/liquidity-thresholds",
        "PUT /api/v1/banks/{bank_id}/liquidity/ewis",
        "PUT /api/v1/banks/{bank_id}/concentration-limits",
        "PUT /api/v1/banks/{bank_id}/credit-thresholds",
        "PUT /api/v1/banks/{bank_id}/crm-haircuts",
        "PUT /api/v1/banks/{bank_id}/ecl-assumptions",    ],
    "tenant-dependencies-85a162b4fc51": [
        "GET /api/v1/attestation/awaiting-my-signature",
        "GET /api/v1/attestation/my-signature-appearance",
        "PUT /api/v1/attestation/my-signature-appearance",
        "PUT /api/v1/attestation/signature-placements",
        "GET /api/v1/attestation/signer-identity",
        "PUT /api/v1/attestation/signing-policies",
        "POST /api/v1/banks/{bank_id}/canonical-withdrawals",
        "POST /api/v1/banks/{bank_id}/canonical-withdrawals/{withdrawal_id}/approve",
        "POST /api/v1/banks/{bank_id}/canonical-withdrawals/{withdrawal_id}/reverse",
        "POST /api/v1/banks/{bank_id}/data-activations",
        "POST /api/v1/banks/{bank_id}/database-direct/connections",
        "DELETE /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}",
        "PATCH /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}",
        "POST /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}/disable",
        "POST /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}/enable",
        "GET /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}/schema",
        "POST /api/v1/banks/{bank_id}/database-direct/connections/{connection_id}/test",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/runs",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/signoffs",
        "PATCH /api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}/attest",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}/step-up",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}/submit",
        "POST /api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}/withdraw",
        "PUT /api/v1/banks/{bank_id}/institution-profile",
        "POST /api/v1/banks/{bank_id}/licenses",
        "PUT /api/v1/banks/{bank_id}/licenses/{license_id}",
        "PUT /api/v1/banks/{bank_id}/liquidity/cfp",
        "POST /api/v1/banks/{bank_id}/liquidity/cfp/activate",
        "POST /api/v1/banks/{bank_id}/liquidity/cfp/approve",
        "POST /api/v1/banks/{bank_id}/liquidity/cfp/de-escalate",
        "POST /api/v1/banks/{bank_id}/liquidity/run-all-scenarios",
        "POST /api/v1/banks/{bank_id}/market-data/connections",
        "DELETE /api/v1/banks/{bank_id}/market-data/connections/{connection_id}",
        "PATCH /api/v1/banks/{bank_id}/market-data/connections/{connection_id}",
        "POST /api/v1/banks/{bank_id}/market-data/connections/{connection_id}/disable",
        "POST /api/v1/banks/{bank_id}/market-data/connections/{connection_id}/enable",
        "POST /api/v1/banks/{bank_id}/market-data/connections/{connection_id}/test",
        "POST /api/v1/banks/{bank_id}/market-data/connections/{connection_id}/validate",
        "PUT /api/v1/banks/{bank_id}/market-data/source-preferences",
        "POST /api/v1/banks/{bank_id}/name-history",
        "PUT /api/v1/banks/{bank_id}/name-history/{entry_id}",
        "POST /api/v1/banks/{bank_id}/official-runs",
        "POST /api/v1/banks/{bank_id}/outlets",
        "PUT /api/v1/banks/{bank_id}/outlets/{outlet_id}",
        "POST /api/v1/banks/{bank_id}/position-snapshots/{snapshot_id}/override",
        "POST /api/v1/banks/{bank_id}/products",
        "PUT /api/v1/banks/{bank_id}/products/{product_id}",
        "POST /api/v1/banks/{bank_id}/reconciliation/exceptions",
        "POST /api/v1/banks/{bank_id}/reconciliation/exceptions/{exception_id}/approve",
        "POST /api/v1/banks/{bank_id}/reconciliation/exceptions/{exception_id}/revoke",
        "POST /api/v1/banks/{bank_id}/refresh",
        "POST /api/v1/banks/{bank_id}/regulatory-packages",
        "POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/certify",
        (
            "POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}"
            "/attestation/certify-and-send"
        ),
        "PUT /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/placements",
        "GET /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/preview",
        "PUT /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/recipients",
        "POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/send-back",
        "POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/step-up",
        "POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation/void",
        "PUT /api/v1/banks/{bank_id}/regulatory-reporting/channel-configs/{channel}",
        "PUT /api/v1/banks/{bank_id}/regulatory-reporting/settings",
        "POST /api/v1/banks/{bank_id}/regulatory-runs",
        "POST /api/v1/banks/{bank_id}/related-parties",
        "PUT /api/v1/banks/{bank_id}/related-parties/{party_id}",
        "POST /api/v1/banks/{bank_id}/related-parties/{party_id}/shareholdings",
        "PUT /api/v1/banks/{bank_id}/related-parties/{party_id}/shareholdings/{shareholding_id}",
        "POST /api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses",
        "DELETE /api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses/{analysis_id}",
        "POST /api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios",
        "PATCH /api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}",
        "POST /api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}/archive",
        "POST /api/v1/banks/{bank_id}/system-of-record",
        "POST /api/v1/banks/{bank_id}/system-of-record/{declaration_id}/approve",
        "POST /api/v1/banks/{bank_id}/system-of-record/{declaration_id}/revoke",
        "POST /api/v1/banks/{bank_id}/temenos/connections",
        "DELETE /api/v1/banks/{bank_id}/temenos/connections/{connection_id}",
        "PATCH /api/v1/banks/{bank_id}/temenos/connections/{connection_id}",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/backfill",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/disable",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/enable",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/pull",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/test",
        "POST /api/v1/banks/{bank_id}/temenos/connections/{connection_id}/validate",
        "POST /api/v1/macro-scenarios",
        "PATCH /api/v1/macro-scenarios/{scenario_id}",
        "POST /api/v1/macro-scenarios/{scenario_id}/approve",
        "POST /api/v1/macro-scenarios/{scenario_id}/archive",
        "POST /api/v1/macro-scenarios/{scenario_id}/clone",
        "POST /api/v1/macro-scenarios/{scenario_id}/submit",
        "POST /api/v1/management-action-plans",
        "PATCH /api/v1/management-action-plans/{plan_id}",
        "POST /api/v1/management-action-plans/{plan_id}/approve",
        "POST /api/v1/management-action-plans/{plan_id}/archive",
        "POST /api/v1/management-action-plans/{plan_id}/submit",    ],
    "tenant-dependencies-cd4682c397f0": [
        "POST /api/v1/banks/{bank_id}/behavioral/{model}/apply",
        "POST /api/v1/banks/{bank_id}/behavioral/proposals/{proposal_id}/review",
        "POST /api/v1/banks/{bank_id}/behavioral/{model}/train",
        "POST /api/v1/banks/{bank_id}/ftp/run-all-scenarios",
        "POST /api/v1/banks/{bank_id}/irr/run-all-scenarios",
        "POST /api/v1/banks/{bank_id}/irr/standardised-framework/runs",    ],
    "tenant-dependencies-f75cd22b5765": [
        "GET /api/v1/banks/{bank_id}/behavioral/proposals",
        "GET /api/v1/banks/{bank_id}/behavioral/liquidity",
        "GET /api/v1/banks/{bank_id}/behavioral/{model}",
        "GET /api/v1/banks/{bank_id}/ftp/dashboard",
        "GET /api/v1/banks/{bank_id}/irr/dashboard",
        "GET /api/v1/banks/{bank_id}/irr/ear-analysis",
        "GET /api/v1/banks/{bank_id}/irr/standardised-framework",
        "GET /api/v1/banks/{bank_id}/irr/standardised-framework/attempts",    ],
}

#: Order of the rules themselves, as the committed file holds them (historical:
#: mostly name-sorted, with hand-named groups inserted next to the groups they
#: were split from and one late addition appended). Rules not yet listed are
#: appended after the pinned ones, sorted by name; names of rules that no
#: longer exist are ignored.

RULE_ORDER_PIN: Final[list[str]] = [
    "operator-dependencies-09529af2555f",
    "operator-dependencies-0deab2a1d10a",
    "operator-dependencies-c8c261541d8d",
    "operator-dependencies-f9b4c59e3f84",
    "operator-password-exchange",
    "public-app.api.health-live",
    "public-app.api.health-ready",
    "public-app.identity.api.auth-logout",
    "public-app.identity.api.auth-sso_status",
    "public-app.operator.main-create_operator_app.<locals>.operator_health",
    "public-fastapi.applications-FastAPI.setup.<locals>.openapi",
    "public-fastapi.applications-FastAPI.setup.<locals>.redoc_html",
    "public-fastapi.applications-FastAPI.setup.<locals>.swagger_ui_html",
    "public-fastapi.applications-FastAPI.setup.<locals>.swagger_ui_redirect",
    "tenant-dependencies-002a1fb57261",
    "tenant-dependencies-054114cafa1a",
    "tenant-dependencies-0621bcbfafd6",
    "tenant-dependencies-0b40efbc6215",
    "tenant-dependencies-0fb99bc15b22",
    "tenant-dependencies-145104d936ff",
    "tenant-dependencies-15342f3123d3",
    "tenant-dependencies-161c970516e6",
    "tenant-dependencies-181554f7d692",
    "tenant-dependencies-filing-templates-scoped",
    "tenant-dependencies-liq-scoped-configure",
    "tenant-dependencies-1b5afa443da6",
    "tenant-dependencies-259cccdeeb99",
    "tenant-dependencies-27761a4b3094",
    "tenant-dependencies-278ad5b308ca",
    "tenant-dependencies-2939d061afb2",
    "tenant-dependencies-2bcb13ecd3f8",
    "tenant-dependencies-2e6b73afd423",
    "tenant-dependencies-319783383ea2",
    "tenant-dependencies-34dc619db2bf",
    "tenant-dependencies-3ca7e605ae55",
    "tenant-dependencies-3fbdb74a1d6d",
    "tenant-dependencies-43641b5f1e9f",
    "tenant-dependencies-45946aebd81b",
    "tenant-dependencies-4dd0c71395ff",
    "tenant-dependencies-4faba49be8dc",
    "tenant-dependencies-57d70dbbb9e8",
    "tenant-dependencies-5cdd95e4dee5",
    "tenant-dependencies-5e8d386dab19",
    "tenant-dependencies-611e2742414c",
    "tenant-dependencies-618f61a85792",
    "tenant-dependencies-63b3513acefa",
    "tenant-dependencies-644d9fb7eaca",
    "tenant-dependencies-65978426c518",
    "tenant-dependencies-65a1ba29ed5d",
    "tenant-dependencies-685d3ed0ff4d",
    "tenant-dependencies-6a83d5966d83",
    "tenant-dependencies-6b5d8e958924",
    "tenant-dependencies-6c26ac4ec193",
    "tenant-dependencies-6c6852407fad",
    "tenant-dependencies-6d77d0dbf3da",
    "tenant-dependencies-6f6088180730",
    "tenant-dependencies-7288e3d1520c",
    "tenant-dependencies-75a23b39c9be",
    "tenant-dependencies-793495930b9a",
    "tenant-dependencies-7a78c6513911",
    "tenant-dependencies-7c9290e0610b",
    "tenant-dependencies-7fb6246a2524",
    "tenant-dependencies-8451766930bb",
    "tenant-dependencies-85a162b4fc51",
    "tenant-dependencies-8ffacc3c2d98",
    "tenant-dependencies-98db0753708e",
    "tenant-dependencies-990826d308c6",
    "tenant-dependencies-9a69fc83be90",
    "tenant-dependencies-9bb7e014fa04",
    "tenant-dependencies-9c7e7ee1e003",
    "tenant-dependencies-9dde6adecbbb",
    "tenant-dependencies-9ebadabf3a62",
    "tenant-dependencies-a2ed0609d0e8",
    "tenant-dependencies-a5378b7cb05d",
    "tenant-dependencies-a721c482677f",
    "tenant-dependencies-a72c987d2be5",
    "tenant-dependencies-ab15180d1766",
    "tenant-dependencies-ae889a3cb781",
    "tenant-dependencies-b8cb4d6b6442",
    "tenant-dependencies-b9d4bafcd27f",
    "tenant-dependencies-bc20545b77d1",
    "tenant-dependencies-c49de8839057",
    "tenant-dependencies-c79eed3f394c",
    "tenant-dependencies-cccf0f228370",
    "tenant-dependencies-cd4682c397f0",
    "tenant-dependencies-ceca96895d81",
    "tenant-dependencies-d09a2a9fd602",
    "tenant-dependencies-d505f56b610e",
    "tenant-dependencies-d559149a3e2c",
    "tenant-dependencies-d69ae4b61469",
    "tenant-dependencies-d877ef894c7b",
    "tenant-dependencies-daa209ee338a",
    "tenant-dependencies-e0c128f92772",
    "tenant-dependencies-e25c8559b6d2",
    "tenant-dependencies-e6bc73594197",
    "tenant-dependencies-e75ec92ce42c",
    "tenant-dependencies-e9b8a399e3a7",
    "tenant-dependencies-ecc493112cb3",
    "tenant-dependencies-f13d6d1ed2eb",
    "tenant-dependencies-f34135a00f85",
    "tenant-dependencies-f75cd22b5765",
    "tenant-dependencies-f99bdc05e41d",
    "tenant-dependencies-fd9a49ba1420",
    "tenant-dependencies-fef55100409a",
    "tenant-oidc-exchange",
    "tenant-password-exchange",
    "tenant-refresh-token",
    "tenant-signed-download-capability",
    "tenant-sso-internal-key",
    "tenant-taxonomy-authenticated",
    "unsupported-case-financial-entity-rejection",
    "tenant-dependencies-ee37d1b6984c",
]


def _pin_environment() -> None:
    """Exporting the declarations must not start background jobs or operator
    dev wiring, and must not depend on a developer's .env: the same guard set
    the test session pins at import time (a hardened .env - a remote database
    URL, TLS enforcement - must not break regeneration or the freshness
    check). Called inside ``_registered`` right before the application
    imports, so importing this module never mutates the environment of the
    surrounding process."""
    os.environ["RUN_INPROCESS_WORKER"] = "0"
    os.environ["TLS_ALLOW_PLAINTEXT"] = "1"
    os.environ["DATABASE_URL"] = ""
    os.environ["MIGRATION_DATABASE_URL"] = ""
    os.environ["WORKER_DATABASE_URL"] = ""
    os.environ["OPERATOR_DATABASE_URL"] = ""
    os.environ["OPERATOR_DEV_AUTH_ENABLED"] = "0"


def _registered() -> list[RegisteredEndpoint]:
    _pin_environment()
    # These imports must stay inside the function, after the environment pin:
    # importing the applications at module level would move the pin to import
    # time and mutate the environment of every importer (see the test that
    # reloads this module).
    from app.api.deps import get_current_principal  # noqa: PLC0415
    from app.core.config import get_operator_settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415
    from app.operator.deps import get_operator_context  # noqa: PLC0415
    from app.operator.main import create_operator_app  # noqa: PLC0415

    get_operator_settings.cache_clear()
    try:
        applications = {"tenant": create_app(), "operator": create_operator_app()}
    finally:
        get_operator_settings.cache_clear()
    endpoints, errors = registered_endpoints(
        applications,
        {"tenant": get_current_principal, "operator": get_operator_context},
    )
    if errors:
        raise _PinError("Route inspection failed:\n  " + "\n  ".join(errors))
    return endpoints


def _derive_references(dependencies: list[str]) -> list[str]:
    references: list[str] = []
    for dependency in dependencies:
        module, _, name = dependency.partition(":")
        if module == "functools" or module.startswith("fastapi."):
            continue
        references.append(module.replace(".", "/") + ".py:" + name)
    return references


def _dependency_group_name(application: str, dependencies: list[str]) -> str:
    digest = hashlib.sha256("\n".join(dependencies).encode()).hexdigest()[:12]
    return f"{application}-dependencies-{digest}"


def _route_json(key: RouteKey, endpoint: str) -> dict[str, str]:
    application, method, path = key
    return {
        "application": application,
        "method": method,
        "path": path,
        "endpoint": endpoint,
    }


@dataclass
class _Rule:
    """One catalog rule, in catalog field order."""

    name: str
    kind: str
    basis: str
    references: list[str]
    authentication: str | None
    dependencies: list[str]
    routes: list[dict[str, str]]

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "basis": self.basis,
            "references": self.references,
            "authentication": self.authentication,
            "dependencies": self.dependencies,
            "routes": self.routes,
        }


def _pinned_routes(
    name: str, spec: _PinnedDependencyGroup | _PinnedHandlerRule
) -> list[PinnedRoute]:
    routes = spec.get("routes")
    if not isinstance(routes, list) or not routes:
        raise _PinError(f"Pinned rule {name} declares no routes")
    return routes


def _pinned_route_keys(
    name: str, spec: _PinnedDependencyGroup | _PinnedHandlerRule
) -> list[RouteKey]:
    return [(route[0], route[1], route[2]) for route in _pinned_routes(name, spec)]


def _committed_baseline(fixture: Path) -> str:
    """The catalog baseline string, carried forward from the committed copy."""
    if not fixture.is_file():
        raise _PinError(
            f"{fixture} is missing; the baseline is carried forward from it. "
            "Restore it or pass --fixture."
        )
    try:
        document = _CommittedFixture.model_validate_json(fixture.read_bytes())
    except ValidationError as error:
        raise _PinError(f"{fixture} has no baseline to carry forward: {error}") from None
    return document.baseline


def _observed_row(
    by_key: dict[RouteKey, RegisteredEndpoint],
    key: RouteKey,
    endpoint: str,
    name: str,
) -> RegisteredEndpoint:
    row = by_key.get(key)
    if row is None:
        raise _PinError(f"Pinned rule {name} names unregistered route {key}")
    if row.endpoint != endpoint:
        raise _PinError(
            f"Pinned rule {name} routes {key} to {row.endpoint}, but the pin "
            f"records {endpoint}. The handler changed: review the change and "
            "update the pin in this script deliberately."
        )
    return row


def _checked_rows(
    by_key: dict[RouteKey, RegisteredEndpoint],
    name: str,
    spec: _PinnedDependencyGroup | _PinnedHandlerRule,
) -> list[RegisteredEndpoint]:
    """The live rows a pin covers, each verified against the pinned handler."""
    return [
        _observed_row(by_key, (route[0], route[1], route[2]), route[3], name)
        for route in _pinned_routes(name, spec)
    ]


def _uniform_dependencies(name: str, rows: list[RegisteredEndpoint]) -> list[str]:
    """The one dependency set every route of a pinned rule must register.

    The rule declares one dependency list for all its routes; a route that
    drifts would otherwise inherit the union of the others' dependencies."""
    expected = rows[0].dependencies
    drifted = [row.key for row in rows if row.dependencies != expected]
    if drifted:
        raise _PinError(
            f"Rule {name} groups routes with different registered "
            f"dependency sets (expected {list(expected)}): {drifted}. Review the "
            "registration change and update or split the pin deliberately."
        )
    return list(expected)


def _pinned_dependency_rules(
    by_key: dict[RouteKey, RegisteredEndpoint],
) -> tuple[dict[str, _Rule], set[RouteKey]]:
    """Pinned dependency groups: routes, basis, name and the reviewed
    dependency set from the pin, the live registration verified against it."""
    rules: dict[str, _Rule] = {}
    assigned: set[RouteKey] = set()
    for name, spec in PINNED.items():
        rows = _checked_rows(by_key, name, spec)
        reviewed = spec.get("dependencies")
        if not isinstance(reviewed, list) or not reviewed:
            raise _PinError(f"Pinned rule {name} declares no reviewed dependency set")
        for row in rows:
            if row.dependencies != tuple(sorted(reviewed)):
                raise _PinError(
                    f"Pinned rule {name} route {row.key} registers dependencies "
                    f"{list(row.dependencies)}, but the pin records {sorted(reviewed)}. "
                    "Review the change and update the pin's dependency set "
                    "deliberately."
                )
        dependencies = sorted(reviewed)
        applications = {key[0] for key in _pinned_route_keys(name, spec)}
        if len(applications) != 1:
            raise _PinError(f"Pinned rule {name} spans applications {sorted(applications)}")
        application = applications.pop()
        for row in rows:
            if application not in row.authentication:
                raise _PinError(
                    f"Pinned rule {name} route {row.key} no longer registers the "
                    f"{application} authenticator (observed "
                    f"{sorted(row.authentication)}). Review the change and update "
                    "the pin in this script deliberately."
                )
        basis = spec.get("basis") or STANDARD_BASIS[application]
        references = spec.get("references") or _derive_references(dependencies)
        rules[name] = _Rule(
            name=name,
            kind="dependency",
            basis=basis,
            references=references,
            authentication=application,
            dependencies=dependencies,
            routes=[_route_json(row.key, row.endpoint) for row in rows],
        )
        assigned.update(row.key for row in rows)
    return rules, assigned


def _pinned_handler_rules(
    by_key: dict[RouteKey, RegisteredEndpoint],
) -> tuple[dict[str, _Rule], set[RouteKey]]:
    """Pinned handler/rejection rules: the handler performs the check itself."""
    rules: dict[str, _Rule] = {}
    assigned: set[RouteKey] = set()
    for name, spec in PINNED_HANDLER_RULES.items():
        rows = _checked_rows(by_key, name, spec)
        dependencies = _uniform_dependencies(name, rows)
        for row in rows:
            if row.authentication:
                raise _PinError(
                    f"Pinned rule {name} route {row.key} registers authenticators "
                    f"{sorted(row.authentication)}; a handler or rejection "
                    "declaration cannot conceal authentication wiring. Review the "
                    "change and update the pin in this script deliberately."
                )
        rules[name] = _Rule(
            name=name,
            kind=spec["kind"],
            basis=spec["basis"],
            references=spec["references"],
            authentication=None,
            dependencies=dependencies,
            routes=[_route_json(row.key, row.endpoint) for row in rows],
        )
        assigned.update(row.key for row in rows)
    return rules, assigned


def _unused_public_reviews(observed: list[RegisteredEndpoint]) -> list[RouteKey]:
    """Reviewed public routes that are no longer registered."""
    live = {row.key for row in observed}
    return sorted(REVIEWED_PUBLIC_ROUTES.keys() - live)


def _public_rules(observed: list[RegisteredEndpoint]) -> dict[str, _Rule]:
    """Public routes: grouped by handler, named and worded by the exact review."""
    by_endpoint: dict[str, list[RegisteredEndpoint]] = defaultdict(list)
    for row in observed:
        review = REVIEWED_PUBLIC_ROUTES.get(row.key)
        if review is None:
            continue
        if row.endpoint != review.endpoint:
            raise _PinError(
                f"Reviewed public route {row.key} now resolves to {row.endpoint}, "
                f"but the review records {review.endpoint}. The handler changed: "
                "review the change and update tests/fixtures/public_routes.py "
                "deliberately."
            )
        if row.authentication:
            raise _PinError(
                f"Reviewed public route {row.key} registers authenticators "
                f"{sorted(row.authentication)}; a public declaration cannot "
                "conceal authentication wiring. Review the change and update "
                "tests/fixtures/public_routes.py deliberately."
            )
        by_endpoint[review.endpoint].append(row)
    rules: dict[str, _Rule] = {}
    for endpoint, rows in by_endpoint.items():
        name = f"public-{endpoint.replace(':', '-')}"
        review = REVIEWED_PUBLIC_ROUTES[rows[0].key]
        dependencies = _uniform_dependencies(name, rows)
        rules[name] = _Rule(
            name=name,
            kind="public",
            basis=review.basis,
            references=[PUBLIC_ROUTES_REFERENCE, review.reference],
            authentication=None,
            dependencies=dependencies,
            routes=[_route_json(row.key, row.endpoint) for row in rows],
        )
    return rules


def _default_rules(
    observed: list[RegisteredEndpoint],
    assigned: set[RouteKey],
    rules: dict[str, _Rule],
) -> tuple[dict[str, _Rule], set[RouteKey]]:
    """Everything else: grouped by the exact registered dependency set."""
    by_profile: dict[tuple[str, tuple[str, ...]], list[RegisteredEndpoint]] = defaultdict(list)
    grouped: set[RouteKey] = set()
    for row in observed:
        if row.key in assigned:
            continue
        if not row.authentication:
            raise _PinError(
                f"Unauthenticated route {row.key} ({row.endpoint}) is neither reviewed "
                "public nor a pinned handler/rejection rule; classify it deliberately."
            )
        if len(row.authentication) != 1:
            raise _PinError(
                f"Route {row.key} registers authenticators "
                f"{sorted(row.authentication)}; expected exactly one. Grouping "
                "would otherwise depend on set iteration order. Classify it "
                "deliberately."
            )
        by_profile[(next(iter(row.authentication)), row.dependencies)].append(row)
        grouped.add(row.key)
    built: dict[str, _Rule] = {}
    for (application, dependencies), rows in by_profile.items():
        name = _dependency_group_name(application, list(dependencies))
        if name in rules:
            raise _PinError(f"Dependency group name {name} collides with an existing rule")
        built[name] = _Rule(
            name=name,
            kind="dependency",
            basis=STANDARD_BASIS[application],
            references=_derive_references(list(dependencies)),
            authentication=application,
            dependencies=list(dependencies),
            routes=[_route_json(row.key, row.endpoint) for row in rows],
        )
    return built, grouped


def _ordered_routes(name: str, routes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Canonical ``(application, method, path, endpoint)`` order, with pinned
    route order honored first for the rules that keep a historical order. Pin
    entries are ``"METHOD /path"`` strings (every pinned rule is
    single-application); unpinned routes follow in canonical order."""
    pin = {
        tuple(entry.partition(" ")[::2]): index
        for index, entry in enumerate(ROUTE_ORDER_PINS.get(name, []))
    }
    appended = len(pin)

    def order(route: dict[str, str]) -> tuple[int, tuple[str, str, str, str]]:
        canonical = (
            route["application"],
            route["method"],
            route["path"],
            route["endpoint"],
        )
        position = pin.get((route["method"], route["path"]))
        if position is None:
            return (appended, canonical)
        return (position, ("", "", "", ""))

    return sorted(routes, key=order)


def _emit_rules(rules: dict[str, _Rule]) -> list[dict[str, object]]:
    ordered_names = [name for name in RULE_ORDER_PIN if name in rules]
    ordered_names.extend(sorted(rules.keys() - set(RULE_ORDER_PIN)))
    emitted: list[dict[str, object]] = []
    for name in ordered_names:
        rule = rules[name]
        document = replace(rule, routes=_ordered_routes(name, rule.routes)).to_json()
        spec = PINNED.get(name)
        field_order = spec.get("field_order") if spec else None
        if field_order is not None:
            # One historical rule was appended with a non-canonical field
            # order; its pin preserves it.
            document = {field: document[field] for field in field_order}
        emitted.append(document)
    return emitted


def _check_unique_keys(observed: list[RegisteredEndpoint]) -> None:
    """A duplicated registration must stop generation, not silently collapse
    into one ``by_key`` entry while both rows reach the grouping."""
    counts = Counter(row.key for row in observed)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise _PinError(
            f"Registered route keys appear more than once: {duplicates}. "
            "Review the duplicate registration deliberately."
        )


def build_rules(fixture: Path) -> dict[str, object]:
    observed = _registered()
    _check_unique_keys(observed)
    unused_reviews = _unused_public_reviews(observed)
    if unused_reviews:
        raise _PinError(
            f"Reviewed public routes are no longer registered: {unused_reviews}. "
            "Remove their reviews in tests/fixtures/public_routes.py deliberately."
        )
    by_key = {row.key: row for row in observed}
    rules, assigned = _pinned_dependency_rules(by_key)
    handler_rules, handler_keys = _pinned_handler_rules(by_key)
    rules.update(handler_rules)
    assigned.update(handler_keys)
    rules.update(_public_rules(observed))
    assigned.update(REVIEWED_PUBLIC_ROUTES.keys())
    default_rules, grouped = _default_rules(observed, assigned, rules)
    rules.update(default_rules)

    missing = sorted(by_key.keys() - assigned - grouped)
    if missing:
        raise _PinError(f"Routes left unclassified: {missing}")

    return {
        "schema_version": 1,
        "baseline": _committed_baseline(fixture),
        "rules": _emit_rules(rules),
    }


def build_fixture_bytes(fixture: Path) -> bytes:
    catalog = build_rules(fixture)
    AuthorizationCatalog.model_validate_json(json.dumps(catalog).encode())
    return (json.dumps(catalog, indent=2) + "\n").encode()


USAGE = (
    "usage: python -m scripts.route_authorization_dump "
    "[--fixture PATH] [--output PATH] [--check]"
)


def _parse_arguments(argv: list[str] | None) -> tuple[Path, Path | None, bool]:
    """``--fixture`` (baseline source and freshness target), ``--output``
    (write there instead of overwriting the fixture) and ``--check``
    (regenerate to a temp path and fail if the committed copy differs)."""
    fixture = FIXTURE_PATH
    output: Path | None = None
    check = False
    pending: str | None = None
    for argument in sys.argv[1:] if argv is None else argv:
        if pending is not None:
            if argument.startswith("--"):
                raise SystemExit(f"{pending} needs a path argument\n{USAGE}")
            if pending == "--fixture":
                fixture = Path(argument)
            else:
                output = Path(argument)
            pending = None
        elif argument in {"--fixture", "--output"}:
            pending = argument
        elif argument == "--check":
            check = True
        else:
            raise SystemExit(f"Unknown argument {argument}\n{USAGE}")
    if pending is not None:
        raise SystemExit(f"{pending} needs a path argument\n{USAGE}")
    return fixture, output, check


def main(argv: list[str] | None = None) -> int:
    fixture, output, check = _parse_arguments(argv)

    generated = build_fixture_bytes(fixture)
    if check:
        committed = fixture.read_bytes()
        if committed != generated:
            print(
                f"{fixture} is stale: it no longer matches the registered "
                f"routes and dependencies. Run `{REGENERATE_TASK}`, review the diff "
                "line by line, and commit it with the route change.",
                file=sys.stderr,
            )
            return 1
        print(f"{fixture} matches the registered routes.")
        return 0

    target = output if output is not None else fixture
    if output is None:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=target.name, delete=False
        ) as staged:
            staged.write(generated)
        Path(staged.name).replace(target)
    else:
        target.write_bytes(generated)
    print(f"Wrote {target} ({len(generated)} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
