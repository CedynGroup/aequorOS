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

ROLE_EXPECTATIONS: tuple[
    tuple[PrincipalType, RoleBundle, frozenset[Permission]],
    ...,
] = (
    (PrincipalType.HUMAN, RoleBundle.VIEWER, frozenset({Permission.VIEW})),
    (PrincipalType.HUMAN, RoleBundle.AUDITOR, frozenset({Permission.VIEW})),
    (
        PrincipalType.HUMAN,
        RoleBundle.ANALYST,
        frozenset(
            {
                Permission.VIEW,
                Permission.CREATE,
                Permission.EDIT,
                Permission.RUN,
                Permission.VALIDATE,
                Permission.EXPORT,
            }
        ),
    ),
    (
        PrincipalType.HUMAN,
        RoleBundle.APPROVER,
        frozenset({Permission.VIEW, Permission.REVIEW, Permission.APPROVE}),
    ),
    (PrincipalType.HUMAN, RoleBundle.ACCOUNT_ADMIN, frozenset({Permission.ADMINISTER})),
    (PrincipalType.MACHINE, RoleBundle.INTEGRATION_WRITER, frozenset({Permission.INGEST})),
)


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


@pytest.mark.parametrize(("principal_type", "role_bundle", "expected"), ROLE_EXPECTATIONS)
def test_each_role_bundle_has_an_explicit_allow_and_deny_contract(
    principal_type: PrincipalType,
    role_bundle: RoleBundle,
    expected: frozenset[Permission],
) -> None:
    principal = replace(PRINCIPAL, principal_type=principal_type)
    binding = replace(
        _binding(role=role_bundle, module=ModuleScope.ALL),
        principal_type=principal_type,
    )

    outcomes = {
        permission: evaluate_permission(
            principal,
            permission,
            _resource(),
            [binding],
            now=NOW,
        ).allowed
        for permission in Permission
    }

    assert {permission for permission, allowed in outcomes.items() if allowed} == expected
    assert {permission for permission, allowed in outcomes.items() if not allowed} == (
        set(Permission) - expected
    )


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


def test_organization_tenant_mismatch_denies_an_otherwise_exact_grant() -> None:
    exact = _binding(role=RoleBundle.VIEWER, module=ModuleScope.LIQUIDITY)
    other_tenant_binding = replace(exact, organization_id="OR-1S000002")
    other_tenant_resource = replace(_resource(), organization_id="OR-1S000002")

    assert not _evaluate(Permission.VIEW, _resource(), [other_tenant_binding]).allowed
    assert not _evaluate(Permission.VIEW, other_tenant_resource, [exact]).allowed


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


def test_validity_window_is_start_inclusive_and_end_exclusive() -> None:
    starts_now = _binding(
        role=RoleBundle.ANALYST,
        module=ModuleScope.LIQUIDITY,
        valid_from=NOW,
        valid_until=NOW + dt.timedelta(microseconds=1),
    )
    ends_now = replace(starts_now, valid_from=NOW - dt.timedelta(days=1), valid_until=NOW)

    assert _evaluate(Permission.RUN, _resource(), [starts_now]).allowed
    assert not _evaluate(Permission.RUN, _resource(), [ends_now]).allowed


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


@pytest.mark.parametrize("kind", tuple(ConditionKind))
def test_non_bypassable_condition_vetoes_every_matching_binding(kind: ConditionKind) -> None:
    bindings = [
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY),
        _binding(role=RoleBundle.ANALYST, module=ModuleScope.ALL),
    ]
    condition = ConditionCheck(
        kind=kind,
        passed=False,
        reason=f"{kind.value} requirement not satisfied",
    )

    decision = _evaluate(Permission.RUN, _resource(), bindings, conditions=(condition,))

    assert not decision.allowed
    assert decision.reason == f"condition_denied:{kind.value}"
    assert len(decision.matching_binding_ids) == 2
    assert decision.to_audit_dict()["conditions"] == [
        {
            "kind": kind.value,
            "passed": False,
            "reason": f"{kind.value} requirement not satisfied",
        }
    ]
