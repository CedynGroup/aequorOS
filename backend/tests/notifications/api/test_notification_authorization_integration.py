from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    DataScope,
    Module,
    ModuleScope,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.models import AuthorizationBinding, Bank, Notification, RegulatorySubmissionEvent
from app.notifications.service import notifications, reporting_deadline_scan
from app.reporting.schemas.packages import ReportingObligationRead
from app.reporting.service.registry import REGISTRY
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.reporting.api.test_package_authorization_integration import _add_bank, _grant, _package
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

BASE = "/api/v1/notifications"
SIBLING = "BK-NOTIF002"
FOREIGN = "BK-NOTIF003"

_REQUEST_ID = re.compile(r'"request_id":"[^"]*"')


def _opaque_body(response: Any) -> str:
    """A 404 body with its per-request correlation id masked out."""
    return _REQUEST_ID.sub('"request_id":""', response.text)


@pytest.fixture
def return_notifications(
    db_client: TestClient, db_session: Session
) -> dict[str, dict[str, set[str]]]:
    db_session.execute(delete(AuthorizationBinding))
    db_session.commit()
    result: dict[str, set[str]] = {"ordinary": set(), "icaap": set(), "hidden": set()}
    deadline_only: dict[str, set[str]] = {"ordinary": set(), "icaap": set()}
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
                    module=Module.REGULATORY,
                    sensitivity=Sensitivity.CONFIDENTIAL,
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
            # The bank-scoped deadline rows only: under locator scoping the
            # org-wide package rows need an organization-scope grant, so the
            # expectations below must not lump them together.
            if key in deadline_only:
                deadline_only[key].update(
                    str(row.id)
                    for row in db_session.scalars(
                        select(Notification).where(
                            Notification.organization_id == org_id,
                            Notification.entity_type == "bank",
                            Notification.entity_id == bank_id,
                            Notification.type.contains(f":{code}:"),
                        )
                    )
                )
    assert len(result["ordinary"]) == len(result["icaap"]) == 7
    assert len(deadline_only["ordinary"]) == len(deadline_only["icaap"]) == 5
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
            module=Module.REGULATORY,
            sensitivity=Sensitivity.CONFIDENTIAL,
        )[0]
        result["hidden"].add(str(row.id))
    general = notifications.emit(
        db_session,
        ctx,
        type="account.notice",
        severity="info",
        title="Account notice",
        body="General",
        module=Module.REGULATORY,
        sensitivity=Sensitivity.CONFIDENTIAL,
    )[0]
    result["general"] = {str(general.id)}
    # A legacy row predating the locator columns: hidden at every read path,
    # whatever the caller holds (no backfill by inference — mission D9).
    legacy = Notification(
        organization_id=ORG_1,
        recipient_user_id=USER_1,
        type="legacy.pre_locator",
        severity="info",
        title="Legacy",
        body="Written before the locator columns existed",
        read_at=None,
    )
    db_session.add(legacy)
    db_session.flush()
    result["hidden"].add(str(legacy.id))
    db_session.commit()
    return {"buckets": result, "deadline_only": deadline_only}


@pytest.mark.parametrize(
    ("module", "scope", "extra_module", "deadline_keys"),
    [
        # No binding at all: default deny, including the org-wide rows.
        (None, DataScope.ALL, None, ()),
        # A complete binding for the WRONG module never composes.
        (ModuleScope.CREDIT, DataScope.BRANCH, None, ()),
        (ModuleScope.CREDIT, DataScope.REGION, None, ()),
        (ModuleScope.CREDIT, DataScope.ALL, None, ()),
        # An exact institution REGULATORY view grant admits that bank's
        # liquidity-family deadline rows; the org-wide rows (package notices,
        # account notice) need an organization-scope grant and stay hidden.
        (ModuleScope.REGULATORY, DataScope.ALL, None, ("ordinary",)),
        # The icaap family additionally requires the CAPITAL module for its
        # return-family gate, so only REGULATORY + CAPITAL together admit both
        # families' deadline rows of the granted bank.
        (ModuleScope.REGULATORY, DataScope.ALL, ModuleScope.CAPITAL, ("ordinary", "icaap")),
    ],
)
def test_return_notification_visibility_covers_counts_and_mutations(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    return_notifications: dict[str, dict[str, set[str]]],
    module: ModuleScope | None,
    scope: DataScope,
    extra_module: ModuleScope | None,
    deadline_keys: tuple[str, ...],
) -> None:
    buckets = return_notifications["buckets"]
    deadline_only = return_notifications["deadline_only"]
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
        if extra_module is not None:
            version = _grant(
                role_bundle=RoleBundle.VIEWER,
                module_scope=extra_module,
                sensitivity_scope=SensitivityScope.CONFIDENTIAL,
                data_scope=scope,
                data_scope_values=() if scope is DataScope.ALL else ("North",),
            )
    auth = headers(roles=("analyst",), authorization_version=version)
    expected: set[str] = set().union(*(deadline_only[key] for key in deadline_keys))
    hidden = set().union(*buckets.values()) - expected
    unknown = db_client.post(f"{BASE}/{uuid4()}/read", headers=auth)
    assert unknown.status_code == 404, unknown.text
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
        # A hidden id is indistinguishable from a nonexistent one, modulo the
        # per-request correlation id.
        assert _opaque_body(refused) == _opaque_body(unknown)
    if expected:
        first_id = next(iter(expected))
        marked = db_client.post(f"{BASE}/{first_id}/read", headers=auth)
        assert marked.status_code == 200, marked.text
        assert marked.json()["read_at"] is not None
    marked_count = 1 if expected else 0
    unread = db_client.get(BASE, headers=auth, params={"unread_only": True}).json()
    assert unread["total"] == unread["unread_count"] == len(expected) - marked_count
    bulk = db_client.post(f"{BASE}/read-all", headers=auth)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["marked"] == len(expected) - marked_count
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
        # Revocation takes effect immediately: nothing stays visible, not even
        # the org-wide rows.
        revoked = db_client.get(BASE, headers=auth).json()
        assert {row["id"] for row in revoked["notifications"]} == set()


def test_a_viewer_with_a_scoped_view_grant_can_mark_visible_notifications_read(
    db_client: TestClient,
    db_session: Session,
    return_notifications: dict[str, dict[str, set[str]]],
) -> None:
    """VAL-NOTIF-003: mark-read and read-all carry no scalar role gate.

    A Viewer whose token holds no write role can still mark the rows its
    scoped ``view`` grants make visible: mark-read succeeds on a visible id,
    a hidden id answers the same opaque 404 as a nonexistent one, read-all
    marks exactly the visible unread rows, and hidden rows are untouched.
    """
    buckets = return_notifications["buckets"]
    deadline_only = return_notifications["deadline_only"]
    version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.ALL,
    )
    auth = headers(roles=("viewer",), authorization_version=version)
    expected = set(deadline_only["ordinary"])
    hidden = set().union(*buckets.values()) - expected
    unknown = db_client.post(f"{BASE}/{uuid4()}/read", headers=auth)
    assert unknown.status_code == 404, unknown.text
    listed = db_client.get(BASE, headers=auth, params={"limit": 100}).json()
    assert {row["id"] for row in listed["notifications"]} == expected
    for notification_id in hidden:
        refused = db_client.post(f"{BASE}/{notification_id}/read", headers=auth)
        assert refused.status_code == 404, refused.text
        assert _opaque_body(refused) == _opaque_body(unknown)
    for notification_id in expected:
        marked = db_client.post(f"{BASE}/{notification_id}/read", headers=auth)
        assert marked.status_code == 200, marked.text
        assert marked.json()["read_at"] is not None
    # Everything visible was just marked, so read-all has nothing left.
    bulk = db_client.post(f"{BASE}/read-all", headers=auth)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["marked"] == 0
    db_session.expire_all()
    for row_id in hidden:
        row = db_session.get(Notification, UUID(row_id))
        assert row is not None
        assert row.read_at is None
    for row_id in expected:
        row = db_session.get(Notification, UUID(row_id))
        assert row is not None
        assert row.read_at is not None


def test_a_viewer_with_a_scoped_view_grant_read_all_marks_exactly_the_visible_rows(
    db_client: TestClient,
    db_session: Session,
    return_notifications: dict[str, dict[str, set[str]]],
) -> None:
    """read-all marks the visible unread rows in one call; hidden rows keep
    their read state whatever the caller once could or could not see."""
    buckets = return_notifications["buckets"]
    deadline_only = return_notifications["deadline_only"]
    version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.ALL,
    )
    auth = headers(roles=("viewer",), authorization_version=version)
    expected = set(deadline_only["ordinary"])
    hidden = set().union(*buckets.values()) - expected
    bulk = db_client.post(f"{BASE}/read-all", headers=auth)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["marked"] == len(expected)
    db_session.expire_all()
    now = datetime.now(UTC)
    for row_id in expected:
        row = db_session.get(Notification, UUID(row_id))
        assert row is not None
        assert row.read_at is not None
        assert abs(now - row.read_at) < timedelta(minutes=1)
    for row_id in hidden:
        row = db_session.get(Notification, UUID(row_id))
        assert row is not None
        assert row.read_at is None


@pytest.mark.usefixtures("return_notifications")
def test_a_viewer_without_any_binding_is_denied_without_side_effects(
    db_client: TestClient,
    db_session: Session,
    return_notifications: dict[str, dict[str, set[str]]],
) -> None:
    """No grant, no viewer role: every id — visible or not — is the same 404,
    read-all marks nothing, and no row changes state."""
    all_ids = set().union(*return_notifications["buckets"].values())
    auth = headers(roles=("viewer",), authorization_version=1)
    unknown = db_client.post(f"{BASE}/{uuid4()}/read", headers=auth)
    assert unknown.status_code == 404, unknown.text
    for notification_id in all_ids:
        refused = db_client.post(f"{BASE}/{notification_id}/read", headers=auth)
        assert refused.status_code == 404, refused.text
        assert _opaque_body(refused) == _opaque_body(unknown)
    bulk = db_client.post(f"{BASE}/read-all", headers=auth)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["marked"] == 0
    db_session.expire_all()
    for row_id in all_ids:
        row = db_session.get(Notification, UUID(row_id))
        assert row is not None
        assert row.read_at is None


@pytest.mark.usefixtures("return_notifications")
def test_the_demo_mode_veto_hides_notifications_with_a_matching_grant(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings  # noqa: PLC0415

    version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.ALL,
        data_scope_values=(),
    )
    monkeypatch.setattr(get_settings().app, "demo_mode", True)
    auth = headers(roles=("analyst",), authorization_version=version)
    payload = db_client.get(BASE, headers=auth).json()
    assert payload["total"] == payload["unread_count"] == 0
