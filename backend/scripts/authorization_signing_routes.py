"""Reviewed issue-185 signing targets, not a source of runtime authority.

The report expands each API route by signing role and source family because the
legacy REG duties and source visibility differ between ordinary returns and
ICAAP. The new Signer sentence is REG/restricted for both return families and
RISK/restricted for enterprise stress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.authorization import Module, Permission, Sensitivity

SigningAction = Literal["preview", "step-up", "certify", "certify-and-send", "attest"]
SigningRole = Literal["preparer", "approver", "board", "witness"]
SourceFamily = Literal["ordinary", "icaap", "enterprise_stress"]
Baseline = Literal[
    "source_view", "legacy_role", "legacy_scalar_approver", "route_not_present"
]

_BANK_PACKAGE = "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation"
_STRESS = "/api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}"


@dataclass(frozen=True)
class SigningRoute:
    route_key: str
    method: str
    path: str
    action: SigningAction
    signing_role: SigningRole
    source_family: SourceFamily
    module: Module
    source_module: Module
    source_sensitivity: Sensitivity
    baseline: Baseline

    @property
    def requires_checker_approval(self) -> bool:
        """Every REG ceremony act runs the checker-approval preflight.

        preview, step-up, certify and certify-and-send all pass through
        ``routing.require_certification_access``, whose checker APPROVE duty is
        unconditional for checker roles — so the report must list a missing
        APPROVE grant for those acts too. The stress ceremony carries
        maker/checker as evaluated conditions instead, not a separate APPROVE
        duty, so its routes never set this.
        """
        return self.source_family != "enterprise_stress" and self.signing_role in {
            "approver",
            "board",
        }

    @property
    def legacy_permission(self) -> Permission | None:
        """The pre-Signer certification action on the source family."""
        if self.baseline != "legacy_role":
            return None
        return {
            "preparer": Permission.EDIT,
            "approver": Permission.APPROVE,
            "board": Permission.APPROVE,
            "witness": Permission.VIEW,
        }[self.signing_role]


def _regulatory_routes() -> tuple[SigningRoute, ...]:
    actions: tuple[tuple[SigningAction, str, str], ...] = (
        ("preview", "GET", "preview"),
        ("step-up", "POST", "step-up"),
        ("certify", "POST", "certify"),
        ("certify-and-send", "POST", "certify-and-send"),
    )
    sources: tuple[tuple[SourceFamily, Module, Sensitivity], ...] = (
        ("ordinary", Module.REGULATORY, Sensitivity.RESTRICTED),
        ("icaap", Module.CAPITAL, Sensitivity.CONFIDENTIAL),
    )
    roles: tuple[SigningRole, ...] = ("preparer", "approver", "board", "witness")
    return tuple(
        SigningRoute(
            route_key=f"reg.{source_family}.{role}.{action}",
            method=method,
            path=f"{_BANK_PACKAGE}/{suffix}",
            action=action,
            signing_role=role,
            source_family=source_family,
            module=Module.REGULATORY,
            source_module=source_module,
            source_sensitivity=source_sensitivity,
            baseline="source_view" if action == "preview" else "legacy_role",
        )
        for source_family, source_module, source_sensitivity in sources
        for role in roles
        for action, method, suffix in actions
    )


SIGNING_ROUTES: tuple[SigningRoute, ...] = (
    *_regulatory_routes(),
    SigningRoute(
        route_key="risk.enterprise_stress.board.step-up",
        method="POST",
        path=f"{_STRESS}/step-up",
        action="step-up",
        signing_role="board",
        source_family="enterprise_stress",
        module=Module.RISK,
        source_module=Module.RISK,
        source_sensitivity=Sensitivity.CONFIDENTIAL,
        baseline="route_not_present",
    ),
    SigningRoute(
        route_key="risk.enterprise_stress.board.attest",
        method="POST",
        path=f"{_STRESS}/attest",
        action="attest",
        signing_role="board",
        source_family="enterprise_stress",
        module=Module.RISK,
        source_module=Module.RISK,
        source_sensitivity=Sensitivity.CONFIDENTIAL,
        baseline="legacy_scalar_approver",
    ),
)
