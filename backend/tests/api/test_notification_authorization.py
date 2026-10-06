from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import DataScope, ModuleScope, RoleBundle, SensitivityScope
from app.models import AuthorizationBinding, Bank, Notification, RegulatorySubmissionEvent
from app.schemas.regulatory_reporting import ReportingObligationRead
from app.services import notifications, reporting_deadline_scan
from app.services.regulatory_reporting.registry import REGISTRY
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.api.test_package_authorization import _add_bank, _grant, _package
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

BASE = "/api/v1/notifications"
SIBLING = "BK-NOTIF002"
FOREIGN = "BK-NOTIF003"


@pytest.fixture
def return_notifications(db_client: TestClient, db_session: Session) -> dict[str, set[str]]:
    db_session.execute(delete(AuthorizationBinding))
    db_session.commit()
    result: dict[str, set[str]] = {"ordinary": set(), "icaap": set(), "hidden": set()}
    today = date(2026, 4, 20)
    for bank_id, org_id in ((SAMPLE_BANK_ID, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
        _add_bank(bank_id, organization_id=org_id)
        bank = db_session.get(Bank, bank_id)
        assert bank is not None
        ctx = TenantContext(organization_id=org_id)
        for family, code in (("liquidity", "LCR-NSFR"), ("icaap", "ICAAP-REPORT")):
            key = (
                ("icaap" if family == "icaap" else "ordinary")
                if bank_id == SAMPLE_BANK_ID
                else "hidden"
            )
            package_id = _package(
                organization_id=org_id,
                bank_id=bank_id,
                family=family,
                return_code=code,
                status="submitted",
            )
            db_session.add(
                RegulatorySubmissionEvent(
                    organization_id=org_id,
                    package_id=package_id,
                    channel="email",
                    event="submitted",
                    detail={"pending_orass_reupload": True},
                )
            )
            db_session.flush()
            definition = REGISTRY[code]
            for days_left in (7, 3, 1, -1):
                obligation = ReportingObligationRead(
                    return_code=code,
                    return_family=definition.family,
                    title=definition.title,
                    frequency=definition.frequency,
                    fidelity=definition.fidelity,
                    default_channel=definition.default_channel,
                    reporting_date=date(2026, 3, 31),
                    due_date=today + timedelta(days=days_left),
                    package_id=package_id,
                    package_status="submitted",
                    package_version=1,
                    rag="due_soon" if days_left > 0 else "overdue",
                )
                reporting_deadline_scan._scan_obligation(db_session, ctx, bank, obligation, today)
            for recipient in (None, USER_1 if org_id == ORG_1 else USER_2):
                notifications.emit(
                    db_session,
                    ctx,
                    type="attestation.signature_requested",
                    severity="warning",
                    title="Signature requested",
                    body="Submitted return requires attention",
                    entity_type="regulatory_package",
                    entity_id=package_id,
                    recipient_user_id=recipient,
                )
            result[key].update(
                str(row.id)
                for row in db_session.scalars(
                    select(Notification).where(
                        Notification.organization_id == org_id,
                        (Notification.entity_id == bank_id)
                        | (Notification.entity_id == str(package_id)),
                        Notification.type.contains(f":{code}:")
                        | (Notification.entity_id == str(package_id)),
                    )
                )
            )
    assert len(result["ordinary"]) == len(result["icaap"]) == 7
    ctx = TenantContext(organization_id=ORG_1)
    for entity_type, entity_id, type_ in (
        ("bank", SAMPLE_BANK_ID, "reporting.deadline.overdue:UNKNOWN:2026-03-31"),
        ("regulatory_package", str(uuid4()), "reporting.package.approved"),
        ("bank", "BK-MISSING1", "reporting.deadline.overdue:LCR-NSFR:2026-03-31"),
    ):
        row = notifications.emit(
            db_session,
            ctx,
            type=type_,
            severity="warning",
            title="Unavailable",
            body="Hidden",
            entity_type=entity_type,
            entity_id=entity_id,
        )[0]
        result["hidden"].add(str(row.id))
    general = notifications.emit(
        db_session,
        ctx,
        type="account.notice",
        severity="info",
        title="Account notice",
        body="General",
    )[0]
    result["general"] = {str(general.id)}
    db_session.commit()
    return result


@pytest.mark.parametrize(
    "grant_case",
    [
        (None, DataScope.ALL, None),
        (ModuleScope.CREDIT, DataScope.BRANCH, None),
        (ModuleScope.CREDIT, DataScope.REGION, None),
        (ModuleScope.CREDIT, DataScope.ALL, None),
        (ModuleScope.REGULATORY, DataScope.ALL, "ordinary"),
        (ModuleScope.CAPITAL, DataScope.ALL, "icaap"),
    ],
)
def test_return_notification_visibility_covers_counts_and_mutations(
    db_client: TestClient,
    db_session: Session,
    return_notifications: dict[str, set[str]],
    grant_case: tuple[ModuleScope | None, DataScope, str | None],
) -> None:
    module, scope, visible_key = grant_case
    version = 1
    if module is not None:
        version = _grant(
            role_bundle=RoleBundle.VIEWER,
            module_scope=module,
            sensitivity_scope=(
                SensitivityScope.CONFIDENTIAL
                if module is ModuleScope.CAPITAL
                else SensitivityScope.RESTRICTED
            ),
            data_scope=scope,
            data_scope_values=() if scope is DataScope.ALL else ("North",),
        )
    auth = headers(roles=("analyst",), authorization_version=version)
    expected = return_notifications["general"] | (
        return_notifications[visible_key] if visible_key else set()
    )
    hidden = set().union(*return_notifications.values()) - expected
    response = db_client.get(BASE, headers=auth, params={"limit": 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == payload["unread_count"] == len(expected)
    assert payload["has_more"] is (len(expected) > 1)
    all_rows = db_client.get(BASE, headers=auth, params={"limit": 100}).json()
    assert {row["id"] for row in all_rows["notifications"]} == expected
    for notification_id in hidden:
        refused = db_client.post(f"{BASE}/{notification_id}/read", headers=auth)
        assert refused.status_code == 404, refused.text
    first_id = next(iter(expected))
    marked = db_client.post(f"{BASE}/{first_id}/read", headers=auth)
    assert marked.status_code == 200, marked.text
    assert marked.json()["read_at"] is not None
    unread = db_client.get(BASE, headers=auth, params={"unread_only": True}).json()
    assert unread["total"] == unread["unread_count"] == len(expected) - 1
    bulk = db_client.post(f"{BASE}/read-all", headers=auth)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["marked"] == len(expected) - 1
    db_session.expire_all()
    for row_id in hidden:
        notification = db_session.get(Notification, UUID(row_id))
        assert notification is not None
        assert notification.read_at is None
    if module is not None:
        db_session.execute(
            update(AuthorizationBinding).values(
                status="revoked",
                revoked_at=datetime.now(UTC),
                revoked_by_type="system",
                revoked_by_id="notification-fixture",
                revoked_reason="Remove return visibility",
            )
        )
        db_session.commit()
        revoked = db_client.get(BASE, headers=auth).json()
        assert {row["id"] for row in revoked["notifications"]} == return_notifications["general"]
