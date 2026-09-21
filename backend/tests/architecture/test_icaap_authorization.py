"""ICAAP routes stay on their scoped dependencies, and each on the right one.

The map below is the authority contract in one place: reads take VIEW, the
create routes take CREATE, everything that changes the report takes EDIT, and
the draft exports take EXPORT. An approver bundle carries VIEW but neither
CREATE nor EDIT, so this table is what keeps maker and checker apart at the
route level rather than inside a service somebody can bypass.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.deps import MUTATION_ROLE_DEPENDENCY_NAMES
from app.main import create_app

_BANK = "/api/v1/banks/{bank_id}/icaap"
_CYCLE = f"{_BANK}/cycles/{{cycle_id}}"

ROUTE_DEPENDENCIES: dict[tuple[str, str], str] = {
    ("GET", f"{_BANK}/frameworks"): "require_icaap_view",
    (
        "GET",
        f"{_BANK}/frameworks/{{framework_code}}/versions/{{version}}",
    ): "require_icaap_view",
    ("GET", f"{_BANK}/block-types"): "require_icaap_view",
    ("GET", f"{_BANK}/cycles"): "require_icaap_view",
    ("POST", f"{_BANK}/cycles"): "require_icaap_create",
    ("GET", _CYCLE): "require_icaap_view",
    ("PATCH", _CYCLE): "require_icaap_edit",
    ("POST", f"{_CYCLE}/archive"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/rebase"): "require_icaap_create",
    ("GET", f"{_CYCLE}/readiness"): "require_icaap_view",
    ("GET", f"{_CYCLE}/sections"): "require_icaap_view",
    ("GET", f"{_CYCLE}/sections/{{section_key}}"): "require_icaap_view",
    ("PUT", f"{_CYCLE}/sections/{{section_key}}/working"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/sections/{{section_key}}/versions"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/sections/{{section_key}}/versions"): "require_icaap_view",
    (
        "GET",
        f"{_CYCLE}/sections/{{section_key}}/versions/{{version_no}}",
    ): "require_icaap_view",
    (
        "PUT",
        f"{_CYCLE}/sections/{{section_key}}/requirements/{{item_id}}",
    ): "require_icaap_edit",
    ("GET", f"{_CYCLE}/blocks"): "require_icaap_view",
    ("POST", f"{_CYCLE}/blocks"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/blocks/{{block_id}}"): "require_icaap_view",
    ("POST", f"{_CYCLE}/blocks/{{block_id}}/refresh"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/blocks/{{block_id}}/bindings"): "require_icaap_view",
    ("POST", f"{_CYCLE}/blocks/{{block_id}}/pin"): "require_icaap_edit",
    ("DELETE", f"{_CYCLE}/blocks/{{block_id}}/pin"): "require_icaap_edit",
    ("PUT", f"{_CYCLE}/blocks/{{block_id}}/manual-table"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/blocks/{{block_id}}/retire"): "require_icaap_edit",
    # --- P4: AI drafting. The POSTs take EDIT plus the egress gates, so an
    # approver (VIEW without EDIT) can read a draft and never request or insert
    # one; the reads stay on VIEW so the examiner branch works unchanged.
    (
        "POST",
        f"{_CYCLE}/sections/{{section_key}}/ai-drafts",
    ): "require_icaap_ai_draft",
    ("GET", f"{_CYCLE}/sections/{{section_key}}/ai-drafts"): "require_icaap_view",
    (
        "GET",
        f"{_CYCLE}/sections/{{section_key}}/ai-drafts/{{suggestion_id}}",
    ): "require_icaap_view",
    (
        "POST",
        f"{_CYCLE}/sections/{{section_key}}/ai-drafts/{{suggestion_id}}/accept",
    ): "require_icaap_ai_draft",
    (
        "POST",
        f"{_CYCLE}/sections/{{section_key}}/ai-drafts/{{suggestion_id}}/reject",
    ): "require_icaap_ai_draft",
    ("GET", f"{_CYCLE}/attachments"): "require_icaap_view",
    ("POST", f"{_CYCLE}/attachments"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/attachments/{{attachment_id}}/download"): "require_icaap_view",
    ("POST", f"{_CYCLE}/attachments/{{attachment_id}}/withdraw"): "require_icaap_edit",
    # --- P2: risk register, appetite, Pillar 2, reconciliation, review ------
    ("GET", f"{_CYCLE}/risks"): "require_icaap_view",
    ("PUT", f"{_CYCLE}/risks/{{risk_key}}"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/risks"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/risks/{{risk_key}}/retire"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/appetite"): "require_icaap_view",
    ("POST", f"{_CYCLE}/appetite/metrics"): "require_icaap_edit",
    ("PUT", f"{_CYCLE}/appetite/metrics/{{metric_id}}"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/appetite/metrics/{{metric_id}}/retire"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/capital-triggers"): "require_icaap_view",
    ("GET", f"{_CYCLE}/pillar2"): "require_icaap_view",
    ("POST", f"{_CYCLE}/pillar2/items"): "require_icaap_edit",
    ("PUT", f"{_CYCLE}/pillar2/items/{{item_id}}"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/pillar2/items/{{item_id}}/compute"): "require_icaap_edit",
    (
        "POST",
        f"{_CYCLE}/pillar2/items/{{item_id}}/approve",
    ): "require_icaap_pillar2_approve",
    ("POST", f"{_CYCLE}/pillar2/items/{{item_id}}/retire"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/pillar2/items/{{item_id}}/revisions"): "require_icaap_view",
    ("GET", f"{_CYCLE}/pillar2/table5"): "require_icaap_view",
    (
        "POST",
        f"{_CYCLE}/pillar2/capital-plan-proposal",
    ): "require_icaap_capital_plan_propose",
    ("GET", f"{_CYCLE}/parameters"): "require_icaap_view",
    ("GET", f"{_CYCLE}/reconciliation"): "require_icaap_view",
    (
        "POST",
        f"{_CYCLE}/reconciliation/requirement/compute",
    ): "require_icaap_edit",
    (
        "PUT",
        f"{_CYCLE}/reconciliation/requirement/lines/{{line_key}}/explanation",
    ): "require_icaap_edit",
    ("POST", f"{_CYCLE}/reconciliation/resources/lines"): "require_icaap_edit",
    ("PUT", f"{_CYCLE}/reconciliation/resources/lines/{{line_id}}"): "require_icaap_edit",
    (
        "DELETE",
        f"{_CYCLE}/reconciliation/resources/lines/{{line_id}}",
    ): "require_icaap_edit",
    (
        "POST",
        f"{_CYCLE}/reconciliation/resources/load-regulatory",
    ): "require_icaap_edit",
    (
        "PUT",
        f"{_CYCLE}/reconciliation/controls/{{control_code}}"
        "/explanations/{comparison_key}",
    ): "require_icaap_edit",
    ("GET", f"{_CYCLE}/allocation"): "require_icaap_view",
    ("PUT", f"{_CYCLE}/allocation"): "require_icaap_edit",
    ("GET", f"{_CYCLE}/audit-reviews"): "require_icaap_view",
    ("POST", f"{_CYCLE}/audit-reviews"): "require_icaap_audit_review",
    ("PUT", f"{_CYCLE}/audit-reviews/{{review_id}}"): "require_icaap_audit_review",
    (
        "POST",
        f"{_CYCLE}/audit-reviews/{{review_id}}/finalise",
    ): "require_icaap_audit_review",
    ("GET", f"{_CYCLE}/challenges"): "require_icaap_view",
    ("POST", f"{_CYCLE}/challenges"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/challenges/{{challenge_id}}/responses"): "require_icaap_edit",
    ("GET", f"{_BANK}/supervisory-addons"): "require_icaap_view",
    ("POST", f"{_BANK}/supervisory-addons"): "require_icaap_create",
    (
        "POST",
        f"{_BANK}/supervisory-addons/{{addon_id}}/confirm",
    ): "require_icaap_addon_approve",
    (
        "POST",
        f"{_BANK}/supervisory-addons/{{addon_id}}/withdraw",
    ): "require_icaap_addon_approve",
    ("GET", f"{_BANK}/supervisory-addons/{{addon_id}}/letter"): "require_icaap_view",
    # --- P3: review chain, freeze and filing, workflow templates, ¶82 -------
    # Three of these need the OBJECT before they can answer, because the answer
    # depends on it: who already reviewed this round, who proposed this chain,
    # who chose what to publish. Their dependencies resolve it and hand the
    # evaluator a maker-checker condition, which is why they are their own
    # names rather than a bare EDIT or APPROVE.
    ("GET", f"{_CYCLE}/stages"): "require_icaap_view",
    ("POST", f"{_CYCLE}/submit-for-review"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/stages/{{seq}}/decisions"): "require_icaap_stage_decision",
    ("POST", f"{_CYCLE}/return"): "require_icaap_review",
    ("GET", f"{_CYCLE}/freeze-preflight"): "require_icaap_view",
    ("POST", f"{_CYCLE}/freeze"): "require_icaap_freeze",
    ("GET", f"{_CYCLE}/filing"): "require_icaap_view",
    ("POST", f"{_CYCLE}/clone"): "require_icaap_create",
    ("GET", f"{_BANK}/workflow-templates"): "require_icaap_view",
    ("POST", f"{_BANK}/workflow-templates"): "require_icaap_create",
    ("PATCH", f"{_BANK}/workflow-templates/{{template_id}}"): "require_icaap_edit",
    ("POST", f"{_BANK}/workflow-templates/{{template_id}}/submit"): "require_icaap_edit",
    (
        "POST",
        f"{_BANK}/workflow-templates/{{template_id}}/decision",
    ): "require_icaap_workflow_approve",
    ("GET", f"{_CYCLE}/disclosure"): "require_icaap_view",
    ("PUT", f"{_CYCLE}/disclosure"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/disclosure/submit"): "require_icaap_edit",
    ("POST", f"{_CYCLE}/disclosure/decision"): "require_icaap_disclosure_approve",
}


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _icaap_routes() -> list[APIRoute]:
    return [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and "/icaap/" in route.path
    ]


def test_every_icaap_route_carries_the_authority_it_claims() -> None:
    routes = _icaap_routes()
    for (method, path), required in ROUTE_DEPENDENCIES.items():
        matches = [
            route for route in routes if route.path == path and method in (route.methods or set())
        ]
        assert len(matches) == 1, f"{method} {path} must have exactly one route"
        names = _dependency_names(matches[0])
        assert required in names, f"{method} {path}: {sorted(names)}"


def test_no_icaap_route_falls_back_to_the_scalar_role_ladder() -> None:
    for route in _icaap_routes():
        names = _dependency_names(route)
        scoped = {name for name in names if name.startswith("require_icaap_")}
        assert len(scoped) == 1, f"{route.methods} {route.path}: {sorted(scoped)}"
        assert "get_mutation_tenant_context" not in names
        assert "get_approver_tenant_context" not in names
        assert not any(name.startswith("require_role_") for name in names)
        # ``require_module_access`` answers 403; an institution outside the
        # ICAAP regime must see 404, and the class guard lives in the
        # dependency itself.
        assert "require_module_access" not in names


def test_the_map_covers_every_route_the_app_serves() -> None:
    """A new route cannot be added without deciding what authority it needs.

    The draft exports are Workstream B's and carry ``require_icaap_export``;
    they are checked for the scoped dependency above but are not in this map,
    which is A's contract.
    """
    served = {
        (method, route.path)
        for route in _icaap_routes()
        for method in (route.methods or set())
        if method != "HEAD"
    }
    exports = {entry for entry in served if entry[1].endswith((".pdf", ".docx"))}
    assert served - exports == set(ROUTE_DEPENDENCIES)
    for _method, path in exports:
        route = next(route for route in _icaap_routes() if route.path == path)
        assert "require_icaap_export" in _dependency_names(route)


def test_the_icaap_write_gates_are_declared_as_mutations() -> None:
    """The impersonation sweep classifies routes by dependency name."""
    assert {
        "require_icaap_create",
        "require_icaap_edit",
        "require_icaap_pillar2_approve",
        "require_icaap_addon_approve",
        "require_icaap_audit_review",
        "require_icaap_capital_plan_propose",
        # P3: a stage decision, a freeze, a post-freeze send-back, a review-chain
        # approval and a ¶82 disclosure approval are all writes, and the
        # impersonation sweep classifies routes by dependency NAME.
        "require_icaap_stage_decision",
        "require_icaap_freeze",
        "require_icaap_review",
        "require_icaap_workflow_approve",
        "require_icaap_disclosure_approve",
    } <= MUTATION_ROLE_DEPENDENCY_NAMES
    # EXPORT and VIEW guard reads only; listing them would claim a write gate
    # that does not exist.
    assert "require_icaap_view" not in MUTATION_ROLE_DEPENDENCY_NAMES
    assert "require_icaap_export" not in MUTATION_ROLE_DEPENDENCY_NAMES
