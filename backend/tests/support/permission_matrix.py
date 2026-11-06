"""Typed, test-only characterization data, never a runtime policy evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization import (
    ConditionCheck,
    DataScope,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)

type BankTarget = Literal["primary", "sibling", "foreign", "missing"]


class MatrixRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ResourceSpec(MatrixRecord):
    scope: InstitutionScope
    bank: BankTarget | None
    module: Module
    sensitivity: Sensitivity

    @model_validator(mode="after")
    def explicit_target(self) -> Self:
        if (self.scope is InstitutionScope.ORGANIZATION) != (self.bank is None):
            raise ValueError("organization targets forbid a bank; institution targets require one")
        return self


class GrantSpec(MatrixRecord):
    bundle: RoleBundle
    scope: InstitutionScope
    bank: Literal["primary", "sibling"] | None
    module: ModuleScope
    sensitivity: SensitivityScope
    data_scope: DataScope
    values: tuple[str, ...] = ()

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if (self.scope is InstitutionScope.ORGANIZATION) != (self.bank is None):
            raise ValueError("organization grants forbid a bank; institution grants require one")
        if (self.data_scope is DataScope.ALL) != (not self.values):
            raise ValueError("narrow grants require values; whole-institution grants forbid them")
        if self.scope is InstitutionScope.ORGANIZATION and self.data_scope is not DataScope.ALL:
            raise ValueError("organization grants must explicitly use whole-institution data scope")
        return self


class RoleRow(GrantSpec):
    principal_type: PrincipalType
    resource: str
    allowed: tuple[Permission, ...]
    denied: tuple[Permission, ...]

    @model_validator(mode="after")
    def complete_actions(self) -> Self:
        actions = (*self.allowed, *self.denied)
        if len(actions) != len(set(actions)) or set(actions) != set(Permission):
            raise ValueError("each action must have exactly one explicit allowed or denied outcome")
        return self


class ScopeScenario(MatrixRecord):
    id: str
    grants: tuple[str, ...]
    resource: str
    action: Permission
    allowed: bool
    reason: str
    conditions: tuple[ConditionCheck, ...] = ()
    principal_active: bool = True
    demo_mode: bool = False


class BankDetailProbe(MatrixRecord):
    scenario: str
    bank: Literal["primary", "sibling", "foreign"]
    status: Literal[200, 404]


class PermissionMatrix(MatrixRecord):
    schema_version: Literal[1]
    baseline: str
    sources: tuple[str, ...] = Field(min_length=1)
    action_preconditions: tuple[ConditionCheck, ...]
    resources: dict[str, ResourceSpec]
    roles: tuple[RoleRow, ...] = Field(min_length=1)
    grants: dict[str, GrantSpec]
    scenarios: tuple[ScopeScenario, ...] = Field(min_length=1)
    bank_detail: tuple[BankDetailProbe, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bounded_references(self) -> Self:
        bundles = [row.bundle for row in self.roles]
        if len(bundles) != len(set(bundles)) or set(bundles) != set(RoleBundle):
            raise ValueError("every current bundle requires exactly one reviewed role row")
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario ids must be unique")
        for row in (*self.roles, *self.scenarios):
            if row.resource not in self.resources:
                raise ValueError(f"unknown resource: {row.resource}")
        for scenario in self.scenarios:
            if set(scenario.grants) - self.grants.keys():
                raise ValueError(f"unknown grant in scenario: {scenario.id}")
        for probe in self.bank_detail:
            if probe.scenario not in ids:
                raise ValueError(f"unknown HTTP scenario: {probe.scenario}")
            scenario = next(row for row in self.scenarios if row.id == probe.scenario)
            if not scenario.principal_active or scenario.demo_mode or scenario.conditions:
                raise ValueError("bank-detail probes support only active, ordinary human sessions")
        return self


@dataclass(frozen=True)
class ActionCase:
    role: RoleRow
    action: Permission
    allowed: bool

    @property
    def id(self) -> str:
        return f"{self.role.bundle}-{self.role.resource}-{self.action}"


def load_matrix() -> PermissionMatrix:
    path = Path(__file__).parents[1] / "fixtures" / "permission_matrix.json"
    return PermissionMatrix.model_validate_json(path.read_bytes())


def action_cases(matrix: PermissionMatrix) -> tuple[ActionCase, ...]:
    """Expand explicit expected cells without consulting production permissions."""
    return tuple(
        ActionCase(role, action, allowed)
        for role in matrix.roles
        for actions, allowed in ((role.allowed, True), (role.denied, False))
        for action in actions
    )
