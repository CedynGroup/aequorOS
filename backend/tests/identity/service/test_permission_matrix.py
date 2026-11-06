"""The declarative oracle rejects incomplete or contradictory expected cells."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.authorization import DataScope, InstitutionScope, Permission, RoleBundle
from tests.support.permission_matrix import (
    GrantSpec,
    PermissionMatrix,
    ResourceSpec,
    RoleRow,
    load_matrix,
)

MATRIX = load_matrix()
VIEWER = next(row for row in MATRIX.roles if row.bundle is RoleBundle.VIEWER)


@pytest.mark.parametrize("contradictory", [False, True])
def test_action_oracle_requires_an_explicit_partition(contradictory: bool) -> None:
    invalid = (
        VIEWER.model_copy(update={"allowed": (*VIEWER.allowed, Permission.EXPORT)})
        if contradictory
        else VIEWER.model_copy(
            update={
                "denied": tuple(
                    action for action in VIEWER.denied if action is not Permission.EXPORT
                )
            }
        )
    )
    with pytest.raises(ValidationError, match="exactly one explicit"):
        RoleRow.model_validate_json(invalid.model_dump_json())


def test_omitting_a_bundle_requires_review_not_a_default_expectation() -> None:
    invalid = MATRIX.model_copy(update={"roles": MATRIX.roles[:-1]})
    with pytest.raises(ValidationError, match="every current bundle"):
        PermissionMatrix.model_validate_json(invalid.model_dump_json())


def test_unknown_resource_is_not_an_implicit_scope() -> None:
    roles = (MATRIX.roles[0].model_copy(update={"resource": "unknown"}), *MATRIX.roles[1:])
    invalid = MATRIX.model_copy(update={"roles": roles})
    with pytest.raises(ValidationError, match="unknown resource"):
        PermissionMatrix.model_validate_json(invalid.model_dump_json())


def test_missing_bank_is_not_organization_coverage() -> None:
    invalid = MATRIX.resources["liquidity"].model_copy(update={"bank": None})
    with pytest.raises(ValidationError, match="institution targets require one"):
        ResourceSpec.model_validate_json(invalid.model_dump_json())
    invalid = MATRIX.resources["liquidity"].model_copy(
        update={"scope": InstitutionScope.ORGANIZATION}
    )
    with pytest.raises(ValidationError, match="organization targets forbid a bank"):
        ResourceSpec.model_validate_json(invalid.model_dump_json())


def test_organization_grant_cannot_silently_drop_narrowing() -> None:
    invalid = MATRIX.grants["credit_branch"].model_copy(
        update={"scope": InstitutionScope.ORGANIZATION, "bank": None}
    )
    with pytest.raises(ValidationError, match="explicitly use whole-institution"):
        GrantSpec.model_validate_json(invalid.model_dump_json())
    assert invalid.data_scope is DataScope.BRANCH


def test_http_probe_cannot_silently_drop_scenario_preconditions() -> None:
    scenarios = (
        MATRIX.scenarios[0].model_copy(update={"principal_active": False}),
        *MATRIX.scenarios[1:],
    )
    invalid = MATRIX.model_copy(update={"scenarios": scenarios})
    with pytest.raises(ValidationError, match="only active, ordinary human sessions"):
        PermissionMatrix.model_validate_json(invalid.model_dump_json())
