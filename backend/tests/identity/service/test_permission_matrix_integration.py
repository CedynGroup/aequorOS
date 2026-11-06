"""Execute the reviewed matrix against stored grants and real bank-detail HTTP."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import authorization as kernel
from app.core.authorization import (
    BindingGrant,
    GrantorType,
    InstitutionScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
)
from app.core.config import get_settings
from app.identity.models.bank import Bank
from app.identity.models.user import User
from app.identity.schemas.banks import BankRead
from app.identity.service import authorization
from app.models import Jurisdiction
from tests.support.factories import role_scope as actors
from tests.support.permission_matrix import (
    ActionCase,
    BankDetailProbe,
    BankTarget,
    GrantSpec,
    ScopeScenario,
    action_cases,
    load_matrix,
)

MATRIX = load_matrix()
ACTION_CASES = action_cases(MATRIX)
GRANTOR = authorization.GrantorRef(GrantorType.SYSTEM, "permission-matrix-test")


@dataclass(frozen=True)
class MatrixWorld:
    primary: Bank
    sibling: Bank
    foreign: Bank

    def bank_id(self, target: BankTarget) -> str:
        match target:
            case "primary":
                return self.primary.id
            case "sibling":
                return self.sibling.id
            case "foreign":
                return self.foreign.id
            case "missing":
                return "BK-MATRX099"

    def resource(self, name: str) -> ResourceLocator:
        spec = MATRIX.resources[name]
        organization_id = (
            self.foreign.organization_id if spec.bank == "foreign" else self.primary.organization_id
        )
        return ResourceLocator(
            organization_id,
            spec.scope,
            None if spec.bank is None else self.bank_id(spec.bank),
            spec.module,
            spec.sensitivity,
        )


@pytest.fixture
def matrix_world(db_session: Session) -> MatrixWorld:
    tenant = actors.organization(db_session, name="Synthetic matrix tenant")
    foreign = actors.organization(db_session, name="Synthetic matrix foreign tenant")
    jurisdiction = db_session.get(Jurisdiction, "GH")
    assert jurisdiction is not None
    banks = [
        actors.bank(
            db_session,
            organization_id=organization_id,
            bank_id=bank_id,
            jurisdiction=jurisdiction,
            institution_type="universal_bank",
        )
        for organization_id, bank_id in (
            (tenant.id, "BK-MATRX001"),
            (tenant.id, "BK-MATRX002"),
            (foreign.id, "BK-MATRX003"),
        )
    ]
    return MatrixWorld(*banks)


def _subject(
    db: Session,
    world: MatrixWorld,
    grants: tuple[GrantSpec, ...],
    principal_type: PrincipalType,
) -> tuple[User, PrincipalLocator]:
    user = (
        actors.human(db, organization_id=world.primary.organization_id, legacy_role="admin")
        if principal_type is PrincipalType.HUMAN
        else actors.machine(db, organization_id=world.primary.organization_id, legacy_role="viewer")
    )
    principal = PrincipalLocator(user.organization_id, user.id, principal_type)
    factory = actors.RoleBindingFactory(db, GRANTOR)
    for grant in grants:
        if grant.scope is InstitutionScope.ORGANIZATION:
            scope = actors.organization_scope(module=grant.module, sensitivity=grant.sensitivity)
        else:
            assert grant.bank is not None
            scope = actors.institution_scope(
                bank_id=world.bank_id(grant.bank),
                module=grant.module,
                sensitivity=grant.sensitivity,
                data_scope=grant.data_scope,
                data_scope_values=grant.values,
            )
        factory.create(
            principal=principal,
            bundle=grant.bundle,
            scope=scope,
            reason="One complete, explicit permission-matrix characterization sentence",
        )
    return user, principal


def _assert_action(
    db: Session, world: MatrixWorld, principal: PrincipalLocator, case: ActionCase
) -> None:
    decision = authorization.evaluate_permission(
        db,
        principal,
        case.action,
        world.resource(case.role.resource),
        conditions=MATRIX.action_preconditions,
    )
    assert decision.allowed is case.allowed, f"{case.id}: {decision}"


def _assert_scenario(
    db: Session, world: MatrixWorld, principal: PrincipalLocator, scenario: ScopeScenario
) -> None:
    decision = authorization.evaluate_permission(
        db,
        principal,
        scenario.action,
        world.resource(scenario.resource),
        conditions=scenario.conditions,
    )
    assert decision.allowed is scenario.allowed, f"{scenario.id}: {decision}"
    assert decision.reason == scenario.reason, f"{scenario.id}: {decision}"


@pytest.mark.parametrize("case", ACTION_CASES, ids=[case.id for case in ACTION_CASES])
def test_role_resource_action_cell(
    db_session: Session, matrix_world: MatrixWorld, case: ActionCase
) -> None:
    _, principal = _subject(db_session, matrix_world, (case.role,), case.role.principal_type)
    _assert_action(db_session, matrix_world, principal, case)


@pytest.mark.parametrize(
    "scenario", MATRIX.scenarios, ids=[scenario.id for scenario in MATRIX.scenarios]
)
def test_scoped_permission_sentence(
    db_session: Session,
    matrix_world: MatrixWorld,
    monkeypatch: pytest.MonkeyPatch,
    scenario: ScopeScenario,
) -> None:
    user, principal = _subject(
        db_session,
        matrix_world,
        tuple(MATRIX.grants[name] for name in scenario.grants),
        PrincipalType.HUMAN,
    )
    if not scenario.principal_active:
        user.is_active = False
        authorization.invalidate_user_authorization(
            db_session,
            organization_id=user.organization_id,
            user_id=user.id,
            reason="Explicit inactive-identity characterization",
            commit=False,
        )
    monkeypatch.setattr(get_settings().app, "demo_mode", scenario.demo_mode)
    _assert_scenario(db_session, matrix_world, principal, scenario)


@pytest.mark.parametrize(
    "probe",
    MATRIX.bank_detail,
    ids=[f"{probe.scenario}-{probe.bank}" for probe in MATRIX.bank_detail],
)
def test_bank_detail_with_real_session(
    db_client: TestClient,
    db_session: Session,
    matrix_world: MatrixWorld,
    probe: BankDetailProbe,
) -> None:
    scenario = next(row for row in MATRIX.scenarios if row.id == probe.scenario)
    user, _ = _subject(
        db_session,
        matrix_world,
        tuple(MATRIX.grants[name] for name in scenario.grants),
        PrincipalType.HUMAN,
    )
    session = actors.tenant_session(db_session, user=user)
    response = db_client.get(
        f"/api/v1/banks/{matrix_world.bank_id(probe.bank)}",
        headers=actors.bearer(session.access_token),
    )
    assert response.status_code == probe.status, response.text
    if probe.status == 200:
        bank = BankRead.model_validate_json(response.text)
        assert bank.id == matrix_world.bank_id(probe.bank)
        assert bank.organization_id == matrix_world.primary.organization_id


def test_matrix_detects_permission_widening(
    db_session: Session, matrix_world: MatrixWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = next(
        case
        for case in ACTION_CASES
        if case.role.bundle is RoleBundle.VIEWER and case.action is Permission.EXPORT
    )
    _, principal = _subject(db_session, matrix_world, (case.role,), PrincipalType.HUMAN)
    _assert_action(db_session, matrix_world, principal, case)
    with monkeypatch.context() as mutation:
        mutation.setattr(
            kernel,
            "ROLE_PERMISSIONS",
            {
                **kernel.ROLE_PERMISSIONS,
                RoleBundle.VIEWER: kernel.ROLE_PERMISSIONS[RoleBundle.VIEWER] | {Permission.EXPORT},
            },
        )
        with pytest.raises(AssertionError, match=case.id):
            _assert_action(db_session, matrix_world, principal, case)
    _assert_action(db_session, matrix_world, principal, case)


def test_matrix_detects_lost_bank_scope(
    db_session: Session, matrix_world: MatrixWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = next(row for row in MATRIX.scenarios if row.id == "viewer_sibling")
    _, principal = _subject(
        db_session,
        matrix_world,
        tuple(MATRIX.grants[name] for name in scenario.grants),
        PrincipalType.HUMAN,
    )
    _assert_scenario(db_session, matrix_world, principal, scenario)

    def ignore_institution(_binding: BindingGrant, _resource: ResourceLocator) -> bool:
        return True

    with monkeypatch.context() as mutation:
        mutation.setattr(kernel, "_institution_matches", ignore_institution)
        with pytest.raises(AssertionError, match=scenario.id):
            _assert_scenario(db_session, matrix_world, principal, scenario)
    _assert_scenario(db_session, matrix_world, principal, scenario)
