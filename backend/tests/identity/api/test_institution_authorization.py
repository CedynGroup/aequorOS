from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity import public
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.institution_profile import InstitutionProfile, Outlet
from app.identity.models.user import User
from app.identity.schemas.institution_profile import InstitutionProfileAccessRead
from app.identity.service import authorization
from app.models.audit_event import AuditEvent
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BANK = "BK-1NST0001"
OTHER_BANK = "BK-1NST0002"
BASE = f"/api/v1/banks/{BANK}"
PROFILE = {
    "reason": "Synthetic profile",
    "institution_type": "universal_bank",
    "legal_entity_structure": "public_limited_company",
}


@pytest.fixture(autouse=True)
def register(db_client: TestClient, db_session: Session) -> None:
    db_session.execute(delete(AuthorizationBinding))
    for bank_id, org in ((BANK, ORG_1), (OTHER_BANK, ORG_2)):
        db_session.add(
            Bank(
                id=bank_id,
                organization_id=org,
                name="Synthetic Institution",
                short_name="Synthetic",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
    db_session.flush()
    db_session.add(
        InstitutionProfile(
            organization_id=ORG_1,
            bank_id=BANK,
            institution_type="universal_bank",
            legal_entity_structure="public_limited_company",
            tin="SYNTHETIC-TAX-ID",
            registration_number="SYNTHETIC-REG",
            orass_institution_code="SYNTHETIC-PORTAL",
        )
    )
    db_session.commit()


def grant(
    db: Session,
    *,
    module: ModuleScope = ModuleScope.INSTITUTION,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    role: RoleBundle = RoleBundle.VIEWER,
) -> AuthorizationBinding:
    binding = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=role,
        scope=authorization.BindingScope(InstitutionScope.INSTITUTION, BANK, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "institution-test"),
        reason="Synthetic exact institution grant",
        commit=False,
    )
    user = db.get(User, USER_1)
    assert user is not None
    user.authorization_version = 1
    db.commit()
    return binding


def test_scalar_administrator_cannot_read_institution_register(db_client: TestClient) -> None:
    response = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    "module", [ModuleScope.ACCOUNT, ModuleScope.REGULATORY, ModuleScope.CREDIT]
)
def test_other_module_does_not_authorize_source_register(
    db_client: TestClient, db_session: Session, module: ModuleScope
) -> None:
    grant(db_session, module=module, sensitivity=SensitivityScope.ALL, role=RoleBundle.ANALYST)
    response = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    "sensitivity", [SensitivityScope.CONFIDENTIAL, SensitivityScope.RESTRICTED]
)
def test_read_sections_have_independent_exact_sensitivity(
    db_client: TestClient, db_session: Session, sensitivity: SensitivityScope
) -> None:
    grant(db_session, sensitivity=sensitivity)
    response = db_client.get(f"{BASE}/institution-profile", headers=headers(roles=("viewer",)))
    assert response.status_code == 200, response.text
    body = InstitutionProfileAccessRead.model_validate_json(response.text)
    general = sensitivity == SensitivityScope.CONFIDENTIAL
    assert body.general_visible is general
    assert body.restricted_visible is (not general)
    if general:
        assert body.profile is not None
        assert body.profile.institution_type == "universal_bank"
        assert body.identifiers is None
        assert "tin" not in body.profile.model_dump()
        assert "SYNTHETIC-TAX-ID" not in response.text
    else:
        assert body.profile is None
        assert body.identifiers is not None
        assert body.identifiers.tin == "SYNTHETIC-TAX-ID"
        assert body.outlets == body.products == body.licenses == body.name_history == []


@pytest.mark.parametrize("defect", ["suspended", "expired", "future", "partial", "wrong_bank"])
def test_incomplete_or_inactive_bindings_never_compose(
    db_client: TestClient, db_session: Session, defect: str
) -> None:
    binding = grant(db_session)
    if defect == "suspended":
        binding.status = BindingStatus.SUSPENDED.value
    elif defect == "expired":
        binding.valid_from = utc_now() - timedelta(days=2)
        binding.valid_until = utc_now() - timedelta(days=1)
    elif defect == "future":
        binding.valid_from = utc_now() + timedelta(days=1)
    elif defect == "wrong_bank":
        # A same-tenant bank isolates the institution dimension from tenancy.
        db_session.add(
            Bank(
                id="BK-1NST0003",
                organization_id=ORG_1,
                name="Other synthetic",
                short_name="Other",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db_session.flush()
        binding.institution_id = "BK-1NST0003"
    else:
        binding.sensitivity_scope = SensitivityScope.AGGREGATED.value
        grant(db_session, module=ModuleScope.REGULATORY, sensitivity=SensitivityScope.CONFIDENTIAL)
    db_session.commit()
    response = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert response.status_code == 403, response.text


def test_confidential_editor_preserves_omitted_identifiers_and_cannot_clear_them(
    db_client: TestClient, db_session: Session
) -> None:
    grant(db_session, role=RoleBundle.ANALYST)
    response = db_client.put(
        f"{BASE}/institution-profile",
        headers=headers(roles=("viewer",)),
        json={**PROFILE, "institution_type": "Synthetic updated"},
    )
    assert response.status_code == 200, response.text
    assert "tin" not in response.json()
    denied = db_client.put(
        f"{BASE}/institution-profile", headers=headers(), json={**PROFILE, "tin": None}
    )
    assert denied.status_code == 403, denied.text
    db_session.expire_all()
    profile = db_session.scalar(
        select(InstitutionProfile).where(InstitutionProfile.bank_id == BANK)
    )
    assert profile is not None
    assert profile.tin == "SYNTHETIC-TAX-ID"
    assert profile.institution_type == "Synthetic updated"


def test_child_updates_hide_ids_and_deny_before_writes(
    db_client: TestClient, db_session: Session
) -> None:
    outlet = Outlet(
        organization_id=ORG_1, bank_id=BANK, outlet_type="branch", name="Synthetic branch"
    )
    db_session.add(outlet)
    db_session.commit()
    for child_id in (outlet.id, UUID(int=1)):
        response = db_client.put(
            f"{BASE}/outlets/{child_id}",
            headers=headers(),
            json={"reason": "Denied update", "outlet_type": "branch", "name": "Changed"},
        )
        assert response.status_code == 404, response.text
    db_session.refresh(outlet)
    assert outlet.name == "Synthetic branch"


def test_cross_tenant_bank_is_hidden(db_client: TestClient, db_session: Session) -> None:
    grant(db_session, sensitivity=SensitivityScope.ALL)
    response = db_client.get(f"/api/v1/banks/{OTHER_BANK}/institution-profile", headers=headers())
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    ("path", "payload", "expected"),
    [
        ("institution-profile", PROFILE, 403),
        ("related-parties", {"party_type": "individual", "full_name": "Synthetic person"}, 403),
        ("related-parties/00000000-0000-0000-0000-000000000001", {"status": "inactive"}, 404),
        (
            "related-parties/00000000-0000-0000-0000-000000000001/shareholdings",
            {
                "share_type": "ordinary",
                "shareholder_rights": "voting",
                "number_of_shares": "1",
                "pct_shareholding": "1",
            },
            404,
        ),
        (
            "related-parties/00000000-0000-0000-0000-000000000001/shareholdings/00000000-0000-0000-0000-000000000002",
            {
                "share_type": "ordinary",
                "shareholder_rights": "voting",
                "number_of_shares": "1",
                "pct_shareholding": "1",
            },
            404,
        ),
        ("outlets", {"outlet_type": "branch", "name": "Synthetic branch"}, 403),
        (
            "outlets/00000000-0000-0000-0000-000000000001",
            {"outlet_type": "branch", "name": "Synthetic branch"},
            404,
        ),
        ("products", {"name": "Synthetic product", "product_type": "savings"}, 403),
        (
            "products/00000000-0000-0000-0000-000000000001",
            {"name": "Synthetic product", "product_type": "savings"},
            404,
        ),
        ("licenses", {"license_name": "Synthetic licence"}, 403),
        (
            "licenses/00000000-0000-0000-0000-000000000001",
            {"license_name": "Synthetic licence"},
            404,
        ),
        ("name-history", {"previous_name": "Synthetic former name"}, 403),
        (
            "name-history/00000000-0000-0000-0000-000000000001",
            {"previous_name": "Synthetic former name"},
            404,
        ),
    ],
)
def test_every_mutation_requires_exact_authority_before_side_effects(
    db_client: TestClient, db_session: Session, path: str, payload: dict[str, object], expected: int
) -> None:
    before = db_session.scalar(select(func.count()).select_from(AuditEvent))
    method = (
        "put"
        if path == "institution-profile"
        or "00000000" in path
        and not path.endswith("shareholdings")
        else "post"
    )
    response = db_client.request(
        method,
        f"{BASE}/{path}",
        headers=headers(),
        json={"reason": "Denied synthetic change", **payload},
    )
    assert response.status_code == expected, response.text
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == before


def test_evaluator_failure_denies_closed(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant(db_session, sensitivity=SensitivityScope.ALL, role=RoleBundle.ANALYST)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Synthetic unavailable evaluator")

    monkeypatch.setattr(authorization, "evaluate_permission", fail)
    response = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert response.status_code == 403, response.text
    response = db_client.post(
        f"{BASE}/outlets",
        headers=headers(),
        json={
            "reason": "Denied synthetic create",
            "outlet_type": "branch",
            "name": "Unwritten",
        },
    )
    assert response.status_code == 403, response.text
    assert db_session.scalar(select(Outlet.id).where(Outlet.bank_id == BANK)) is None


def test_server_generation_reader_retains_complete_source_without_dashboard_authority(
    db_client: TestClient, db_session: Session
) -> None:
    source = public.get_full_profile(db_session, TenantContext(organization_id=ORG_1), BANK)
    assert source.profile is not None
    assert source.profile.tin == "SYNTHETIC-TAX-ID"
    response = db_client.get(f"{BASE}/institution-profile", headers=headers())
    assert response.status_code == 403, response.text
