"""Reviewed issue-184 targets, not a source of runtime authority.

The baseline names the pre-cutover dependency, not an observed HTTP outcome.
Operational connection test/schema/sync/pull routes deliberately stay outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.authorization import InstitutionScope, Module

Baseline = Literal["analyst", "approver", "org_owner"]


@dataclass(frozen=True)
class ConfigureRoute:
    method: str
    path: str
    module: Module
    baseline: Baseline = "analyst"
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION


_BANK = "/api/v1/banks/{bank_id}"


def _lifecycle(prefix: str, module: Module, *, validate: bool) -> list[ConfigureRoute]:
    """Inventory connection configuration, excluding operational test and pull."""
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
    return routes


CONFIGURE_ROUTES: tuple[ConfigureRoute, ...] = (
    *(
        ConfigureRoute("PUT", f"{_BANK}/{path}", module, "approver")
        for module, paths in (
            (Module.LIQUIDITY, ("liquidity-thresholds", "liquidity-haircuts", "liquidity/ewis")),
            (
                Module.CREDIT,
                ("ecl-assumptions", "crm-haircuts", "concentration-limits", "credit-thresholds"),
            ),
        )
        for path in paths
    ),
    ConfigureRoute("POST", f"{_BANK}/mapping-configs", Module.DATA),
    *_lifecycle("database-direct", Module.DATA, validate=False),
    *_lifecycle("temenos", Module.DATA, validate=True),
    *_lifecycle("market-data", Module.MARKETS, validate=True),
    ConfigureRoute("PUT", f"{_BANK}/market-data/source-preferences", Module.MARKETS),
    ConfigureRoute(
        "PUT", f"{_BANK}/regulatory-reporting/channel-configs/{{channel}}", Module.REGULATORY
    ),
    ConfigureRoute("PUT", f"{_BANK}/regulatory-reporting/settings", Module.REGULATORY),
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
