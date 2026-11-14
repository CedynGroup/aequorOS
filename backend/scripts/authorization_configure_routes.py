"""Reviewed issue-184 targets, not a source of runtime authority.

The baseline names the pre-cutover dependency, not an observed HTTP outcome.
The market-data ``/test`` pull joined the configuration duties (#172); the
operational data/temenos test, schema, sync and pull routes stay outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.authorization import InstitutionScope, Module, Permission

Baseline = Literal["analyst", "approver", "org_owner"]


@dataclass(frozen=True)
class ConfigureRoute:
    method: str
    path: str
    module: Module
    baseline: Baseline = "analyst"
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION
    effect_scope: Literal["bank_local"] | None = None
    storage_table: str | None = None
    #: The permission the cutover demands. Configuration duties take CONFIGURE;
    #  a cutover that moved an approval or a read onto scoped authority names its
    #  own verb so the projected eligibility matches the runtime gate.
    permission: Permission = Permission.CONFIGURE


_BANK = "/api/v1/banks/{bank_id}"


def _lifecycle(
    prefix: str, module: Module, *, validate: bool, test: bool = False
) -> list[ConfigureRoute]:
    """Inventory connection configuration, excluding operational pull routes.

    ``test`` joins the representative test pull only where a cutover made it a
    configuration duty (the market-data ``/test`` pull, #172)."""
    base = f"{_BANK}/{prefix}/connections"
    item = base + "/{connection_id}"
    routes = [
        ConfigureRoute("POST", base, module),
        ConfigureRoute("PATCH", item, module),
        ConfigureRoute("POST", item + "/disable", module),
        ConfigureRoute("POST", item + "/enable", module),
        ConfigureRoute("DELETE", item, module),
    ]
    if validate:
        routes.append(ConfigureRoute("POST", item + "/validate", module))
    if test:
        routes.append(ConfigureRoute("POST", item + "/test", module))
    return routes


CONFIGURE_ROUTES: tuple[ConfigureRoute, ...] = (
    ConfigureRoute(
        "PUT",
        f"{_BANK}/liquidity-thresholds",
        Module.LIQUIDITY,
        "approver",
        effect_scope="bank_local",
        storage_table="bank_liquidity_threshold",
    ),
    ConfigureRoute(
        "PUT",
        f"{_BANK}/liquidity-haircuts",
        Module.LIQUIDITY,
        "approver",
        effect_scope="bank_local",
        storage_table="bank_liquidity_haircut",
    ),
    *(
        ConfigureRoute("PUT", f"{_BANK}/{path}", module, "approver")
        for module, paths in (
            (Module.LIQUIDITY, ("liquidity/ewis",)),
        )
        for path in paths
    ),
    *(
        ConfigureRoute(
            "PUT",
            f"{_BANK}/{path}",
            Module.CREDIT,
            "approver",
            effect_scope="bank_local",
            storage_table=table,
        )
        for path, table in (
            ("ecl-assumptions", "bank_ecl_assumption"),
            ("crm-haircuts", "bank_crm_haircut"),
            ("concentration-limits", "bank_concentration_limit"),
            ("credit-thresholds", "bank_credit_threshold"),
        )
    ),
    ConfigureRoute("POST", f"{_BANK}/mapping-configs", Module.DATA),
    *_lifecycle("database-direct", Module.DATA, validate=False),
    *_lifecycle("temenos", Module.DATA, validate=True),
    *_lifecycle("market-data", Module.MARKETS, validate=True, test=True),
    ConfigureRoute("PUT", f"{_BANK}/market-data/source-preferences", Module.MARKETS),
    ConfigureRoute(
        "PUT", f"{_BANK}/regulatory-reporting/channel-configs/{{channel}}", Module.REGULATORY
    ),
    ConfigureRoute("PUT", f"{_BANK}/regulatory-reporting/settings", Module.REGULATORY),
    # The filing review-chain templates: proposing, editing and submitting the
    # bank's chain are configuration duties; deciding one is the approval duty
    # the maker-checker condition governs. All four moved off the scalar
    # mutation/approval dependencies onto the exact REG sentence.
    ConfigureRoute(
        "POST",
        f"{_BANK}/filing-workflow-templates",
        Module.REGULATORY,
        "analyst",
        effect_scope="bank_local",
        storage_table="filing_workflow_templates",
        permission=Permission.CONFIGURE,
    ),
    ConfigureRoute(
        "PATCH",
        f"{_BANK}/filing-workflow-templates/{{template_id}}",
        Module.REGULATORY,
        "analyst",
        effect_scope="bank_local",
        storage_table="filing_workflow_templates",
        permission=Permission.CONFIGURE,
    ),
    ConfigureRoute(
        "POST",
        f"{_BANK}/filing-workflow-templates/{{template_id}}/submit",
        Module.REGULATORY,
        "analyst",
        effect_scope="bank_local",
        storage_table="filing_workflow_templates",
        permission=Permission.CONFIGURE,
    ),
    ConfigureRoute(
        "POST",
        f"{_BANK}/filing-workflow-templates/{{template_id}}/decision",
        Module.REGULATORY,
        "approver",
        effect_scope="bank_local",
        storage_table="filing_workflow_templates",
        permission=Permission.APPROVE,
    ),
    ConfigureRoute(
        "PUT",
        "/api/v1/attestation/signing-policies",
        Module.REGULATORY,
        "org_owner",
        InstitutionScope.ORGANIZATION,
    ),
    ConfigureRoute(
        "PUT",
        "/api/v1/attestation/signature-placements",
        Module.REGULATORY,
        "org_owner",
        InstitutionScope.ORGANIZATION,
    ),
    ConfigureRoute("POST", f"{_BANK}/behavioral/{{model}}/apply", Module.BEHAVIORAL),
)
