"""Generative checks for indivisible authorization-binding semantics.

The fixed examples in ``test_authorization.py`` explain the policy.  These
properties protect it across combinations that are easy to miss by hand:
dimensions may match on different rows, binding order may change, and validity
boundaries may land exactly on the evaluation instant.  The reference matcher
is intentionally written without calling evaluator helpers so it can catch a
shared-implementation error rather than merely repeat it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.authorization import (
    ROLE_PERMISSIONS,
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
    principal_bundle_compatible,
)

_NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
_ORG = "OR-DEM00001"
_OTHER_ORG = "OR-OTHR0001"
_BANK = "BK-SAMP0001"
_OTHER_BANK = "BK-OTHR0001"
_PRINCIPAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_OTHER_PRINCIPAL_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _reference_match(
    binding: BindingGrant,
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
) -> bool:
    active = (
        binding.status is BindingStatus.ACTIVE
        and binding.revoked_at is None
        and binding.valid_from <= _NOW
        and (binding.valid_until is None or binding.valid_until > _NOW)
    )
    permission_matches = (
        principal_bundle_compatible(binding.principal_type, binding.role_bundle)
        and permission in ROLE_PERMISSIONS[binding.role_bundle]
    )
    organization_matches = (
        principal.organization_id == resource.organization_id
        and binding.organization_id == principal.organization_id
        and binding.principal_id == principal.principal_id
        and binding.principal_type is principal.principal_type
    )
    if binding.institution_scope is InstitutionScope.ORGANIZATION:
        institution_matches = binding.institution_id is None
    else:
        institution_matches = (
            resource.institution_id is not None
            and binding.institution_id == resource.institution_id
        )
    module_matches = (
        binding.module_scope is ModuleScope.ALL
        or binding.module_scope.value == resource.module.value
    )
    sensitivity_matches = (
        binding.sensitivity_scope is SensitivityScope.ALL
        or binding.sensitivity_scope.value == resource.sensitivity.value
    )
    return all(
        (
            active,
            permission_matches,
            organization_matches,
            institution_matches,
            module_matches,
            sensitivity_matches,
        )
    )


@st.composite
def _binding_collections(draw: st.DrawFn) -> list[BindingGrant]:
    count = draw(st.integers(min_value=0, max_value=7))
    binding_ids = draw(st.lists(st.uuids(version=4), min_size=count, max_size=count, unique=True))
    bindings: list[BindingGrant] = []
    for binding_id in binding_ids:
        until_offset = draw(st.one_of(st.none(), st.integers(min_value=-1, max_value=1)))
        bindings.append(
            BindingGrant(
                binding_id=binding_id,
                organization_id=draw(st.sampled_from((_ORG, _OTHER_ORG))),
                principal_id=draw(st.sampled_from((_PRINCIPAL_ID, _OTHER_PRINCIPAL_ID))),
                principal_type=draw(st.sampled_from(tuple(PrincipalType))),
                role_bundle=draw(st.sampled_from(tuple(RoleBundle))),
                institution_scope=draw(st.sampled_from(tuple(InstitutionScope))),
                institution_id=draw(st.sampled_from((None, _BANK, _OTHER_BANK))),
                module_scope=draw(st.sampled_from(tuple(ModuleScope))),
                sensitivity_scope=draw(st.sampled_from(tuple(SensitivityScope))),
                status=draw(st.sampled_from(tuple(BindingStatus))),
                valid_from=_NOW + timedelta(seconds=draw(st.integers(min_value=-1, max_value=1))),
                valid_until=(
                    None if until_offset is None else _NOW + timedelta(seconds=until_offset)
                ),
                revoked_at=draw(st.one_of(st.none(), st.just(_NOW))),
            )
        )
    return bindings


@settings(max_examples=100)
@given(
    bindings=_binding_collections(),
    permission=st.sampled_from(tuple(Permission)),
    module=st.sampled_from(tuple(Module)),
    sensitivity=st.sampled_from(tuple(Sensitivity)),
    resource_organization_id=st.sampled_from((_ORG, _OTHER_ORG)),
    institution_id=st.sampled_from((None, _BANK, _OTHER_BANK)),
    condition_results=st.lists(st.booleans(), min_size=0, max_size=len(ConditionKind)),
)
def test_evaluator_matches_independent_per_binding_oracle(  # noqa: PLR0913
    bindings: list[BindingGrant],
    permission: Permission,
    module: Module,
    sensitivity: Sensitivity,
    resource_organization_id: str,
    institution_id: str | None,
    condition_results: list[bool],
) -> None:
    principal = PrincipalLocator(_ORG, _PRINCIPAL_ID, PrincipalType.HUMAN)
    resource = ResourceLocator(resource_organization_id, institution_id, module, sensitivity)
    conditions = tuple(
        ConditionCheck(kind, passed, f"{kind.value}:{passed}")
        for kind, passed in zip(ConditionKind, condition_results, strict=False)
    )
    expected_ids = tuple(
        binding.binding_id
        for binding in bindings
        if _reference_match(binding, principal, permission, resource)
    )
    expected_allowed = bool(expected_ids) and all(check.passed for check in conditions)

    decision = evaluate_permission(
        principal,
        permission,
        resource,
        bindings,
        conditions=conditions,
        now=_NOW,
    )

    assert decision.allowed is expected_allowed
    assert decision.matching_binding_ids == expected_ids
    assert (
        tuple(trace.binding_id for trace in decision.binding_trace if trace.matched) == expected_ids
    )

    reversed_decision = evaluate_permission(
        principal,
        permission,
        resource,
        list(reversed(bindings)),
        conditions=conditions,
        now=_NOW,
    )
    assert reversed_decision.allowed is expected_allowed
    assert set(reversed_decision.matching_binding_ids) == set(expected_ids)


def _exact_binding(  # noqa: PLR0913 - explicit indivisible binding dimensions
    binding_id: UUID,
    *,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    status: BindingStatus = BindingStatus.ACTIVE,
    valid_from: datetime = _NOW,
    valid_until: datetime | None = None,
    revoked_at: datetime | None = None,
) -> BindingGrant:
    return BindingGrant(
        binding_id=binding_id,
        organization_id=_ORG,
        principal_id=_PRINCIPAL_ID,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=_BANK,
        module_scope=module_scope,
        sensitivity_scope=sensitivity_scope,
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
        revoked_at=revoked_at,
    )


@settings(max_examples=100)
@given(
    module=st.sampled_from(tuple(Module)),
    wrong_module=st.sampled_from(tuple(Module)),
    sensitivity=st.sampled_from(tuple(Sensitivity)),
    wrong_sensitivity=st.sampled_from(tuple(Sensitivity)),
    binding_ids=st.lists(st.uuids(version=4), min_size=3, max_size=3, unique=True),
)
def test_partial_scope_matches_on_different_rows_never_compose(
    module: Module,
    wrong_module: Module,
    sensitivity: Sensitivity,
    wrong_sensitivity: Sensitivity,
    binding_ids: list[UUID],
) -> None:
    if wrong_module is module or wrong_sensitivity is sensitivity:
        return
    principal = PrincipalLocator(_ORG, _PRINCIPAL_ID, PrincipalType.HUMAN)
    resource = ResourceLocator(_ORG, _BANK, module, sensitivity)
    module_only = _exact_binding(
        binding_ids[0],
        module_scope=ModuleScope(module.value),
        sensitivity_scope=SensitivityScope(wrong_sensitivity.value),
    )
    sensitivity_only = _exact_binding(
        binding_ids[1],
        module_scope=ModuleScope(wrong_module.value),
        sensitivity_scope=SensitivityScope(sensitivity.value),
    )

    partial = evaluate_permission(
        principal,
        Permission.RUN,
        resource,
        [module_only, sensitivity_only],
        now=_NOW,
    )
    assert not partial.allowed
    assert partial.matching_binding_ids == ()

    complete = _exact_binding(
        binding_ids[2],
        module_scope=ModuleScope(module.value),
        sensitivity_scope=SensitivityScope(sensitivity.value),
    )
    union = evaluate_permission(
        principal,
        Permission.RUN,
        resource,
        [module_only, sensitivity_only, complete],
        now=_NOW,
    )
    assert union.allowed
    assert union.matching_binding_ids == (complete.binding_id,)


@settings(max_examples=100)
@given(
    status=st.sampled_from(tuple(BindingStatus)),
    starts_in=st.integers(min_value=-1, max_value=1),
    ends_in=st.one_of(st.none(), st.integers(min_value=-1, max_value=1)),
    revoked=st.booleans(),
    binding_id=st.uuids(version=4),
)
def test_lifecycle_boundaries_fail_closed(
    status: BindingStatus,
    starts_in: int,
    ends_in: int | None,
    revoked: bool,
    binding_id: UUID,
) -> None:
    binding = _exact_binding(
        binding_id,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        status=status,
        valid_from=_NOW + timedelta(seconds=starts_in),
        valid_until=None if ends_in is None else _NOW + timedelta(seconds=ends_in),
        revoked_at=_NOW if revoked else None,
    )
    expected_active = (
        status is BindingStatus.ACTIVE
        and not revoked
        and starts_in <= 0
        and (ends_in is None or ends_in > 0)
    )
    decision = evaluate_permission(
        PrincipalLocator(_ORG, _PRINCIPAL_ID, PrincipalType.HUMAN),
        Permission.RUN,
        ResourceLocator(_ORG, _BANK, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        [binding],
        now=_NOW,
    )

    assert decision.allowed is expected_active
    assert decision.binding_trace[0].active is expected_active
