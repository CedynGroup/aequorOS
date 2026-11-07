"""Exact per-module VIEW authority for the live engine's read surfaces.

One complete active binding per module decision, mirroring the live summary's
per-module gate. Unknown or unmapped module names default-deny: they are
omitted from responses, never surfaced with empty fields. No scalar role or
token-claim shortcut; evaluator failures deny closed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.identity import public as scoped_authorization
from app.identity.public import Bank

#: Live-plane module names → the authorization module whose exact VIEW binding
#: governs them. Reuses the live summary's engine mapping verbatim, including
#: the implied-rating figures served under MARKETS authority. A module name
#: missing from this map is unauthorized by construction (default deny).
FRESHNESS_VIEW_MODULES: dict[str, Module] = {
    "liquidity": Module.LIQUIDITY,
    "capital": Module.CAPITAL,
    "credit": Module.CREDIT,
    "irr": Module.IRRBB,
    "fx": Module.FX,
    "ftp": Module.FTP,
    "rating": Module.MARKETS,
    "forecast": Module.FORECASTING,
}


def module_for(module_name: str) -> Module | None:
    """The authorization module governing one live-plane module name.

    Unknown names return ``None`` — unauthorized by construction.
    """
    return FRESHNESS_VIEW_MODULES.get(module_name)


def can_view_module(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module_name: str,
    *,
    surface: str,
) -> bool:
    """One complete active VIEW binding decision for one module name.

    Unknown names deny without touching the evaluator; the sensitivity matches
    the live summary's aggregated read grain.
    """
    module = module_for(module_name)
    if module is None:
        return False
    decision = scoped_authorization.evaluate_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=module,
        sensitivity=Sensitivity.AGGREGATED,
        surface=surface,
    )
    return decision is not None and decision.allowed


def allowed_modules(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module_names: tuple[str, ...] | list[str],
    *,
    surface: str,
) -> list[str]:
    """The caller-authorized subset of ``module_names``, in the given order."""
    return [
        module_name
        for module_name in module_names
        if can_view_module(db, ctx, bank, module_name, surface=surface)
    ]
