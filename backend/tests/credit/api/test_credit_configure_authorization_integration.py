"""Configurator authority is complete, active, and independent of read authority.

The four CREDIT register PUTs follow the same allow/deny rule as the LIQ
registers (issue 184, m8): one complete active ``credit``/``restricted``
``configure`` binding for the exact institution — never a scalar role, never a
partial sentence — and the response is write evidence built from this request
only (D3), never a resolved register a configure-only caller cannot read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
from app.credit.service import credit_params
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    Job,
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    User,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BANK = "BK-CRDCFG01"
OTHER = "BK-CRDCFG02"
PATHS = ("ecl-assumptions", "crm-haircuts", "concentration-limits", "credit-thresholds")


@pytest.fixture(autouse=True)
def empty_authority(db_client: TestClient) -> None:
    with get_sessionmaker()() as db:
        db.execute(delete(AuthorizationBinding))
        db.add_all(
            Bank(
                id=bank_id,
                organization_id=ORG_1,
                name="Credit configuration test institution",
                jurisdiction_code="GH",
                currency="GHS",
                short_name="Credit config test",
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
    module: ModuleScope = ModuleScope.CREDIT,
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "credit-configure-test"),
            reason="Verify explicit credit configuration authority",
        )
        user = db.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version


def _payload(path: str) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"approved_by": "Board minute", "reason": "Annual review"}
    if path == "ecl-assumptions":
        result["assumptions"] = [
            {"segment": "ALL", "stage": 1, "pd_pct": "2", "lgd_pct": "40"}
        ]
        result["effective_from"] = "2026-04-01"
    elif path == "crm-haircuts":
        result["haircuts"] = {"CASH": "5"}
        result["effective_from"] = "2026-04-01"
    elif path == "concentration-limits":
        result["limits"] = [
            {"dimension": "single_name", "limit_kind": "share_of_capital_pct", "value": "25"}
        ]
        result["effective_from"] = "2026-04-01"
    else:
        result["thresholds"] = {"npl_board_trigger_pct": "5"}
        result["effective_from"] = "2026-04-01"
    return result


def _state() -> list[int | None]:
    with get_sessionmaker()() as db:
        return [
            db.scalar(select(func.count()).select_from(model))
            for model in (
                ParamEclAssumption,
                ParamCrmHaircut,
                ParamConcentrationLimit,
                ParamCreditThreshold,
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
        module=ModuleScope.LIQUIDITY if mismatch in ("module", "partial") else ModuleScope.CREDIT,
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
                    binding.revoked_by_id = "credit-configure-test"
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
    """A prior generation with read-protected evidence must stay private."""
    with get_sessionmaker()() as db:
        common = {
            "organization_id": ORG_1,
            "bank_id": BANK,
            "jurisdiction_code": "GH",
            "effective_from": date(2026, 1, 1),
            "approved_by": "Private prior Board evidence",
            "approval_timestamp": utc_now(),
        }
        if path == "ecl-assumptions":
            db.add(
                ParamEclAssumption(
                    **common,
                    notes="Read-protected prior policy",
                    segment="ALL",
                    stage=1,
                    pd_pct="9",
                    lgd_pct="99",
                )
            )
        elif path == "crm-haircuts":
            for collateral_class in ("CASH", "PRIVATE_ASSET"):
                db.add(
                    ParamCrmHaircut(
                        **common,
                        notes="Read-protected prior policy",
                        collateral_class=collateral_class,
                        haircut_pct="11",
                    )
                )
        elif path == "concentration-limits":
            db.add(
                ParamConcentrationLimit(
                    **common,
                    dimension="single_name",
                    limit_kind="share_of_capital_pct",
                    bucket_key=None,
                    value="99",
                )
            )
        else:
            db.add(
                ParamCreditThreshold(
                    **common, threshold_code="npl_board_trigger_pct", value_pct="9"
                )
            )
        db.commit()


def _assert_write_acknowledgement(path: str, content: bytes, effective_from: date) -> UUID:
    body = TypeAdapter(dict[str, JsonValue]).validate_json(content)
    assert set(body) == {
        "bank_id",
        "effective_from",
        "approved_by",
        "approval_timestamp",
        "written",
    } | ({"notes"} if path in ("ecl-assumptions", "crm-haircuts") else set())
    raw_written = body["written"]
    assert isinstance(raw_written, list) and len(raw_written) == 1
    raw_row = raw_written[0]
    assert isinstance(raw_row, dict)
    if path == "ecl-assumptions":
        assert set(raw_row) == {"id", "segment", "stage", "pd_pct", "lgd_pct"}
        model: type[
            ParamEclAssumption | ParamCrmHaircut | ParamConcentrationLimit | ParamCreditThreshold
        ] = ParamEclAssumption
    elif path == "crm-haircuts":
        assert set(raw_row) == {"id", "collateral_class", "haircut_pct"}
        model = ParamCrmHaircut
    elif path == "concentration-limits":
        assert set(raw_row) == {"id", "dimension", "limit_kind", "bucket_key", "value"}
        model = ParamConcentrationLimit
    else:
        assert set(raw_row) == {"id", "threshold_code", "value_pct"}
        model = ParamCreditThreshold
    row_id = UUID(str(raw_row["id"]))
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        row = db.get(model, row_id)
        assert row is not None
        assert row.effective_from == effective_from
        assert row.approved_by == "Board minute"
        # SQLite drops timezone metadata; PostgreSQL retains it.
        stored_timestamp = row.approval_timestamp
        if stored_timestamp.tzinfo is None:
            stored_timestamp = stored_timestamp.replace(tzinfo=UTC)
        ack = TypeAdapter(dict[str, JsonValue]).validate_json(content)
        acked_timestamp = TypeAdapter(datetime).validate_python(ack["approval_timestamp"])
        assert stored_timestamp == acked_timestamp
    return row_id


def _assert_private_conflicts(
    db_client: TestClient, path: str, auth: dict[str, str], stored_date: date
) -> None:
    for effective_from in (stored_date - timedelta(days=1), stored_date):
        payload = _payload(path)
        payload["effective_from"] = effective_from.isoformat()
        before = _state()
        response = db_client.put(f"/api/v1/banks/{BANK}/{path}", headers=auth, json=payload)
        assert response.status_code == 409, response.text
        assert (
            "open generation already starts on or after the requested effective date."
            in response.text
        )
        assert stored_date.isoformat() not in response.text
        assert "Private prior Board evidence" not in response.text
        assert "Read-protected prior policy" not in response.text
        assert _state() == before


@pytest.mark.parametrize("path", PATHS)
def test_configure_only_put_succeeds_but_read_needs_separate_viewer(
    db_client: TestClient, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, version = _grant()
    auth = headers(roles=("viewer",), authorization_version=version)
    _private_policy(path)
    _assert_private_conflicts(db_client, path, auth, date(2026, 1, 1))

    def unexpected_read(*args: object, **kwargs: object) -> None:
        pytest.fail("PUT must not resolve a read-protected register")

    with monkeypatch.context() as patch:
        patch.setattr(credit_params, "get_ecl_register", unexpected_read)
        patch.setattr(credit_params, "get_crm_register", unexpected_read)
        patch.setattr(credit_params, "get_concentration_limit_register", unexpected_read)
        patch.setattr(credit_params, "get_credit_threshold_register", unexpected_read)
        response = db_client.put(f"/api/v1/banks/{BANK}/{path}", headers=auth, json=_payload(path))
    assert response.status_code == 200, response.text
    _assert_write_acknowledgement(path, response.content, date(2026, 4, 1))
    _assert_private_conflicts(db_client, path, auth, date(2026, 4, 1))
    assert db_client.get(f"/api/v1/banks/{BANK}/{path}", headers=auth).status_code == 403
    _, version = _grant(RoleBundle.VIEWER)
    read_auth = headers(authorization_version=version)
    read = db_client.get(f"/api/v1/banks/{BANK}/{path}?as_of=2026-06-30", headers=read_auth)
    assert read.status_code == 200, read.text
    # The same acknowledgement contract applies with separate read authority;
    # registers/history remain available only via GET.
    payload = _payload(path)
    payload["effective_from"] = "2026-06-01"
    response = db_client.put(
        f"/api/v1/banks/{BANK}/{path}",
        headers=read_auth,
        json=payload,
    )
    assert response.status_code == 200, response.text
    _assert_write_acknowledgement(path, response.content, date(2026, 6, 1))
    _assert_private_conflicts(db_client, path, read_auth, date(2026, 6, 1))
    read = db_client.get(
        f"/api/v1/banks/{BANK}/{path}?as_of=2026-06-30",
        headers=read_auth,
    )
    assert read.status_code == 200, read.text


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
        binding.revoked_by_id = "credit-configure-test"
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
