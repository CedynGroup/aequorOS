"""Configurator authority is complete, active, and independent of read authority."""

from datetime import UTC, date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter
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
from app.liquidity.schemas.liquidity_thresholds import (
    LiquidityHaircutWriteAcknowledgement,
    LiquidityThresholdWriteAcknowledgement,
)
from app.liquidity.service import liquidity_thresholds
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
from tests.liquidity.configure_helpers import CONFIGURATOR, configure_headers
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


def _private_policy(path: str) -> None:
    """A prior generation, untouched code and sibling policy must stay private."""
    with get_sessionmaker()() as db:
        for bank_id in (BANK, OTHER):
            common = {
                "organization_id": ORG_1,
                "bank_id": bank_id,
                "jurisdiction_code": "GH",
                "effective_from": date(2026, 1, 1),
                "approved_by": "Private prior Board evidence",
                "approval_timestamp": utc_now(),
                "notes": "Read-protected prior policy",
            }
            if path == "liquidity-thresholds":
                for code in ("narrow_to_volatile", "broad_to_volatile"):
                    db.add(
                        ParamLiquidityThreshold(
                            **common,
                            institution_class="bank",
                            threshold_code=code,
                            threshold_pct=Decimal("111"),
                        )
                    )
            else:
                for asset_class in ("CASH", "PRIVATE_ASSET"):
                    db.add(
                        ParamLiquidityHaircut(
                            **common,
                            asset_class=asset_class,
                            haircut_pct=Decimal("11"),
                        )
                    )
        db.commit()


def _assert_write_acknowledgement(path: str, content: bytes, effective_from: date) -> UUID | None:
    if path == "liquidity/ewis":
        assert content == b""
        return None
    body = TypeAdapter(dict[str, JsonValue]).validate_json(content)
    assert set(body) == {
        "bank_id",
        "effective_from",
        "approved_by",
        "approval_timestamp",
        "notes",
        "written",
    } | ({"institution_class"} if path == "liquidity-thresholds" else set())
    raw_written = body["written"]
    assert isinstance(raw_written, list) and len(raw_written) == 1
    raw_row = raw_written[0]
    assert isinstance(raw_row, dict)
    if path == "liquidity-thresholds":
        ack = LiquidityThresholdWriteAcknowledgement.model_validate_json(content)
        assert ack.institution_class == "bank"
        assert set(raw_row) == {"id", "threshold_code", "threshold_pct"}
        assert ack.written[0].threshold_code == "narrow_to_volatile"
        assert ack.written[0].threshold_pct == Decimal("85")
        model = ParamLiquidityThreshold
    else:
        ack = LiquidityHaircutWriteAcknowledgement.model_validate_json(content)
        assert set(raw_row) == {"id", "asset_class", "haircut_pct"}
        assert ack.written[0].asset_class == "CASH"
        assert ack.written[0].haircut_pct == Decimal("5")
        model = ParamLiquidityHaircut
    assert ack.bank_id == BANK
    assert ack.effective_from == effective_from
    assert ack.approved_by == "Board minute"
    assert ack.notes is None
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        row = db.scalar(
            select(model).where(
                model.id == ack.written[0].id,
                model.organization_id == ORG_1,
                model.bank_id == BANK,
            )
        )
        assert row is not None
        assert row.bank_id == BANK
        assert row.effective_from == effective_from
        # SQLite drops timezone metadata; PostgreSQL retains it.
        stored_timestamp = row.approval_timestamp
        if stored_timestamp.tzinfo is None:
            stored_timestamp = stored_timestamp.replace(tzinfo=UTC)
        assert stored_timestamp == ack.approval_timestamp
    return ack.written[0].id


@pytest.mark.parametrize("path", PATHS)
def test_configure_only_put_succeeds_but_read_needs_separate_viewer(
    db_client: TestClient, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, version = _grant()
    auth = headers(roles=("viewer",), authorization_version=version)
    if path != "liquidity/ewis":
        _private_policy(path)

    def unexpected_read(*args: object, **kwargs: object) -> None:
        pytest.fail("PUT must not resolve a read-protected register")

    with monkeypatch.context() as patch:
        patch.setattr(liquidity_thresholds, "get_register", unexpected_read)
        patch.setattr(liquidity_thresholds, "get_haircut_schedule", unexpected_read)
        response = db_client.put(f"/api/v1/banks/{BANK}/{path}", headers=auth, json=_payload(path))
    assert response.status_code == (204 if path == "liquidity/ewis" else 200), response.text
    first_id = _assert_write_acknowledgement(path, response.content, date(2026, 4, 1))
    assert db_client.get(f"/api/v1/banks/{BANK}/{path}", headers=auth).status_code == 403
    _, version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)
    response = db_client.get(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(authorization_version=version),
    )
    assert response.status_code == 200, response.text
    # The same acknowledgement contract applies with separate read authority;
    # registers/history remain available only via GET.
    payload = _payload(path)
    if path != "liquidity/ewis":
        payload["effective_from"] = "2026-06-01"
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}",
        headers=headers(authorization_version=version),
        json=payload,
    )
    assert response.status_code == (204 if path == "liquidity/ewis" else 200), response.text
    second_id = _assert_write_acknowledgement(path, response.content, date(2026, 6, 1))
    if path != "liquidity/ewis":
        assert second_id != first_id
        read = db_client.get(
            f"/api/v1/banks/{BANK}/{path}?as_of=2026-06-30",
            headers=headers(authorization_version=version),
        )
        assert read.status_code == 200, read.text
        register = TypeAdapter(dict[str, JsonValue]).validate_json(read.content)
        history = register["history"]
        assert isinstance(history, list) and len(history) == 4


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


def test_configure_fixture_grants_each_bank_without_duplicate_bindings(
    db_client: TestClient,
) -> None:
    for bank_id in (BANK, OTHER, BANK):
        response = db_client.put(
            f"/api/v1/banks/{bank_id}/liquidity/ewis",
            headers=configure_headers(bank_id),
            json=_payload("liquidity/ewis"),
        )
        assert response.status_code == 204, response.text
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthorizationBinding)
                .where(
                    AuthorizationBinding.organization_id == ORG_1,
                    AuthorizationBinding.principal_user_id == CONFIGURATOR,
                    AuthorizationBinding.role_bundle == RoleBundle.CONFIGURATOR.value,
                )
            )
            == 2
        )
