"""Configurator authority is complete, active, and independent of read authority."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import delete, func, select

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    Job,
    LiquidityEwiIndicator,
    ParamLiquidityHaircut,
    ParamLiquidityThreshold,
    User,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BANK = "BK-LIQCFG01"
OTHER = "BK-LIQCFG02"
PATHS = ("liquidity-thresholds", "liquidity-haircuts", "liquidity/ewis")


@pytest.fixture(autouse=True)
def empty_authority(db_client: TestClient) -> None:
    with get_sessionmaker()() as db:
        db.execute(delete(AuthorizationBinding))
        db.add_all(
            Bank(
                id=bank_id,
                organization_id=ORG_1,
                name="Configuration test institution",
                jurisdiction_code="GH",
                currency="GHS",
                short_name="Config test",
                license_type="universal",
                institution_type="universal_bank",
            )
            for bank_id in (BANK, OTHER)
        )
        db.commit()


def _grant(
    bundle: RoleBundle = RoleBundle.CONFIGURATOR,
    *,
    bank_id: str = BANK,
    module: ModuleScope = ModuleScope.LIQUIDITY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
) -> tuple[UUID, int]:
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        binding = authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION, bank_id, module, sensitivity
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "configure-test"),
            reason="Verify explicit liquidity configuration authority",
        )
        user = db.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version


def _payload(path: str) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"approved_by": "Board minute", "reason": "Annual review"}
    if path == "liquidity/ewis":
        result["indicators"] = [{"code": "funding_concentration", "watch_threshold": "50"}]
    else:
        result["effective_from"] = "2026-04-01"
        result["thresholds" if path == "liquidity-thresholds" else "haircuts"] = (
            {"narrow_to_volatile": "85"} if path == "liquidity-thresholds" else {"CASH": "5"}
        )
    return result


def _state() -> list[int | None]:
    with get_sessionmaker()() as db:
        return [
            db.scalar(select(func.count()).select_from(model))
            for model in (
                ParamLiquidityThreshold,
                ParamLiquidityHaircut,
                LiquidityEwiIndicator,
                AuditEvent,
                Job,
            )
        ]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "bundle",
    [None, RoleBundle.ANALYST, RoleBundle.APPROVER, RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER],
)
def test_scalar_and_nonconfigure_bindings_deny_without_side_effects(
    db_client: TestClient, path: str, bundle: RoleBundle | None
) -> None:
    version = 1 if bundle is None else _grant(bundle)[1]
    before = _state()
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(roles=("admin", "approver", "analyst"), authorization_version=version),
        json=_payload(path),
    )
    assert response.status_code == 403, response.text
    assert _state() == before


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "mismatch",
    ["bank", "module", "sensitivity", "expired", "future", "suspended", "revoked", "partial"],
)
def test_incomplete_or_inactive_configuration_binding_denies(
    db_client: TestClient, path: str, mismatch: str
) -> None:
    binding_id, version = _grant(
        bank_id=OTHER if mismatch == "bank" else BANK,
        module=ModuleScope.CREDIT if mismatch in ("module", "partial") else ModuleScope.LIQUIDITY,
        sensitivity=(
            SensitivityScope.CONFIDENTIAL
            if mismatch == "sensitivity"
            else SensitivityScope.RESTRICTED
        ),
    )
    if mismatch == "partial":
        _, version = _grant(RoleBundle.APPROVER)
    if mismatch in ("expired", "future", "suspended", "revoked"):
        with get_sessionmaker()() as db:
            binding = db.get(AuthorizationBinding, binding_id)
            assert binding is not None
            now = utc_now()
            if mismatch == "expired":
                binding.valid_from = now - timedelta(days=2)
                binding.valid_until = now - timedelta(days=1)
            elif mismatch == "future":
                binding.valid_from = now + timedelta(days=1)
            else:
                binding.status = mismatch
                if mismatch == "revoked":
                    binding.revoked_at = now
                    binding.revoked_by_type = GrantorType.SYSTEM.value
                    binding.revoked_by_id = "configure-test"
                    binding.revoked_reason = "lifecycle denial"
            authorization.invalidate_user_authorization(
                db, organization_id=ORG_1, user_id=USER_1, reason="binding lifecycle test"
            )
            db.commit()
            user = db.get(User, USER_1)
            assert user is not None
            version = user.authorization_version
    before = _state()
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(roles=("approver",), authorization_version=version),
        json=_payload(path),
    )
    assert response.status_code == 403, response.text
    assert _state() == before


@pytest.mark.parametrize("path", PATHS)
def test_configure_only_put_succeeds_but_read_needs_separate_viewer(
    db_client: TestClient, path: str
) -> None:
    _, version = _grant()
    auth = headers(roles=("viewer",), authorization_version=version)
    response = db_client.put(f"/api/v1/banks/{BANK}/{path}", headers=auth, json=_payload(path))
    assert response.status_code == (204 if path == "liquidity/ewis" else 200), response.text
    assert db_client.get(f"/api/v1/banks/{BANK}/{path}", headers=auth).status_code == 403
    _, version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)
    response = db_client.get(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(authorization_version=version),
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path", PATHS)
def test_revocation_rejects_stale_session_and_fresh_session_has_no_authority(
    db_client: TestClient, path: str
) -> None:
    binding_id, version = _grant()
    with get_sessionmaker()() as db:
        binding = db.get(AuthorizationBinding, binding_id)
        assert binding is not None
        binding.status = "revoked"
        binding.revoked_at = utc_now()
        binding.revoked_by_type = GrantorType.SYSTEM.value
        binding.revoked_by_id = "configure-test"
        binding.revoked_reason = "revoke configure"
        authorization.invalidate_user_authorization(
            db, organization_id=ORG_1, user_id=USER_1, reason="revoke configure"
        )
        db.commit()
        user = db.get(User, USER_1)
        assert user is not None
        fresh = user.authorization_version
    before = _state()
    for authv, expected in ((version, 401), (fresh, 403)):
        response = db_client.put(
            f"/api/v1/banks/{BANK}/{path}",
            headers=headers(authorization_version=authv),
            json=_payload(path),
        )
        assert response.status_code == expected, response.text
    assert _state() == before


@pytest.mark.parametrize("path", PATHS)
def test_evaluator_failure_denies_closed(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _, version = _grant()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail)
    before = _state()
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(authorization_version=version),
        json=_payload(path),
    )
    assert response.status_code == 403, response.text
    assert _state() == before


@pytest.mark.parametrize("path", PATHS)
def test_cross_tenant_bank_is_hidden(db_client: TestClient, path: str) -> None:
    before = _state()
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}", headers=headers(ORG_2), json=_payload(path)
    )
    assert response.status_code == 404, response.text
    assert _state() == before
