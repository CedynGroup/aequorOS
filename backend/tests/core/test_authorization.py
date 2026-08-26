"""Pure proofs for the scoped, deny-by-default authorization kernel."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.core.authorization import (
    BindingGrant,
    BindingStatus,
    ConditionCheck,
    ConditionKind,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
    evaluate_permission,
)

NOW = dt.datetime(2026, 8, 25, 12, tzinfo=dt.UTC)
ORG = "OR-DEM00001"
BANK_A = "BK-SAMP0001"
BANK_B = "BK-SAMP0002"
USER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PRINCIPAL = PrincipalLocator(ORG, USER, PrincipalType.HUMAN)


def _resource(
    *,
    bank_id: str | None = BANK_A,
    module: Module = Module.LIQUIDITY,
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
) -> ResourceLocator:
    return ResourceLocator(ORG, bank_id, module, sensitivity)


def _binding(  # noqa: PLR0913 - scope/lifecycle dimensions stay explicit in tests
    *,
    role: RoleBundle,
    module: ModuleScope,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = BANK_A,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    status: BindingStatus = BindingStatus.ACTIVE,
    valid_from: dt.datetime = NOW - dt.timedelta(days=1),
    valid_until: dt.datetime | None = None,
    revoked_at: dt.datetime | None = None,
) -> BindingGrant:
    return BindingGrant(
        binding_id=uuid4(),
        organization_id=ORG,
        principal_id=USER,
        principal_type=PrincipalType.HUMAN,
        role_bundle=role,
        institution_scope=institution_scope,
        institution_id=institution_id,
        module_scope=module,
        sensitivity_scope=sensitivity,
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
        revoked_at=revoked_at,
    )


def _evaluate(
    permission: Permission,
    resource: ResourceLocator,
    bindings: list[BindingGrant],
    *,
    conditions: tuple[ConditionCheck, ...] = (),
):  # noqa: ANN202 - concise policy-test helper
    return evaluate_permission(
        PRINCIPAL,
        permission,
        resource,
        bindings,
        conditions=conditions,
        now=NOW,
    )


def test_liq_analyst_plus_reg_approver_does_not_create_cross_product_authority() -> None:
    bindings = [
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY),
        _binding(role=RoleBundle.APPROVER, module=ModuleScope.REGULATORY),
    ]

    assert _evaluate(Permission.RUN, _resource(module=Module.LIQUIDITY), bindings).allowed
    assert _evaluate(Permission.APPROVE, _resource(module=Module.REGULATORY), bindings).allowed
    assert not _evaluate(Permission.APPROVE, _resource(module=Module.LIQUIDITY), bindings).allowed
    assert not _evaluate(Permission.RUN, _resource(module=Module.REGULATORY), bindings).allowed


@pytest.mark.parametrize(
    ("principal_type", "role_bundle", "permission"),
    [
        (PrincipalType.HUMAN, RoleBundle.INTEGRATION_WRITER, Permission.INGEST),
        (PrincipalType.MACHINE, RoleBundle.ANALYST, Permission.RUN),
    ],
)
def test_evaluator_denies_incompatible_principal_bundle_pairs(
    principal_type: PrincipalType,
    role_bundle: RoleBundle,
    permission: Permission,
) -> None:
    binding = replace(
        _binding(role=role_bundle, module=ModuleScope.LIQUIDITY),
        principal_type=principal_type,
    )
    principal = replace(PRINCIPAL, principal_type=principal_type)

    decision = evaluate_permission(
        principal,
        permission,
        _resource(),
        [binding],
        now=NOW,
    )

    assert not decision.allowed
    assert decision.binding_trace[0].reason == "principal_bundle_incompatible"


def test_organization_wide_scope_is_explicit_and_institution_scope_is_exact() -> None:
    institution_only = _binding(role=RoleBundle.VIEWER, module=ModuleScope.LIQUIDITY)
    organization_wide = _binding(
        role=RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
    )

    assert _evaluate(Permission.VIEW, _resource(bank_id=BANK_A), [institution_only]).allowed
    assert not _evaluate(Permission.VIEW, _resource(bank_id=BANK_B), [institution_only]).allowed
    assert _evaluate(Permission.VIEW, _resource(bank_id=BANK_B), [organization_wide]).allowed
    assert _evaluate(Permission.VIEW, _resource(bank_id=None), [organization_wide]).allowed


def test_module_and_sensitivity_dimensions_both_have_to_match() -> None:
    grant = _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY)

    module_denial = _evaluate(Permission.RUN, _resource(module=Module.CAPITAL), [grant])
    sensitivity_denial = _evaluate(
        Permission.RUN,
        _resource(sensitivity=Sensitivity.RESTRICTED),
        [grant],
    )

    assert not module_denial.allowed
    assert module_denial.binding_trace[0].reason == "module_mismatch"
    assert not sensitivity_denial.allowed
    assert sensitivity_denial.binding_trace[0].reason == "sensitivity_mismatch"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"valid_until": NOW}, "binding_expired"),
        ({"status": BindingStatus.SUSPENDED}, "binding_suspended"),
        (
            {"status": BindingStatus.REVOKED, "revoked_at": NOW - dt.timedelta(hours=1)},
            "binding_revoked",
        ),
        ({"valid_from": NOW + dt.timedelta(minutes=1)}, "binding_not_yet_valid"),
    ],
)
def test_inactive_lifecycle_states_deny(changes: dict[str, object], reason: str) -> None:
    grant = replace(
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY),
        **changes,
    )

    decision = _evaluate(Permission.RUN, _resource(), [grant])

    assert not decision.allowed
    assert decision.reason == "no_active_exact_binding"
    assert decision.binding_trace[0].reason == reason


def test_bindings_union_only_after_each_independent_binding_matches() -> None:
    scope_match_without_permission = _binding(
        role=RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
    )
    permission_match_without_scope = _binding(
        role=RoleBundle.ANALYST,
        module=ModuleScope.REGULATORY,
    )

    denied = _evaluate(
        Permission.RUN,
        _resource(module=Module.LIQUIDITY),
        [scope_match_without_permission, permission_match_without_scope],
    )
    allowed = _evaluate(
        Permission.RUN,
        _resource(module=Module.LIQUIDITY),
        [
            scope_match_without_permission,
            permission_match_without_scope,
            _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY),
        ],
    )

    assert not denied.allowed
    assert not denied.matching_binding_ids
    assert allowed.allowed
    assert len(allowed.matching_binding_ids) == 1


def test_non_bypassable_condition_vetoes_every_matching_binding() -> None:
    bindings = [
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY),
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.ALL),
    ]
    condition = ConditionCheck(
        kind=ConditionKind.DEMO_MODE,
        passed=False,
        reason="mutations are disabled in demo mode",
    )

    decision = _evaluate(Permission.RUN, _resource(), bindings, conditions=(condition,))

    assert not decision.allowed
    assert decision.reason == "condition_denied:demo_mode"
    assert len(decision.matching_binding_ids) == 2
    assert decision.to_audit_dict()["conditions"] == [
        {
            "kind": "demo_mode",
            "passed": False,
            "reason": "mutations are disabled in demo mode",
        }
    ]
